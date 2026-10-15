"""
Method 6 — Council (Parallel Multi-Strategy Ensemble + Verifier Judge).

Pure Responses-API + function-tool loop, like M4, but with:

  * 3 strategists running in parallel (ThreadPoolExecutor):
        Strategist A — Onto-First   (M4-shaped prompt, tighter budget)
        Strategist B — SQL-First    (schema_card injected, biased away from ontology)
        Strategist C — Decompose    (explicit sub-question planning)
  * A separate Verifier (cheaper model, structured-output) scores all 3
    candidates and picks the winner.
  * If winner_score < 70 OR top-2 differ by < 10, escalate to a Synthesiser
    (more expensive model) that consolidates the 3 candidates into one answer.
  * Few-shot retrieval from m6_fewshot.py (silently skipped if cache missing).

Reuses M4's tool kit verbatim: schema_card, sample_values, query_ontology,
query_lakehouse, final_answer. Reuses fabric_ontology.py and m3_sql_handler.py
unchanged.

Public API
----------
    run_m6(openai, case, deadline, tool_call_counter, token_totals, trace,
           *, on_progress=None) -> (final_response, error_str)

EXACT same signature as run_m4, so it's a drop-in replacement in run_tests.py
and the MCP /ask/stream endpoint.
"""

from __future__ import annotations

import concurrent.futures as _futures
import json
import contextvars
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

# Reuse M4's tool kit verbatim.
from fabric_tools import (  # type: ignore[import-not-found]
    _TOOL_SCHEMAS,
    _execute_tool,
    _format_final_answer,
    _is_function_call,
    _accumulate_tokens,
    _resp_to_jsonable,
    _extract_text,
    _schema_card_payload,
    _FinalWrapper,
    _tools_for,
    _ONTOLOGY_DISABLED_NOTE,
    MAX_TOOL_OUTPUT_CHARS,
)

# Few-shot retrieval — graceful no-op if cache missing.
try:
    from m6_fewshot import retrieve_few_shot, format_few_shot_prompt  # type: ignore[import-not-found]
except Exception:
    def retrieve_few_shot(question: str, k: int = 3,
                            exclude_codes: tuple[str, ...] = ()) -> list[dict]:
        return []
    def format_few_shot_prompt(examples: list[dict]) -> str:
        return ""


# ─── Tunables (per-strategist budgets — tighter than M4) ──────────────────
M6_MAX_TURNS              = 8     # vs M4: 16
M6_MAX_SQL_ATTEMPTS       = 4     # vs M4: 8
M6_MAX_ONTOLOGY_ATTEMPTS  = 2     # vs M4: 3
M6_MAX_EMPTY_NUDGES       = 2

# Cost-tier model deployments. Defaults assume single MODEL_DEPLOYMENT_NAME
# is available; FAST/PRO override only when the user has provisioned them.
DEFAULT_MODEL = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-5.4")
FAST_MODEL    = os.environ.get("MODEL_DEPLOYMENT_NAME_FAST", DEFAULT_MODEL)
PRO_MODEL     = os.environ.get("MODEL_DEPLOYMENT_NAME_PRO",  DEFAULT_MODEL)

# Verifier escalation threshold — below this we run the Synthesiser.
VERIFIER_ESCALATION_SCORE = 70
VERIFIER_TIE_MARGIN        = 10  # if top-2 differ by < this, escalate too

# Per-strategist wall-clock cap (seconds). Verifier + synthesiser get the
# remaining budget out of the run-level deadline.
PER_STRATEGIST_BUDGET_SEC = 180


# ─── Prompts — extracted from the markdown spec ────────────────────────────
_DOCS_PATH = Path(__file__).resolve().parents[1] / "Method6_Council_Instructions.md"


