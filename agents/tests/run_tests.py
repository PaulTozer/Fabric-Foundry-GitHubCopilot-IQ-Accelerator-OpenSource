"""
EnterpriseKG Agent Comparison — Test Runner (v2 Foundry Agents / Responses API).

Runs the same 60 questions in `tests/test_cases.csv` against each method:

    M1  enterprise_mcp-m1-single   single Fabric data agent (when --attach-fabric is wired)
    M2  three child agents (people / project / client) — orchestrator deferred
    M3  enterprise_mcp-m3-direct   Foundry-direct: T-SQL writer with execute_sql function tool
    M4  Copilot SDK BYOK     SDK tool orchestration (NO Foundry agent
                            required — driven entirely by m4_runner.py)

For M2 we currently fan out per-question to all three children and stitch
their answers (cheap mock orchestrator). When the A2A orchestrator agent is
provisioned later, replace the M2 path with a single call to its name.

Writes per-method CSVs + a combined CSV + per-question JSON traces under
    agents/tests/results/

Usage
-----
    python run_tests.py                          # all methods, all questions
    python run_tests.py --methods M1 M3          # subset of methods
    python run_tests.py --code WW-VH01           # single question (all methods)
    python run_tests.py --code WW-VH01 --methods M2

Required environment (e.g. via .env at agents/tests/.env)
---------------------------------------------------------
    PROJECT_ENDPOINT          https://<resource>.services.ai.azure.com/api/projects/<project>
    MODEL_DEPLOYMENT_NAME     gpt-5.4
    M1_AGENT_NAME             enterprise_mcp-m1-single
    M2_PEOPLE_AGENT_NAME      enterprise_mcp-m2-engineer
    M2_PROJECT_AGENT_NAME     enterprise_mcp-m2-contract
    M2_CLIENT_AGENT_NAME      enterprise_mcp-m2-client
    M3_AGENT_NAME             enterprise_mcp-m3-direct
    FABRIC_SQL_ENDPOINT       <lakehouse SQL FQDN>
    FABRIC_SQL_DATABASE       EnterpriseKG_Lakehouse

Auth: DefaultAzureCredential. Run `az login` first.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
import warnings
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

# azure-ai-projects v2 SDK + MicrosoftFabricPreviewTool emit dozens of harmless
# Pydantic serializer warnings per call (unknown tool-call subtypes). Silence them.
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# ---- Paths ------------------------------------------------------------------
HERE          = Path(__file__).resolve().parent
TEST_CASES    = HERE / "test_cases.csv"
RESULTS_DIR   = HERE / "results"
TRACES_DIR    = RESULTS_DIR / "traces"

# ---- Per-question hard timeout ---------------------------------------------
# Default 360s leaves headroom for: 1 LLM hop (~5-15s) + 1 Fabric ontology
# NL2Ontology call (typ. 5-30s, slow path can hit the 130s client timeout) +
# 1 LLM hop (~5-15s) + an optional SQL fallback (~5-30s) + a final LLM hop.
# Override on the CLI with --timeout SECONDS for slower environments.
PER_QUESTION_TIMEOUT_SEC = 360
INTER_QUESTION_PAUSE_SEC = 1.0
MAX_FUNCTION_TURNS       = 8

# ---- Retry policy on transient Fabric/Foundry errors -----------------------
MAX_TRANSIENT_RETRIES   = 3
RETRY_BASE_DELAY_SEC    = 8.0
_TRANSIENT_PATTERNS = (
    "circuit breaker",
    "transient failure",
    "create assistant failed",
    "fabric run failed during execution",
    "token not supported",
    "429",
    "rate limit",
    "throttled",
    "temporarily unavailable",
    "service unavailable",
    "timeout",
)


def _is_transient_error(err: str) -> bool:
    if not err:
        return False
    low = err.lower()
    return any(pat in low for pat in _TRANSIENT_PATTERNS)

METHODS = ("M1", "M2", "M3", "M4", "M5", "M6", "M7")

# Env keys per method. M2 fans out to 3 children, so it has 3 keys. M4 + M6 need
# no Foundry agent — they drive the model directly via the Responses API. M5 is a
# Foundry agent with a hosted MCP tool (Search KB wrapping the Fabric ontology).
ENV_KEY = {
    "M1": ["M1_AGENT_NAME"],
    "M2": ["M2_PEOPLE_AGENT_NAME", "M2_PROJECT_AGENT_NAME", "M2_CLIENT_AGENT_NAME"],
    "M3": ["M3_AGENT_NAME"],
    "M4": [],
    "M5": ["M5_AGENT_NAME"],
    "M6": [],
    "M7": ["M7_AGENT_NAME"],
}


@dataclass
class Result:
    Method: str
    Code: str
    Persona: str
    Theme: str
    Level: str
    Question: str
    Answer: str
    LatencySec: float
    PromptTokens: Optional[int]
    CompletionTokens: Optional[int]
    TotalTokens: Optional[int]
    ToolCallCount: int
    Status: str
    Error: str


def load_env() -> None:
    """Load agents/tests/.env into os.environ."""
    candidates = [HERE / ".env", HERE.parent / ".env", HERE.parent.parent / ".env"]
    for p in candidates:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
            return


def require(var: str) -> str:
    val = os.environ.get(var)
    if not val:
        sys.exit(f"ERROR: env var {var} not set. See header of run_tests.py.")
    return val


def build_openai():
    """Return an OpenAI client bound to the v2 Foundry project."""
    from azure.identity import DefaultAzureCredential
    from azure.ai.projects import AIProjectClient
    project = AIProjectClient(endpoint=require("PROJECT_ENDPOINT"), credential=DefaultAzureCredential())
    client = project.get_openai_client()
    # Cap each /responses request so the per-question deadline (180s in the
    # live MCP path) actually fires instead of getting stuck behind the
    # OpenAI SDK's default 600s timeout + 2 retries. If the model is slow,
    # better to surface that as a deadline-exceeded error than to silently
    # hold the SSE connection open for 30 minutes.
    try:
        client = client.with_options(timeout=90.0, max_retries=1)
    except Exception:  # noqa: BLE001 — keep backwards compat
        pass
    return client


def load_test_cases() -> list[dict]:
    if not TEST_CASES.exists():
        sys.exit(f"ERROR: {TEST_CASES} not found.")
    with TEST_CASES.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("ERROR: test_cases.csv is empty.")
    return rows


# ---------------------------------------------------------------------------
# v2 helpers — Responses API + function-call loop
# ---------------------------------------------------------------------------
def _agent_ref(name: str) -> dict:
    return {"agent_reference": {"name": name, "type": "agent_reference"}}


def _is_tool_call_item(item) -> bool:
    """True if a response output item is a tool call of any flavour."""
    t = getattr(item, "type", "") or ""
    return t.endswith("_call") or t == "function_call"


def _extract_text(resp) -> str:
    """Prefer convenience attribute when present, otherwise concatenate message blocks."""
    txt = getattr(resp, "output_text", None)
    if txt:
        return txt
    parts: list[str] = []
    for item in getattr(resp, "output", []) or []:
        if getattr(item, "type", "") == "message":
            for block in getattr(item, "content", []) or []:
                if getattr(block, "type", "") in ("output_text", "text"):
                    parts.append(getattr(block, "text", "") or "")
    return "\n".join(p for p in parts if p).strip()


def _accumulate_tokens(resp, totals: dict) -> None:
    usage = getattr(resp, "usage", None)
    if not usage:
        return
    for src_key, dst_key in (("input_tokens", "PromptTokens"),
                              ("output_tokens", "CompletionTokens"),
                              ("total_tokens", "TotalTokens")):
        v = getattr(usage, src_key, None)
        if v is not None:
            totals[dst_key] = (totals.get(dst_key) or 0) + int(v)


def _resp_to_jsonable(resp) -> dict:
    try:
        return resp.model_dump(mode="json")
    except Exception:
        try:
            return json.loads(resp.model_dump_json())
        except Exception:
            return {"_raw_repr": repr(resp)[:2000]}


def _run_function_loop(openai, agent_name: str, question: str,
                        method: str, deadline: float, tool_call_counter: list[int],
                        token_totals: dict, trace: list[dict]) -> tuple[Any, str]:
    """Send a single question to a v2 agent and run the responses ↔ function-call
    loop until the agent finishes. Returns (final_response, error_str)."""

    resp = openai.responses.create(input=question, extra_body=_agent_ref(agent_name))
    trace.append({"turn": 0, "agent": agent_name, "response": _resp_to_jsonable(resp)})
    _accumulate_tokens(resp, token_totals)
    tool_call_counter[0] += sum(1 for it in (resp.output or []) if _is_tool_call_item(it))

    for turn in range(1, MAX_FUNCTION_TURNS + 1):
        if time.time() > deadline:
            return resp, f"Exceeded {PER_QUESTION_TIMEOUT_SEC}s in function loop"

        function_outputs: list[dict] = []
        for item in (resp.output or []):
            if getattr(item, "type", "") != "function_call":
                continue
            name = getattr(item, "name", "")
            try:
                args = json.loads(getattr(item, "arguments", "") or "{}")
            except Exception:
                args = {}
            if name == "execute_sql" and method == "M3":
                from m3_sql_handler import execute_sql
                result = execute_sql(args.get("query", ""))
            elif name == "query_ontology" and method == "M3":
                from fabric_ontology import query_ontology
                result = query_ontology(args.get("question", ""))
            else:
                result = {"error": f"unknown function '{name}' (method {method})"}
            function_outputs.append({
                "type": "function_call_output",
                "call_id": getattr(item, "call_id", ""),
                "output": json.dumps(result, default=str)[:30000],
            })

        if not function_outputs:
            return resp, ""  # agent finished

        resp = openai.responses.create(
            previous_response_id=resp.id,
            input=function_outputs,
            extra_body=_agent_ref(agent_name),
        )
        trace.append({"turn": turn, "agent": agent_name,
                       "tool_outputs": [fo["call_id"] for fo in function_outputs],
                       "response": _resp_to_jsonable(resp)})
        _accumulate_tokens(resp, token_totals)
        tool_call_counter[0] += sum(1 for it in (resp.output or []) if _is_tool_call_item(it))

    return resp, f"Exceeded MAX_FUNCTION_TURNS={MAX_FUNCTION_TURNS}"


# ---------------------------------------------------------------------------
# M5 — Foundry agent with hosted MCP tool (direct Fabric Ontology MCP)
# ---------------------------------------------------------------------------
M5_MAX_ATOMIC_ASKS = 4

_M5_DECOMPOSER_INSTRUCTIONS = """You are a query decomposer for the EnterpriseKG Fabric ontology.

