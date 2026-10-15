"""
EnterpriseKG Agent Comparison — LLM-judge scorer + report generator.

Reads agents/tests/results/results_M{1,2,3}.csv produced by run_tests.py and
asks gpt-5.4 to score each method's answer to each question on a 0-3 scale,
then writes:

    agents/tests/results/scores.csv        — per-question per-method score + reasoning
    agents/tests/results/RESULTS_REPORT.md — human-readable comparison report

Score scale:
    0 = no answer / refusal / wrong domain
    1 = wrong answer (data hallucinated or misinterpreted)
    2 = partial (right shape but missing rows, wrong filter interpretation, no totals, etc.)
    3 = fully correct (correct interpretation, plausible rows, citations or SQL evidence)

Usage:
    python score_runs.py
    python score_runs.py --judge-model gpt-5.4
    python score_runs.py --skip-judge   # just rebuild the report from existing scores.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import warnings
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_a, **_kw):
        return False

from openai import OpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider


HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
ENV_PATH = HERE / ".env"

METHODS = ["M1", "M2", "M3"]
METHOD_LABELS = {
    "M1": "M1 — Single Fabric data agent",
    "M2": "M2 — 3-agent decomposition (planner + people/project/client)",
    "M3": "M3 — Foundry-direct (T-SQL via execute_sql)",
}

# Approx pricing per 1K tokens for gpt-5.4 (placeholder — adjust if known)
PRICE_PER_1K_INPUT = 0.005
PRICE_PER_1K_OUTPUT = 0.015


_JUDGE_INSTRUCTIONS = """You are an impartial judge scoring three AI agent answers
to the same data question about an Enterprise engineering knowledge graph.

The data covers Staff (with offices, expertise via RoleName, line managers),
Projects (with sector, status, financials, project teams), and Clients (with
ClientSector, opportunity pipeline, RM). Answers can use either an ontology
graph query (GQL) or T-SQL against ENT_* lakehouse tables.

For EACH method (M1, M2, M3) score the answer on this 0-3 scale:
  0 = no answer, refusal, system error, or wholly off-topic
  1 = wrong: misinterpreted the question, hallucinated data, or returned a
      list that obviously doesn't match the question (e.g. clients when staff
      were asked, project names treated as client names, etc.)
  2 = partial: right shape but missing pieces — e.g. only counts no names,
      only one of two requested entities, defers steps to the user, picks a
      defensible-but-narrower filter than the question implies, or hand-waves
      "this would need another query"
  3 = full: correct interpretation, concrete rows/numbers/SQL, internally
      consistent, addresses every part of the question

Be strict: a verbose answer that ducks the question is a 1 or 2, not a 3.
Penalise made-up entity names. Reward concrete tables and named sources.