def _load_strategist_prompt(strategist_id: str) -> str:
    """Read Method6 instructions MD and slice out the named system prompt section.

    strategist_id ∈ {"A", "B", "C", "V", "S"}
        A → Strategist A (Onto-First)
        B → Strategist B (SQL-First with Schema Card)
        C → Strategist C (Decompose-First)
        V → Verifier Judge
        S → Synthesiser (escalation only)

    Falls back to a minimal one-paragraph stub if the marker isn't found.
    """
    headers = {
        "A": "## System prompt — Strategist A (Onto-First)",
        "B": "## System prompt — Strategist B (SQL-First with Schema Card)",
        "C": "## System prompt — Strategist C (Decompose-First)",
        "V": "## System prompt — Verifier Judge",
        "S": "## System prompt — Synthesiser (escalation only)",
    }
    fallbacks = {
        "A": ("You are Strategist A — Onto-First. Try query_ontology first; "
              "fall back to query_lakehouse only when ontology can't translate. "
              "Always cite by human label. Call final_answer."),
        "B": ("You are Strategist B — SQL-First. Start with schema_card, then "
              "compose CTE-stitched T-SQL via query_lakehouse. Avoid ontology "
              "unless the question is pure graph traversal. Call final_answer."),
        "C": ("You are Strategist C — Decompose-First. Restate the question "
              "as 2-5 atomic sub-questions, run each as one tool call, then "
              "compose the final markdown. Call final_answer."),
        "V": ("You are the Verifier. Score 3 candidate answers 0-100 on "
              "grounding, coverage, cardinality, specificity, caveats, and "
              "tool economy. Return JSON: scores[], winner, winner_score, "
              "synthesise_recommended, synthesis_brief."),
        "S": ("You are the Synthesiser. Consolidate the 3 strategists' "
              "candidates plus the Verifier brief into ONE final answer. "
              "Use schema_card / query_lakehouse / query_ontology if needed. "
              "Call final_answer once."),
    }
    if not _DOCS_PATH.exists():
        return fallbacks.get(strategist_id, "")
    text = _DOCS_PATH.read_text(encoding="utf-8")
    header = headers.get(strategist_id)
    if not header:
        return fallbacks.get(strategist_id, "")
    idx = text.find(header)
    if idx < 0:
        return fallbacks.get(strategist_id, "")
    body = text[idx + len(header):]
    # Stop at the next "## " header.
    end = re.search(r"\n## ", body)
    if end:
        body = body[:end.start()]
    # Strip the leading blockquote intro line if present.
    body = re.sub(r"^\s*> \*[^\n]*\n+", "", body, count=1)
    return body.strip() or fallbacks.get(strategist_id, "")


_PROMPTS: dict[str, str] = {
    sid: _load_strategist_prompt(sid)
    for sid in ("A", "B", "C", "V", "S")
}


# ─── Threading helpers — shared mutable state needs a lock ────────────────
def _make_safe_counter(real_counter: list[int]) -> tuple[list[int], threading.Lock]:
    """Return a per-strategist counter list backed by its own slot. Caller
    merges back into the run-level counter after the strategist finishes."""
    return [0], threading.Lock()


