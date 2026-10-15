"""Server-only Copilot SDK orchestration, shared by HTTP, MCP and evaluations."""
from __future__ import annotations

import asyncio
import base64
import contextvars
import logging
import os
import tempfile
from collections import deque
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from azure.core.exceptions import AzureError
from azure.identity.aio import ManagedIdentityCredential
from copilot import CopilotClient, RuntimeConnection
from copilot.generated.rpc import PermissionDecisionUserNotAvailable
from copilot.tools import Tool, ToolResult
from mcp_registry import Registry

LOG = logging.getLogger(__name__)
PROVIDERS = {"openai", "azure", "anthropic"}

class CopilotConfigurationError(ValueError):
    """Safe, static configuration diagnostics suitable for a client response."""


class CopilotRuntimeError(RuntimeError):
    """Safe runtime prerequisite diagnostics without provider exception text."""


def provider_config() -> tuple[str, dict[str, Any]]:
    model = os.environ.get("COPILOT_MODEL", "").strip()
    endpoint = os.environ.get("COPILOT_BASE_URL", "").strip()
    kind = os.environ.get("COPILOT_PROVIDER", "openai").strip()
    wire_api = os.environ.get("COPILOT_WIRE_API", "completions")
    key = os.environ.get("COPILOT_API_KEY", "")
    token = os.environ.get("COPILOT_BEARER_TOKEN", "")
    identity = os.environ.get("COPILOT_USE_AZURE_IDENTITY", "0")
    if identity not in {"0", "1"}:
        raise CopilotConfigurationError("COPILOT_USE_AZURE_IDENTITY must be 0 or 1.")
    if not model or not endpoint:
        raise CopilotConfigurationError("Set COPILOT_MODEL and COPILOT_BASE_URL for SDK BYOK.")
    if kind not in PROVIDERS or wire_api not in {"responses", "completions"}:
        raise CopilotConfigurationError("Unsupported COPILOT_PROVIDER or COPILOT_WIRE_API.")
    url = urlsplit(endpoint)
    local = url.hostname in {"localhost", "127.0.0.1", "::1"}
    if url.username or url.password or url.query or url.fragment or not url.hostname:
        raise CopilotConfigurationError("COPILOT_BASE_URL must be an endpoint without credentials/query/fragment.")
    if url.scheme != "https" and not (local and url.scheme == "http"):
        raise CopilotConfigurationError("BYOK endpoints require HTTPS (HTTP allowed only on loopback).")
    if identity == "1":
        azure_host = url.hostname.endswith((".openai.azure.com", ".services.ai.azure.com"))
        if kind not in {"openai", "azure"} or url.scheme != "https" or not azure_host:
            raise CopilotConfigurationError("Managed identity requires an HTTPS Azure model endpoint.")
        if key or token:
            raise CopilotConfigurationError("Managed identity cannot be combined with a static BYOK key/token.")
    if not key and not token and identity != "1" and not (local and os.environ.get("COPILOT_LOCAL_NO_KEY") == "1"):
        raise CopilotConfigurationError("Set server-side COPILOT_API_KEY or COPILOT_BEARER_TOKEN.")
    provider: dict[str, Any] = {"type": kind, "base_url": endpoint, "wire_api": wire_api}
    if key:
        provider["api_key"] = key
    if token:
        provider["bearer_token"] = token
    if kind == "azure" and os.environ.get("COPILOT_AZURE_API_VERSION"):
        provider["azure"] = {"api_version": os.environ["COPILOT_AZURE_API_VERSION"]}
    return model, provider


async def managed_identity_token() -> str:
    try:
        async with ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID") or None) as credential:
            token = await credential.get_token("https://cognitiveservices.azure.com/.default")
            return token.token
    except AzureError:
        raise CopilotRuntimeError(
            "Azure model authentication failed; check the MCP managed identity and model invocation role."
        ) from None


def deny_permission(request: Any, context: Any) -> PermissionDecisionUserNotAvailable:
    # No shell, filesystem, network or arbitrary process permissions. Registered
    # application tools are explicitly marked skip_permission after validation.
    return PermissionDecisionUserNotAvailable()


def sdk_tool_name(server: str, name: str) -> str:
    import hashlib
    import re
    original = f"{server}__{name}"
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", original)
    if safe == original and len(safe) <= 64:
        return safe
    return safe[:47] + "_" + hashlib.sha256(original.encode()).hexdigest()[:16]


