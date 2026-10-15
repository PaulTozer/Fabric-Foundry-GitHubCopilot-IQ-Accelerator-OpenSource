import asyncio
from types import SimpleNamespace as Obj
from uuid import uuid4

import pytest
from azure.identity.aio import ManagedIdentityCredential
from copilot.generated.rpc import PermissionDecisionUserNotAvailable
from copilot.session_events import SessionEvent
from copilot.tools import ToolInvocation, ToolResult
from copilot_runner import deny_permission, provider_config, run_copilot, sdk_tool_name


@pytest.fixture(autouse=True)
def provider(monkeypatch):
    for key in ("COPILOT_API_KEY", "COPILOT_BEARER_TOKEN", "COPILOT_PROVIDER",
                "COPILOT_LOCAL_NO_KEY", "COPILOT_WIRE_API", "COPILOT_AZURE_API_VERSION"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("COPILOT_USE_AZURE_IDENTITY", raising=False)
    monkeypatch.delenv("COPILOT_AZURE_RESOURCE_ID", raising=False)
    monkeypatch.setenv("COPILOT_MODEL", "example-deployment")
    monkeypatch.setenv("COPILOT_BASE_URL", "https://example.openai.azure.com/openai/v1/")
    monkeypatch.setenv("COPILOT_API_KEY", "test-only-key")
    monkeypatch.setenv("IQ_DEMO_MODE", "1")


class FakeRegistry:
    instances = []
    def __init__(self, **kwargs):
        self.closed = False
        self.disabled = kwargs.get("disabled")
        self.user_servers = kwargs.get("user_servers", [])
        self.instances.append(self)
    async def discover(self, **kwargs):
        return [{"id": "fabric-iq", "error": "", "tools": [
            {"name": "portfolio", "description": "Read only", "inputSchema": {"type": "object"}}
        ]}]
    async def call(self, server, name, arguments):
        assert (server, name) == ("fabric-iq", "portfolio")
        return {"simulation_only": True}
    async def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, mode):
        self.mode, self.closed, self.aborted, self.unsubscribed = mode, False, False, False
    def on(self, callback):
        self.callback = callback
        def unsubscribe():
            self.unsubscribed = True
        return unsubscribe
    async def send_and_wait(self, prompt, **kwargs):
        self.prompt = prompt
        self.callback(sdk_event("assistant.message_delta", {"messageId": "answer", "deltaContent": "Answer"}))
        if self.mode == "error":
            self.callback(sdk_event("session.error", {"errorType": "provider", "message": "test-only-key"}))
        if self.mode == "crash":
            raise OSError("Authorization: test-only-key")
        if self.mode == "wait":
            await asyncio.sleep(20)
        return Obj(data=Obj(content="Grounded answer"))
    async def abort(self):
        self.aborted = True
    async def disconnect(self):
        self.closed = True


class FakeClient:
    instances = []
    mode = "ok"
    def __init__(self, **kwargs):
        self.kwargs, self.stopped = kwargs, False
        self.session = FakeSession(self.mode)
        self.instances.append(self)
    async def start(self):
        pass
    async def create_session(self, **kwargs):
        self.options = kwargs
        return self.session
    async def stop(self):
        self.stopped = True


def sdk_event(kind, data):
    return SessionEvent.from_dict({
        "type": kind, "data": data, "id": str(uuid4()),
        "timestamp": "2026-10-09T12:00:00Z",
    })


@pytest.mark.asyncio
async def test_byok_options_tools_stream_cleanup():
    FakeClient.mode = "ok"
    progress = []
    answer = await run_copilot("Question", client_factory=FakeClient, registry_factory=FakeRegistry,
                               on_progress=lambda phase, data: progress.append((phase, data)))
    client = FakeClient.instances[-1]
    assert answer == "Grounded answer"
    assert client.kwargs["use_logged_in_user"] is False
    assert client.options["provider"]["api_key"] == "test-only-key"
    assert client.options["model"] == "example-deployment"
    assert client.options["streaming"] is True
    assert client.options["enable_config_discovery"] is False
    assert client.options["available_tools"] == ["fabric-iq__portfolio"]
    result = await client.options["tools"][0].handler(ToolInvocation(arguments={}))
    assert isinstance(result, ToolResult)
    assert '"simulation_only": true' in result.text_result_for_llm
    assert any(phase == "delta" for phase, _ in progress)
    assert client.stopped and client.session.closed and client.session.unsubscribed
    assert FakeRegistry.instances[-1].closed
    assert isinstance(deny_permission(None, None), PermissionDecisionUserNotAvailable)
    assert "test-only-key" not in str(progress)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["error", "crash", "wait"])