# ─── Single-strategist inner loop ─────────────────────────────────────────
def _run_strategist(strategist_id: str,
                     system_prompt: str,
                     openai: Any,
                     model: str,
                     case: dict,
                     question_input: str,
                     deadline: float,
                     tool_caps: dict[str, int],
                     trace_sink: list[dict],
                     emit: Any) -> tuple[Any, str, dict]:
    """Run one strategist's inner Responses-API loop.

    Returns (final_response_or_None, error_str, sql_state).
    The wrapper is a _FinalWrapper from m4_runner with .output_text /
    .confidence / .sql_log / .ontology_log tacked on.

    `tool_caps` keys: max_turns, max_sql, max_ontology, max_nudges,
    ontology_enabled.
    """
    max_turns        = tool_caps["max_turns"]
    max_sql          = tool_caps["max_sql"]
    max_ontology     = tool_caps["max_ontology"]
    max_nudges       = tool_caps["max_nudges"]
    ontology_enabled = bool(tool_caps.get("ontology_enabled", True))

    # When the operator has toggled the ontology off, strip it from the
    # tool list and amend the strategist's system prompt with a runtime
    # override note. _execute_tool also defensively refuses the call via
    # sql_state["ontology_enabled"].
    tools_list           = _tools_for(ontology_enabled)
    effective_system     = (system_prompt if ontology_enabled
                            else system_prompt + _ONTOLOGY_DISABLED_NOTE)

    # Per-strategist state — isolated from the other strategists.
    sql_state: dict[str, Any] = {
        "strategist":          strategist_id,
        "sql_attempts":        0,
        "sql_log":             [],
        "ontology_attempts":   0,
        "ontology_log":        [],
        "any_rows":            False,
        "last_row_count":      None,
        "last_error":          None,
        "final_answer":        None,
        "empty_response_nudges": 0,
        # M6 strategists do NOT critique-loop — that's the Verifier's job.
        "critique_attempts":   999,
        "ontology_enabled":    ontology_enabled,
    }
    # Override caps inside the shared _execute_tool helper by clamping
    # via the per-strategist budget. _execute_tool uses module-level
    # constants from m4_runner — we approximate the cap by injecting a
    # local guard here.
    def execute_with_caps(name: str, args: dict[str, Any]) -> tuple[Any, bool]:
        if name == "query_lakehouse" and sql_state["sql_attempts"] >= max_sql:
            return ({"error": f"Strategist {strategist_id}: SQL cap "
                                f"({max_sql}) reached. Submit final_answer."},
                    False)
        if name == "query_ontology" and sql_state["ontology_attempts"] >= max_ontology:
            return ({"error": f"Strategist {strategist_id}: ontology cap "
                                f"({max_ontology}) reached. Use query_lakehouse "
                                "or call final_answer."},
                    False)
        return _execute_tool(name, args, sql_state)

    emit("strategy_start", {
        "strategist": strategist_id,
        "model":      model,
        "max_turns":  max_turns,
        "max_sql":    max_sql,
        "max_ontology": max_ontology,
        "ontology_enabled": ontology_enabled,
    })

    try:
        resp = openai.responses.create(
            model=model,
            instructions=effective_system,
            input=question_input,
            tools=tools_list,
        )
    except Exception as exc:
        return None, f"[{strategist_id}] initial responses.create failed: {exc!r}", sql_state

    trace_sink.append({"strategist": strategist_id, "turn": 0,
                        "agent": f"M6.{strategist_id}({model})",
                        "purpose": "initial",
                        "response": _resp_to_jsonable(resp)})

    # Loop pattern: process function calls in `resp`, then (if there were
    # tool outputs to feed back) create a new `resp`. We allow up to
    # max_turns roundtrips, but ALSO process the final response one last
    # time so a `final_answer` issued on the final turn is honoured.
    for turn in range(1, max_turns + 2):
        if time.time() > deadline:
            return resp, f"[{strategist_id}] strategist deadline at turn {turn}", sql_state

        function_outputs: list[dict] = []
        for item in (resp.output or []):
            if not _is_function_call(item):
                continue
            name = getattr(item, "name", "") or ""
            try:
                args = json.loads(getattr(item, "arguments", "") or "{}")
            except Exception:
                args = {}
            emit("strategy_tool", {
                "strategist": strategist_id,
                "turn":       turn,
                "name":       name,
                "purpose":    (args.get("purpose") or "")[:120] if isinstance(args, dict) else "",
                "sql":        (args.get("sql") or "")[:160] if isinstance(args, dict) else "",
                "question":   (args.get("question") or "")[:120] if isinstance(args, dict) else "",
            })

            result, is_final = execute_with_caps(name, args)

            function_outputs.append({
                "type":    "function_call_output",
                "call_id": getattr(item, "call_id", "") or "",
                "output":  json.dumps(result, default=str)[:MAX_TOOL_OUTPUT_CHARS],
            })

            if is_final:
                fa_text = _format_final_answer(sql_state["final_answer"], sql_state)
                emit("strategy_done", {
                    "strategist":  strategist_id,
                    "turn":        turn,
                    "confidence":  sql_state["final_answer"].get("confidence"),
                    "sql_attempts": sql_state["sql_attempts"],
                    "ontology_attempts": sql_state["ontology_attempts"],
                    "answer_chars": len(fa_text),
                })
                wrapper = _FinalWrapper(
                    text=fa_text,
                    usage=getattr(resp, "usage", None),
                    sql_log=sql_state["sql_log"],
                    confidence=sql_state["final_answer"].get("confidence"),
                )
                # Tag the wrapper with strategist + ontology log for the verifier.
                wrapper.strategist    = strategist_id  # type: ignore[attr-defined]
                wrapper.ontology_log  = sql_state["ontology_log"]  # type: ignore[attr-defined]
                wrapper.final_payload = sql_state["final_answer"]   # type: ignore[attr-defined]
                return wrapper, "", sql_state

        if not function_outputs:
            text = _extract_text(resp)
            if not text and sql_state["empty_response_nudges"] < max_nudges:
                sql_state["empty_response_nudges"] += 1
                nudge_msg = (
                    "Continue. Either call query_ontology / query_lakehouse / "
                    "schema_card / sample_values to gather evidence, or call "
                    "final_answer. Do not return an empty message."
                ) if ontology_enabled else (
                    "Continue. Either call query_lakehouse / schema_card / "
                    "sample_values to gather evidence, or call final_answer. "
                    "Do not return an empty message. Note: query_ontology is "
                    "disabled for this run."
                )
                try:
                    resp = openai.responses.create(
                        model=model,
                        instructions=effective_system,
                        previous_response_id=resp.id,
                        input=[{"role": "user", "content": nudge_msg}],
                        tools=tools_list,
                    )
                except Exception as exc:
                    return resp, f"[{strategist_id}] nudge failed: {exc!r}", sql_state
                trace_sink.append({"strategist": strategist_id,
                                    "turn": f"{turn}.empty-nudge",
                                    "agent": f"M6.{strategist_id}({model})",
                                    "response": _resp_to_jsonable(resp)})
                continue

            # Coerce a final answer from whatever text we got.
            wrapper = _FinalWrapper(
                text=text or f"({strategist_id} produced no final_answer)",
                usage=getattr(resp, "usage", None),
                sql_log=sql_state["sql_log"],
                confidence="low",
            )
            wrapper.strategist    = strategist_id  # type: ignore[attr-defined]
            wrapper.ontology_log  = sql_state["ontology_log"]  # type: ignore[attr-defined]
            wrapper.final_payload = None  # type: ignore[attr-defined]
            return wrapper, f"[{strategist_id}] finished without final_answer", sql_state

        # Last iteration: we've already executed any final_answer call in
        # this resp; don't make another LLM call (out of budget).
        if turn > max_turns:
            break

        # Feed tool outputs back to the strategist.
        try:
            resp = openai.responses.create(
                model=model,
                instructions=effective_system,
                previous_response_id=resp.id,
                input=function_outputs,
                tools=tools_list,
            )
        except Exception as exc:
            return resp, f"[{strategist_id}] turn {turn} responses.create failed: {exc!r}", sql_state

        trace_sink.append({"strategist": strategist_id, "turn": turn,
                            "agent": f"M6.{strategist_id}({model})",
                            "tool_outputs": [fo["call_id"] for fo in function_outputs],
                            "response": _resp_to_jsonable(resp)})

    # Out of turns — coerce best-available answer.
    text = _extract_text(resp) or f"({strategist_id} exceeded max_turns={max_turns})"
    wrapper = _FinalWrapper(
        text=text,
        usage=getattr(resp, "usage", None),
        sql_log=sql_state["sql_log"],
        confidence="low",
    )
    wrapper.strategist    = strategist_id  # type: ignore[attr-defined]
    wrapper.ontology_log  = sql_state["ontology_log"]  # type: ignore[attr-defined]
    wrapper.final_payload = None  # type: ignore[attr-defined]
    return wrapper, f"[{strategist_id}] hit max_turns={max_turns}", sql_state


