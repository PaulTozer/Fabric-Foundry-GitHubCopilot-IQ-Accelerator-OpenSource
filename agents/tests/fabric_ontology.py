"""Shared Fabric Ontology MCP client.

Used by:
    - agents/tests/m4_runner.py     (Copilot SDK BYOK)
    - agents/tests/run_tests.py     (M3 dispatcher when M3's Foundry agent
                                     calls the `query_ontology` function tool)

Calls the public Fabric Ontology MCP endpoint's `search_ontology` tool
directly with a Fabric-audience bearer token from DefaultAzureCredential.
See https://learn.microsoft.com/fabric/iq/ontology/how-to-use-ontology-mcp-server
"""

from __future__ import annotations

import json
import os
import sys
from contextvars import ContextVar
from typing import Any

# TEMPORARY DEBUG: emit ontology call/response to stderr so it shows in Container Apps logs.
_ONTOLOGY_DEBUG = os.environ.get("ENTERPRISEIQ_ONTOLOGY_DEBUG", "0") == "1"

def _odbg(msg: str) -> None:
    if _ONTOLOGY_DEBUG:
        try:
            sys.stderr.write(f"[ontology-dbg] {msg}\n")
            sys.stderr.flush()
        except Exception:
            pass


# Per-request user-bearer token forwarded by the WebUI (Easy Auth puts the
# user's Fabric-audience access token on the request as
# `X-MS-TOKEN-AAD-ACCESS-TOKEN`; the WebUI forwards it to MCP as
# `Authorization: Bearer <token>`; the /ask/stream handler stashes it here).
# Used by `_fabric_user_token()` to call Fabric Ontology under the signed-in
# user's identity instead of the container's UAMI (which lacks the
# Copilot/AI entitlements Fabric needs to translate NL→GQL).
USER_FABRIC_TOKEN: ContextVar[str | None] = ContextVar(
    "USER_FABRIC_TOKEN", default=None,
)


# ─── Endpoint config (read at import) ──────────────────────────────────────
FABRIC_API_ENDPOINT = os.environ.get(
    "FABRIC_API_ENDPOINT", "https://api.fabric.microsoft.com"
)
FABRIC_WORKSPACE_ID = os.environ.get("FABRIC_WORKSPACE_ID", "")
FABRIC_ONTOLOGY_ID  = os.environ.get("FABRIC_ONTOLOGY_ID", "")

ONTOLOGY_MCP_URL = (
    os.environ.get("FABRIC_ONTOLOGY_MCP_URL")
    or (
        f"{FABRIC_API_ENDPOINT}/v1/mcp/dataPlane"
        f"/workspaces/{FABRIC_WORKSPACE_ID}"
        f"/items/{FABRIC_ONTOLOGY_ID}/ontologyEndpoint"
        if FABRIC_WORKSPACE_ID and FABRIC_ONTOLOGY_ID
        else ""
    )
)
ONTOLOGY_ENABLED = bool(ONTOLOGY_MCP_URL)


def _fabric_user_token() -> str | None:
    """Acquire a Fabric-audience bearer token for the MCP endpoint.

    Order of preference:
      1. Forwarded user-bearer (set by /ask/stream from the WebUI's
         Easy Auth `X-MS-TOKEN-AAD-ACCESS-TOKEN` header). This is the only
         path that gives Fabric Ontology's NL→GQL translator the
         entitlements it needs in production.
      2. DefaultAzureCredential — Azure CLI when running locally
         (`az login`), or the container's UAMI in Azure (will only work
         for ontology questions if the SP has been licensed for Fabric
         Copilot/AI features).

    Returns None on failure; caller surfaces an actionable error.
    """
    from fabric_connection import CURRENT
    connection = CURRENT.get()
    if connection:
        return connection.caller.fabric_token
    forwarded = USER_FABRIC_TOKEN.get()
    if forwarded:
        _odbg(f"using forwarded user token (len={len(forwarded)})")
        return forwarded
    try:
        from azure.identity import DefaultAzureCredential
        _odbg("no forwarded token; falling back to DefaultAzureCredential")
        return DefaultAzureCredential().get_token(
            "https://api.fabric.microsoft.com/.default"
        ).token
    except Exception:
        return None