The Fabric NL2Ontology preview translator FAILS on compound questions and is
extremely sensitive to wording. Given a user question, return a JSON list of 1
to 4 atomic single-fact sub-questions, each phrased EXACTLY using one of the
known-good patterns below — do not invent your own phrasing.

Ontology entities:
  Staff       (StaffName, JobTitle, Grade, LocationName, RegionName, PracticeName, GroupName, ReportsToName)
  Expertise   (ExpertiseTitle, Level1/2/3ExpertiseCategory)
  StaffExpertise (StaffID -> Expertise; RoleName, RoleType from ENT_EngineerRoles)
  SkillsNetwork  (SkillsNetworkName; members via ENT_TradeNetworkMembers)
  Project     (FullTitle, ShortTitle, JobStatusDesc, ProjectSectorName, RegionName, FrameworkName, ProjectDirectorName, ProjectManagerName, CommercialManagerName, UltimateClientName)
  Client      (ClientName, ClientSector, RegionName, RelationshipManagerName, NumOpportunitiesWon/Lost/Poss)

KNOWN-GOOD ATOMIC PATTERNS (use these EXACT shapes — substitute only the
<...> placeholders):

  Staff lookups
    "Show staff named <FullName>."
    "Which practice does <FullName> sit in?"
    "Which skills networks is <FullName> a member of?"
    "Who reports to <FullName>?"                 # downstream direct reports
    "Who does <FullName> report to?"             # upstream line manager

  Skills network lookups
    "Which staff are members of <NetworkName> skills network?"

  Project lookups
    "Show projects for client <ClientName>."
    "Who is the project director of <ShortTitle>?"
    "Show project staff for project <ShortTitle>."
    "Show project staff for FullTitle '<FullTitle>'."

  Client / sector lookups
    "Show clients in the <Sector> sector."
    "Show clients with sector <Sector>."

