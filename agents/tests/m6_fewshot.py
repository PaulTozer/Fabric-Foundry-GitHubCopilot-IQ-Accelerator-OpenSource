"""
M6 Council — few-shot retrieval cache.

Offline build:
    python m6_fewshot.py build              # embeds all questions in test_cases.csv
    python m6_fewshot.py build --with-traces  # also pull solved answers from results/traces/M4_*.json

Runtime usage (from m6_runner.py):
    from m6_fewshot import retrieve_few_shot
    examples = retrieve_few_shot(question, k=3, exclude_codes=("WW-I02",))
    # → [{"code", "question", "persona", "level", "answer", "similarity"}]

Cache file: agents/tests/m6_fewshot.json

Embedding model: env var ``EMBEDDING_DEPLOYMENT_NAME`` (default ``text-embedding-ada-002``).
The runtime retriever silently returns ``[]`` if the cache file is missing or
embeddings cannot be computed — M6 still works, it just loses the few-shot signal.
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

HERE       = Path(__file__).resolve().parent
TEST_CASES = HERE / "test_cases.csv"
TRACES_DIR = HERE / "results" / "traces"
CACHE_PATH = HERE / "m6_fewshot.json"

EMBED_MODEL_DEFAULT = "text-embedding-ada-002"
EMBED_API_VERSION   = "2024-10-21"
ANSWER_TRUNC = 1200  # chars per stored example answer — keeps prompt budget sane


def _embed_model() -> str:
    """Resolve the embedding deployment name at call time so a .env loaded
    by ``_main()`` actually takes effect."""
    return os.environ.get("EMBEDDING_DEPLOYMENT_NAME", EMBED_MODEL_DEFAULT)


def _derive_aoai_endpoint() -> str | None:
    """Derive the Azure OpenAI endpoint from PROJECT_ENDPOINT (or env override).

    PROJECT_ENDPOINT looks like ``https://<account>.services.ai.azure.com/api/projects/<name>``;
    the bare AOAI endpoint is ``https://<account>.openai.azure.com``. Both are
    backed by the same AI Services account in EUS2.
    """
    explicit = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if explicit:
        return explicit
    project = os.environ.get("PROJECT_ENDPOINT", "")
    if not project:
        return None
    # Extract the host's account label and rebuild the .openai.azure.com URL.
    import re as _re
    m = _re.match(r"https?://([^./]+)\.services\.ai\.azure\.com", project)
    if not m:
        return None
    return f"https://{m.group(1)}.openai.azure.com"


def _build_embed_client() -> Any | None:
    """Return a direct AzureOpenAI client for embeddings (with token provider).
    Falls back to None silently — callers must handle that."""
    endpoint = _derive_aoai_endpoint()
    if not endpoint:
        return None
    try:
        from openai import AzureOpenAI
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    except Exception:
        return None
    try:
        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(),
            "https://cognitiveservices.azure.com/.default",
        )
        return AzureOpenAI(
            azure_endpoint=endpoint,
            azure_ad_token_provider=token_provider,
            api_version=EMBED_API_VERSION,
        )
    except Exception:
        return None


# ─── Build mode ────────────────────────────────────────────────────────────
def _load_test_cases() -> list[dict]:
    if not TEST_CASES.exists():
        raise SystemExit(f"ERROR: {TEST_CASES} not found.")
    with TEST_CASES.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_m4_solved_answers() -> dict[str, str]:
    """For each test code, return the most recent M4 trace's `answer` field
    (the formatted final_answer markdown). Empty dict if no traces yet."""
    if not TRACES_DIR.exists():
        return {}
    out: dict[str, str] = {}
    for p in TRACES_DIR.glob("M4_*.json"):
        code = p.stem.removeprefix("M4_")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        ans = (data.get("answer") or "").strip()
        if not ans:
            continue
        # Skip refusals / errors as few-shot patterns — they teach the wrong thing.
        low = ans.lower()
        if any(b in low for b in ("i couldn't", "could not retrieve",
                                    "no matches", "(no answer", "technical issue")):
            continue
        out[code] = ans[:ANSWER_TRUNC]
    return out


def _embed_batch(client: Any, texts: list[str]) -> list[list[float]]:
    """Batch-embed texts via the supplied OpenAI client. Returns list of vectors."""
    # Azure OpenAI embeddings allow up to ~2048 inputs per call; we'll send 60 at a time.
    BATCH = 64
    model = _embed_model()
    vectors: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        chunk = texts[i:i + BATCH]
        resp = client.embeddings.create(model=model, input=chunk)
        for d in resp.data:
            vectors.append(list(d.embedding))
    return vectors


def build_cache(include_traces: bool = False) -> dict:
    """Build (or rebuild) the few-shot cache. Writes to CACHE_PATH and returns it."""
    cases = _load_test_cases()
    answers = _load_m4_solved_answers() if include_traces else {}

    # Prefer the direct AzureOpenAI client (account endpoint + token provider)
    # because the AIProjectClient.get_openai_client() routes embeddings calls
    # to a path that doesn't always include the deployment for AI Services
    # accounts. Fall back to the project client only if the direct one fails.
    client = _build_embed_client()
    if client is None:
        from run_tests import build_openai  # type: ignore[import-not-found]
        client = build_openai()

    print(f"[m6_fewshot] embedding {len(cases)} questions via {_embed_model()}...")
    questions = [c.get("Question", "") for c in cases]
    vectors   = _embed_batch(client, questions)

    entries: list[dict] = []
    for case, vec in zip(cases, vectors):
        code = case.get("Code", "")
        entries.append({
            "code":      code,
            "question":  case.get("Question", ""),
            "persona":   case.get("Persona", ""),
            "theme":     case.get("Theme", ""),
            "level":     case.get("Level", ""),
            "answer":    answers.get(code, ""),
            "embedding": vec,
        })

    cache = {
        "model":    _embed_model(),
        "count":    len(entries),
        "with_traces": include_traces,
        "entries":  entries,
    }
    CACHE_PATH.write_text(json.dumps(cache, default=str), encoding="utf-8")
    n_with_ans = sum(1 for e in entries if e["answer"])
    print(f"[m6_fewshot] wrote {CACHE_PATH} ({len(entries)} entries, "
          f"{n_with_ans} with M4 answers)")
    return cache


# ─── Runtime retrieval ─────────────────────────────────────────────────────
_CACHE: dict | None = None
_EMBED_CLIENT: Any | None = None


def _load_cache() -> dict | None:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not CACHE_PATH.exists():
        return None
    try:
        _CACHE = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return _CACHE
    except Exception:
        return None


def _get_embed_client() -> Any | None:
    """Return a cached OpenAI client for runtime embeddings.

    Falls back to None silently — callers must handle that as "no few-shot".
    """
    global _EMBED_CLIENT
    if _EMBED_CLIENT is not None:
        return _EMBED_CLIENT
    # Prefer the direct AzureOpenAI client; fall back to the project client.
    direct = _build_embed_client()
    if direct is not None:
        _EMBED_CLIENT = direct
        return _EMBED_CLIENT
    try:
        from run_tests import build_openai  # type: ignore[import-not-found]
        _EMBED_CLIENT = build_openai()
        return _EMBED_CLIENT
    except Exception:
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot  = sum(x * y for x, y in zip(a, b))
    na   = math.sqrt(sum(x * x for x in a))
    nb   = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def retrieve_few_shot(question: str, k: int = 3,
                        exclude_codes: tuple[str, ...] = ()) -> list[dict]:
    """Return the top-k most-similar prior solved examples for ``question``.

    Each result dict has keys: ``code, question, persona, level, answer, similarity``.
    Returns ``[]`` silently if the cache file is missing or embeddings can't be computed.
    """
    cache = _load_cache()
    if not cache or not cache.get("entries"):
        return []

    client = _get_embed_client()
    if client is None:
        return []
    try:
        resp = client.embeddings.create(model=_embed_model(), input=[question])
    except Exception:
        return []
    qvec = list(resp.data[0].embedding)

    excl = set(exclude_codes or ())
    scored: list[tuple[float, dict]] = []
    for e in cache["entries"]:
        if e["code"] in excl:
            continue
        sim = _cosine(qvec, e["embedding"])
        scored.append((sim, e))

    scored.sort(key=lambda t: t[0], reverse=True)
    out: list[dict] = []
    for sim, e in scored[:k]:
        out.append({
            "code":       e["code"],
            "question":   e["question"],
            "persona":    e["persona"],
            "level":      e["level"],
            "answer":     e["answer"],
            "similarity": round(sim, 4),
        })
    return out


def format_few_shot_prompt(examples: list[dict]) -> str:
    """Render a list of few-shot examples as a single prompt fragment to
    inject as `Prior similar questions` context. Empty string if no examples."""
    if not examples:
        return ""
    lines = ["Prior similar questions you (or another strategist) have answered well:"]
    for ex in examples:
        lines.append("")
        lines.append(f"--- {ex['code']} ({ex['persona']}, {ex['level']}, sim={ex['similarity']:.2f}) ---")
        lines.append(f"Q: {ex['question']}")
        if ex.get("answer"):
            lines.append(f"A:\n{ex['answer']}")
        else:
            lines.append("A: (no prior answer captured — pattern only)")
    lines.append("")
    lines.append("Treat the above as patterns — entities and counts in the current question may differ.")
    lines.append("Do NOT copy the answer verbatim. Use it to inform tool choice and SQL/GQL shape.")
    return "\n".join(lines)


# ─── CLI ──────────────────────────────────────────────────────────────────
def _main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = args[0]
    if cmd == "build":
        with_traces = "--with-traces" in args
        # Load .env so EMBEDDING_DEPLOYMENT_NAME / PROJECT_ENDPOINT are available.
        try:
            from run_tests import load_env  # type: ignore[import-not-found]
            load_env()
        except Exception:
            pass
        build_cache(include_traces=with_traces)
        return 0
    if cmd == "show":
        cache = _load_cache()
        if not cache:
            print("No cache file. Run `python m6_fewshot.py build` first.")
            return 1
        print(f"Cache: {cache['count']} entries, model={cache['model']}, "
              f"with_traces={cache.get('with_traces', False)}")
        for e in cache["entries"][:10]:
            print(f"  {e['code']:8s}  {e['persona']:20s}  {e['level']:10s}  "
                  f"{e['question'][:60]}{'...' if len(e['question']) > 60 else ''}  "
                  f"answer_chars={len(e['answer'])}")
        return 0
    print(f"Unknown command '{cmd}'. Use 'build' or 'show'.")
    return 1


if __name__ == "__main__":
    sys.exit(_main())