async def test_errors_and_timeout_are_explicit_and_cleanup(mode, caplog):
    caplog.set_level("INFO", logger="copilot_runner")
    FakeClient.mode = mode
    progress = []
    with pytest.raises((RuntimeError, TimeoutError)) as error:
        await run_copilot("Question", timeout=0.03, client_factory=FakeClient, registry_factory=FakeRegistry,
                          on_progress=lambda phase, data: progress.append((phase, data)))
    assert "test-only-key" not in str(error.value)
    client = FakeClient.instances[-1]
    assert client.stopped and client.session.closed
    assert FakeRegistry.instances[-1].closed
    assert progress[-1][0] == "failed"
    assert "test-only-key" not in str(progress)
    assert "phase=failed" in caplog.text and "phase=complete" not in caplog.text
    assert "test-only-key" not in caplog.text
    if mode == "wait":
        assert client.session.aborted


@pytest.mark.asyncio
async def test_cancellation_aborts_session(caplog):
    caplog.set_level("INFO", logger="copilot_runner")
    FakeClient.mode = "wait"
    progress = []
    task = asyncio.create_task(run_copilot(
        "Question", client_factory=FakeClient, registry_factory=FakeRegistry,
        on_progress=lambda phase, data: progress.append((phase, data)),
    ))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    client = FakeClient.instances[-1]
    assert client.session.aborted and client.session.closed and client.stopped
    assert progress[-1] == ("cancelled", {})
    assert "phase=cancelled" in caplog.text and "phase=complete" not in caplog.text


@pytest.mark.asyncio
async def test_pinned_sdk_iterations_tools_failure_fallback_and_redaction(monkeypatch, caplog):
    import importlib.metadata

    assert importlib.metadata.version("github-copilot-sdk") == "1.0.15"
    caplog.set_level("INFO", logger="copilot_runner")
    monkeypatch.setenv("IQ_DEMO_MODE", "0")
    monkeypatch.setattr("fabric_readiness.fabric_readiness", lambda: {"sql": True, "ontology": True})
    events = [
        sdk_event("assistant.turn_start", {"turnId": "first"}),
        sdk_event("assistant.intent", {"intent": "private reasoning and test-only-key"}),
        sdk_event("assistant.reasoning_delta", {"reasoningId": "private", "deltaContent": "private reasoning"}),
        sdk_event("tool.execution_start", {"toolCallId": "ontology", "toolName": "query_ontology",
                                          "arguments": {"authorization": "test-only-key"}}),
        sdk_event("tool.execution_complete", {"toolCallId": "ontology", "success": False,
                                             "error": {"message": "Authorization: test-only-key"}}),
        sdk_event("assistant.turn_retry", {"turnId": "first", "reason": "private provider details"}),
        sdk_event("tool.execution_start", {"toolCallId": "sql", "toolName": "query_lakehouse"}),
        sdk_event("tool.execution_complete", {"toolCallId": "sql", "success": True,
                                             "result": {"content": "private rows"}}),
        sdk_event("assistant.turn_start", {"turnId": "second"}),
        sdk_event("assistant.message_delta", {"messageId": "answer", "deltaContent": "Safe answer"}),
        sdk_event("assistant.turn_end", {"turnId": "second"}),
    ]
    async def scripted(self, prompt, **kwargs):
        for event in events:
            self.callback(event)
            self.callback(event)  # Delivery duplicates must not duplicate answer text or statuses.
        return Obj(data=Obj(content="Safe final answer"))
    monkeypatch.setattr(FakeSession, "send_and_wait", scripted)
    FakeClient.mode = "ok"
    progress, trace = [], []
    answer = await run_copilot(
        "Question", client_factory=FakeClient, registry_factory=FakeRegistry, trace=trace,
        on_progress=lambda phase, data: progress.append((phase, data)),
    )
    assert answer == "Safe final answer"
    assert [phase for phase, _ in progress] == [
        "start", "assistant_turn", "assistant_activity", "tool_start", "tool_done",
        "assistant_retry", "tool_start", "tool_done", "assistant_turn", "delta", "complete",
    ]
    assert [data["iteration"] for phase, data in progress if phase == "assistant_turn"] == [1, 2]
    done = [data for phase, data in progress if phase == "tool_done"]
    assert done[0]["success"] is False and done[0]["error"] == "Tool execution failed."
    assert done[1]["success"] is True and done[1]["error"] is None
    assert next(data for phase, data in progress if phase == "start")["method"] == "M4"
    assert not any(value in str(progress) + str(trace) for value in [
        "private reasoning", "private provider", "private rows", "test-only-key", "arguments",
    ])
    start = next(data for phase, data in progress if phase == "start")
    request_id = start["requestId"]
    assert all(f"request={request_id}" in record.message for record in caplog.records if record.name == "copilot_runner")
    assert "ontology_requested=True ontology_configured=True ontology_tool_available=True" in caplog.text
    assert "phase=tool_start tool=query_ontology" in caplog.text
    assert "phase=tool_done tool=query_ontology success=False" in caplog.text
    assert "phase=tool_done tool=query_ontology success=True" not in caplog.text
    assert "phase=tool_done tool=query_lakehouse success=True" in caplog.text
    assert not any(value in caplog.text for value in [
        "private", "test-only-key", "Question", "Safe answer", "example.openai.azure.com",
    ])


