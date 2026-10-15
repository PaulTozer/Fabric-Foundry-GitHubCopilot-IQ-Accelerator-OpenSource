import json

import pytest
from mcp_registry import Registry, configurations


@pytest.mark.asyncio
async def test_three_real_demo_mcp_servers(monkeypatch):
    monkeypatch.setenv("IQ_DEMO_MODE", "1")
    registry = Registry()
    try:
        entries = await registry.discover()
        assert len(entries) == 3
        assert all(not item["error"] and item["tools"] for item in entries), entries
        portfolio = await registry.call("fabric-iq", "fabric_iq_query_portfolio", {})
        assert "DEMO-001" in str(portfolio)
        docs = await registry.call("foundry-iq", "foundry_iq_search_knowledge", {"query": "handover"})
        assert "/api/documents/demo-delivery?page=1" in str(docs)
        with pytest.raises(RuntimeError, match="returned an error"):
            await registry.call("foundry-iq", "foundry_iq_get_document", {"document_id": "missing"})
        with pytest.raises(ValueError, match="Unknown"):
            await registry.call("fabric-iq", "nonexistent", {})
    finally:
        await registry.close()


@pytest.mark.asyncio
async def test_disabled_and_workiq_optin(monkeypatch):
    monkeypatch.setenv("IQ_DEMO_MODE", "1")
    registry = Registry(disabled=["foundry-iq"])
    try:
        entries = await registry.discover(work_iq_enabled=False)
        assert [e["id"] for e in entries if e["enabled"]] == ["fabric-iq"]
        with pytest.raises(ValueError, match="disabled"):
            await registry.call("foundry-iq", "foundry_iq_get_document", {})
    finally:
        await registry.close()


@pytest.mark.parametrize("entry", [
    {"id": "test", "type": "http", "url": "http://example.com/mcp", "allowedOrigins": ["http://example.com"]},
    {"id": "test", "type": "http", "url": "https://example.com/mcp", "allowedOrigins": []},
    {"id": "test", "type": "stdio", "command": "cmd", "trusted": True},
    {"id": "test", "type": "stdio", "command": "C:\\Windows\\System32\\cmd.exe", "trusted": False},
    {"id": "../unsafe", "type": "http"},
])
def test_registry_validation(monkeypatch, tmp_path, entry):
    monkeypatch.delenv("IQ_DEMO_MODE", raising=False)
    path = tmp_path / "servers.json"
    path.write_text(json.dumps({"servers": [entry]}))
    monkeypatch.setenv("IQ_MCP_CONFIG", str(path))
    with pytest.raises(ValueError):
        configurations()