# ─── Verifier judge ────────────────────────────────────────────────────────
_VERIFIER_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "minItems": 3, "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "candidate": {"type": "string", "enum": ["A", "B", "C"]},
                    "score":     {"type": "integer", "minimum": 0, "maximum": 100},
                    "rationale": {"type": "string"},
                },
                "required": ["candidate", "score", "rationale"],
                "additionalProperties": False,
            },
        },
        "winner":                {"type": "string", "enum": ["A", "B", "C"]},
        "winner_score":          {"type": "integer", "minimum": 0, "maximum": 100},
        "synthesise_recommended": {"type": "boolean"},
        "synthesis_brief":       {"type": "string"},
    },
    "required": ["scores", "winner", "winner_score",
                  "synthesise_recommended", "synthesis_brief"],
    "additionalProperties": False,
}


def _build_verifier_input(question: str, persona: str,
                            candidates: dict[str, Any]) -> str:
    """Compose the user-facing payload for the verifier from 3 candidates.

    `candidates` keys: "A", "B", "C". Each value is a _FinalWrapper or None
    (None = strategist crashed)."""
    blocks: list[str] = [
        f"Persona: {persona or 'Example Enterprise employee'}",
        f"Question: {question}",
        "",
        "You will see THREE candidate answers (A, B, C). Score each independently.",
        "",
    ]
    for sid in ("A", "B", "C"):
        cand = candidates.get(sid)
        if cand is None:
            blocks.append(f"--- Candidate {sid} ---")
            blocks.append("(strategist crashed — runner error, no answer produced)")
            blocks.append("")
            continue
        body = (getattr(cand, "output_text", "") or "").strip()
        if not body:
            body = "(empty answer)"
        # Extract per-candidate metadata for the verifier's tool-economy axis.
        sql_log     = getattr(cand, "sql_log", []) or []
        onto_log    = getattr(cand, "ontology_log", []) or []
        confidence  = getattr(cand, "confidence", None)
        blocks.append(f"--- Candidate {sid} ---")
        blocks.append(
            f"(self-reported confidence: {confidence}; "
            f"sql_calls={len(sql_log)}; ontology_calls={len(onto_log)})"
        )
        blocks.append(body[:6000])
        blocks.append("")
    return "\n".join(blocks)


