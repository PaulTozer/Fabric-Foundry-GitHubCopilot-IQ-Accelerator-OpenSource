import pytest
from enterprise_mcp.http_auth import ServiceTokenMiddleware
from enterprise_mcp.tools import agents
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient


def test_regular_and_stream_share_copilot(monkeypatch):
    seen = []
    async def run(question, **kwargs):
        seen.append((question, kwargs))
        if kwargs.get("on_progress"):
            kwargs["on_progress"]("delta", {"content": "Grounded"})
        return "Grounded answer"
    monkeypatch.setattr("copilot_runner.run_copilot", run)
    mcp = FastMCP("test", stateless_http=True)
    agents.register(mcp)
    body = {"question": "/stage 2 Question", "method": "M4",
            "riba_stage_override": "obsolete", "riba_enabled_stages": {"obsolete": True}}
    with TestClient(mcp.streamable_http_app()) as client:
        result = client.post("/ask", json=body).json()
        assert result["answer"] == "Grounded answer"
        stream = client.post("/ask/stream", json=body).text
        assert "event: delta" in stream and "event: result" in stream
        assert "Grounded answer" in stream
    assert len(seen) == 2
    assert seen[0][0] == seen[1][0] == body["question"]
    assert all(not key.startswith("riba") for _, options in seen for key in options)
    assert "riba_stage" not in stream


def test_real_sdk_event_mapping_survives_http_and_sse_without_payload_leaks(monkeypatch):
    import json
    from types import SimpleNamespace

    import copilot_runner
    from test_copilot import FakeClient, FakeRegistry, FakeSession, sdk_event

    monkeypatch.setenv("IQ_DEMO_MODE", "1")
    monkeypatch.setenv("COPILOT_MODEL", "offline-model")
    monkeypatch.setenv("COPILOT_BASE_URL", "https://example.openai.azure.com/openai/v1/")
    monkeypatch.setenv("COPILOT_API_KEY", "test-only-key")
    monkeypatch.delenv("COPILOT_USE_AZURE_IDENTITY", raising=False)
    monkeypatch.delenv("COPILOT_AZURE_RESOURCE_ID", raising=False)
    async def scripted(self, prompt, **kwargs):
        for kind, data in [
            ("assistant.turn_start", {"turnId": "one"}),
            ("assistant.intent", {"intent": "private-intent"}),
            ("tool.execution_start", {"toolName": "fabric-iq__portfolio", "toolCallId": "call",
                                      "arguments": {"secret": "private-args"}}),
            ("tool.execution_complete", {"toolCallId": "call", "success": False,
                                         "error": {"message": "private-error"}}),
            ("assistant.turn_retry", {"turnId": "one", "reason": "private-reason"}),
            ("assistant.message_delta", {"messageId": "answer", "deltaContent": "Safe answer"}),
        ]:
            self.callback(sdk_event(kind, data))
        return SimpleNamespace(data=SimpleNamespace(content="Safe answer"))
    monkeypatch.setattr(FakeSession, "send_and_wait", scripted)
    original = copilot_runner.run_copilot
    async def offline(question, **kwargs):
        return await original(question, **kwargs, client_factory=FakeClient, registry_factory=FakeRegistry)
    monkeypatch.setattr(copilot_runner, "run_copilot", offline)
    mcp = FastMCP("test", stateless_http=True)
    agents.register(mcp)
    with TestClient(mcp.streamable_http_app()) as client:
        regular = client.post("/ask", json={"question": "Question", "method": "M4"}).json()
        stream = client.post("/ask/stream", json={"question": "Question", "method": "M4"}).text
    blocks = [block for block in stream.split("\n\n") if block.startswith("event: ")]
    assert [block.splitlines()[0] for block in blocks] == [
        "event: start", "event: assistant_turn", "event: assistant_activity", "event: tool_start",
        "event: tool_done", "event: assistant_retry", "event: delta", "event: complete", "event: result",
    ]
    result = json.loads(blocks[-1].splitlines()[1][6:])
    for key in ("answer", "method", "model", "grounding", "toolCallCount"):
        assert result[key] == regular[key]
    assert regular["method"] == "M4" and regular["model"] == "offline-model"
    from uuid import UUID
    assert str(UUID(regular["requestId"])) == regular["requestId"]
    start = json.loads(blocks[0].splitlines()[1][6:])
    assert result["requestId"] == start["requestId"] != regular["requestId"]
    assert all(value not in stream for value in ["private-", "test-only-key"])


