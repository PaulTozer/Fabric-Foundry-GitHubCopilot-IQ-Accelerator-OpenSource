import asyncio
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fabric_connection import (
    API,
    CURRENT,
    Caller,
    FabricSelectionError,
    Resolver,
    connection_scope,
    selection,
)
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

from enterprise_schema import TABLE_NAMES

TENANT = "11111111-1111-4111-8111-111111111111"
OID = "22222222-2222-4222-8222-222222222222"
WORKSPACE = "33333333-3333-4333-8333-333333333333"
LAKEHOUSE = "44444444-4444-4444-8444-444444444444"
ONTOLOGY = "55555555-5555-4555-8555-555555555555"
OTHER = "66666666-6666-4666-8666-666666666666"
CHOSEN = {"workspaceId": WORKSPACE, "lakehouseId": LAKEHOUSE, "ontologyId": ONTOLOGY}


@pytest.fixture
def signed():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = "test-key"

    def token(audience="https://api.fabric.microsoft.com", **changes):
        now = int(time.time())
        claims = {"iss": f"https://sts.windows.net/{TENANT}/", "aud": audience, "tid": TENANT,
                  "oid": OID, "scp": "Workspace.Read.All Item.ReadWrite.All", "iat": now,
                  "nbf": now - 1, "exp": now + 600}
        claims.update(changes)
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})
    return token, {"keys": [jwk]}


def metadata(url):
    path = url.path
    workspace = OTHER if OTHER in path else WORKSPACE
    if path == f"/v1/workspaces/{workspace}":
        return {"id": workspace}
    if path.endswith("/items"):
        return {"value": [{"id": LAKEHOUSE, "type": "Lakehouse", "workspaceId": workspace,
                           "displayName": "EnterpriseKG_Lakehouse"},
                          {"id": ONTOLOGY, "type": "Ontology", "workspaceId": workspace,
                           "displayName": "EnterpriseKG_Ontology"}]}
    if path.endswith(f"/lakehouses/{LAKEHOUSE}"):
        return {"id": LAKEHOUSE, "type": "Lakehouse", "workspaceId": workspace,
                "displayName": "EnterpriseKG_Lakehouse",
                "properties": {"sqlEndpointProperties": {"connectionString": "unit.datawarehouse.fabric.microsoft.com",
                                                         "provisioningStatus": "Success"}}}
    if path.endswith("/tables"):
        return {"data": [{"name": table.lower()} for table in TABLE_NAMES]}
    if path.endswith(f"/ontologies/{ONTOLOGY}"):
        return {"id": ONTOLOGY, "type": "Ontology", "workspaceId": workspace,
                "properties": {"generation": 1}}
    raise AssertionError(f"Unexpected metadata URL: {url}")


@pytest.fixture
def setup_resolver(signed, monkeypatch):
    token, keys = signed
    fabric, sql = token(), token("https://database.windows.net/")
    caller = Caller(TENANT, OID, fabric, sql)
    headers = {"x-iq-owner": "a" * 64, "x-iq-tenant-id": TENANT, "x-iq-entra-oid": OID,
               "authorization": "Bearer " + fabric, "x-iq-sql-token": sql}
    original = httpx.AsyncClient
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "login.microsoftonline.com":
            assert "authorization" not in request.headers
            return httpx.Response(200, json=keys)
        assert request.url.host == "api.fabric.microsoft.com"
        assert request.headers["authorization"].startswith("Bearer ")
        return httpx.Response(200, json=metadata(request.url))

    monkeypatch.setattr("fabric_connection.httpx.AsyncClient", lambda **kwargs:
                        original(transport=httpx.MockTransport(handler), **kwargs))
    return caller, headers, requests, keys