Output STRICT JSON (no prose, no fences) with this exact schema:
{
  "M1": {"score": 0|1|2|3, "reasoning": "one sentence"},
  "M2": {"score": 0|1|2|3, "reasoning": "one sentence"},
  "M3": {"score": 0|1|2|3, "reasoning": "one sentence"},
  "best_method": "M1|M2|M3|tie",
  "notes": "one short sentence on agreement/disagreement between answers"
}
"""


def _load_env():
    load_dotenv(ENV_PATH)


def _build_client(judge_model_env: str = "MODEL_DEPLOYMENT_NAME") -> tuple[OpenAI, str]:
    endpoint = os.environ["PROJECT_ENDPOINT"].rstrip("/")
    if "/api/projects/" in endpoint:
        base_url = endpoint.split("/api/projects/")[0] + "/openai/v1"
    else:
        base_url = endpoint + "/openai/v1"
    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(),
        "https://cognitiveservices.azure.com/.default",
    )
    client = OpenAI(
        base_url=base_url,
        api_key=token_provider,
    )
    model = os.environ.get(judge_model_env, "gpt-5.4")
    return client, model


def _read_results() -> dict[str, dict[str, dict]]:
    """Returns {code: {method: row}}."""
    by_code: dict[str, dict[str, dict]] = {}
    for m in METHODS:
        path = RESULTS_DIR / f"results_{m}.csv"
        if not path.exists():
            print(f"  ! missing {path}", file=sys.stderr)
            continue
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                by_code.setdefault(row["Code"], {})[m] = row
    return by_code


def _judge_one(client: OpenAI, model: str, question: str,
                answers: dict[str, str]) -> dict[str, Any]:
    payload = (
        f"Question: {question}\n\n"
        + "\n\n".join(
            f"=== {m} answer ===\n{(answers.get(m) or '<no answer>').strip()}"
            for m in METHODS
        )
    )
    resp = client.responses.create(
        model=model,
        instructions=_JUDGE_INSTRUCTIONS,
        input=payload,
    )
    text = (resp.output_text or "").strip()
    if text.startswith("```"):
        nl = text.find("\n")
        text = text[nl + 1:] if nl != -1 else text
        if text.endswith("```"):
            text = text[:-3]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        return {
            "M1": {"score": 0, "reasoning": "judge JSON parse failed"},
            "M2": {"score": 0, "reasoning": "judge JSON parse failed"},
            "M3": {"score": 0, "reasoning": "judge JSON parse failed"},
            "best_method": "tie",
            "notes": f"parse error: {exc}: raw={text[:200]}",
        }


def _score_all(by_code: dict[str, dict[str, dict]],
                client: OpenAI, model: str) -> list[dict]:
    rows: list[dict] = []
    codes = sorted(by_code.keys())
    for i, code in enumerate(codes, start=1):
        per_method = by_code[code]
        # any method's row has the question text
        sample = next(iter(per_method.values()))
        question = sample["Question"]
        answers = {m: per_method.get(m, {}).get("Answer", "") for m in METHODS}
        print(f"  [{i:2d}/{len(codes)}] {code:<10s} judging…", flush=True)
        verdict = _judge_one(client, model, question, answers)
        rows.append({
            "Code": code,
            "Persona": sample.get("Persona", ""),
            "Theme": sample.get("Theme", ""),
            "Level": sample.get("Level", ""),
            "Question": question,
            "M1_Score": verdict.get("M1", {}).get("score", 0),
            "M1_Reasoning": verdict.get("M1", {}).get("reasoning", ""),
            "M2_Score": verdict.get("M2", {}).get("score", 0),
            "M2_Reasoning": verdict.get("M2", {}).get("reasoning", ""),
            "M3_Score": verdict.get("M3", {}).get("score", 0),
            "M3_Reasoning": verdict.get("M3", {}).get("reasoning", ""),
            "BestMethod": verdict.get("best_method", "tie"),
            "Notes": verdict.get("notes", ""),
        })
    return rows


def _write_scores_csv(rows: list[dict]):
    out = RESULTS_DIR / "scores.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        if not rows:
            return
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {out}")


def _read_scores_csv() -> list[dict]:
    path = RESULTS_DIR / "scores.csv"
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _safe_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _build_report(by_code: dict[str, dict[str, dict]], scores: list[dict]) -> str:
    lines: list[str] = []
    lines.append("# EnterpriseKG Agent Comparison — Results Report")
    lines.append("")
    lines.append("Three methods were evaluated on the same 60+ benchmark questions:")
    lines.append("")
    for m in METHODS:
        lines.append(f"- **{m}** — {METHOD_LABELS[m].split(' — ', 1)[1]}")
    lines.append("")

    # Aggregate per-method stats
    def agg(method: str) -> dict[str, Any]:
        latencies, tokens_in, tokens_out, tool_calls, scores_vals, completed, failed = (
            [], [], [], [], [], 0, 0,
        )
        for code, per in by_code.items():
            row = per.get(method)
            if not row:
                continue
            if row.get("Status") == "completed":
                completed += 1
            else:
                failed += 1
            latencies.append(_safe_float(row.get("LatencySec")))
            tokens_in.append(_safe_int(row.get("PromptTokens")))
            tokens_out.append(_safe_int(row.get("CompletionTokens")))
            tool_calls.append(_safe_int(row.get("ToolCallCount")))
        for s in scores:
            scores_vals.append(_safe_int(s.get(f"{method}_Score")))
        in_total = sum(tokens_in)
        out_total = sum(tokens_out)
        return {
            "n": completed + failed,
            "completed": completed,
            "failed": failed,
            "median_latency": statistics.median(latencies) if latencies else 0,
            "p95_latency": (statistics.quantiles(latencies, n=20)[18]
                             if len(latencies) >= 20 else max(latencies, default=0)),
            "mean_tool_calls": statistics.mean(tool_calls) if tool_calls else 0,
            "tokens_in": in_total,
            "tokens_out": out_total,
            "tokens_total": in_total + out_total,
            "cost_usd": (in_total / 1000 * PRICE_PER_1K_INPUT
                          + out_total / 1000 * PRICE_PER_1K_OUTPUT),
            "mean_score": statistics.mean(scores_vals) if scores_vals else 0,
            "score_dist": {s: scores_vals.count(s) for s in (0, 1, 2, 3)},
        }

    aggs = {m: agg(m) for m in METHODS}

    lines.append("## Headline aggregates")
    lines.append("")
    lines.append("| Metric | M1 | M2 | M3 |")
    lines.append("|---|---:|---:|---:|")
    lines.append(f"| Questions answered | {aggs['M1']['n']} | {aggs['M2']['n']} | {aggs['M3']['n']} |")
    lines.append(f"| Completed | {aggs['M1']['completed']} | {aggs['M2']['completed']} | {aggs['M3']['completed']} |")
    lines.append(f"| Failed/timeout | {aggs['M1']['failed']} | {aggs['M2']['failed']} | {aggs['M3']['failed']} |")
    lines.append(f"| Median latency (s) | {aggs['M1']['median_latency']:.1f} | {aggs['M2']['median_latency']:.1f} | {aggs['M3']['median_latency']:.1f} |")
    lines.append(f"| p95 latency (s) | {aggs['M1']['p95_latency']:.1f} | {aggs['M2']['p95_latency']:.1f} | {aggs['M3']['p95_latency']:.1f} |")
    lines.append(f"| Mean tool calls / question | {aggs['M1']['mean_tool_calls']:.1f} | {aggs['M2']['mean_tool_calls']:.1f} | {aggs['M3']['mean_tool_calls']:.1f} |")
    lines.append(f"| Total prompt tokens | {aggs['M1']['tokens_in']:,} | {aggs['M2']['tokens_in']:,} | {aggs['M3']['tokens_in']:,} |")
    lines.append(f"| Total completion tokens | {aggs['M1']['tokens_out']:,} | {aggs['M2']['tokens_out']:,} | {aggs['M3']['tokens_out']:,} |")
    lines.append(f"| Estimated cost (USD) | ${aggs['M1']['cost_usd']:.2f} | ${aggs['M2']['cost_usd']:.2f} | ${aggs['M3']['cost_usd']:.2f} |")
    lines.append(f"| Mean judge score (0-3) | {aggs['M1']['mean_score']:.2f} | {aggs['M2']['mean_score']:.2f} | {aggs['M3']['mean_score']:.2f} |")
    lines.append("")

    lines.append("## Score distribution (count of questions)")
    lines.append("")
    lines.append("| Score | M1 | M2 | M3 |")
    lines.append("|---|---:|---:|---:|")
    for s in (3, 2, 1, 0):
        label = {3: "3 — full", 2: "2 — partial", 1: "1 — wrong", 0: "0 — none"}[s]
        lines.append(f"| {label} | {aggs['M1']['score_dist'][s]} | {aggs['M2']['score_dist'][s]} | {aggs['M3']['score_dist'][s]} |")
    lines.append("")

    # Best-method count
    if scores:
        best_counts = {"M1": 0, "M2": 0, "M3": 0, "tie": 0}
        for s in scores:
            best_counts[s.get("BestMethod", "tie")] = best_counts.get(s.get("BestMethod", "tie"), 0) + 1
        lines.append("## Per-question winner (judge's pick)")
        lines.append("")
        lines.append("| Winner | Count |")
        lines.append("|---|---:|")
        for k in ("M1", "M2", "M3", "tie"):
            lines.append(f"| {k} | {best_counts.get(k, 0)} |")
        lines.append("")

    # Per-question table
    lines.append("## Per-question scores")
    lines.append("")
    lines.append("| Code | Level | Question | M1 | M2 | M3 | Winner |")
    lines.append("|---|---|---|:---:|:---:|:---:|:---:|")
    for s in scores:
        q = s["Question"].replace("|", "\\|")
        if len(q) > 80:
            q = q[:77] + "..."
        lines.append(
            f"| {s['Code']} | {s.get('Level','')} | {q} | "
            f"{s['M1_Score']} | {s['M2_Score']} | {s['M3_Score']} | {s['BestMethod']} |"
        )
    lines.append("")

    # Failure detail (anything scoring <2 on any method)
    bad = [s for s in scores if any(_safe_int(s[f"{m}_Score"]) < 2 for m in METHODS)]
    if bad:
        lines.append("## Failure / partial detail (any method scored <2)")
        lines.append("")
        for s in bad:
            lines.append(f"### {s['Code']} — {s['Question']}")
            lines.append("")
            for m in METHODS:
                lines.append(f"- **{m}** ({s[f'{m}_Score']}/3): {s[f'{m}_Reasoning']}")
            lines.append(f"- **Notes**: {s.get('Notes','')}")
            lines.append("")

    # Recommendation
    lines.append("## Recommendation")
    lines.append("")
    best = max(METHODS, key=lambda m: aggs[m]["mean_score"])
    cheapest = min(METHODS, key=lambda m: aggs[m]["cost_usd"])
    fastest = min(METHODS, key=lambda m: aggs[m]["median_latency"])
    lines.append(f"- Highest mean score: **{best}** ({aggs[best]['mean_score']:.2f}/3)")
    lines.append(f"- Cheapest: **{cheapest}** (${aggs[cheapest]['cost_usd']:.2f} total)")
    lines.append(f"- Fastest median: **{fastest}** ({aggs[fastest]['median_latency']:.1f}s)")
    lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--judge-model", default=None,
                    help="Override the judge model (default: $MODEL_DEPLOYMENT_NAME).")
    p.add_argument("--skip-judge", action="store_true",
                    help="Reuse existing scores.csv and only rebuild the markdown report.")
    args = p.parse_args()

    _load_env()
    by_code = _read_results()
    if not by_code:
        print("No results CSVs found.", file=sys.stderr)
        return 1

    if args.skip_judge:
        scores = _read_scores_csv()
        if not scores:
            print("scores.csv missing — cannot --skip-judge", file=sys.stderr)
            return 1
    else:
        client, model = _build_client()
        if args.judge_model:
            model = args.judge_model
        print(f"Judging {len(by_code)} questions with {model}…")
        scores = _score_all(by_code, client, model)
        _write_scores_csv(scores)

    report = _build_report(by_code, scores)
    out = RESULTS_DIR / "RESULTS_REPORT.md"
    out.write_text(report, encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
