import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from agent_settings import validate_agent
from mcp_registry import Registry


@pytest.mark.asyncio
@pytest.mark.parametrize("transport,path", [
    ("streamable-http", "/mcp"), ("sse", "/sse"),
    ("streamable-http", "/unauthorized"), ("sse", "/unauthorized"),
    ("streamable-http", "/redirect"),
])
async def test_real_user_remote_discovery_tool_allowlist_and_cleanup(monkeypatch, transport, path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP"}}
    process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("mcp_echo_fixture.py")), transport, str(port)],
                               env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    origin = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
            for _ in range(60):
                if process.poll() is not None:
                    pytest.fail(f"Controlled MCP fixture exited: {process.stderr.read().decode(errors='replace')}")
                try:
                    if (await client.get(origin + "/health")).status_code == 200:
                        break
                except httpx.TransportError:
                    await asyncio.sleep(0.1)
            else:
                pytest.fail("Controlled MCP fixture did not become responsive.")
        monkeypatch.setenv("IQ_DEMO_MODE", "0")
        monkeypatch.setenv("IQ_MCP_LOOPBACK_TESTS", "1")
        monkeypatch.setenv("IQ_MCP_TRUST_POLICY", json.dumps({"origins": [{
            "origin": origin, "authRefs": ["public"], "allowedIPs": ["127.0.0.1"], "tools": ["echo"],
        }]}))
        server = {"id": "echo", "type": "http" if transport == "streamable-http" else "sse",
                  "url": origin + path, "authRef": "public", "enabled": True, "tools": ["echo"]}
        agent = validate_agent({"id": "generic", "name": "Generic", "skills": [], "servers": [server]})
        registry = Registry(user_servers=agent["servers"], user_only=True)
        try:
            health = await registry.discover()
            if path in {"/unauthorized", "/redirect"}:
                assert health[0]["error"] and health[0]["tools"] == []
                with pytest.raises(ValueError):
                    await registry.call("user-echo", "echo", {})
                return
            assert health[0]["error"] == ""
            assert [tool["name"] for tool in health[0]["tools"]] == ["echo"]
            result = await registry.call("user-echo", "echo", {"message": "Actual transport round trip"})
            assert "Actual transport round trip" in json.dumps(result)
            with pytest.raises(ValueError):
                await registry.call("user-echo", "arbitrary", {})
        finally:
            await registry.close()
        disabled = Registry(user_servers=[{**server, "enabled": False}], user_only=True)
        try:
            assert (await disabled.discover())[0]["tools"] == []
            with pytest.raises(ValueError):
                await disabled.call("user-echo", "echo", {})
        finally:
            await disabled.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
        process.stderr.close()