@pytest.mark.asyncio
async def test_resolve_effective_tools_and_delegated_sql_no_identity_fallback(setup_resolver, monkeypatch):
    caller, headers, requests, _ = setup_resolver
    monkeypatch.setenv("FABRIC_WORKSPACE_ID", "operator-unchanged")
    monkeypatch.setenv("FABRIC_DATA_MODE", "test-sample")
    from fabric_readiness import fabric_data_mode, fabric_readiness
    from fabric_tools import _tools_for
    from m3_sql_handler import _access_token_struct, _connection_string
    monkeypatch.setattr("m3_sql_handler.DefaultAzureCredential", lambda: pytest.fail("Identity fallback forbidden"))
    async with connection_scope(CHOSEN, headers):
        assert CURRENT.get().workspace == WORKSPACE
        assert fabric_readiness() == {"sql": True, "ontology": True}
        assert fabric_data_mode() == "existing"
        assert "unit.datawarehouse.fabric.microsoft.com" in _connection_string()
        assert caller.sql_token.encode("utf-16-le") in _access_token_struct()
        assert "query_ontology" in {tool["name"] for tool in _tools_for(True)}
        assert "query_ontology" not in {tool["name"] for tool in _tools_for(False)}
        assert (await asyncio.to_thread(CURRENT.get)).workspace == WORKSPACE
    assert CURRENT.get() is None
    assert fabric_data_mode() == "test-sample"
    assert all(request.method == "GET" for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"aud": "https://untrusted.example"}, {"tid": OTHER}, {"oid": OTHER},
    {"iss": "https://untrusted.example"}, {"exp": 1}, {"scp": ""}, {"scp": None},
])
async def test_signed_wrong_identity_audience_expiry_denied(signed, changes):
    token, keys = signed
    caller = Caller(TENANT, OID, token(**changes))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=keys))) as client:
        with pytest.raises(FabricSelectionError, match="identity, tenant, audience or expiry"):
            await Resolver(client, caller).verify_tokens()


@pytest.mark.asyncio
async def test_forged_signature_and_application_only_token_denied(signed):
    token, keys = signed
    forged_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = jwt.decode(token(), options={"verify_signature": False})
    forged = jwt.encode(claims, forged_key, algorithm="RS256", headers={"kid": "test-key"})
    del claims["scp"]
    app = jwt.encode(claims, forged_key, algorithm="RS256", headers={"kid": "test-key"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=keys))) as client:
        for credential in (forged, app):
            with pytest.raises(FabricSelectionError):
                await Resolver(client, Caller(TENANT, OID, credential)).verify_tokens()


@pytest.mark.asyncio
async def test_metadata_save_does_not_claim_sql_access_and_turn_requires_sql(setup_resolver):
    caller, headers, _, _ = setup_resolver
    from fabric_connection import resolver_for
    async with resolver_for(Caller(TENANT, OID, caller.fabric_token)) as resolver:
        inventory = await resolver.inventory(CHOSEN)
        result = await resolver.resolve(CHOSEN, inventory, require_sql=False)
        assert result.public()["metadataValidated"]
    without_sql = {key: value for key, value in headers.items() if key != "x-iq-sql-token"}
    with pytest.raises(FabricSelectionError, match="delegated SQL"):
        async with connection_scope(CHOSEN, without_sql):
            pytest.fail("must not run")
    assert CURRENT.get() is None


@pytest.mark.asyncio
async def test_ontology_only_filters_sql_and_restores_on_error(setup_resolver):
    _, headers, _, _ = setup_resolver
    from fabric_tools import _tools_for
    with pytest.raises(RuntimeError, match="cancel"):
        async with connection_scope({**CHOSEN, "lakehouseId": None}, headers):
            names = {tool["name"] for tool in _tools_for(True)}
            assert "query_ontology" in names and not names & {"query_lakehouse", "schema_card", "sample_values"}
            raise RuntimeError("cancel")
    assert CURRENT.get() is None


@pytest.mark.asyncio
async def test_concurrent_owners_workspaces_do_not_share_context(setup_resolver, signed):
    _, headers, _, _ = setup_resolver
    token, _ = signed
    other_headers = {**headers, "x-iq-owner": "b" * 64, "x-iq-entra-oid": OTHER,
                     "authorization": "Bearer " + token(oid=OTHER),
                     "x-iq-sql-token": token("https://database.windows.net/", oid=OTHER)}
    async def turn(workspace, identity, current_headers):
        async with connection_scope({**CHOSEN, "workspaceId": workspace}, current_headers):
            await asyncio.sleep(0)
            assert CURRENT.get().workspace == workspace
            assert CURRENT.get().caller.oid == identity
            return (await asyncio.to_thread(CURRENT.get)).workspace
    assert await asyncio.gather(turn(WORKSPACE, OID, headers), turn(OTHER, OTHER, other_headers)) == [WORKSPACE, OTHER]
    assert CURRENT.get() is None