@pytest.mark.asyncio
@pytest.mark.parametrize("requested,configured,available", [
    (True, True, True), (False, True, False), (True, False, False),
])
async def test_logs_distinguish_requested_configured_offered_without_inferring_calls(
    monkeypatch, caplog, requested, configured, available,
):
    from uuid import UUID

    caplog.set_level("INFO", logger="copilot_runner")
    monkeypatch.setenv("IQ_DEMO_MODE", "0")
    monkeypatch.setattr("fabric_readiness.fabric_readiness", lambda: {"sql": True, "ontology": configured})
    FakeClient.mode = "ok"
    progress = []
    await run_copilot("private prompt or SQL", ontology_enabled=requested,
                      client_factory=FakeClient, registry_factory=FakeRegistry,
                      on_progress=lambda phase, data: progress.append((phase, data)))
    start = next(data for phase, data in progress if phase == "start")
    assert str(UUID(start["requestId"])) == start["requestId"]
    assert start["ontologyEnabled"] is requested and start["ontologyConfigured"] is configured
    assert start["ontologyToolAvailable"] is available
    assert f"ontology_requested={requested} ontology_configured={configured} ontology_tool_available={available}" in caplog.text
    assert "phase=tool_start" not in caplog.text and "phase=tool_done" not in caplog.text
    assert "private" not in caplog.text and "phase=complete" in caplog.text


@pytest.mark.asyncio
async def test_log_activity_is_bounded_but_retains_terminal_status(monkeypatch, caplog):
    caplog.set_level("INFO", logger="copilot_runner")
    async def scripted(self, prompt, **kwargs):
        for turn in range(140):
            self.callback(sdk_event("assistant.turn_start", {"turnId": str(turn)}))
        return Obj(data=Obj(content="private answer"))
    monkeypatch.setattr(FakeSession, "send_and_wait", scripted)
    FakeClient.mode = "ok"
    await run_copilot("private prompt", client_factory=FakeClient, registry_factory=FakeRegistry)
    records = [record for record in caplog.records if record.name == "copilot_runner"]
    assert len(records) == 131
    assert sum("phase=assistant_turn" in record.message for record in records) == 128
    assert sum("phase=activity_log_limit" in record.message for record in records) == 1
    assert "phase=complete" in records[-1].message
    assert "private" not in caplog.text


@pytest.mark.asyncio
async def test_teardown_failure_is_not_logged_as_success_and_remaining_resources_close(monkeypatch, caplog):
    caplog.set_level("INFO", logger="copilot_runner")
    async def fail_disconnect(self):
        self.closed = True
        raise OSError("private transport exception with test-only-key")
    monkeypatch.setattr(FakeSession, "disconnect", fail_disconnect)
    FakeClient.mode = "ok"
    progress = []
    with pytest.raises(RuntimeError, match="cleanup failed") as error:
        await run_copilot("Question", client_factory=FakeClient, registry_factory=FakeRegistry,
                          on_progress=lambda phase, data: progress.append((phase, data)))
    assert FakeClient.instances[-1].stopped and FakeRegistry.instances[-1].closed
    assert progress[-1] == ("failed", {})
    assert "phase=complete" not in caplog.text
    assert "private" not in caplog.text and "test-only-key" not in str(error.value)