def test_stream_runtime_error_is_explicit(monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("secret-not-for-browser")
    monkeypatch.setattr("copilot_runner.run_copilot", fail)
    mcp = FastMCP("test", stateless_http=True)
    agents.register(mcp)
    with TestClient(mcp.streamable_http_app()) as client:
        response = client.post("/ask", json={"question": "Question", "method": "M4"})
        assert response.status_code == 502 and "secret-not" not in response.text
        stream = client.post("/ask/stream", json={"question": "Question", "method": "M4"}).text
        assert "event: error" in stream and "event: result" not in stream
        assert "secret-not" not in stream


def test_invalid_requests():
    mcp = FastMCP("test", stateless_http=True)
    agents.register(mcp)
    with TestClient(mcp.streamable_http_app()) as client:
        assert client.post("/ask", json={"question": "", "method": "M4"}).status_code == 400
        assert client.post("/ask/stream", json={"method": "M1"}).status_code == 400


def test_service_auth_protects_routes_and_preserves_fabric_identity(monkeypatch):
    from fabric_ontology import USER_FABRIC_TOKEN
    seen = []
    async def run(*args, **kwargs):
        seen.append(USER_FABRIC_TOKEN.get())
        return "Answer"
    monkeypatch.setattr("copilot_runner.run_copilot", run)
    mcp = FastMCP("test", stateless_http=True)
    agents.register(mcp)
    token = "test-service-token-not-live-value-123456"
    with TestClient(ServiceTokenMiddleware(mcp.streamable_http_app(), token)) as client:
        for path in ("/ask", "/ask/stream", "/mcp"):
            assert client.post(path, json={"question": "Q"}).status_code == 401
        assert client.get("/registry").status_code == 401
        assert client.get("/registry", headers={"x-iq-service-token": "wrong"}).status_code == 401
        headers = {"x-iq-service-token": token, "authorization": "Bearer delegated-fabric-token"}
        assert client.post("/ask", json={"question": "Q"}, headers=headers).status_code == 200
        assert "event: result" in client.post("/ask/stream", json={"question": "Q"}, headers=headers).text
        assert client.post("/ask", json={"question": "Q"}, headers={"x-iq-service-token": token}).status_code == 200
    assert seen == ["delegated-fabric-token", "delegated-fabric-token", None]


def test_health_probe_exposes_no_config(monkeypatch):
    from enterprise_mcp.server import build_server
    monkeypatch.setenv("COPILOT_API_KEY", "not-a-real-key")
    mcp = build_server()
    mcp.settings.stateless_http = True
    with TestClient(ServiceTokenMiddleware(mcp.streamable_http_app(), "a" * 32)) as client:
        response = client.get("/healthz")
        assert response.status_code == 200 and response.json() == {"status": "ok"}
        assert client.post("/healthz").status_code == 401


def test_manager_auth_catalog_discovery_and_regular_stream_options(monkeypatch):
    seen = []
    async def catalog(**kwargs):
        return [{"id": "enabled", "model": "actual", "version": "1"}]
    async def run(question, **kwargs):
        seen.append(kwargs)
        kwargs["on_progress"]("start", {"model": kwargs["selected_model"], "agentId": kwargs["agent"]["id"], "grounding": "ungrounded"})
        return "Ungrounded model response"
    monkeypatch.setattr("model_catalog.enabled_models", catalog)
    monkeypatch.setattr("copilot_runner.run_copilot", run)
    mcp = FastMCP("test", stateless_http=True)
    agents.register(mcp)
    secret = "s" * 32
    profile = {"id": "generic", "name": "Generic", "systemPrompt": "Be concise.", "skills": [], "servers": []}
    headers = {"x-iq-service-token": secret, "x-iq-owner": "a" * 64}
    with TestClient(ServiceTokenMiddleware(mcp.streamable_http_app(), secret)) as client:
        for path in ("/configuration", "/models"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"x-iq-service-token": secret}).status_code == 401
        assert client.get("/models", headers=headers).json()["models"][0]["id"] == "enabled"
        assert client.post("/discover", headers=headers, json={"agent": profile}).json() == {"servers": []}
        body = {"question": "Question", "agent": profile, "selected_model": "enabled"}
        regular = client.post("/ask", headers=headers, json=body).json()
        stream = client.post("/ask/stream", headers=headers, json=body).text
        assert regular["model"] == "enabled" and regular["agentId"] == "generic"
        assert '"model": "enabled"' in stream and "event: result" in stream
        assert client.post("/ask", headers=headers, json={**body, "method": "M6"}).status_code == 400
    assert len(seen) == 2 and all(options["agent"] == profile and options["selected_model"] == "enabled" for options in seen)


