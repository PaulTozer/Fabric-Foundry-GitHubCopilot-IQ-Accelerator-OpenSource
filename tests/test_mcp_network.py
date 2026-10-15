import asyncio
import json
from types import SimpleNamespace

import httpcore
import httpx
import pytest
from mcp_network import ApprovedTransport, PinnedBackend, approved_url, connection_headers, validate_addresses
from mcp_registry import Registry


def test_origin_policy_is_operator_owned_and_credentials_are_origin_bound(monkeypatch):
    monkeypatch.setenv("IQ_MCP_TRUST_POLICY", '{"origins":[{"origin":"https://mcp.example","authRefs":["public"]}]}')
    assert approved_url("https://mcp.example/mcp", "public")["origin"] == "https://mcp.example"
    for url in ["https://evil.example/mcp", "http://mcp.example/mcp",
                "https://user:password@mcp.example/mcp", "https://mcp.example/mcp?token=secret"]:
        with pytest.raises(ValueError):
            approved_url(url, "public")
    with pytest.raises(ValueError):
        approved_url("https://mcp.example/mcp", "unapproved-secret")


@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "169.254.169.254", "10.0.0.1",
                                    "192.168.1.1", "100.64.0.1", "::ffff:127.0.0.1"])
def test_dns_private_metadata_and_mixed_answers_blocked(address):
    with pytest.raises(ValueError, match="public"):
        validate_addresses(["8.8.8.8", address], {})


def test_public_dns_and_explicit_exact_internal_grant():
    assert validate_addresses(["8.8.8.8"], {}) == ["8.8.8.8"]
    assert validate_addresses(["10.20.30.40"], {"allowedIPs": ["10.20.30.40"]}) == ["10.20.30.40"]
    with pytest.raises(ValueError):
        validate_addresses(["10.20.30.41"], {"allowedIPs": ["10.20.30.40"]})


@pytest.mark.parametrize("field,value", [("authRefs", "public"), ("allowedIPs", "10.0.0.10"), ("tools", "admin_action")])
def test_policy_never_interprets_malformed_string_grants_as_substrings(monkeypatch, field, value):
    monkeypatch.setenv("IQ_MCP_TRUST_POLICY", json.dumps({"origins": [{
        "origin": "https://approved.example", "authRefs": ["public"], field: value,
    }]}))
    with pytest.raises(ValueError, match="arrays of exact"):
        approved_url("https://approved.example/mcp", "public")


def test_user_operator_session_id_collision_rejected_before_connect(monkeypatch):
    monkeypatch.setattr("mcp_registry.configurations", lambda: [{"id": "user-knowledge", "type": "http"}])
    with pytest.raises(ValueError, match="collide"):
        Registry(user_servers=[{"id": "knowledge", "type": "http"}])


@pytest.mark.asyncio
async def test_empty_operator_allowlist_is_deny_all(monkeypatch):
    monkeypatch.setattr("mcp_registry.configurations", lambda: [{"id": "operator", "type": "http", "tools": []}])
    registry = Registry()
    class Tool:
        name = "admin_action"
        def model_dump(self, **kwargs):
            return {"name": self.name, "inputSchema": {"type": "object"}}
    async def connect(entry):
        async def tools():
            return SimpleNamespace(tools=[Tool()])
        return SimpleNamespace(list_tools=tools)
    monkeypatch.setattr(registry, "connect", connect)
    assert (await registry.discover())[0]["tools"] == []
    with pytest.raises(ValueError):
        await registry.call("operator", "admin_action", {})
    await registry.close()


@pytest.mark.asyncio
async def test_internal_secrets_cannot_be_origin_bound_profiles_even_with_case_change(monkeypatch):
    monkeypatch.setenv("IQ_MCP_TRUST_POLICY", '{"origins":[{"origin":"https://approved.example","authRefs":["internal"]}]}')
    monkeypatch.setenv("IQ_MCP_AUTH_REFERENCES", '[{"id":"internal","type":"header","env":"mcp_service_token"}]')
    monkeypatch.setenv("mcp_service_token", "test-only-do-not-relay")
    with pytest.raises(ValueError, match="Internal service"):
        await connection_headers({"url": "https://approved.example/mcp", "authRef": "internal"})


@pytest.mark.asyncio
async def test_transport_rejects_sse_cross_origin_callbacks_and_compression(monkeypatch):
    monkeypatch.setenv("IQ_MCP_TRUST_POLICY", '{"origins":[{"origin":"https://approved.example","authRefs":["public"]}]}')
    transport = ApprovedTransport({"url": "https://approved.example/sse"})
    class Pool:
        async def handle_async_request(self, request):
            assert dict(request.headers)[b"Accept-Encoding"] == b"identity"
            return httpcore.Response(200, headers=[(b"content-encoding", b"gzip")], content=b"compressed")
        async def aclose(self):
            pass
    await transport.pool.aclose()
    transport.pool = Pool()
    with pytest.raises(ValueError, match="another origin"):
        await transport.handle_async_request(httpx.Request("POST", "https://evil.example/messages?session_id=1"))
    with pytest.raises(ValueError, match="Compressed"):
        await transport.handle_async_request(httpx.Request("GET", "https://approved.example/sse"))
    await transport.aclose()


@pytest.mark.asyncio
async def test_validated_dns_is_pinned_and_does_not_resolve_hostname_on_connect(monkeypatch):
    seen = []
    async def dns(host, port, **kwargs):
        seen.append(("dns", host))
        return [(None, None, None, None, ("8.8.8.8", port))]
    async def connect(host, port):
        seen.append(("connect", host))
        return None, None
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    monkeypatch.setattr(asyncio, "open_connection", connect)
    await PinnedBackend({}).connect_tcp("approved.example", 443, timeout=1)
    assert seen == [("dns", "approved.example"), ("connect", "8.8.8.8")]