def _run_verifier(openai: Any, question: str, persona: str,
                    candidates: dict[str, Any],
                    trace_sink: list[dict], emit: Any,
                    token_totals: dict) -> dict:
    """Call the verifier model with structured output. Returns the decision dict.

    Falls back to a heuristic decision (highest-confidence candidate, score 60)
    if the model call fails or returns malformed JSON.
    """
    user_input = _build_verifier_input(question, persona, candidates)
    system     = _PROMPTS["V"]

    emit("verifier_start", {"model": FAST_MODEL,
                              "candidates": list(candidates.keys())})

    decision: dict | None = None
    try:
        resp = openai.responses.create(
            model=FAST_MODEL,
            instructions=system,
            input=user_input,
            text={
                "format": {
                    "type":   "json_schema",
                    "name":   "verifier_decision",
                    "strict": True,
                    "schema": _VERIFIER_JSON_SCHEMA,
                },
            },
        )
        _accumulate_tokens(resp, token_totals)
        trace_sink.append({"strategist": "V", "agent": f"M6.V({FAST_MODEL})",
                            "purpose": "verifier",
                            "response": _resp_to_jsonable(resp)})
        text = _extract_text(resp)
        if text:
            decision = json.loads(text)
    except Exception as exc:
        trace_sink.append({"strategist": "V", "agent": f"M6.V({FAST_MODEL})",
                            "purpose": "verifier-error",
                            "error": repr(exc)})

    if not decision:
        # Heuristic fallback: pick the candidate whose answer has the most
        # rows of markdown table content, prefer 'high' confidence.
        ranking: list[tuple[str, int]] = []
        for sid, cand in candidates.items():
            if cand is None:
                ranking.append((sid, 0))
                continue
            text = getattr(cand, "output_text", "") or ""
            score = len([ln for ln in text.splitlines() if "|" in ln])
            if (getattr(cand, "confidence", "") or "") == "high":
                score += 5
            ranking.append((sid, score))
        ranking.sort(key=lambda t: t[1], reverse=True)
        winner = ranking[0][0]
        decision = {
            "scores": [{"candidate": sid, "score": 60,
                          "rationale": "fallback heuristic — verifier model failed"}
                         for sid, _ in ranking],
            "winner":                winner,
            "winner_score":          60,
            "synthesise_recommended": False,
            "synthesis_brief":       "",
        }

    emit("verifier_done", {
        "winner":      decision.get("winner"),
        "winner_score": decision.get("winner_score"),
        "synthesise_recommended": decision.get("synthesise_recommended"),
    })
    return decision


# ─── Synthesiser (escalation only) ─────────────────────────────────────────
def _build_synthesiser_input(question: str, persona: str,
                                candidates: dict[str, Any],
                                verdict: dict) -> str:
    blocks: list[str] = [
        f"Persona: {persona or 'Example Enterprise employee'}",
        f"Question: {question}",
        "",
        f"Verifier picked candidate {verdict.get('winner')} "
        f"(score={verdict.get('winner_score')}) but flagged it as needing "
        f"synthesis. Brief: {verdict.get('synthesis_brief') or '(none)'}",
        "",
        "Verifier scores:",
    ]
    for s in verdict.get("scores", []):
        blocks.append(f"  - {s['candidate']}: {s['score']} ({s['rationale']})")
    blocks.append("")
    blocks.append("Candidate answers (use as evidence; cite the strongest rows):")
    for sid in ("A", "B", "C"):
        cand = candidates.get(sid)
        if cand is None:
            blocks.append(f"--- Candidate {sid} (crashed) ---")
            blocks.append("")
            continue
        body = (getattr(cand, "output_text", "") or "").strip()
        blocks.append(f"--- Candidate {sid} ---")
        blocks.append(body[:5000] or "(empty)")
        blocks.append("")
    blocks.append("Produce ONE consolidated final_answer that fixes the "
                    "weakness named in the synthesis_brief. You have 1 ontology "
                    "call, 2 SQL calls, 3 turns.")
    return "\n".join(blocks)