@pytest.mark.asyncio
async def test_sdk_tool_aliases_correlate_parallel_calls_and_ignore_unregistered_tools(monkeypatch):
    async def scripted(self, prompt, **kwargs):
        for name, call_id in [("fabric-iq__portfolio", "first"), ("fabric-iq__portfolio", "second"),
                              ("shell", "denied")]:
            self.callback(sdk_event("tool.execution_start", {"toolCallId": call_id, "toolName": name}))
        for call_id, success in [("second", False), ("first", True), ("denied", False)]:
            self.callback(sdk_event("tool.execution_complete", {"toolCallId": call_id, "success": success}))
        return Obj(data=Obj(content="Answer"))
    monkeypatch.setattr(FakeSession, "send_and_wait", scripted)
    FakeClient.mode = "ok"
    progress = []
    await run_copilot("Question", client_factory=FakeClient, registry_factory=FakeRegistry,
                      on_progress=lambda phase, data: progress.append((phase, data)))
    tools = [data for phase, data in progress if phase.startswith("tool_")]
    assert len(tools) == 4 and all(data["name"] == "portfolio" and data["server"] == "fabric-iq" for data in tools)
    assert [data["callId"] for data in tools] == ["first", "second", "second", "first"]


@pytest.mark.asyncio
async def test_per_turn_selected_model_agent_skills_without_global_mutation(monkeypatch):
    import os
    seen = []
    async def catalog(**kwargs):
        seen.append(kwargs)
        return [{"id": "first"}, {"id": "second"}]
    monkeypatch.setattr("model_catalog.enabled_models", catalog)
    FakeClient.mode = "ok"
    profile = {"id": "generic", "name": "Generic", "systemPrompt": "Be concise.",
               "skills": [{"id": "on", "name": "On", "prompt": "Enabled instruction.", "enabled": True},
                          {"id": "off", "name": "Off", "prompt": "Disabled instruction.", "enabled": False}],
               "servers": []}
    for selected in ("first", "second"):
        await run_copilot("Question", selected_model=selected, agent=profile,
                          client_factory=FakeClient, registry_factory=FakeRegistry)
        client = FakeClient.instances[-1]
        assert client.options["model"] == selected
        prompt = client.options["system_message"]["content"]
        assert "Be concise." in prompt and "Enabled instruction." in prompt and "Disabled instruction." not in prompt
    assert os.environ["COPILOT_MODEL"] == "example-deployment"
    assert seen == [{"refresh": True}, {"refresh": True}]
    count = len(FakeClient.instances)
    with pytest.raises(ValueError, match="no longer enabled"):
        await run_copilot("Question", selected_model="removed",
                          client_factory=FakeClient, registry_factory=FakeRegistry)
    assert len(FakeClient.instances) == count


@pytest.mark.asyncio
async def test_unconfigured_live_fabric_not_advertised_and_no_tools_explicitly_ungrounded(monkeypatch):
    monkeypatch.setenv("IQ_DEMO_MODE", "0")
    for key in ("FABRIC_SQL_ENDPOINT", "FABRIC_ONTOLOGY_MCP_URL", "IQ_ENABLE_CROWD_TOOLS"):
        monkeypatch.delenv(key, raising=False)
    class EmptyRegistry(FakeRegistry):
        async def discover(self, **kwargs):
            return []
    FakeClient.mode = "ok"
    progress = []
    await run_copilot("Question", client_factory=FakeClient, registry_factory=EmptyRegistry,
                      on_progress=lambda phase, payload: progress.append((phase, payload)))
    client = FakeClient.instances[-1]
    assert client.options["available_tools"] == []
    assert "Ungrounded model response" in client.options["system_message"]["content"]
    assert next(payload for phase, payload in progress if phase == "start")["grounding"] == "ungrounded"


@pytest.mark.parametrize("kind", ["openai", "azure", "anthropic"])
def test_provider_types(monkeypatch, kind):
    monkeypatch.setenv("COPILOT_PROVIDER", kind)
    monkeypatch.setenv("COPILOT_WIRE_API", "responses")
    monkeypatch.setenv("COPILOT_AZURE_API_VERSION", "2024-10-21")
    model, provider = provider_config()
    assert provider["type"] == kind and model
    if kind == "azure":
        assert provider["azure"]["api_version"] == "2024-10-21"


