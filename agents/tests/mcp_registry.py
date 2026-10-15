"""Operator-controlled, request-scoped MCP registry. No browser-supplied hosts."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from contextlib import AsyncExitStack
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp_network import MCPPolicyError, approved_url

from mcp import ClientSession, StdioServerParameters

ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,79}$")
def configurations() -> list[dict[str, Any]]:
    if os.environ.get("IQ_DEMO_MODE") == "1":
        return [
            {"id": kind, "type": "stdio", "enabled": True, "trusted": True,
             "demo": True, "command": sys.executable,
             "args": [str(resources.files("enterprise_mcp").joinpath("demo.py")), kind]}
            for kind in ("fabric-iq", "foundry-iq", "work-iq")
        ]
    path = os.environ.get("IQ_MCP_CONFIG")
    if not path:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
        raise ValueError("IQ_MCP_CONFIG must contain a servers array.")
    entries = data["servers"]
    if len(entries) > 16:
        raise ValueError("Maximum 16 MCP servers.")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or not ID.fullmatch(entry.get("id", "")):
            raise ValueError("Invalid MCP server ID.")
        if entry["id"] in seen:
            raise ValueError("Duplicate MCP server ID.")
        seen.add(entry["id"])
        if entry.get("type") not in {"http", "sse", "stdio"}:
            raise ValueError("MCP transport must be http, sse or stdio.")
        if not isinstance(entry.get("enabled", True), bool):
            raise ValueError("MCP enabled must be boolean.")
        if entry["type"] == "stdio":
            # Commands come ONLY from an operator-managed file, and require an
            # explicit trust grant; never accepted in an HTTP request.
            if entry.get("trusted") is not True or not Path(entry.get("command", "")).is_absolute():
                raise ValueError("stdio requires trusted:true and an absolute executable path.")
            if not isinstance(entry.get("args", []), list) or not all(isinstance(v, str) for v in entry.get("args", [])):
                raise ValueError("MCP args must be strings.")
        else:
            url = urlsplit(entry.get("url", ""))
            local = url.hostname in {"localhost", "127.0.0.1", "::1"}
            if not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError("MCP URL cannot contain credentials, query or fragment.")
            if url.scheme != "https" and not (local and url.scheme == "http"):
                raise ValueError("Remote MCP requires HTTPS; HTTP is loopback-only.")
            origin = f"{url.scheme}://{url.netloc}"
            if origin not in entry.get("allowedOrigins", []):
                raise ValueError("MCP URL origin requires explicit operator allowedOrigins.")
        for mapping in ("env", "headers"):
            if not isinstance(entry.get(mapping, {}), dict):
                raise ValueError("MCP env/headers must reference environment variable names.")
            for name, reference in entry.get(mapping, {}).items():
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", reference) or "\n" in name or "\r" in name:
                    raise ValueError("MCP secrets must be environment references.")
        if "tools" in entry and (not isinstance(entry["tools"], list) or not all(NAME.fullmatch(v) for v in entry["tools"])):
            raise ValueError("MCP tools must be an allowlist of tool names.")
    return entries


class SessionWorker:
    """Keep MCP task groups and their failures inside the connection's owning task."""
    def __init__(self, registry: Registry, entry: dict) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.ready = asyncio.get_running_loop().create_future()
        self.pending: asyncio.Future | None = None
        self.shutdown_requested = False
        self.task = asyncio.create_task(self.run(registry, entry))

    async def run(self, registry: Registry, entry: dict) -> None:
        failure: Exception = RuntimeError("MCP connection stopped.")
        try:
            async with AsyncExitStack() as stack:
                session = await registry.open_session(entry, stack)
                self.ready.set_result(self)
                while True:
                    operation, args, self.pending = await self.queue.get()
                    if self.pending.cancelled():
                        self.pending = None
                        continue
                    result = await getattr(session, operation)(*args)
                    if not self.pending.done():
                        self.pending.set_result(result)
                    self.pending = None
        except Exception as exc:
            failure = exc
            logging.getLogger(__name__).warning("MCP session stopped or cleanup failed (%s).", type(exc).__name__)
        finally:
            for future in (self.ready, self.pending):
                if future is not None and not future.done():
                    future.set_exception(failure)
            while not self.queue.empty():
                _, _, future = self.queue.get_nowait()
                if not future.done():
                    future.set_exception(failure)

    async def request(self, operation: str, *args: Any) -> Any:
        if self.task.done():
            raise RuntimeError("MCP connection is no longer available.")
        future = asyncio.get_running_loop().create_future()
        await self.queue.put((operation, args, future))
        try:
            return await future
        except asyncio.CancelledError:
            await self.close()
            raise

    async def list_tools(self) -> Any:
        return await self.request("list_tools")

    async def call_tool(self, name: str, arguments: dict) -> Any:
        return await self.request("call_tool", name, arguments)

    async def close(self) -> None:
        caller = asyncio.current_task()
        initial_cancellations = caller.cancelling()
        interrupted = False
        if not self.shutdown_requested:
            self.shutdown_requested = True
            self.task.cancel()
        while not self.task.done():
            try:
                await asyncio.shield(self.task)
            except asyncio.CancelledError:
                interrupted = interrupted or caller.cancelling() > initial_cancellations
        if interrupted:
            raise asyncio.CancelledError