def _run_synthesiser(openai: Any, question: str, persona: str,
                       case: dict,
                       candidates: dict[str, Any], verdict: dict,
                       deadline: float,
                       trace_sink: list[dict], emit: Any,
                       token_totals: dict,
                       ontology_enabled: bool = True) -> _FinalWrapper:
    """Run the Synthesiser as a tight 3-turn function-tool loop. Returns
    a _FinalWrapper. Falls back to the verifier's winner candidate if the
    synthesiser itself fails."""
    emit("synth_start", {"model": PRO_MODEL,
                           "winner": verdict.get("winner"),
                           "ontology_enabled": ontology_enabled})
    user_input = _build_synthesiser_input(question, persona, candidates, verdict)
    system     = (_PROMPTS["S"] if ontology_enabled
                  else _PROMPTS["S"] + _ONTOLOGY_DISABLED_NOTE)
    tools_list = _tools_for(ontology_enabled)

    sql_state: dict[str, Any] = {
        "sql_attempts":        0,
        "sql_log":             [],
        "ontology_attempts":   0,
        "ontology_log":        [],
        "any_rows":            False,
        "final_answer":        None,
        "empty_response_nudges": 0,
        "critique_attempts":   999,
        "ontology_enabled":    ontology_enabled,
    }
    # Tighter caps for the synthesiser.
    SYNTH_MAX_TURNS    = 3
    SYNTH_MAX_SQL      = 2
    SYNTH_MAX_ONTOLOGY = 1

    def execute_with_caps(name: str, args: dict[str, Any]) -> tuple[Any, bool]:
        if name == "query_lakehouse" and sql_state["sql_attempts"] >= SYNTH_MAX_SQL:
            return ({"error": "Synthesiser: SQL cap (2) reached. Call final_answer."},
                    False)
        if name == "query_ontology" and sql_state["ontology_attempts"] >= SYNTH_MAX_ONTOLOGY:
            return ({"error": "Synthesiser: ontology cap (1) reached. Call final_answer."},
                    False)
        return _execute_tool(name, args, sql_state)

    try:
        resp = openai.responses.create(
            model=PRO_MODEL,
            instructions=system,
            input=user_input,
            tools=tools_list,
        )
    except Exception as exc:
        emit("synth_failed", {"error": repr(exc)})
        # Fall back to verifier winner.
        winner = candidates.get(verdict.get("winner"))
        if winner is not None:
            return winner
        # Last resort: pick first non-None.
        for c in candidates.values():
            if c is not None:
                return c
        return _FinalWrapper("(M6 synthesiser failed; no candidate available)",
                              None, [], "low")

    trace_sink.append({"strategist": "S", "agent": f"M6.S({PRO_MODEL})",
                        "purpose": "synthesis-initial",
                        "response": _resp_to_jsonable(resp)})
    _accumulate_tokens(resp, token_totals)

    # Loop pattern: process function calls in `resp`, then (if there were
    # tool outputs to feed back) create a new `resp`. We allow up to
    # SYNTH_MAX_TURNS roundtrips, but ALSO process the final response one
    # last time so a `final_answer` issued on the final turn is honoured.
    for turn in range(1, SYNTH_MAX_TURNS + 2):
        if time.time() > deadline:
            break
        function_outputs: list[dict] = []
        for item in (resp.output or []):
            if not _is_function_call(item):
                continue
            name = getattr(item, "name", "") or ""
            try:
                args = json.loads(getattr(item, "arguments", "") or "{}")
            except Exception:
                args = {}
            emit("synth_tool", {"turn": turn, "name": name})
            result, is_final = execute_with_caps(name, args)
            function_outputs.append({
                "type":    "function_call_output",
                "call_id": getattr(item, "call_id", "") or "",
                "output":  json.dumps(result, default=str)[:MAX_TOOL_OUTPUT_CHARS],
            })
            if is_final:
                fa_text = _format_final_answer(sql_state["final_answer"], sql_state)
                wrapper = _FinalWrapper(
                    text=fa_text,
                    usage=getattr(resp, "usage", None),
                    sql_log=sql_state["sql_log"],
                    confidence=sql_state["final_answer"].get("confidence"),
                )
                wrapper.strategist    = "S"  # type: ignore[attr-defined]
                wrapper.ontology_log  = sql_state["ontology_log"]  # type: ignore[attr-defined]
                wrapper.final_payload = sql_state["final_answer"]   # type: ignore[attr-defined]
                emit("synth_done", {
                    "turn": turn,
                    "confidence": sql_state["final_answer"].get("confidence"),
                    "answer_chars": len(fa_text),
                })
                return wrapper

        if not function_outputs:
            break

        # Last iteration: we've already executed any final_answer call in
        # this resp; don't make another LLM call (out of budget).
        if turn > SYNTH_MAX_TURNS:
            break

        try:
            resp = openai.responses.create(
                model=PRO_MODEL,
                instructions=system,
                previous_response_id=resp.id,
                input=function_outputs,
                tools=tools_list,
            )
        except Exception as exc:
            emit("synth_failed", {"turn": turn, "error": repr(exc)})
            break
        trace_sink.append({"strategist": "S", "agent": f"M6.S({PRO_MODEL})",
                            "purpose": f"synthesis-turn-{turn}",
                            "response": _resp_to_jsonable(resp)})
        _accumulate_tokens(resp, token_totals)

    # Synthesiser didn't call final_answer — fall back to verifier winner.
    emit("synth_fallback", {"reason": "synthesiser exhausted budget"})
    winner = candidates.get(verdict.get("winner"))
    if winner is not None:
        return winner
    for c in candidates.values():
        if c is not None:
            return c
    return _FinalWrapper("(M6 synthesiser exhausted budget; no candidate available)",
                          None, [], "low")