def test_packaged_domain_and_selected_builtin_reach_real_sdk_config_on_regular_and_sse(monkeypatch):
    import json
    from pathlib import Path

    import copilot_runner
    from test_copilot import FakeClient

    root = Path(__file__).resolve().parents[1]
    domain = (root / "agents" / "CopilotSDK_Instructions.md").read_text(encoding="utf-8")
    # Read the actual shipped TS prompt, not a stand-in instruction.
    catalog = (root / "webui" / "lib" / "skills-seed.ts").read_text(encoding="utf-8")
    selected_prompt = catalog.split("prompt:\n`", 1)[1].split("`,", 1)[0]
    monkeypatch.setattr(agents.config, "load_dotenv", lambda: None)
    monkeypatch.setenv("IQ_DEMO_MODE", "0")
    monkeypatch.setenv("COPILOT_MODEL", "offline-model")
    monkeypatch.setenv("COPILOT_BASE_URL", "https://example.openai.azure.com/openai/v1/")
    monkeypatch.setenv("COPILOT_API_KEY", "test-only-key")
    monkeypatch.setenv("COPILOT_USE_AZURE_IDENTITY", "0")
    monkeypatch.setenv("COPILOT_AZURE_RESOURCE_ID", "")
    monkeypatch.setenv("COPILOT_PROVIDER", "openai")
    monkeypatch.setenv("COPILOT_WIRE_API", "completions")
    monkeypatch.setenv("COPILOT_BEARER_TOKEN", "")
    monkeypatch.setattr("fabric_readiness.fabric_readiness", lambda: {"sql": False, "ontology": False})
    class EmptyRegistry:
        def __init__(self, **kwargs):
            assert kwargs["disabled"] == ["work-iq"]
            assert kwargs["user_servers"] == []
        async def discover(self, **kwargs):
            assert kwargs == {"work_iq_enabled": False, "ontology_enabled": False}
            return []
        async def close(self):
            pass
    original = copilot_runner.run_copilot
    async def offline(question, **kwargs):
        return await original(question, **kwargs, client_factory=FakeClient, registry_factory=EmptyRegistry)
    monkeypatch.setattr(copilot_runner, "run_copilot", offline)
    FakeClient.mode = "ok"
    mcp = FastMCP("test", stateless_http=True)
    agents.register(mcp)
    with TestClient(mcp.streamable_http_app()) as client:
        config = client.get("/configuration", headers={"x-iq-owner": "a" * 64}).json()
        assert config["defaultSystemPrompt"] == domain
        for prompt, enabled in [(domain, True), ("My explicit portfolio persona.", False), ("", False)]:
            profile = {"id": "analyst", "name": "Enterprise Consultancy Analyst", "systemPrompt": prompt,
                       "servers": [], "skills": [
                           {"id": "seed-bid-transport-pursuit", "name": "Bid Qualification",
                            "description": "", "prompt": selected_prompt, "enabled": enabled},
                           {"id": "private-off", "name": "Off", "prompt": "Do not append this.", "enabled": False},
                       ]}
            body = {"question": "Qualify the pursuit.", "agent": profile, "method": "M4",
                    "ontology_enabled": False, "workiq_enabled": False, "disabled_mcp_servers": ["work-iq"]}
            for route in ("/ask", "/ask/stream"):
                response = client.post(route, json=body)
                assert response.status_code == 200
                if route.endswith("stream"):
                    assert "event: result" in response.text
                    result = next(block for block in response.text.split("\n\n") if block.startswith("event: result"))
                    assert json.loads(result.splitlines()[1][6:])["grounding"] == "ungrounded"
                else:
                    assert response.json()["grounding"] == "ungrounded"
                options = FakeClient.instances[-1].options
                instructions = options["system_message"]["content"]
                assert domain in instructions
                assert instructions.count(domain) == 1
                assert ("MEDDPICC-style" in instructions) is enabled
                assert "Do not append this." not in instructions
                assert ("My explicit portfolio persona." in instructions) is (prompt == "My explicit portfolio persona.")
                assert options["enable_skills"] is False and options["enable_config_discovery"] is False
                assert options["available_tools"] == []
                assert "Ungrounded model response" in instructions