@pytest.mark.asyncio
async def test_ambiguous_wrong_item_and_unsupported_ontology_are_explicit(setup_resolver):
    caller, _, _, _ = setup_resolver
    async with httpx.AsyncClient() as client:
        resolver = Resolver(client, caller)
        inventory = await resolver.inventory(CHOSEN)
        ambiguous = {**inventory, "lakehouses": inventory["lakehouses"] * 2}
        with pytest.raises(FabricSelectionError, match="Multiple"):
            await resolver.resolve({"workspaceId": WORKSPACE}, ambiguous, require_sql=False)
        with pytest.raises(FabricSelectionError, match="not a readable"):
            await resolver.resolve({**CHOSEN, "lakehouseId": OTHER}, inventory, require_sql=False)
        original = resolver.json
        async def unsupported(url, **kwargs):
            value = await original(url, **kwargs)
            if "/ontologies/" in url:
                value["properties"]["generation"] = 2
            return value
        resolver.json = unsupported
        with pytest.raises(FabricSelectionError, match="generation-1"):
            await resolver.resolve({**CHOSEN, "lakehouseId": None}, inventory, require_sql=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,status", [
    ({"value": [], "continuationUri": "https://untrusted.example/v1/workspaces/x/items"}, 200),
    ({"value": [], "continuationUri": API + f"/workspaces/{WORKSPACE}/items"}, 200),
    ({"value": "invalid"}, 200), ({}, 302), ({}, 401), ({}, 403), ({}, 404), ({}, 429),
])
async def test_metadata_errors_and_untrusted_pagination(payload, status, signed):
    token, _ = signed
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(status, json=payload))) as client:
        with pytest.raises(FabricSelectionError):
            await Resolver(client, Caller(TENANT, OID, token())).collection(f"/workspaces/{WORKSPACE}/items")


@pytest.mark.asyncio
async def test_page_byte_limits_and_missing_tables(signed):
    token, _ = signed
    caller = Caller(TENANT, OID, token())
    responses = [httpx.Response(200, json={"value": [], "continuationToken": "next"}),
                 httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))]
    for response in responses:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req, r=response: r)) as client:
            with pytest.raises(FabricSelectionError):
                await Resolver(client, caller).collection(f"/workspaces/{WORKSPACE}/items")


@pytest.mark.asyncio
async def test_direct_mcp_tools_share_selected_credentials_and_restore_defaults(setup_resolver, monkeypatch):
    from types import SimpleNamespace

    from enterprise_mcp.tools import lakehouse, ontology
    from m3_sql_handler import _access_token_struct, _connection_string

    caller, headers, _, _ = setup_resolver
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers=headers)))
    mcp = FastMCP("direct")
    lakehouse.register(mcp)
    ontology.register(mcp)
    seen = []

    def sql(query):
        seen.append(CURRENT.get().workspace)
        assert "unit.datawarehouse.fabric.microsoft.com" in _connection_string()
        assert caller.sql_token.encode("utf-16-le") in _access_token_struct()
        return {"rows": [], "row_count": 0}

    def search(question):
        assert CURRENT.get().ontology_url.endswith(f"/items/{ONTOLOGY}/ontologyEndpoint")
        return {"answer": "Fixture ontology"}

    monkeypatch.setattr("m3_sql_handler.execute_sql", sql)
    monkeypatch.setattr("fabric_ontology.query_ontology", search)
    monkeypatch.setattr(ontology, "_fetch_ontology_definition", lambda: {"definition": {"parts": []}})
    monkeypatch.setattr(ontology, "operator_token", lambda resource: pytest.fail("Operator identity fallback"))
    assert (await mcp._tool_manager.get_tool("query_lakehouse").fn(
        "SELECT TOP 1 StaffName FROM ent_staff", ctx, CHOSEN,
    ))["row_count"] == 0
    assert (await mcp._tool_manager.get_tool("query_ontology").fn("Question", ctx, CHOSEN))["answer"] == "Fixture ontology"
    result = await mcp._tool_manager.get_tool("describe_ontology").fn(ctx, CHOSEN)
    assert result["workspaceId"] == WORKSPACE and result["ontologyId"] == ONTOLOGY
    assert seen == [WORKSPACE] and CURRENT.get() is None


@pytest.mark.asyncio
async def test_malformed_sql_endpoint_and_missing_supported_tables_denied(setup_resolver):
    caller, _, _, _ = setup_resolver
    for failure in ("endpoint", "null-endpoint", "tables"):
        async with httpx.AsyncClient() as client:
            resolver = Resolver(client, caller)
            inventory = await resolver.inventory(CHOSEN)
            original = resolver.json
            async def malformed(url, original=original, failure=failure, **kwargs):
                data = await original(url, **kwargs)
                if failure == "endpoint" and "/lakehouses/" in url and not url.endswith("/tables"):
                    data["properties"]["sqlEndpointProperties"]["connectionString"] = "untrusted.example;UID=other"
                if failure == "tables" and url.endswith("/tables"):
                    data["data"] = []
                if failure == "null-endpoint" and "/lakehouses/" in url and not url.endswith("/tables"):
                    data["properties"]["sqlEndpointProperties"] = None
                return data
            resolver.json = malformed
            with pytest.raises(FabricSelectionError):
                await resolver.resolve({**CHOSEN, "ontologyId": None}, inventory, require_sql=False)