class Registry:
    def __init__(self, disabled: list[str] | None = None, user_servers: list[dict] | None = None,
                 *, user_only: bool = False) -> None:
        self.entries = [] if user_only else configurations()
        for entry in user_servers or []:
            self.entries.append({**entry, "id": "user-" + entry["id"], "user": True})
        if len({entry["id"] for entry in self.entries}) != len(self.entries):
            raise ValueError("User and operator MCP server IDs collide. Choose a different user server ID.")
        self.disabled = set(disabled or [])
        if self.disabled - {e["id"] for e in self.entries}:
            raise ValueError("Unknown MCP server selection.")
        self.stack = AsyncExitStack()
        self.sessions: dict[str, SessionWorker] = {}
        self.tool_names: dict[str, set[str]] = {}

    async def connect(self, entry: dict) -> SessionWorker:
        if entry["id"] in self.sessions:
            return self.sessions[entry["id"]]
        worker = SessionWorker(self, entry)
        self.stack.push_async_callback(worker.close)
        await worker.ready
        self.sessions[entry["id"]] = worker
        return worker

    async def open_session(self, entry: dict, stack: AsyncExitStack) -> ClientSession:
        headers = {}
        if entry.get("user"):
            from mcp_network import connection_headers
            headers = await connection_headers(entry)
        for header, variable in entry.get("headers", {}).items():
            value = os.environ.get(variable)
            if not value:
                raise ValueError(f"MCP auth environment reference '{variable}' is unset.")
            headers[header] = value
        if entry.get("useFabricToken"):
            from fabric_ontology import USER_FABRIC_TOKEN
            token = USER_FABRIC_TOKEN.get()
            if not token:
                raise ValueError("This MCP server requires delegated Fabric sign-in.")
            headers["Authorization"] = f"Bearer {token}"
        if entry["type"] == "stdio":
            child_env = {k: os.environ[k] for k in ("PATH", "SystemRoot", "TEMP", "TMP", "HOME") if k in os.environ}
            for name, variable in entry.get("env", {}).items():
                if variable not in os.environ:
                    raise ValueError(f"MCP env reference '{variable}' is unset.")
                child_env[name] = os.environ[variable]
            transport = stdio_client(StdioServerParameters(
                command=entry["command"], args=entry.get("args", []), env=child_env,
            ))
        elif entry["type"] == "http":
            from mcp_network import ApprovedTransport
            http = await stack.enter_async_context(httpx.AsyncClient(
                headers=headers, timeout=20, follow_redirects=False,
                transport=ApprovedTransport(entry) if entry.get("user") else None,
                trust_env=not entry.get("user"),
            ))
            transport = streamable_http_client(entry["url"], http_client=http)
        else:
            from mcp_network import ApprovedTransport
            def factory(headers: dict | None = None, **kwargs: Any) -> httpx.AsyncClient:
                kwargs.pop("follow_redirects", None)
                return httpx.AsyncClient(
                    headers=headers, follow_redirects=False,
                    transport=ApprovedTransport(entry) if entry.get("user") else None,
                    trust_env=not entry.get("user"), **kwargs,
                )
            transport = sse_client(entry["url"], headers=headers, timeout=20,
                                   httpx_client_factory=factory)
        streams = await stack.enter_async_context(transport)
        session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
        await session.initialize()
        return session

    async def discover(self, *, work_iq_enabled: bool = True,
                       ontology_enabled: bool = True) -> list[dict]:
        results = []
        for entry in self.entries:
            enabled = entry.get("enabled", True) and entry["id"] not in self.disabled
            enabled = enabled and (work_iq_enabled or entry["id"] != "work-iq")
            enabled = enabled and (ontology_enabled or not entry.get("ontology"))
            item = {"id": entry["id"], "transport": entry["type"],
                    "enabled": enabled, "demo": bool(entry.get("demo")),
                    "tools": [], "error": ""}
            if enabled:
                try:
                    async with asyncio.timeout(20):
                        session = await self.connect(entry)
                        discovered = await session.list_tools()
                        allowed = entry.get("tools")
                        if entry.get("user") and allowed == []:
                            allowed = None
                        tools = [tool.model_dump(by_alias=True) for tool in discovered.tools
                                 if allowed is None or tool.name in allowed]
                        if entry.get("user"):
                            operator_tools = approved_url(entry["url"]).get("tools", [])
                            tools = [tool for tool in tools if tool["name"] in operator_tools]
                        if len(tools) > 64 or len(json.dumps(tools)) > 200_000:
                            raise ValueError("MCP tool metadata exceeds safe limits.")
                        if any(not NAME.fullmatch(tool["name"]) for tool in tools):
                            raise ValueError("Unsafe MCP tool name.")
                        self.tool_names[entry["id"]] = {tool["name"] for tool in tools}
                        item["tools"] = tools
                except Exception as exc:
                    # Never leak transport headers, URLs or command output.
                    item["error"] = (
                        str(exc) if isinstance(exc, MCPPolicyError) and entry.get("user")
                        else f"MCP discovery failed ({type(exc).__name__}); check approved origin, server URL, authentication and transport."
                    )
            results.append(item)
        return results

    async def call(self, server: str, tool: str, arguments: dict) -> dict:
        if server not in self.sessions or tool not in self.tool_names.get(server, set()):
            raise ValueError("Unknown, disabled or undiscovered MCP tool.")
        try:
            async with asyncio.timeout(30):
                result = await self.sessions[server].call_tool(tool, arguments)
        except Exception as exc:
            raise RuntimeError(f"MCP tool '{server}/{tool}' failed ({type(exc).__name__}).") from None
        if result.isError:
            raise RuntimeError(f"MCP tool '{server}/{tool}' returned an error.")
        return result.model_dump(by_alias=True, exclude_none=True)

    async def close(self) -> None:
        await self.stack.aclose()
