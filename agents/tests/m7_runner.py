"""M7 runner — Work IQ agent (Microsoft 365 grounding, preview).

Thin wrapper around the Foundry-hosted prompt agent `enterprise-kg-m7-workiq`
whose only tool is `work_iq_preview`. Because the tool is hosted by
Foundry the client side does not run a function-call loop — we just
dispatch the question to the agent and pull the final answer back.

The critical bit is **which identity the Foundry call runs as**:

  - Work IQ requires the request to be made on behalf of the signed-in
    user (Microsoft 365 Copilot licence + delegated WorkIQAgent.Ask
    permission). The container's managed identity is application-only
    and Work IQ rejects app-only auth.

  - The caller is therefore responsible for constructing the OpenAI
    client with the user's AI-audience bearer token (acquired in the
    browser via MSAL and forwarded through the WebUI -> MCP chain).

The MCP /ask/stream handler does this and passes the resulting client
straight in.

See agents/Method7_WorkIQ_Instructions.md for the M7 agent's system
prompt and intended workflow.
See agents/provisioning/provision_workiq.py for the Entra app reg,
Foundry connection, and agent registration plumbing.
"""

from __future__ import annotations

import time
from typing import Any, Callable

# Re-use the established helpers — no need for our own copies.
import run_tests as rt  # type: ignore[import-not-found]


def run_m7(
    openai: Any,
    question: str,
    *,
    deadline: float,
    tool_call_counter: list[int],
    token_totals: dict,
    trace: list[dict],
    agent_name: str,
    on_progress: Callable[[str, dict], None] | None = None,
) -> tuple[Any, str]:
    """Dispatch ``question`` to the Work IQ prompt agent.

    Parameters mirror the other run_* helpers in this directory so the
    MCP /ask/stream worker can call any method through one signature.

    Emits two progress events for the live UI:

      - ``workiq_request``  — payload {"question": "<truncated>"}
      - ``workiq_invoked``  — payload {"agent": <name>, "elapsed_sec": …}
        Sent only on success; the WebUI uses it to attach a "Work IQ"
        chip to the assistant bubble. On failure we surface the Foundry exception verbatim
        through the standard error channel.
    """
    if on_progress is not None:
        try:
            on_progress("workiq_request", {"question": question[:240]})
        except Exception:
            pass

    if time.time() > deadline:
        return None, "M7 hit deadline before dispatch"

    started = time.time()
    try:
        resp = openai.responses.create(
            input=question,
            extra_body=rt._agent_ref(agent_name),
        )
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"

    trace.append({
        "turn":     0,
        "agent":    agent_name,
        "method":   "M7",
        "response": rt._resp_to_jsonable(resp),
    })
    rt._accumulate_tokens(resp, token_totals)
    tool_call_counter[0] += sum(
        1 for it in (resp.output or []) if rt._is_tool_call_item(it)
    )

    if on_progress is not None:
        try:
            on_progress("workiq_invoked", {
                "agent":       agent_name,
                "elapsed_sec": round(time.time() - started, 2),
            })
        except Exception:
            pass

    return resp, ""