@pytest.mark.asyncio
async def test_mixed_sample_selection_remains_synthetic(setup_resolver):
    caller, _, _, _ = setup_resolver
    async with httpx.AsyncClient() as client:
        resolver = Resolver(client, caller)
        inventory = await resolver.inventory(CHOSEN)
        original = resolver.json

        async def marked(url, **kwargs):
            data = await original(url, **kwargs)
            if "/lakehouses/" in url and not url.endswith("/tables"):
                data["description"] = "enterprise-iq-test:v1:" + "a" * 12 + ":" + "b" * 64
            return data

        resolver.json = marked
        result = await resolver.resolve(CHOSEN, inventory, require_sql=False)
        assert result.data_mode == "test-sample"


@pytest.mark.asyncio
async def test_selected_ontology_error_response_is_bounded_and_closed(setup_resolver, monkeypatch):
    import io
    import urllib.error

    from enterprise_mcp.tools import ontology

    _, headers, _, _ = setup_resolver
    body = io.BytesIO(b"x" * (2 * 1024 * 1024 + 1))
    error = urllib.error.HTTPError(API + "/operations/fixture", 403, "Forbidden", {}, body)

    class Opener:
        def open(self, *args, **kwargs):
            raise error

    monkeypatch.setattr(ontology.urllib.request, "build_opener", lambda *args: Opener())
    async with connection_scope(CHOSEN, headers):
        with pytest.raises(FabricSelectionError, match="response limit"):
            ontology._fabric_request("GET", API + "/operations/fixture")
    assert body.closed


@pytest.mark.asyncio
async def test_sdk_reader_thread_tools_retain_their_originating_verified_context(setup_resolver, signed, monkeypatch):
    import threading
    from types import SimpleNamespace as Obj

    from copilot._jsonrpc import JsonRpcClient
    from copilot.session import CopilotSession, SessionEvent
    from copilot_runner import run_copilot
    from fabric_ontology import USER_FABRIC_TOKEN
    from m3_sql_handler import _access_token_struct, _connection_string

    _, headers, _, _ = setup_resolver
    token, _ = signed
    monkeypatch.setenv("IQ_DEMO_MODE", "0")
    monkeypatch.setenv("COPILOT_MODEL", "fixture-model")
    monkeypatch.setenv("COPILOT_BASE_URL", "https://example.openai.azure.com/openai/v1/")
    monkeypatch.setenv("COPILOT_API_KEY", "fixture-key")
    monkeypatch.delenv("COPILOT_USE_AZURE_IDENTITY", raising=False)
    monkeypatch.delenv("COPILOT_AZURE_RESOURCE_ID", raising=False)
    monkeypatch.setattr("m3_sql_handler.DefaultAzureCredential", lambda: pytest.fail("Delegated token required"))
    seen = []

    def execute(name, arguments, state):
        current = CURRENT.get()
        assert current is not None and USER_FABRIC_TOKEN.get() == current.caller.fabric_token
        assert "unit.datawarehouse.fabric.microsoft.com" in _connection_string()
        assert current.caller.sql_token.encode("utf-16-le") in _access_token_struct()
        seen.append(("local", current.workspace, current.caller.oid))
        return {"rows": []}, state

    monkeypatch.setattr("fabric_tools._execute_tool", execute)

    class Registry:
        def __init__(self, **kwargs):
            self.origin = CURRENT.get()

        async def discover(self, **kwargs):
            return [{"id": "fixture", "error": "", "tools": [
                {"name": "read", "inputSchema": {"type": "object"}},
            ]}]

        async def call(self, server, name, arguments):
            assert CURRENT.get() is self.origin
            assert USER_FABRIC_TOKEN.get() == self.origin.caller.fabric_token
            seen.append(("remote", self.origin.workspace, self.origin.caller.oid))
            return {"answer": "Fixture"}

        async def close(self):
            pass

    class Session:
        def __init__(self, tools):
            self.sdk = CopilotSession("fixture-session", Obj())
            self.sdk._register_tools(tools)
            self.done = asyncio.Event()

            async def respond(request):
                assert request.error is None
                self.done.set()

            self.sdk._rpc = Obj(tools=Obj(handle_pending_tool_call=respond))
            self.bridge = JsonRpcClient(None)
            self.bridge._loop = asyncio.get_running_loop()
            self.bridge.notification_handler = lambda method, params: self.sdk._dispatch_event(
                SessionEvent.from_dict(params["event"]),
            )

        def on(self, callback):
            return self.sdk.on(callback)

        async def send_and_wait(self, question, **kwargs):
            for name in ("query_lakehouse", "fixture__read"):
                self.done.clear()
                message = {"method": "session.event", "params": {"event": {
                    "id": WORKSPACE, "timestamp": "2026-01-01T00:00:00Z",
                    "type": "external_tool.requested", "data": {
                        "sessionId": "fixture-session", "requestId": name,
                        "toolCallId": name, "toolName": name, "arguments": {},
                    },
                }}}
                reader = threading.Thread(target=self.bridge._handle_message, args=(message,))
                reader.start()
                reader.join()
                await asyncio.wait_for(self.done.wait(), 2)
            return Obj(data=Obj(content="Fixture answer"))

        async def abort(self):
            self.sdk._cancel_pending_external_tools()

        async def disconnect(self):
            self.sdk._destroyed = True
            self.sdk._cancel_pending_external_tools()

    class Client:
        def __init__(self, **kwargs):
            pass

        async def start(self):
            pass

        async def create_session(self, **kwargs):
            return Session(kwargs["tools"])

        async def stop(self):
            pass

    async def turn(workspace, identity, credentials):
        async with connection_scope({**CHOSEN, "workspaceId": workspace}, credentials):
            bearer = USER_FABRIC_TOKEN.set(CURRENT.get().caller.fabric_token)
            try:
                assert await run_copilot("Fixture", client_factory=Client, registry_factory=Registry) == "Fixture answer"
                assert CURRENT.get().workspace == workspace
                assert CURRENT.get().caller.oid == identity
            finally:
                USER_FABRIC_TOKEN.reset(bearer)

    other_headers = {**headers, "x-iq-owner": "b" * 64, "x-iq-entra-oid": OTHER,
                     "authorization": "Bearer " + token(oid=OTHER),
                     "x-iq-sql-token": token("https://database.windows.net/", oid=OTHER)}
    await asyncio.gather(turn(WORKSPACE, OID, headers), turn(OTHER, OTHER, other_headers))
    assert sorted(seen) == sorted([
        ("local", WORKSPACE, OID), ("remote", WORKSPACE, OID),
        ("local", OTHER, OTHER), ("remote", OTHER, OTHER),
    ])
    assert CURRENT.get() is None and USER_FABRIC_TOKEN.get() is None