@pytest.mark.parametrize("asset", [None, "", "x" * 20001], ids=["missing", "empty", "oversized"])
def test_missing_domain_asset_is_explicit_not_an_empty_default(monkeypatch, asset):
    from pathlib import Path

    import copilot_runner

    def missing(*args, **kwargs):
        if asset is None:
            raise FileNotFoundError("private-server-path")
        return asset
    monkeypatch.setattr(Path, "read_text", missing)
    monkeypatch.setattr(agents.config, "load_dotenv", lambda: None)
    monkeypatch.setattr("copilot_runner.provider_config", lambda: ("offline", {}))
    monkeypatch.setenv("COPILOT_AZURE_RESOURCE_ID", "")
    def unexpected_runtime(**kwargs):
        raise AssertionError("Invalid instructions must fail before starting the runtime or registry.")
    original = copilot_runner.run_copilot
    async def offline(question, **kwargs):
        return await original(question, **kwargs, client_factory=unexpected_runtime, registry_factory=unexpected_runtime)
    monkeypatch.setattr(copilot_runner, "run_copilot", offline)
    mcp = FastMCP("test", stateless_http=True)
    agents.register(mcp)
    with TestClient(mcp.streamable_http_app()) as client:
        response = client.get("/configuration", headers={"x-iq-owner": "a" * 64})
        regular = client.post("/ask", json={"question": "Offline question"})
        stream = client.post("/ask/stream", json={"question": "Offline question"}).text
    assert response.status_code == 503
    assert "packaged" in response.json()["error"]
    assert "private-server-path" not in response.text
    assert regular.status_code == 400 and "packaged" in regular.json()["error"]
    assert "event: error" in stream and "packaged" in stream and "event: result" not in stream
    assert "private-server-path" not in regular.text + stream


def test_discovery_owner_body_bounds_and_per_owner_rate_limit():
    mcp = FastMCP("test", stateless_http=True)
    agents.register(mcp)
    secret = "s" * 32
    headers = {"x-iq-service-token": secret, "x-iq-owner": "a" * 64}
    body = {"agent": {"id": "generic", "name": "Generic", "skills": [], "servers": []}}
    with TestClient(ServiceTokenMiddleware(mcp.streamable_http_app(), secret)) as client:
        assert client.post("/discover", headers={"x-iq-service-token": secret}, json=body).status_code == 401
        assert client.post("/discover", headers=headers, content=" " * 100_001).status_code == 413
        for _ in range(5):
            assert client.post("/discover", headers=headers, json=body).status_code == 200
        assert client.post("/discover", headers=headers, json=body).status_code == 429
        assert client.post("/discover", headers={**headers, "x-iq-owner": "b" * 64}, json=body).status_code == 200


def test_sample_provenance_survives_regular_and_stream_terminal_result(monkeypatch):
    import json

    async def run(question, **kwargs):
        kwargs["on_progress"]("start", {"model": "test-model", "agentId": None,
                                      "grounding": "tools", "fabricDataMode": "test-sample"})
        return "Synthetic Fabric test sample"
    monkeypatch.setenv("IQ_DEMO_MODE", "0")
    monkeypatch.setenv("FABRIC_DATA_MODE", "test-sample")
    monkeypatch.setattr("copilot_runner.run_copilot", run)
    mcp = FastMCP("test", stateless_http=True)
    agents.register(mcp)
    with TestClient(mcp.streamable_http_app()) as client:
        response = client.post("/ask", json={"question": "Question"}).json()
        assert response["fabricDataMode"] == "test-sample"
        stream = client.post("/ask/stream", json={"question": "Question"}).text
        result = next(block for block in stream.split("\n\n") if block.startswith("event: result"))
        data = json.loads(next(line[6:] for line in result.splitlines() if line.startswith("data: ")))
        assert data["fabricDataMode"] == "test-sample"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["M6", "M7"])
async def test_retained_foundry_responses_preserve_configured_fabric_sample_provenance(monkeypatch, method):
    from types import SimpleNamespace

    import m6_runner
    import run_tests
    from enterprise_mcp import config
    monkeypatch.setenv("FABRIC_DATA_MODE", "test-sample")
    monkeypatch.setattr(config, "require_env", lambda *keys: {key: "inactive-offline-setting" for key in keys})
    client = SimpleNamespace(responses=SimpleNamespace(), close=lambda: None)
    client.with_options = lambda **options: client
    monkeypatch.setattr(run_tests, "build_openai", lambda: client)
    monkeypatch.setattr(run_tests, "_extract_text", lambda response: "Synthetic test response")
    monkeypatch.setattr(run_tests, "_run_function_loop", lambda *args, **kwargs: (object(), ""))
    monkeypatch.setattr(m6_runner, "run_m6", lambda *args, **kwargs: (object(), ""))
    result = await agents.ask_async({"question": "Question", "method": method})
    assert result["fabricDataMode"] == "test-sample"