def test_missing_config_and_local_no_key(monkeypatch):
    monkeypatch.delenv("COPILOT_API_KEY")
    with pytest.raises(ValueError, match="server-side"):
        provider_config()
    monkeypatch.setenv("COPILOT_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("COPILOT_LOCAL_NO_KEY", "1")
    assert "api_key" not in provider_config()[1]
    monkeypatch.setenv("COPILOT_BASE_URL", "http://untrusted.example/v1")
    with pytest.raises(ValueError, match="HTTPS"):
        provider_config()


def test_remote_sdk_aliases_are_bounded_and_preserve_distinct_routing_names():
    import re
    names = [sdk_tool_name("user-" + "x" * 34, value) for value in ("a" * 80, "a" * 79 + "b", "a.b")]
    assert len(set(names)) == 3
    assert all(len(name) <= 64 and re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in names)
    assert sdk_tool_name("fabric-iq", "portfolio") == "fabric-iq__portfolio"


def test_fabric_readiness_preserves_workspace_ontology_and_sql_default_database(monkeypatch):
    from fabric_readiness import fabric_readiness
    monkeypatch.delenv("FABRIC_ONTOLOGY_MCP_URL", raising=False)
    monkeypatch.delenv("FABRIC_SQL_DATABASE", raising=False)
    monkeypatch.setenv("FABRIC_WORKSPACE_ID", "workspace")
    monkeypatch.setenv("FABRIC_ONTOLOGY_ID", "ontology")
    monkeypatch.setenv("FABRIC_SQL_ENDPOINT", "sql.example")
    assert fabric_readiness() == {"sql": True, "ontology": True}


@pytest.mark.asyncio
async def test_real_fabric_sample_provenance_is_separate_from_demo_registry(monkeypatch):
    monkeypatch.setenv("IQ_DEMO_MODE", "0")
    monkeypatch.setenv("FABRIC_DATA_MODE", "test-sample")
    FakeClient.mode = "ok"
    progress = []
    await run_copilot("Question", client_factory=FakeClient, registry_factory=FakeRegistry,
                      on_progress=lambda phase, data: progress.append((phase, data)))
    prompt = FakeClient.instances[-1].options["system_message"]["content"]
    assert "Synthetic Fabric test sample" in prompt and "label the answer DEMO" not in prompt
    assert next(data for phase, data in progress if phase == "start")["fabricDataMode"] == "test-sample"


@pytest.mark.asyncio
@pytest.mark.parametrize("demo", ["0", "1"])
async def test_no_riba_tools_context_or_question_rewriting(monkeypatch, demo):
    monkeypatch.setenv("IQ_DEMO_MODE", demo)
    FakeClient.mode = "ok"
    question = "/stage 2 Explain this project brief."
    progress = []
    await run_copilot(question, client_factory=FakeClient, registry_factory=FakeRegistry,
                      on_progress=lambda phase, data: progress.append((phase, data)))
    client = FakeClient.instances[-1]
    assert client.session.prompt == question
    assert all("riba" not in name.lower() for name in client.options["available_tools"])
    instructions = client.options["system_message"]["content"].lower()
    assert "riba" not in instructions and "stage context" not in instructions
    assert all("riba" not in phase.lower() for phase, _ in progress)
    if demo == "0":
        from fabric_tools import _build_instructions
        assert "riba" not in _build_instructions(True).lower()
        assert "riba" not in _build_instructions(False).lower()


@pytest.mark.asyncio
async def test_managed_identity_refreshes_each_turn_and_closes_credential(monkeypatch):
    scopes, clients, closed = [], [], []
    class Credential:
        def __init__(self, *, client_id):
            clients.append(client_id)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            closed.append(True)
        async def get_token(self, scope):
            scopes.append(scope)
            return Obj(token=f"identity-token-{len(scopes)}")
    monkeypatch.setattr("copilot_runner.ManagedIdentityCredential", Credential)
    monkeypatch.delenv("COPILOT_API_KEY")
    monkeypatch.setenv("COPILOT_USE_AZURE_IDENTITY", "1")
    monkeypatch.setenv("AZURE_CLIENT_ID", "mcp-identity")
    FakeClient.mode = "ok"
    for expected in ("identity-token-1", "identity-token-2"):
        await run_copilot("Question", client_factory=FakeClient, registry_factory=FakeRegistry)
        assert FakeClient.instances[-1].options["provider"]["bearer_token"] == expected
    assert scopes == ["https://cognitiveservices.azure.com/.default"] * 2
    assert clients == ["mcp-identity"] * 2 and len(closed) == 2


@pytest.mark.asyncio
async def test_async_identity_transport_initializes_without_cloud_calls():
    async with ManagedIdentityCredential(client_id="test-only-identity"):
        pass


@pytest.mark.parametrize("endpoint,kind,key", [
    ("https://untrusted.example/v1", "openai", ""),
    ("https://example.openai.azure.com/", "anthropic", ""),
    ("https://example.openai.azure.com/", "azure", "static-key"),
])
def test_managed_identity_rejects_untrusted_endpoint_or_conflicting_credentials(monkeypatch, endpoint, kind, key):
    monkeypatch.setenv("COPILOT_USE_AZURE_IDENTITY", "1")
    monkeypatch.setenv("COPILOT_BASE_URL", endpoint)
    monkeypatch.setenv("COPILOT_PROVIDER", kind)
    monkeypatch.setenv("COPILOT_API_KEY", key)
    with pytest.raises(ValueError):
        provider_config()