def test_dto_rejects_credentials_urls_unknown_ids():
    for value in ({**CHOSEN, "url": "https://untrusted.example"},
                  {**CHOSEN, "workspaceId": "not-a-uuid"}, {}, []):
        with pytest.raises(FabricSelectionError):
            selection(value)


def test_regular_sse_and_metadata_gateway_share_effective_selection(setup_resolver, monkeypatch):
    _, headers, _, _ = setup_resolver
    from enterprise_mcp.http_auth import ServiceTokenMiddleware
    from enterprise_mcp.tools import agents
    from fabric_ontology import USER_FABRIC_TOKEN
    monkeypatch.setenv("IQ_DEMO_MODE", "0")
    seen = []
    async def run(question, **kwargs):
        seen.append(CURRENT.get().workspace)
        assert USER_FABRIC_TOKEN.get() == CURRENT.get().caller.fabric_token
        kwargs["on_progress"]("start", {"grounding": "tools", "fabricDataMode": CURRENT.get().data_mode})
        return "Grounded fixture"
    monkeypatch.setattr("copilot_runner.run_copilot", run)
    mcp = FastMCP("test", stateless_http=True)
    agents.register(mcp)
    secret = "s" * 32
    headers = {**headers, "x-iq-service-token": secret}
    with TestClient(ServiceTokenMiddleware(mcp.streamable_http_app(), secret)) as client:
        assert client.post("/fabric/workspace", json=CHOSEN).status_code == 401
        assert client.post("/fabric/workspace", headers=headers, json=CHOSEN).json()["metadataValidated"]
        body = {"question": "Fixture", "fabric_selection": CHOSEN}
        regular = client.post("/ask", headers=headers, json=body)
        assert regular.status_code == 200 and regular.json()["fabricDataMode"] == "existing"
        stream = client.post("/ask/stream", headers=headers, json=body)
        assert "event: result" in stream.text and '"fabricDataMode": "existing"' in stream.text
        failure = client.post("/ask", headers=headers, json={**body, "fabric_selection": {**CHOSEN, "ontologyId": OTHER}})
        assert failure.status_code == 400
        assert client.post("/ask", headers=headers, json={**body, "method": "M7"}).status_code == 400
    assert seen == [WORKSPACE, WORKSPACE] and CURRENT.get() is None