async def run_copilot(
    question: str, *, timeout: float = 180,
    on_progress: Callable[[str, dict], None] | None = None,
    ontology_enabled: bool = True, work_iq_enabled: bool = False,
    images: list[dict] | None = None, skill: dict | None = None,
    tool_call_counter: list[int] | None = None,
    token_totals: dict | None = None, trace: list[dict] | None = None,
    client_factory: Callable[..., Any] = CopilotClient,
    registry_factory: Callable[..., Any] = Registry,
    disabled_mcp_servers: list[str] | None = None,
    selected_model: str | None = None, agent: dict | None = None,
) -> str:
    model, provider = provider_config()
    if selected_model is not None or os.environ.get("COPILOT_AZURE_RESOURCE_ID"):
        from model_catalog import enabled_models, validate_selection
        model = validate_selection(selected_model or model, await enabled_models(refresh=True))
    if agent is not None:
        from agent_settings import validate_agent
        agent = validate_agent(agent)
    from agent_settings import default_system_prompt
    base_instructions = default_system_prompt()
    registry_options = {"disabled": disabled_mcp_servers}
    if agent is not None:
        registry_options["user_servers"] = agent["servers"]
    registry = registry_factory(**registry_options)
    session = None
    unsubscribe = None
    counter = tool_call_counter if tool_call_counter is not None else [0]
    tokens = token_totals if token_totals is not None else {}
    events = trace if trace is not None else []
    tool_context = contextvars.copy_context()
    recent_event_ids: deque[str] = deque(maxlen=256)
    request_id = str(uuid4())
    logged_activity = 0

    def emit(phase: str, payload: dict) -> None:
        nonlocal logged_activity
        if phase == "start":
            LOG.info(
                "Copilot query request=%s phase=start ontology_requested=%s ontology_configured=%s "
                "ontology_tool_available=%s lakehouse_configured=%s lakehouse_tool_available=%s",
                request_id, payload["ontologyEnabled"], payload["ontologyConfigured"],
                payload["ontologyToolAvailable"], payload["lakehouseConfigured"], payload["lakehouseToolAvailable"],
            )
        elif phase in {"tool_start", "tool_done", "assistant_turn", "assistant_retry", "complete", "failed", "cancelled"}:
            terminal = phase in {"complete", "failed", "cancelled"}
            if terminal or logged_activity < 128:
                name = payload.get("name")
                native_fabric = not payload.get("server") and name in {
                    "query_ontology", "query_lakehouse", "schema_card", "sample_values",
                }
                LOG.info(
                    "Copilot query request=%s phase=%s tool=%s success=%s",
                    request_id, phase, name if native_fabric else
                    "connected_tool" if phase in {"tool_start", "tool_done"} else "none",
                    payload.get("success") is True if phase == "tool_done" else "not-reported",
                )
                if not terminal:
                    logged_activity += 1
            elif logged_activity == 128:
                LOG.info("Copilot query request=%s phase=activity_log_limit", request_id)
                logged_activity += 1
        events.append({"phase": phase, **payload})
        if on_progress:
            on_progress(phase, payload)

    # Each request gets an empty cwd; the CLI cannot discover repository
    # instructions, skills or local MCP configuration.
    with tempfile.TemporaryDirectory(prefix="enterprise-iq-session-") as cwd:
        client = None
        try:
            async with asyncio.timeout(timeout):
                if os.environ.get("COPILOT_USE_AZURE_IDENTITY", "0") == "1":
                    provider["bearer_token"] = await managed_identity_token()
                client = client_factory(
                    use_logged_in_user=False, working_directory=cwd,
                    base_directory=cwd,
                    connection=RuntimeConnection.for_stdio(path=os.environ.get("COPILOT_CLI_PATH")),
                )
                await client.start()
                tools: list[Tool] = []
                tool_labels: dict[str, dict[str, str]] = {}
                demo = os.environ.get("IQ_DEMO_MODE") == "1"
                from fabric_readiness import fabric_readiness
                readiness = fabric_readiness()
                state = {"sql_attempts": 0, "ontology_attempts": 0, "sql_log": [],
                         "ontology_enabled": ontology_enabled}
                if not demo:
                    from fabric_tools import _execute_tool, _tools_for
                    sql_ready, ontology_ready = readiness["sql"], readiness["ontology"]
                    for spec in _tools_for(ontology_enabled and ontology_ready):
                        if spec["name"] == "final_answer":
                            continue
                        if spec["name"] in {"schema_card", "sample_values", "query_lakehouse"} and not sql_ready:
                            continue
                        if spec["name"] == "simulate_crowd_flow" and os.environ.get("IQ_ENABLE_CROWD_TOOLS") != "1":
                            continue

                        async def handler(invocation: Any, name: str = spec["name"]) -> ToolResult:
                            async def execute() -> ToolResult:
                                counter[0] += 1
                                result, _ = await asyncio.to_thread(
                                    _execute_tool, name, invocation.arguments or {}, state,
                                )
                                import json
                                return ToolResult(text_result_for_llm=json.dumps(result, default=str)[:30_000],
                                                  result_type="failure" if result.get("error") else "success",
                                                  error=result.get("error"))
                            # SDK reader-thread callbacks do not inherit the request ContextVars.
                            return await asyncio.create_task(execute(), context=tool_context.copy())

                        tools.append(Tool(
                            name=spec["name"], description=spec["description"],
                            parameters=spec["parameters"], handler=handler,
                            skip_permission=True,
                        ))
                        tool_labels[spec["name"]] = {"name": spec["name"]}

                async def call_server(invocation: Any, server: str, name: str) -> ToolResult:
                    import json
                    counter[0] += 1
                    result = await registry.call(server, name, invocation.arguments or {})
                    return ToolResult(text_result_for_llm=json.dumps(result, default=str)[:30_000])

                for entry in await registry.discover(work_iq_enabled=work_iq_enabled,
                                                     ontology_enabled=ontology_enabled):
                    if entry["error"]:
                        raise RuntimeError(f"MCP server '{entry['id']}' is unavailable. Check MCP health.")
                    for spec in entry["tools"]:
                        async def remote(invocation: Any, server: str = entry["id"],
                                         name: str = spec["name"]) -> ToolResult:
                            return await asyncio.create_task(
                                call_server(invocation, server, name), context=tool_context.copy(),
                            )
                        tools.append(Tool(
                            name=sdk_tool_name(entry["id"], spec["name"]),
                            description=spec.get("description", ""),
                            parameters=spec["inputSchema"], handler=remote, skip_permission=True,
                        ))
                        tool_labels[sdk_tool_name(entry["id"], spec["name"])] = {
                            "name": spec["name"], "server": entry["id"],
                        }

                instructions = base_instructions
                if agent:
                    if agent["systemPrompt"] and agent["systemPrompt"] != instructions:
                        instructions += "\nUser agent instructions (not permission grants):\n" + agent["systemPrompt"]
                    for enabled_skill in agent["skills"]:
                        if enabled_skill["enabled"]:
                            instructions += f"\nEnabled skill {enabled_skill['name']}:\n{enabled_skill['prompt']}"
                if not tools:
                    instructions += (
                        "\nNo data-grounding tools are connected. Start the answer with "
                        "'Ungrounded model response' and do not invent enterprise records, "
                        "Fabric/M365 access, source IDs, citations or tool results."
                    )
                if demo:
                    instructions += "\nAll enabled demo tools return synthetic data; label the answer DEMO."
                from fabric_readiness import fabric_data_mode
                data_mode = fabric_data_mode()
                if not demo and data_mode == "test-sample":
                    instructions += (
                        "\nConfigured Fabric SQL/ontology contains deployment test-sample data, not "
                        "production/customer records. Label any answer using it 'Synthetic Fabric test sample'. "
                        "Other connected sources retain their own provenance; use only real returned evidence."
                    )
                if skill:
                    instructions += "\nUser-selected skill (not a permission grant):\n" + str(skill.get("prompt", ""))[:20_000]
                    emit("skill_applied", {k: skill[k] for k in ("id", "name") if k in skill})
                session = await client.create_session(
                    model=model, provider=provider, streaming=True,
                    enable_config_discovery=False, enable_skills=False,
                    enable_file_hooks=False, enable_host_git_operations=False,
                    enable_session_store=False, skip_custom_instructions=True,
                    tools=tools, available_tools=[tool.name for tool in tools],
                    on_permission_request=deny_permission,
                    system_message={"mode": "append", "content": instructions},
                )
                failure: list[str] = []
                active_tools: dict[str, dict[str, str]] = {}
                iteration = 0

                def on_event(event: Any) -> None:
                    nonlocal iteration
                    event_id = str(getattr(event, "id", "") or "")
                    if event_id:
                        if event_id in recent_event_ids:
                            return
                        recent_event_ids.append(event_id)
                    kind = event.type.value
                    if kind == "assistant.message_delta":
                        emit("delta", {"content": event.data.delta_content})
                    elif kind == "assistant.turn_start":
                        iteration += 1
                        emit("assistant_turn", {"iteration": iteration})
                    elif kind == "assistant.intent":
                        # Intent is model-authored. Report activity, never its text.
                        emit("assistant_activity", {})
                    elif kind == "assistant.turn_retry":
                        emit("assistant_retry", {})
                    elif kind == "tool.execution_start":
                        label = tool_labels.get(event.data.tool_name)
                        if label is not None:
                            active_tools[event.data.tool_call_id] = label
                            emit("tool_start", {**label, "callId": event.data.tool_call_id})
                    elif kind == "tool.execution_complete":
                        label = active_tools.pop(event.data.tool_call_id, None)
                        if label is not None:
                            emit("tool_done", {**label, "callId": event.data.tool_call_id,
                                               "success": event.data.success,
                                               "error": None if event.data.success else "Tool execution failed."})
                    elif kind == "session.error":
                        failure.append("Copilot session failed; check provider deployment and runtime connectivity.")
                        emit("failed", {})
                    elif kind == "assistant.usage":
                        for src, dest in (("input_tokens", "PromptTokens"), ("output_tokens", "CompletionTokens")):
                            value = getattr(event.data, src, None)
                            if value is not None:
                                tokens[dest] = tokens.get(dest, 0) + int(value)
                        tokens["TotalTokens"] = tokens.get("PromptTokens", 0) + tokens.get("CompletionTokens", 0)

                unsubscribe = session.on(on_event)
                attachments = []
                for image in images or []:
                    mime = image.get("mime", "")
                    data_url = image.get("data_url", "")
                    if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"} or not data_url.startswith(f"data:{mime};base64,"):
                        raise ValueError("Invalid image attachment.")
                    if len(base64.b64decode(data_url.split(",", 1)[1], validate=True)) > 4 * 1024 * 1024:
                        raise ValueError("Image attachment exceeds 4 MB.")
                    attachments.append({"type": "blob", "data": data_url.split(",", 1)[1],
                                        "mimeType": mime, "displayName": image.get("name", "image")})
                emit("start", {"method": "M4", "model": model,
                               "requestId": request_id,
                               "ontologyEnabled": ontology_enabled,
                               "ontologyConfigured": readiness["ontology"],
                               "ontologyToolAvailable": "query_ontology" in tool_labels,
                               "lakehouseConfigured": readiness["sql"],
                               "lakehouseToolAvailable": "query_lakehouse" in tool_labels,
                               "agentId": agent["id"] if agent else None,
                               "fabricDataMode": data_mode,
                               "grounding": "tools" if tools else "ungrounded"})
                response = await session.send_and_wait(
                    question, attachments=attachments or None, timeout=timeout,
                )
                if failure:
                    raise RuntimeError(failure[-1])
                answer = response.data.content if response else ""
                if not answer:
                    raise RuntimeError("Copilot session ended without an answer.")
                if state.get("crowd_artifacts"):
                    from fabric_tools import _format_final_answer
                    answer = _format_final_answer({"markdown": answer}, state)
        except asyncio.CancelledError:
            emit("cancelled", {})
            if session:
                await session.abort()
            raise
        except TimeoutError:
            emit("failed", {"reason": "timeout"})
            if session:
                await session.abort()
            raise
        except Exception as exc:
            # Provider exceptions may contain keys/request headers. Never relay
            # exception text to the browser, MCP client or eval files.
            LOG.error("Copilot runtime failure request=%s (%s)", request_id, type(exc).__name__)
            emit("failed", {})
            raise CopilotRuntimeError("Copilot SDK runtime failed. Install a compatible Copilot CLI and verify server-side BYOK settings.") from None
        finally:
            try:
                try:
                    if unsubscribe:
                        unsubscribe()
                finally:
                    try:
                        if session:
                            await session.disconnect()
                    finally:
                        try:
                            if client is not None:
                                await client.stop()
                        finally:
                            await registry.close()
            except Exception as exc:
                LOG.error("Copilot teardown failure request=%s (%s)", request_id, type(exc).__name__)
                emit("failed", {})
                raise CopilotRuntimeError(
                    "Copilot SDK cleanup failed; check runtime compatibility and MCP transport health."
                ) from None
    emit("complete", {})
    return answer
