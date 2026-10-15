"""Origin-bound MCP trust and DNS-pinned transports; never a generic URL proxy."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import ssl
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx
from azure.core.exceptions import AzureError
from azure.identity.aio import ManagedIdentityCredential


class MCPPolicyError(ValueError):
    """Static, safe connection-policy diagnostic."""


def trust_policy() -> list[dict]:
    try:
        value = json.loads(os.environ.get("IQ_MCP_TRUST_POLICY", '{"origins":[]}'))
    except json.JSONDecodeError:
        raise MCPPolicyError("Operator MCP trust policy is invalid JSON.") from None
    if not isinstance(value, dict) or not isinstance(value.get("origins"), list) or len(value["origins"]) > 32:
        raise MCPPolicyError("Operator MCP trust policy is invalid.")
    seen = set()
    for entry in value["origins"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("origin"), str):
            raise MCPPolicyError("Operator MCP origin is invalid.")
        origin = urlsplit(entry["origin"])
        if (not origin.hostname or origin.username or origin.password or origin.query or origin.fragment
                or origin.path or origin.scheme not in {"https", "http"} or entry["origin"] in seen):
            raise MCPPolicyError("Operator MCP origin must be a unique exact origin.")
        seen.add(entry["origin"])
        for key, limit in (("authRefs", 32), ("tools", 64), ("allowedIPs", 32)):
            grants = entry.get(key, [])
            if not isinstance(grants, list) or len(grants) > limit or any(not isinstance(item, str) for item in grants):
                raise MCPPolicyError("Operator MCP grants must be bounded arrays of exact values.")
        try:
            for address in entry.get("allowedIPs", []):
                ipaddress.ip_address(address)
        except ValueError:
            raise MCPPolicyError("Operator MCP IP grants must be exact IP addresses.") from None
    return value["origins"]


def auth_references() -> list[dict]:
    try:
        value = json.loads(os.environ.get("IQ_MCP_AUTH_REFERENCES", "[]"))
    except json.JSONDecodeError:
        raise MCPPolicyError("Operator MCP auth references are invalid JSON.") from None
    if not isinstance(value, list) or len(value) > 32:
        raise MCPPolicyError("Operator MCP auth references are invalid.")
    seen = set()
    for entry in value:
        if (not isinstance(entry, dict) or not isinstance(entry.get("id"), str)
                or not entry["id"] or len(entry["id"]) > 64 or entry["id"] == "public"
                or entry["id"] in seen or entry.get("type") not in {"header", "managed-identity"}):
            raise MCPPolicyError("Operator MCP authentication reference is invalid.")
        seen.add(entry["id"])
        for key in ("label", "header", "env", "scope"):
            if key in entry and not isinstance(entry[key], str):
                raise MCPPolicyError("Operator MCP authentication fields must be strings.")
    return value


def approved_url(url: str, auth_ref: str | None = None) -> dict:
    parsed = urlsplit(url)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or "\\" in url or any(ord(char) < 33 for char in url)):
        raise MCPPolicyError("MCP URL must not contain credentials, query, fragment or control characters.")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    policy = next((entry for entry in trust_policy() if entry.get("origin") == origin), None)
    local_test = (os.environ.get("IQ_MCP_LOOPBACK_TESTS") == "1"
                  and os.environ.get("IQ_ENVIRONMENT") != "production"
                  and parsed.hostname in {"127.0.0.1", "::1"})
    if parsed.scheme != "https" and not (local_test and parsed.scheme == "http"):
        raise MCPPolicyError("Remote MCP requires HTTPS.")
    if not policy:
        raise MCPPolicyError("MCP origin is not operator-approved. Ask the operator to add its exact origin to the trust policy.")
    if auth_ref is not None:
        if auth_ref not in policy.get("authRefs", []):
            raise MCPPolicyError("MCP authentication reference is not approved for this origin.")
        if auth_ref != "public" and not any(ref.get("id") == auth_ref for ref in auth_references()):
            raise MCPPolicyError("MCP authentication reference is not configured.")
    return policy


def validate_addresses(addresses: list[str], policy: dict) -> list[str]:
    if not addresses or len(addresses) > 32:
        raise MCPPolicyError("MCP DNS returned no usable public addresses.")
    allowed = policy.get("allowedIPs", [])
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global and address not in allowed:
            raise MCPPolicyError("MCP DNS must resolve only to public addresses; internal IPs require an exact operator grant.")
    return addresses


async def connection_headers(entry: dict) -> dict[str, str]:
    ref_id = entry.get("authRef", "public")
    approved_url(entry["url"], ref_id)
    if ref_id == "public":
        return {}
    reference = next(ref for ref in auth_references() if ref.get("id") == ref_id)
    if reference.get("type") == "header":
        header = reference.get("header", "Authorization")
        variable = reference.get("env", "")
        value = os.environ.get(variable, "")
        if not value or not isinstance(header, str) or "\n" in header or "\r" in header:
            raise MCPPolicyError("Operator MCP header credential is unavailable.")
        if (variable.upper().startswith(("COPILOT_", "IDENTITY_", "MSI_"))
                or variable.upper() in {"MCP_SERVICE_TOKEN", "AZURE_CLIENT_SECRET", "AZURE_FEDERATED_TOKEN_FILE",
                                        "AZURE_OPENAI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}):
            raise MCPPolicyError("Internal service and model credentials cannot be MCP authentication references.")
        return {header: value}
    if reference.get("type") == "managed-identity":
        scope = reference.get("scope", "")
        if not isinstance(scope, str) or not scope.startswith("https://") or not scope.endswith("/.default"):
            raise MCPPolicyError("Operator MCP managed-identity audience is invalid.")
        try:
            async with ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID") or None) as credential:
                token = await credential.get_token(scope)
            return {"Authorization": f"Bearer {token.token}"}
        except AzureError:
            raise MCPPolicyError("MCP managed-identity authentication failed; check its approved audience and service role.") from None
    raise MCPPolicyError("Unsupported operator MCP authentication reference.")


def public_policy() -> dict:
    return {
        "origins": [{"origin": entry["origin"], "authRefs": entry.get("authRefs", [])} for entry in trust_policy()],
        "authReferences": [{"id": "public", "label": "Public service (no credential)", "type": "none"}] + [
            {"id": entry["id"], "label": entry.get("label", entry["id"]), "type": entry["type"]}
            for entry in auth_references()
        ],
    }


class NetworkStream(httpcore.AsyncNetworkStream):
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader, self.writer = reader, writer

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        try:
            return await asyncio.wait_for(self.reader.read(max_bytes), timeout)
        except TimeoutError as exc:
            raise httpcore.ReadTimeout("MCP read timed out.") from exc
        except OSError as exc:
            raise httpcore.ReadError("MCP read failed.") from exc

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        try:
            self.writer.write(buffer)
            await asyncio.wait_for(self.writer.drain(), timeout)
        except TimeoutError as exc:
            raise httpcore.WriteTimeout("MCP write timed out.") from exc
        except OSError as exc:
            raise httpcore.WriteError("MCP write failed.") from exc

    async def aclose(self) -> None:
        self.writer.close()
        await asyncio.wait_for(self.writer.wait_closed(), 5)

    async def start_tls(self, ssl_context: ssl.SSLContext, server_hostname: str | None = None,
                        timeout: float | None = None) -> NetworkStream:
        try:
            await asyncio.wait_for(self.writer.start_tls(ssl_context, server_hostname=server_hostname), timeout)
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout("MCP TLS timed out.") from exc
        except OSError as exc:
            raise httpcore.ConnectError("MCP TLS failed.") from exc
        return self

    def get_extra_info(self, info: str) -> Any:
        if info == "is_readable":
            return self.reader.at_eof()
        return self.writer.get_extra_info({"server_addr": "peername", "client_addr": "sockname"}.get(info, info))


class PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, policy: dict) -> None:
        self.policy = policy

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                          local_address: str | None = None, socket_options: Any = None) -> NetworkStream:
        try:
            addresses = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout,
            )
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout("MCP DNS timed out.") from exc
        except OSError as exc:
            raise httpcore.ConnectError("MCP DNS failed.") from exc
        ips = validate_addresses(list(dict.fromkeys(item[4][0] for item in addresses)), self.policy)
        for ip in ips:
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
                return NetworkStream(reader, writer)
            except TimeoutError as exc:
                raise httpcore.ConnectTimeout("MCP connection timed out.") from exc
            except OSError:
                continue
        raise httpcore.ConnectError("Approved MCP addresses could not be reached.")

    async def connect_unix_socket(self, path: str, timeout: float | None = None,
                                   socket_options: Any = None) -> NetworkStream:
        raise MCPPolicyError("MCP Unix socket connections are not permitted.")

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class ResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: Any) -> None:
        self.stream = stream

    async def __aiter__(self):
        size = 0
        with httpx_errors():
            async for part in self.stream:
                size += len(part)
                if size > 2_000_000:
                    raise MCPPolicyError("MCP response exceeded the safe limit.")
                yield part

    async def aclose(self) -> None:
        await self.stream.aclose()


@contextmanager
def httpx_errors():
    try:
        yield
    except (httpcore.TimeoutException, httpcore.NetworkError, httpcore.ProtocolError,
            httpcore.ProxyError, httpcore.UnsupportedProtocol) as exc:
        mappings = (
            (httpcore.ConnectTimeout, httpx.ConnectTimeout), (httpcore.ReadTimeout, httpx.ReadTimeout),
            (httpcore.WriteTimeout, httpx.WriteTimeout), (httpcore.PoolTimeout, httpx.PoolTimeout),
            (httpcore.ConnectError, httpx.ConnectError), (httpcore.ReadError, httpx.ReadError),
            (httpcore.WriteError, httpx.WriteError), (httpcore.RemoteProtocolError, httpx.RemoteProtocolError),
            (httpcore.LocalProtocolError, httpx.LocalProtocolError),
        )
        error = next((target for source, target in mappings if isinstance(exc, source)), httpx.TransportError)
        raise error("Approved MCP transport failed.") from exc


class ApprovedTransport(httpx.AsyncBaseTransport):
    def __init__(self, entry: dict) -> None:
        self.origin = approved_url(entry["url"], entry.get("authRef", "public"))["origin"]
        self.pool = httpcore.AsyncConnectionPool(
            network_backend=PinnedBackend(approved_url(entry["url"])),
            max_connections=4, max_keepalive_connections=0,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        parsed = urlsplit(str(request.url))
        if f"{parsed.scheme}://{parsed.netloc}" != self.origin:
            raise MCPPolicyError("MCP transport cannot send credentials to another origin.")
        approved_url(parsed._replace(query="").geturl())
        request.headers["Accept-Encoding"] = "identity"
        with httpx_errors():
            response = await self.pool.handle_async_request(httpcore.Request(
                method=request.method, url=httpcore.URL(
                    scheme=request.url.raw_scheme, host=request.url.raw_host,
                    port=request.url.port, target=request.url.raw_path,
                ), headers=request.headers.raw, content=request.stream, extensions=request.extensions,
            ))
        if 300 <= response.status < 400:
            await response.aclose()
            raise MCPPolicyError("MCP redirects are not permitted.")
        if any(name.lower() == b"content-encoding" and value.lower() != b"identity"
               for name, value in response.headers):
            await response.aclose()
            raise MCPPolicyError("Compressed MCP responses are not permitted; the service must honor identity encoding.")
        return httpx.Response(
            response.status, headers=response.headers, stream=ResponseStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self.pool.aclose()