# ─── Schema injection block ────────────────────────────────────────────────
def _schema_injection_block() -> str:
    """Render the live schema_card payload as a markdown block for prompt injection.
    All 3 strategists get this so they don't have to call schema_card themselves."""
    payload = _schema_card_payload()
    lines = ["", "## EnterpriseKG_Lakehouse schema (live snapshot)", ""]
    for t in payload.get("tables", []):
        cols = ", ".join(t.get("columns", []))
        notes = "; ".join(t.get("notes", []))
        lines.append(f"### {t['name']} (rows≈{t.get('rows', '?')})")
        lines.append(f"- key: `{t.get('key')}`  •  label: `{t.get('label')}`")
        lines.append(f"- columns: {cols}")
        if notes:
            lines.append(f"- notes: {notes}")
        lines.append("")
    lines.append("## Translation rules")
    for r in payload.get("translation_rules_summary", []):
        lines.append(f"- {r}")
    lines.append("")
    return "\n".join(lines)


_SCHEMA_BLOCK = _schema_injection_block()


def _compose_strategist_prompt(strategist_id: str, fewshot_block: str) -> str:
    """Compose the final strategist system prompt = base prompt + schema + few-shot."""
    base = _PROMPTS[strategist_id]
    parts = [base, _SCHEMA_BLOCK]
    if fewshot_block:
        parts.append(fewshot_block)
    return "\n\n".join(parts)