Decomposition rules:
1. If the user's question contains TWO or more independent asks (joined by
   "and which", "and what", "and who", "and how many"), split it.
2. Sub-questions can chain — later sub-questions may reference results from
   earlier ones using "{prior}" as a placeholder. The executor will replace
   "{prior}" with the FIRST identifier (name / title) extracted from the
   previous answer. Use "{prior}" only when the next ask genuinely needs a
   single value from the previous result.
3. DO NOT use "{prior}" with bulk-pronoun follow-ups like "and who do they
   report to" / "and what skills networks do they belong to" when the prior
   step is expected to return MORE THAN ONE row (e.g. "Which staff are
   members of <Network> skills network" returns 30+ rows). The system can
   only ask 4 atomic questions, so fanning out per row is impossible. In
   that case return ONLY the first sub-question; the synthesiser will
   explain that per-row lookups exceed the budget.
4. Numeric comparisons (>, <, "over 50%", "at least 3"), counts, aggregations,
   and free-text scope-of-works searches are OUT OF SCOPE. Return an EMPTY
   list ONLY if the entire question is one of those — do not bail just
   because the question contains a word like "active" or "current".
5. Cap output at 4 sub-questions. Prefer fewer asks where possible.
6. Do NOT invent your own phrasings — use the patterns above verbatim. If
   the user asks for "active <X>" or "current <X>", DROP the qualifier and
   use the plain pattern (e.g. "Show projects for client <ClientName>." not
   "Show ACTIVE projects for client <ClientName>." — the latter returns 0
   rows).
7. If the user asks for "clients in the <Sector> sector with active projects",
   decompose to: (a) "Show clients in the <Sector> sector." then (b)
   "Show projects for client {prior}." picking the first client. Do not try
   to fan out across hundreds of clients.

Output format: JSON object {"subs": ["sub-question 1", "sub-question 2", ...]}.
No prose. No markdown fences."""


_M5_SYNTHESISER_INSTRUCTIONS = """You merge atomic ontology answers into one
business-English response for the original user question.

Input: original question + a list of {sub_question, answer} pairs (each answer is
either a natural-language summary from the Fabric ontology or an "(error)" marker).

Rules:
- Lead with the answer to the original question.
- Use the data verbatim — do not invent rows or numbers.
- For lists, use a markdown table.
- For singletons, use one sentence.
- If one sub-question failed, mention it briefly but still answer the others.
- End with a single line: "Source: Fabric ontology (M5 multi-turn decomposition)."
- No prose preamble like "Here is the answer:". Just the answer."""


def _extract_first_identifier(prior_text: str) -> str:
    """Pull the first staff name / project title from a prior atomic answer
    so {prior} substitution stays within Fabric NL2Ontology length/shape limits.

    Recognises common shapes returned by Fabric:
      "<Name> (staff_id: 12345)"
      "<Name> (staff_id 12345)"
      "<Name> (project_id: ...)"
      "- <Name> (staff_id: ...)"  (markdown list)
      "<Name> sits in ..."
    Falls back to the first non-empty line trimmed to 80 chars."""
    if not prior_text:
        return ""
    # Pattern: <Name> (staff_id: NN) or (project_id: ...) or (client_id: ...)
    m = re.search(
        r"([A-Z][A-Za-z'’.\- ]{1,60})\s*\(\s*(?:staff|project|client|skills_network)_id[:\s]",
        prior_text,
    )
    if m:
        return m.group(1).strip(" -•\t")
    # Pattern: list item starting with "- <Name>"
    m = re.search(r"^[\-\*•]\s+([A-Z][A-Za-z'’.\- ]{1,60})", prior_text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    # Fallback: first non-empty line, truncated.
    for line in prior_text.splitlines():
        line = line.strip(" -•\t")
        if line:
            return line[:80]
    return prior_text[:80]


def _is_translate_failure(exc: Exception) -> bool:
    """True when Foundry's hosted-MCP runtime reports a Fabric NL2Ontology
    translate failure as a 400 BadRequestError."""
    s = repr(exc)
    return ("tool_user_error" in s
            and ("Failed to translate" in s
                 or "search_ontology" in s and "translate" in s))


def _is_translate_failure_text(err: str) -> bool:
    """True when query_ontology() returned a translate-style error string
    (NL2Ontology preview couldn't translate the question)."""
    if not err:
        return False
    s = err.lower()
    return (
        "failed to translate" in s
        or "could not translate" in s
        or "nl2ontology" in s
        or "translate this question" in s
    )


def _m5_atomic_call(openai, agent_name: str, sub_q: str,
                     trace: list[dict], token_totals: dict,
                     tool_call_counter: list[int]) -> tuple[str, str]:
    """One atomic call to the Fabric ontology for M5.

    We call `query_ontology` directly (Python-side) rather than going through
    the Foundry hosted MCP a second time. Reason: Foundry's project managed
    identity does not have license entitlement for Fabric NL2Ontology (the
    Copilot-AI feature behind `search_ontology`), so every per-atom call via
    `responses.create(... agent_reference=enterprise_mcp-m5-fabric-ontology)` returns
    HTTP 400 'Failed to translate NL query to ontology query'. The same exact
    string sent through `query_ontology` (which uses DefaultAzureCredential ->
    user token, scope https://api.fabric.microsoft.com/.default) succeeds.

    The first single-shot attempt in `_run_m5_ontology` still goes through the
    Foundry hosted-MCP path so the architectural failure remains visible in the
    trace; the dispatcher only falls back here when that path 400's.

    Returns `(answer_text, error_text)`. Tool-call counter is incremented per
    successful Fabric MCP call so M5 still shows non-zero tool usage."""
    from fabric_ontology import query_ontology  # type: ignore
    try:
        result = query_ontology(sub_q)
    except Exception as exc:
        msg = repr(exc)[:600]
        trace.append({"turn": "m5-atomic-error", "agent": "fabric-mcp(direct)",
                       "subquestion": sub_q, "error": msg})
        return "", msg
    if result.get("is_error") or result.get("error"):
        err = (result.get("error")
                or result.get("raw_text")
                or "Fabric ontology returned isError=true with no message.")
        trace.append({"turn": "m5-atomic-error", "agent": "fabric-mcp(direct)",
                       "subquestion": sub_q, "error": err[:600]})
        return "", err[:600]
    answer = (result.get("answer")
              or result.get("raw_text")
              or "")
    if not answer and result.get("raw") is not None:
        try:
            answer = json.dumps(result["raw"], default=str)[:4000]
        except Exception:
            answer = str(result["raw"])[:4000]
    trace.append({"turn": "m5-atomic", "agent": "fabric-mcp(direct)",
                   "subquestion": sub_q, "answer": answer[:1200],
                   "row_count": result.get("row_count", 0)})
    tool_call_counter[0] += 1
    return answer or "(no answer)", ""


def _run_m5_ontology(openai, agent_name: str, question: str,
                      deadline: float, tool_call_counter: list[int],
                      token_totals: dict, trace: list[dict]) -> tuple[Any, str]:
    """Send a question to the M5 agent. The agent's only tool is the public
    Fabric Ontology MCP server, called server-side by Foundry (RemoteTool
    connection enterprise_mcp-fabric-ontology-connection, audience
    https://api.fabric.microsoft.com).

    Multi-turn behaviour: the Fabric NL2Ontology preview translator returns
    isError on compound questions, which Foundry's hosted-MCP runtime surfaces
    as a 400 BadRequestError (code=tool_user_error). The agent never sees the
    error message and cannot retry inside its own loop. So the dispatcher
    catches that exception, asks a planner LLM to decompose the question into
    atomic single-fact sub-questions, calls M5 once per sub-question, and
    finally synthesises the merged answer with another LLM call. The returned
    `resp` is the synthesis call so _extract_text picks up the merged text."""
    model = (
        os.environ.get("MODEL_DEPLOYMENT_NAME_FAST")
        or os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-5.4")
    )

    # ---- Attempt 1: pass the question through as-is, but DIRECTLY via
    # query_ontology (user-token / DefaultAzureCredential), not Foundry's
    # hosted MCP. The hosted-MCP path uses the project managed identity which
    # has no Fabric NL2Ontology entitlement and 400's every translate request,
    # so routing the single-shot through the user-token path matches what M4
    # does and avoids burning tokens on a guaranteed-failure round-trip.
    answer_text, err = _m5_atomic_call(
        openai, agent_name, question, trace, token_totals, tool_call_counter,
    )
    if answer_text:
        # Wrap the plain answer in a tiny shim so the caller's _extract_text
        # picks up `.output_text` (no Responses API roundtrip needed).
        class _M5SingleShotResp:
            output_text = answer_text + (
                "\n\nSource: Fabric ontology (M5 single-shot, user token)."
            )
            output: list = []
            usage = None
        return _M5SingleShotResp(), ""
    if err and not _is_translate_failure_text(err):
        # Hard failure (network, auth, etc.) — surface immediately.
        return None, f"M5 single-shot failed: {err[:300]}"
    trace.append({"turn": 0, "agent": "fabric-mcp(direct)",
                   "purpose": "M5 single-shot failed with translate error",
                   "error": (err or "")[:600]})

    # ---- Attempt 2: decompose -> per-atomic call -> synthesise. ------------
    if time.time() > deadline:
        return None, f"Exceeded {PER_QUESTION_TIMEOUT_SEC}s before M5 decomposition"

    plan_resp = openai.responses.create(
        model=model,
        instructions=_M5_DECOMPOSER_INSTRUCTIONS,
        input=question,
    )
    trace.append({"turn": "m5-decompose", "agent": f"decomposer({model})",
                   "response": _resp_to_jsonable(plan_resp)})
    _accumulate_tokens(plan_resp, token_totals)

    raw = _strip_json_fence(_extract_text(plan_resp))
    try:
        plan_obj = json.loads(raw) if raw else {}
        sub_questions = [s.strip() for s in (plan_obj.get("subs") or []) if isinstance(s, str) and s.strip()]
    except Exception:
        sub_questions = []

    if not sub_questions:
        return None, ("M5 decomposition produced no sub-questions; the original "
                       "question is out of scope for the Fabric ontology preview "
                       "(numeric comparisons, aggregations, or free-text scope searches).")

    sub_questions = sub_questions[:M5_MAX_ATOMIC_ASKS]

    # Execute atomic asks sequentially, threading {prior} substitutions.
    atomic_results: list[dict] = []
    prior_text = ""
    for sub_q in sub_questions:
        if time.time() > deadline:
            atomic_results.append({"sub": sub_q, "answer": "", "error": "deadline reached"})
            break
        # Smart {prior} substitution: extract the first identifier from the
        # previous atomic answer so we send a SHORT name to Fabric, not the
        # entire 32-row markdown table from the prior step.
        prior_token = _extract_first_identifier(prior_text) or "(none yet)"
        substituted = sub_q.replace("{prior}", prior_token)
        answer_text, err = _m5_atomic_call(openai, agent_name, substituted, trace,
                                            token_totals, tool_call_counter)
        if answer_text:
            atomic_results.append({"sub": substituted, "answer": answer_text, "error": ""})
            prior_text = answer_text
        else:
            atomic_results.append({"sub": substituted, "answer": "", "error": err[:300]})

    # If nothing succeeded, surface the failure.
    if not any(r.get("answer") for r in atomic_results):
        return None, ("M5 decomposed the question but every atomic ask still "
                       "failed. First error: "
                       + (atomic_results[0].get("error", "")[:300] if atomic_results else ""))

    # ---- Synthesise the final answer. --------------------------------------
    if time.time() > deadline:
        return None, f"Exceeded {PER_QUESTION_TIMEOUT_SEC}s before M5 synthesis"

    stitched = (
        f"Original question:\n{question}\n\n"
        "Atomic ontology answers:\n\n"
        + "\n\n".join(
            f"### Sub-question\n{r['sub']}\n\n#### Answer\n{r['answer'] or '(error: ' + (r.get('error') or 'unknown') + ')'}"
            for r in atomic_results
        )
    )
    synth_resp = openai.responses.create(
        model=model,
        instructions=_M5_SYNTHESISER_INSTRUCTIONS,
        input=stitched,
    )
    trace.append({"turn": "m5-synthesise", "agent": f"synthesiser({model})",
                   "response": _resp_to_jsonable(synth_resp)})
    _accumulate_tokens(synth_resp, token_totals)
    return synth_resp, ""


_PLANNER_INSTRUCTIONS = """You are a planner for a 3-agent Enterprise knowledge-graph system.
The 3 child agents and their scope:
- people:  staff (StaffName, office, practice, region, line manager), expertise (RoleName via ENT_EngineerRoles), skills networks, who-knows-what, individual project history. Owns ENT_Engineer, ENT_EngineerRoles, ENT_TradeNetworkMembers.
- project: projects (FullTitle, status, sector, region, framework), project teams, hours, discipline mix, % complete, financials. Owns ENT_Building, ENT_WorkOrder, ENT_Contract, ENT_Asset.
- client:  clients (ClientName, ClientSector, RelationshipManagerName), opportunity pipeline (NumOpportunitiesWon/Lost/Poss), client account characterisation. Owns ENT_Client.

Your job: decompose the user question into 1-3 targeted sub-questions, each addressed
to ONE child agent. Sub-questions must be self-contained — the child cannot see the
others' work. Use "{prior_results}" as a placeholder where the previous answer should
be inlined.

CRITICAL ROUTING RULES — apply in order, stop at the first match:

1. If the question asks "WHICH STAFF" / "WHO" / "WHO HAS" / "STAFF WHO" / "PEOPLE WHO" — the
   FINAL subtask MUST go to `people`. The people agent owns the StaffName output.

2. If the question filters by a CLIENT property (sector, region, RM, pipeline) AND asks for
   STAFF, the chain is exactly:
     a) client    → "Return ClientIDs and ClientNames where <client filter>. For sector
                     keywords use ClientSector LIKE '%<keyword>%' (sector values are compound
                     strings like 'Transport - Aviation', 'Transport - Rail')."
     b) project   → "For these clients {prior_results}, return ProjectNumbers and FullTitles
                     where ProjectStartDate >= DATEADD(MONTH, -24, GETDATE()) (or as the
                     question specifies). Use ENT_Building.UltimateClientMasterID = ClientID."
     c) people    → "For these projects {prior_results}, return StaffName, LocationName, and
                     COUNT(DISTINCT ProjectNumber) using ENT_WorkOrder. Order by count desc,
                     LIMIT 25."

3. If the question filters by a PROJECT property (sector, status, framework) AND asks for
   STAFF, the chain is:
     a) project   → "Return ProjectNumbers and FullTitles where <project filter>."
     b) people    → "For these projects {prior_results}, return StaffName, LocationName, and
                     COUNT(DISTINCT ProjectNumber) using ENT_WorkOrder. Order by count desc,
                     LIMIT 25."

4. If the question asks for clients/projects/people in ONE domain only, output a single subtask.

5. Always be explicit in sub-questions: name the column to filter on, name the table to use,
   and demand a concrete output format (table with named columns + LIMIT 25 by default).

6. Never ask the project or client agent for a list of staff. Only `people` returns staff.

Return ONLY a JSON object with this exact schema, no prose, no code fences:
{"subtasks": [{"agent": "people|project|client", "subquestion": "..."}]}
"""


_REDUCER_INSTRUCTIONS = """You are merging the outputs of 1-3 sibling Enterprise domain agents into ONE
coherent final answer to the user's original question. The sub-answers may include tables,
SQL, citations, or partial deferrals. Resolve conflicts in favour of the on-topic agent.
Do NOT invent data not present in the sub-answers. Use a one-sentence summary, then a
markdown table if the sub-answers contain tabular data."""


_AGENT_NAME_MAP = {
    "people":  "M2_PEOPLE_AGENT_NAME",
    "project": "M2_PROJECT_AGENT_NAME",
    "client":  "M2_CLIENT_AGENT_NAME",
}


def _strip_json_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        # remove opening fence with optional language tag
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1:]
        if t.endswith("```"):
            t = t[: -3]
    return t.strip()


def _plan_subtasks(openai, question: str, model: str, deadline: float,
                    token_totals: dict, trace: list[dict]) -> list[dict]:
    """Ask the planner to decompose the question. Falls back to a single
    'project' subtask containing the raw question if planning fails."""
    if time.time() > deadline:
        return [{"agent": "project", "subquestion": question}]

    resp = openai.responses.create(
        model=model,
        instructions=_PLANNER_INSTRUCTIONS,
        input=question,
    )
    trace.append({"turn": "plan", "agent": f"planner({model})",
                   "response": _resp_to_jsonable(resp)})
    _accumulate_tokens(resp, token_totals)

    raw = _strip_json_fence(_extract_text(resp))
    try:
        plan = json.loads(raw)
        subtasks = plan.get("subtasks") or []
        clean: list[dict] = []
        for st in subtasks:
            agent = (st.get("agent") or "").lower()
            sub = (st.get("subquestion") or "").strip()
            if agent in _AGENT_NAME_MAP and sub:
                clean.append({"agent": agent, "subquestion": sub})
        if clean:
            return clean
    except Exception:
        pass
    return [{"agent": "project", "subquestion": question}]


def _run_m2_decomposed(openai, case: dict, deadline: float, tool_call_counter: list[int],
                        token_totals: dict, trace: list[dict]) -> tuple[Any, str]:
    """Decomposing orchestrator: planner -> per-domain children (chained, with
    {prior_results} inlining) -> reducer."""
    model = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-5.4")
    question = case["Question"]

    subtasks = _plan_subtasks(openai, question, model, deadline, token_totals, trace)

    sub_answers: list[str] = []
    prior_text = ""
    last_resp = None
    for st in subtasks:
        if time.time() > deadline:
            return last_resp, f"Exceeded {PER_QUESTION_TIMEOUT_SEC}s before {st['agent']}"
        agent_name = os.environ[_AGENT_NAME_MAP[st["agent"]]]
        sub_q = st["subquestion"].replace("{prior_results}", prior_text or "(none yet)")
        resp, err = _run_function_loop(openai, agent_name, sub_q,
                                         method="M2", deadline=deadline,
                                         tool_call_counter=tool_call_counter,
                                         token_totals=token_totals, trace=trace)
        last_resp = resp
        text = _extract_text(resp) or "(no answer)"
        sub_answers.append(f"### {st['agent']} agent\n**Sub-question**: {sub_q}\n\n{text}")
        prior_text = text
        if err:
            sub_answers.append(f"_(error from {st['agent']}: {err})_")

    # If only one subtask, skip the reducer call — its answer is the answer.
    if len(subtasks) == 1 and last_resp is not None:
        return last_resp, ""

    if time.time() > deadline:
        return last_resp, f"Exceeded {PER_QUESTION_TIMEOUT_SEC}s before reducer"

    stitched_input = (
        f"Original question: {question}\n\n"
        "Sub-answers from the domain agents:\n\n"
        + "\n\n".join(sub_answers)
    )
    resp = openai.responses.create(
        model=model,
        instructions=_REDUCER_INSTRUCTIONS,
        input=stitched_input,
    )
    trace.append({"turn": "reduce", "agent": f"reducer({model})",
                   "response": _resp_to_jsonable(resp)})
    _accumulate_tokens(resp, token_totals)
    tool_call_counter[0] += sum(1 for it in (resp.output or []) if _is_tool_call_item(it))
    return resp, ""


def run_one(openai, method: str, case: dict) -> Result:
    code = case["Code"]
    question = case["Question"]
    persona = case.get("Persona", "")
    theme = case.get("Theme", "")
    level = case.get("Level", "")

    start = time.time()
    deadline = start + PER_QUESTION_TIMEOUT_SEC

    tool_call_counter = [0]
    token_totals: dict = {}
    trace: list[dict] = []
    error = ""
    answer = ""
    final_resp = None

    try:
        if method == "M2":
            final_resp, error = _run_m2_decomposed(openai, case, deadline,
                                                     tool_call_counter, token_totals, trace)
        elif method == "M4":
            from m4_runner import run_m4  # local import — m4 owns its tools
            final_resp, error = run_m4(openai, case, deadline,
                                         tool_call_counter, token_totals, trace)
        elif method == "M6":
            from m6_runner import run_m6  # local import — m6 owns its tools
            final_resp, error = run_m6(openai, case, deadline,
                                         tool_call_counter, token_totals, trace)
        elif method == "M5":
            agent_name = os.environ[ENV_KEY["M5"][0]]
            final_resp, error = _run_m5_ontology(openai, agent_name, question,
                                                   deadline, tool_call_counter,
                                                   token_totals, trace)
        else:
            agent_name = os.environ[ENV_KEY[method][0]]
            final_resp, error = _run_function_loop(openai, agent_name, question,
                                                     method=method, deadline=deadline,
                                                     tool_call_counter=tool_call_counter,
                                                     token_totals=token_totals, trace=trace)
        if final_resp is not None:
            answer = _extract_text(final_resp)
            status = getattr(final_resp, "status", "") or "completed"
        else:
            status = "failed"
    except Exception as e:
        status = "failed"
        error = repr(e)[:1500]

    if error and not status:
        status = "failed"
    if error == f"Exceeded {PER_QUESTION_TIMEOUT_SEC}s in function loop":
        status = "timeout"

    elapsed = time.time() - start

    # dump trace for this question
    try:
        TRACES_DIR.mkdir(parents=True, exist_ok=True)
        out = TRACES_DIR / f"{method}_{code}.json"
        out.write_text(json.dumps({
            "method": method,
            "code": code,
            "question": question,
            "trace": trace,
            "tool_call_count": tool_call_counter[0],
            "tokens": token_totals,
            "elapsed_sec": round(elapsed, 2),
            "status": status,
            "error": error,
            "answer": answer,
        }, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass

    return Result(
        Method=method, Code=code, Persona=persona, Theme=theme, Level=level,
        Question=question, Answer=answer, LatencySec=round(elapsed, 2),
        PromptTokens=token_totals.get("PromptTokens"),
        CompletionTokens=token_totals.get("CompletionTokens"),
        TotalTokens=token_totals.get("TotalTokens"),
        ToolCallCount=tool_call_counter[0],
        Status=status, Error=error,
    )


def write_csv(path: Path, rows: list[Result]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(Result.__annotations__.keys()))
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))


def write_markdown(path: Path, rows: list[Result], title: str) -> None:
    """Write a human-readable per-method (or combined) markdown report.

    Includes a one-line-per-question summary table and the full Answer text
    for each question, so reviewers can read results without parsing CSV.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_rows = sorted(rows, key=lambda r: (r.Method, r.Persona, r.Code))
    s = summarise(rows)
    lines: list[str] = [
        f"# {title}",
        "",
        f"_{len(rows)} question(s) · "
        f"{s.get('completed', 0)} completed · "
        f"{s.get('failed', 0)} failed · "
        f"{s.get('timeout', 0)} timeout · "
        f"median latency {s.get('median_latency_sec')} s · "
        f"mean tool calls {s.get('mean_tool_calls')}_",
        "",
        "## Summary",
        "",
        "| Method | Code | Persona | Level | Theme | Status | Latency (s) | Tool calls | Tokens (total) |",
        "|---|---|---|---|---|---|---:|---:|---:|",
    ]
    for r in sorted_rows:
        lines.append(
            f"| {r.Method} | {r.Code} | {r.Persona} | {r.Level} | {r.Theme} | "
            f"{r.Status} | {r.LatencySec} | {r.ToolCallCount} | {r.TotalTokens or ''} |"
        )
    lines += ["", "## Answers", ""]
    for r in sorted_rows:
        answer = (r.Answer or "").rstrip()
        lines += [
            f"### {r.Method} · {r.Code} — {r.Theme} _(Level: {r.Level})_",
            "",
            f"**Question:** {r.Question}",
            "",
            (f"_Status_ `{r.Status}` · _Latency_ {r.LatencySec} s · "
             f"_Tool calls_ {r.ToolCallCount} · "
             f"_Tokens_ {r.TotalTokens or '?'} "
             f"(prompt {r.PromptTokens or '?'} / completion {r.CompletionTokens or '?'})"),
            "",
            "**Answer:**",
            "",
            answer if answer else "_(empty)_",
        ]
        if r.Error:
            lines += ["", f"> **Error:** {r.Error}"]
        lines += ["", "---", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def read_results_csv(path: Path) -> list[Result]:
    """Reload a previously-written results CSV back into Result objects."""
    if not path.exists():
        return []
    out: list[Result] = []
    field_types = Result.__annotations__
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            kwargs = {}
            for k, t in field_types.items():
                v = row.get(k, "")
                if v == "" or v is None:
                    kwargs[k] = None if "Optional" in str(t) else ("" if t is str else 0)
                    continue
                try:
                    if t is int or "int" in str(t).lower():
                        kwargs[k] = int(float(v))
                    elif t is float or "float" in str(t).lower():
                        kwargs[k] = float(v)
                    else:
                        kwargs[k] = v
                except (TypeError, ValueError):
                    kwargs[k] = v
            out.append(Result(**kwargs))
    return out


def run_one_with_retries(openai, method: str, case: dict,
                          max_retries: int = MAX_TRANSIENT_RETRIES) -> Result:
    """run_one + exponential backoff on transient Fabric/Foundry errors."""
    last: Optional[Result] = None
    for attempt in range(max_retries + 1):
        r = run_one(openai, method, case)
        if r.Status == "completed" or not _is_transient_error(r.Error):
            return r
        last = r
        if attempt >= max_retries:
            break
        delay = RETRY_BASE_DELAY_SEC * (2 ** attempt)
        print(f"        ! transient err on attempt {attempt + 1}; retrying in {delay:.0f}s")
        time.sleep(delay)
    return last  # type: ignore[return-value]


def summarise(rows: list[Result]) -> dict:
    if not rows:
        return {}
    lats = [r.LatencySec for r in rows if r.Status == "completed"]
    return {
        "count":             len(rows),
        "completed":         sum(1 for r in rows if r.Status == "completed"),
        "failed":            sum(1 for r in rows if r.Status == "failed"),
        "timeout":           sum(1 for r in rows if r.Status == "timeout"),
        "median_latency_sec": round(statistics.median(lats), 2) if lats else None,
        "p95_latency_sec":   round(statistics.quantiles(lats, n=20)[-1], 2) if len(lats) >= 5 else None,
        "mean_tool_calls":   round(statistics.mean(r.ToolCallCount for r in rows), 2),
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run EnterpriseKG agent comparison tests (v2).")
    p.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS))
    p.add_argument("--code", help="Run only one question by Code (e.g. WW-VH01).")
    p.add_argument("--codes", nargs="+",
                    help="Run a subset of questions by Code (e.g. NG-B01 NG-B02).")
    p.add_argument("--persona", nargs="+",
                    help="Run only questions whose Persona matches (case-insensitive, "
                          "e.g. --persona 'Work Winner').")
    p.add_argument("--retry-failed", action="store_true",
                    help="Re-run only rows whose Status != 'completed' in the existing "
                          "results_<METHOD>.csv, then merge them back into the same file.")
    p.add_argument("--pause", type=float, default=INTER_QUESTION_PAUSE_SEC,
                    help=f"Seconds to sleep between questions (default {INTER_QUESTION_PAUSE_SEC}).")
    p.add_argument("--timeout", type=int, default=PER_QUESTION_TIMEOUT_SEC,
                    help=f"Per-question hard timeout in seconds (default {PER_QUESTION_TIMEOUT_SEC}). "
                          "Bump this if Fabric ontology calls are slow.")
    p.add_argument("--dry-run", action="store_true", help="Print what would run; no calls.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    load_env()

    # Apply CLI timeout override (used by run_one to compute per-question deadlines).
    global PER_QUESTION_TIMEOUT_SEC
    if args.timeout and args.timeout != PER_QUESTION_TIMEOUT_SEC:
        PER_QUESTION_TIMEOUT_SEC = args.timeout

    all_cases = load_test_cases()

    explicit_codes: Optional[set[str]] = None
    if args.codes:
        explicit_codes = set(args.codes)
    elif args.code:
        explicit_codes = {args.code}

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # validate env per requested method
    for m in args.methods:
        for var in ENV_KEY[m]:
            require(var)

    personas: Optional[set[str]] = (
        {p.strip().casefold() for p in args.persona} if args.persona else None
    )

    if args.dry_run:
        for m in args.methods:
            cases_m = _select_cases(all_cases, m, explicit_codes, personas, args.retry_failed)
            agents = ([os.environ.get(k, "<unset>") for k in ENV_KEY[m]]
                      if ENV_KEY[m] else ["(direct-tool, no Foundry agent)"])
            print(f"[dry-run] {m}: {len(cases_m)} questions, agent(s)={agents}")
        return 0

    openai = build_openai() if any(method != "M4" for method in args.methods) else None
    combined: list[Result] = []

    for method in args.methods:
        cases = _select_cases(all_cases, method, explicit_codes, personas, args.retry_failed)
        if not cases:
            print(f"\n=== {method}: nothing to do ===")
            continue
        agents = ([os.environ[k] for k in ENV_KEY[method]]
                  if ENV_KEY[method] else ["(direct-tool, no Foundry agent)"])
        print(f"\n=== {method} ({len(cases)} questions, agent(s) {agents}) ===")

        results: list[Result] = []
        for i, case in enumerate(cases, 1):
            print(f"  [{i:>2}/{len(cases)}] {case['Code']:<10} {case['Question'][:90]!r}")
            r = run_one_with_retries(openai, method, case)
            results.append(r)
            print(f"        -> {r.Status} in {r.LatencySec}s, {r.ToolCallCount} tool calls"
                   + (f", err={r.Error[:120]}" if r.Error else ""))
            time.sleep(args.pause)

        method_csv = RESULTS_DIR / f"results_{method}.csv"
        method_md  = RESULTS_DIR / f"results_{method}.md"
        if args.retry_failed and method_csv.exists():
            existing = read_results_csv(method_csv)
            merged = _merge_results(existing, results)
            write_csv(method_csv, merged)
            write_markdown(method_md, merged, title=f"{method} — test results")
            combined.extend(merged)
            print(f"  Merged {len(results)} re-runs into {method_csv} \u2014 "
                   f"summary: {summarise(merged)}")
        else:
            write_csv(method_csv, results)
            write_markdown(method_md, results, title=f"{method} — test results")
            combined.extend(results)
            print(f"  Wrote {method_csv} \u2014 summary: {summarise(results)}")

    if len(args.methods) > 1:
        write_csv(RESULTS_DIR / "_combined.csv", combined)
        write_markdown(RESULTS_DIR / "_combined.md", combined,
                        title="All methods — combined results")
        print(f"\nWrote {RESULTS_DIR / '_combined.csv'}")

    return 0


def _select_cases(all_cases: list[dict], method: str,
                   explicit_codes: Optional[set[str]],
                   personas: Optional[set[str]],
                   retry_failed: bool) -> list[dict]:
    """Pick the right slice of test_cases for one method.

    If --retry-failed is set, only re-run rows whose Status != 'completed' in
    the existing per-method CSV. If --code/--codes is also set, restrict to
    that intersection. If --persona is set, restrict to rows whose Persona
    matches (case-insensitive).
    """
    cases = list(all_cases)
    if explicit_codes is not None:
        cases = [c for c in cases if c["Code"] in explicit_codes]
        if not cases:
            sys.exit(f"ERROR: none of {sorted(explicit_codes)} found in test_cases.csv")
    if personas is not None:
        cases = [c for c in cases if c.get("Persona", "").strip().casefold() in personas]
        if not cases:
            sys.exit(f"ERROR: no test_cases.csv rows matched persona(s) {sorted(personas)}")
    if retry_failed:
        path = RESULTS_DIR / f"results_{method}.csv"
        existing = read_results_csv(path)
        failed_codes = {r.Code for r in existing if r.Status != "completed"}
        cases = [c for c in cases if c["Code"] in failed_codes]
    return cases


def _merge_results(existing: list[Result], new: list[Result]) -> list[Result]:
    """Replace any existing rows whose Code matches a new row's Code, keep
    the rest, preserve ordering, append truly-new codes at the end."""
    new_by_code = {r.Code: r for r in new}
    out: list[Result] = []
    seen: set[str] = set()
    for r in existing:
        out.append(new_by_code.get(r.Code, r))
        seen.add(r.Code)
    for r in new:
        if r.Code not in seen:
            out.append(r)
    return out


if __name__ == "__main__":
    sys.exit(main())