def _condense_ontology_response(payload: dict) -> dict:
    """JSON-RPC tools/call response → compact dict for the model.

    Fabric's `search_ontology` returns content[0].text as a JSON string. As of
    the May 2026 ontology preview the inner shape is:

        {
          "raw": {"Fields": ["staff_id", "office"], "Value": [[10254, "Dubai"]]},
          "naturalLanguageResponse": "Priya Birch (staff_id: 10254) works in the Dubai office."
        }

    Older shapes used `data` / `results` / `rows` / `queryResult` / `records` and
    `naturalLanguageAnswer` / `answer` / `summary`. We support all of them and
    always emit a uniform `{is_error, answer, raw, row_count, raw_text}`."""
    result = payload.get("result") or {}
    is_err = bool(result.get("isError"))
    content = result.get("content") or []
    texts = [c.get("text", "") for c in content if c.get("type") == "text"]
    raw_text = "\n".join(t for t in texts if t).strip()

    # Prefer the structured form if Fabric provides it (avoids re-parsing).
    parsed: Any = result.get("structuredContent")
    if not isinstance(parsed, dict) and raw_text:
        try:
            parsed = json.loads(raw_text)
        except Exception:
            parsed = None

    answer:    str | None = None
    rows:      Any        = None
    row_count: int | None = None

    if isinstance(parsed, dict):
        # 1) Natural-language answer.
        for k in ("naturalLanguageResponse", "naturalLanguageAnswer",
                   "answer", "summary"):
            v = parsed.get(k)
            if isinstance(v, str) and v.strip():
                answer = v.strip()
                break

        # 2) Rows. New shape: parsed["raw"] = {Fields: [...], Value: [[...]]}.
        raw_block = parsed.get("raw")
        if isinstance(raw_block, dict) and isinstance(raw_block.get("Value"), list):
            fields = raw_block.get("Fields") or []
            values = raw_block.get("Value") or []
            if fields and all(isinstance(r, list) for r in values):
                rows = [
                    {fields[i]: r[i] for i in range(min(len(fields), len(r)))}
                    for r in values
                ]
            else:
                rows = values
        else:
            # 3) Older shapes — first key wins.
            for k in ("data", "results", "rows", "queryResult", "records"):
                v = parsed.get(k)
                if v is not None:
                    rows = v
                    break

        if rows is None and answer is None:
            rows = parsed

    if isinstance(rows, list):
        row_count = len(rows)
    elif rows is not None:
        row_count = 1

    # If the inner Fabric response signalled an error, promote the most
    # informative text to a top-level `error` so callers (m4_runner / UI /
    # model) surface a real failure reason instead of "0 rows".
    error_msg: str | None = None
    if is_err:
        if isinstance(parsed, str) and parsed.strip():
            error_msg = parsed.strip()
        elif raw_text:
            error_msg = raw_text
        elif answer:
            error_msg = answer
        else:
            error_msg = "Fabric ontology returned isError=true with no message."

    # Fabric NL2Ontology preview consistently fails on compound questions
    # (anything joined by "and what" / "and which" / containing two distinct
    # asks). Direct single-fact phrasings like "What practice is X in?" or
    # "List projects worked on by X." succeed reliably. When we detect this
    # error, replace the unhelpful message with directive guidance so the
    # downstream LLM (M4 planner / UI agent) knows exactly how to recover.
    if error_msg and "Failed to translate NL query" in error_msg:
        error_msg = (
            "Fabric ontology could not translate this question. The NL2Ontology "
            "preview cannot handle compound questions. RECOVERY: split the "
            "question into atomic single-fact asks and call query_ontology "
            "ONCE PER ATOMIC ASK, then merge the results yourself. Examples: "
            "instead of 'what projects has X worked on AND which practice is X in', "
            "send two separate calls: (1) 'List projects worked on by X.' and "
            "(2) 'What practice is X in?'. Only fall back to query_lakehouse "
            "if a single-fact ask also fails."
        )

    out: dict[str, Any] = {
        "is_error":  is_err,
        "answer":    answer,
        "raw":       rows if rows is not None else (parsed if parsed is not None else raw_text or None),
        "row_count": row_count if row_count is not None else 0,
        "raw_text":  raw_text if (rows is None and parsed is None) else None,
    }
    if error_msg:
        out["error"] = f"Fabric ontology error: {error_msg}"
    return out


def query_ontology(question: str) -> dict:
    """Call the public Fabric Ontology MCP endpoint's `search_ontology` tool.

    Auth: Fabric-audience bearer token via DefaultAzureCredential. The user
    must have at least Viewer access to the Fabric workspace that holds the
    ontology item."""
    from fabric_connection import CURRENT
    connection = CURRENT.get()
    endpoint = connection.ontology_url if connection else ONTOLOGY_MCP_URL
    if not endpoint:
        return {
            "error": (
                "Fabric Ontology MCP URL not configured. Set "
                "FABRIC_WORKSPACE_ID + FABRIC_ONTOLOGY_ID (or "
                "FABRIC_ONTOLOGY_MCP_URL) in agents/tests/.env."
            )
        }
    try:
        import requests
    except ImportError:
        return {"error": "requests not installed"}

    _odbg(f"query_ontology called: question={question[:200]!r}")
    _odbg(f"ONTOLOGY_MCP_URL={ONTOLOGY_MCP_URL!r}")

    token = _fabric_user_token()
    if not token:
        _odbg("_fabric_user_token returned None")
        return {"error": "could not acquire Fabric bearer token (run `az login`)"}
    _odbg(f"got fabric token len={len(token)}")

    body = {
        "jsonrpc": "2.0",
        "id":      1,
        "method":  "tools/call",
        "params":  {
            "name":      "search_ontology",
            "arguments": {
                "naturalLanguageQuery":    question,
                "naturalLanguageResponse": True,
            },
        },
    }
    try:
        r = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
                "Accept":        "application/json, text/event-stream",
            },
            json=body,
            timeout=130,
            allow_redirects=False,
        )
    except Exception as exc:
        _odbg(f"requests.post raised: {exc!r}")
        return {"error": f"Fabric ontology MCP request failed: {exc!r}"}

    _odbg(f"http status={r.status_code}")
    _odbg(f"http body[:1500]={(r.text or '')[:1500]!r}")

    if r.status_code >= 400:
        return {
            "error": f"Fabric ontology MCP HTTP {r.status_code}: {(r.text or '')[:400]}",
            "status_code": r.status_code,
        }
    try:
        payload = r.json()
    except Exception:
        return {"error": f"Fabric ontology MCP returned non-JSON: {(r.text or '')[:400]}"}

    if isinstance(payload, dict) and payload.get("error"):
        _odbg(f"jsonrpc error: {payload.get('error')!r}")
        return {"error": f"JSON-RPC error: {payload['error']}"}

    condensed = _condense_ontology_response(payload)
    _odbg(
        f"condensed: row_count={condensed.get('row_count')!r}, "
        f"has_answer={bool(condensed.get('answer'))}, "
        f"is_error={condensed.get('is_error')!r}, "
        f"raw_type={type(condensed.get('raw')).__name__}, "
        f"raw_preview={str(condensed.get('raw'))[:300]!r}"
    )
    return condensed