# ─── Main entrypoint ──────────────────────────────────────────────────────
def run_m6(openai: Any, case: dict, deadline: float,
            tool_call_counter: list[int], token_totals: dict,
            trace: list[dict],
            *,
            on_progress: Any = None,
            ontology_enabled: bool = True,
            work_iq_enabled: bool = False,
            skill: dict | None = None) -> tuple[Any, str]:
    """Run the M6 Council protocol for one question.

    Returns (final_response, error_str). `final_response` is a _FinalWrapper
    that quacks like an OpenAI Responses object (has `.output_text`, `.usage`,
    `.status`, `.id`, `.output=[]`, plus M6-specific `.strategies_run`,
    `.verifier_score`, `.confidence`, `.sql_log`).

    Same signature as `run_m4` so it's a drop-in replacement. When
    `ontology_enabled=False` the `query_ontology` tool is removed from every
    strategist (and the synthesiser) so the council answers SQL-only.

    `work_iq_enabled` is accepted for signature parity with the WebUI
    settings feature, but the M6 council does not consult Work IQ.
    """
    def _emit(phase: str, payload: dict) -> None:
        if on_progress is None:
            return
        try:
            on_progress(phase, payload)
        except Exception:
            pass

    persona  = (case.get("Persona") or "").strip()
    question = (case.get("Question") or "").strip()
    code     = (case.get("Code") or "").strip()
    if not question:
        return None, "empty question"

    # ─── 1. Few-shot retrieval (silently no-op if cache missing) ─────────
    fewshot_examples: list[dict] = []
    try:
        fewshot_examples = retrieve_few_shot(
            question, k=3,
            exclude_codes=(code,) if code else (),
        )
    except Exception:
        fewshot_examples = []
    fewshot_block = format_few_shot_prompt(fewshot_examples)
    _emit("plan", {
        "model_strategist": DEFAULT_MODEL,
        "model_verifier":   FAST_MODEL,
        "model_synthesiser": PRO_MODEL,
        "persona":          persona or "Example Enterprise employee",
        "fewshot_count":    len(fewshot_examples),
        "fewshot_codes":    [e["code"] for e in fewshot_examples],
        "ontology_enabled": ontology_enabled,
    })

    initial_input = (
        f"Persona: {persona or 'Example Enterprise employee'}\n"
        f"Question: {question}"
    )

    # ─── 2. Spawn 3 strategists in parallel ──────────────────────────────
    tool_caps = {
        "max_turns":        M6_MAX_TURNS,
        "max_sql":          M6_MAX_SQL_ATTEMPTS,
        "max_ontology":     M6_MAX_ONTOLOGY_ATTEMPTS,
        "max_nudges":       M6_MAX_EMPTY_NUDGES,
        "ontology_enabled": ontology_enabled,
    }

    # Per-strategist deadline = run-level deadline minus a 30s reserve for
    # verifier+synthesiser. Also clamp by PER_STRATEGIST_BUDGET_SEC.
    now = time.time()
    strategist_deadline = min(
        deadline - 30.0,
        now + PER_STRATEGIST_BUDGET_SEC,
    )

    # Per-strategist trace sinks (merged into the main trace at the end).
    trace_sinks: dict[str, list[dict]] = {"A": [], "B": [], "C": []}
    candidates: dict[str, Any]         = {"A": None, "B": None, "C": None}
    errors:     dict[str, str]         = {"A": "",   "B": "",   "C": ""}
    states:     dict[str, dict]        = {}

    def _runone(sid: str) -> tuple[str, Any, str, dict]:
        prompt = _compose_strategist_prompt(sid, fewshot_block)
        resp, err, state = _run_strategist(
            strategist_id=sid,
            system_prompt=prompt,
            openai=openai,
            model=DEFAULT_MODEL,
            case=case,
            question_input=initial_input,
            deadline=strategist_deadline,
            tool_caps=tool_caps,
            trace_sink=trace_sinks[sid],
            emit=_emit,
        )
        return sid, resp, err, state

    with _futures.ThreadPoolExecutor(max_workers=3,
                                       thread_name_prefix="m6-strat") as pool:
        futures = [pool.submit(contextvars.copy_context().run, _runone, sid) for sid in ("A", "B", "C")]
        for fut in _futures.as_completed(futures, timeout=PER_STRATEGIST_BUDGET_SEC + 5):
            try:
                sid, resp, err, state = fut.result()
            except Exception as exc:
                # We don't know which strategist crashed at the executor level —
                # the per-strategist try/except inside _run_strategist makes this
                # rare. Skip; verifier sees None for that slot.
                trace.append({"strategist": "?", "agent": "M6.executor",
                                "error": repr(exc)})
                continue
            candidates[sid] = resp
            errors[sid]     = err
            states[sid]     = state

    # Merge per-strategist tool counts into the run-level counter.
    for sid, st in states.items():
        tool_call_counter[0] += int(st.get("sql_attempts") or 0)
        tool_call_counter[0] += int(st.get("ontology_attempts") or 0)

    # Merge token totals from each candidate's wrapper.usage.
    for sid in ("A", "B", "C"):
        cand = candidates[sid]
        if cand is None:
            continue
        usage = getattr(cand, "usage", None)
        if usage is None:
            continue
        for src, dst in (("input_tokens", "PromptTokens"),
                          ("output_tokens", "CompletionTokens"),
                          ("total_tokens", "TotalTokens")):
            v = getattr(usage, src, None)
            if v is not None:
                token_totals[dst] = (token_totals.get(dst) or 0) + int(v)

    # Merge per-strategist traces into the main trace.
    for sid in ("A", "B", "C"):
        trace.extend(trace_sinks[sid])
        if errors[sid]:
            trace.append({"strategist": sid, "purpose": "strategist-error",
                            "error": errors[sid]})

    # ─── 3. Verifier judge ───────────────────────────────────────────────
    verdict = _run_verifier(openai, question, persona, candidates,
                              trace_sink=trace, emit=_emit,
                              token_totals=token_totals)

    # ─── 4. Pick winner; escalate to Synthesiser if needed ───────────────
    scores = {s["candidate"]: int(s.get("score") or 0)
                for s in verdict.get("scores", [])}
    winner_sid    = verdict.get("winner")
    winner_score  = int(verdict.get("winner_score") or 0)
    sorted_scores = sorted(scores.values(), reverse=True)
    margin        = (sorted_scores[0] - sorted_scores[1]) if len(sorted_scores) >= 2 else 100

    needs_synth = bool(verdict.get("synthesise_recommended")) or (
        winner_score < VERIFIER_ESCALATION_SCORE
    ) or (margin < VERIFIER_TIE_MARGIN)

    final: Any
    if needs_synth and time.time() < deadline - 5.0:
        final = _run_synthesiser(openai, question, persona, case,
                                   candidates, verdict, deadline,
                                   trace_sink=trace, emit=_emit,
                                   token_totals=token_totals,
                                   ontology_enabled=ontology_enabled)
        # Tag for downstream consumers.
        final.strategies_run = ["A", "B", "C", "V", "S"]  # type: ignore[attr-defined]
        final.verifier_verdict = verdict                   # type: ignore[attr-defined]
        final.verifier_score = winner_score                # type: ignore[attr-defined]
        final.method_route   = "synthesiser"               # type: ignore[attr-defined]
    else:
        final = candidates.get(winner_sid)
        if final is None:
            # Verifier picked a crashed strategist. Pick first non-None.
            for sid, c in candidates.items():
                if c is not None:
                    final = c
                    winner_sid = sid
                    break
        if final is None:
            return None, "all 3 strategists crashed"
        final.strategies_run = ["A", "B", "C", "V"]  # type: ignore[attr-defined]
        final.verifier_verdict = verdict              # type: ignore[attr-defined]
        final.verifier_score = winner_score           # type: ignore[attr-defined]
        final.method_route   = f"strategist:{winner_sid}"  # type: ignore[attr-defined]

    _emit("final", {
        "winner":         winner_sid,
        "winner_score":   winner_score,
        "method_route":   getattr(final, "method_route", "?"),
        "answer_chars":   len(getattr(final, "output_text", "") or ""),
    })

    # Append a council-summary to the trace.
    trace.append({
        "strategist": "council",
        "purpose": "verdict",
        "verifier_verdict": verdict,
        "winner_strategist": winner_sid,
        "method_route": getattr(final, "method_route", "?"),
        "fewshot_codes": [e["code"] for e in fewshot_examples],
    })

    return final, ""
