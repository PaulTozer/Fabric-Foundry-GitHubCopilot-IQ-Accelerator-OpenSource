"""Authenticated, read-only Fabric selection with request-local credentials."""
from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import jwt

API = "https://api.fabric.microsoft.com/v1"
FABRIC_AUDIENCES = ["https://api.fabric.microsoft.com", "00000009-0000-0000-c000-000000000000"]
SQL_AUDIENCES = ["https://database.windows.net", "https://database.windows.net/"]
MAX_BYTES = 2 * 1024 * 1024


class FabricSelectionError(ValueError):
    """Safe diagnostics; never include service bodies, credentials or URLs."""


def identifier(value: Any) -> str:
    if not isinstance(value, str):
        raise FabricSelectionError("Fabric IDs must be hyphenated UUIDs.")
    try:
        parsed = str(uuid.UUID(value))
    except ValueError:
        raise FabricSelectionError("Fabric IDs must be hyphenated UUIDs.") from None
    if parsed != value.lower():
        raise FabricSelectionError("Fabric IDs must be hyphenated UUIDs.")
    return parsed


def selection(value: Any) -> dict[str, str | None]:
    if not isinstance(value, dict) or set(value) - {"workspaceId", "lakehouseId", "ontologyId"}:
        raise FabricSelectionError("Supply only workspace and optional Fabric item IDs.")
    result: dict[str, str | None] = {"workspaceId": identifier(value.get("workspaceId"))}
    for key in ("lakehouseId", "ontologyId"):
        if key in value:
            result[key] = None if value[key] is None else identifier(value[key])
    return result


@dataclass(frozen=True)
class Caller:
    tenant: str
    oid: str
    fabric_token: str = field(repr=False)
    sql_token: str = field(default="", repr=False)

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> Caller:
        if not re.fullmatch(r"[a-f0-9]{64}", headers.get("x-iq-owner", "")):
            raise FabricSelectionError("Authenticated owner context is required for workspace selection.")
        tenant = identifier(headers.get("x-iq-tenant-id"))
        oid = identifier(headers.get("x-iq-entra-oid"))
        auth = headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else ""
        if not token or len(token) > 24000:
            raise FabricSelectionError("Workspace selection requires delegated Fabric sign-in.")
        sql = headers.get("x-iq-sql-token", "")
        if len(sql) > 24000:
            raise FabricSelectionError("Invalid delegated SQL credential.")
        return cls(tenant, oid, token, sql)


@dataclass(frozen=True)
class Connection:
    workspace: str
    lakehouse: str | None
    ontology: str | None
    sql_host: str
    sql_database: str
    data_mode: str
    caller: Caller = field(repr=False)

    @property
    def ontology_url(self) -> str:
        return (f"{API}/mcp/dataPlane/workspaces/{self.workspace}/items/{self.ontology}/ontologyEndpoint"
                if self.ontology else "")

    def public(self) -> dict:
        return {"selection": {"workspaceId": self.workspace, "lakehouseId": self.lakehouse,
                              "ontologyId": self.ontology},
                "sql": bool(self.sql_host), "ontology": bool(self.ontology),
                "fabricDataMode": self.data_mode, "metadataValidated": True}


CURRENT: ContextVar[Connection | None] = ContextVar("fabric_connection", default=None)


class Resolver:
    def __init__(self, client: httpx.AsyncClient, caller: Caller):
        self.client = client
        self.caller = caller
        self.calls = 0

    async def json(self, url: str, *, authenticated: bool = True) -> dict:
        self.calls += 1
        if self.calls > 20:
            raise FabricSelectionError("Fabric metadata call limit reached.")
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self.caller.fabric_token}"
        try:
            async with self.client.stream("GET", url, headers=headers, follow_redirects=False) as response:
                if response.status_code != 200:
                    code = response.status_code
                    if code in {401, 403}:
                        raise FabricSelectionError("Fabric access denied; check existing delegated scopes and item permissions.")
                    if code == 404:
                        raise FabricSelectionError("Selected Fabric workspace or item is unavailable.")
                    if code == 429:
                        raise FabricSelectionError("Fabric metadata is throttled; retry later.")
                    raise FabricSelectionError("Fabric metadata request failed or redirected.")
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise FabricSelectionError("Compressed Fabric metadata is not supported.")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES:
                        raise FabricSelectionError("Fabric metadata exceeds the response limit.")
            import json
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise FabricSelectionError("Fabric metadata must be a JSON object.")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, FabricSelectionError):
                raise
            raise FabricSelectionError("Fabric metadata transport or JSON validation failed.") from None

    async def verify_tokens(self, *, sql: bool = False) -> None:
        keys = await self.json(
            f"https://login.microsoftonline.com/{self.caller.tenant}/discovery/v2.0/keys",
            authenticated=False,
        )
        for token, audiences in [(self.caller.fabric_token, FABRIC_AUDIENCES)] + (
            [(self.caller.sql_token, SQL_AUDIENCES)] if sql else []
        ):
            if not token:
                raise FabricSelectionError("Selected SQL requires an existing-consented delegated SQL token. No identity fallback.")
            try:
                header = jwt.get_unverified_header(token)
                if header.get("alg") != "RS256":
                    raise jwt.InvalidTokenError()
                matches = [key for key in keys["keys"] if key.get("kid") == header.get("kid")]
                if len(matches) != 1:
                    raise jwt.InvalidTokenError()
                public_key = jwt.PyJWK.from_dict(matches[0]).key
                claims = jwt.decode(
                    token, public_key, algorithms=["RS256"], audience=audiences,
                    issuer=[f"https://sts.windows.net/{self.caller.tenant}/",
                            f"https://login.microsoftonline.com/{self.caller.tenant}/v2.0"],
                    options={"require": ["exp", "iat", "nbf", "oid", "tid", "scp"]},
                )
                if (claims["tid"] != self.caller.tenant or claims["oid"] != self.caller.oid
                        or not isinstance(claims["scp"], str) or not claims["scp"].strip()):
                    raise jwt.InvalidTokenError()
            except (jwt.PyJWTError, KeyError, TypeError, ValueError):
                raise FabricSelectionError("Delegated token identity, tenant, audience or expiry is invalid. Sign in with the current account.") from None

    async def collection(self, path: str) -> list[dict]:
        values: list[dict] = []
        url = API + path
        for _ in range(5):
            data = await self.json(url)
            page = data.get("value", data.get("data"))
            if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
                raise FabricSelectionError("Malformed Fabric metadata collection.")
            values.extend(page)
            if len(values) > 500:
                raise FabricSelectionError("Fabric metadata item limit reached.")
            continuation = data.get("continuationToken")
            uri = data.get("continuationUri")
            if uri:
                parsed = urlsplit(uri)
                if (parsed.scheme != "https" or parsed.netloc != "api.fabric.microsoft.com"
                        or parsed.path != "/v1" + path or parsed.fragment):
                    raise FabricSelectionError("Fabric continuation is outside the selected collection.")
            if not continuation:
                if uri:
                    raise FabricSelectionError("Incomplete Fabric pagination.")
                return values
            if not isinstance(continuation, str) or len(continuation) > 4096:
                raise FabricSelectionError("Invalid Fabric continuation token.")
            url = API + path + "?continuationToken=" + quote(continuation, safe="")
        raise FabricSelectionError("Fabric metadata page limit reached; narrow the workspace.")

    async def inventory(self, chosen: dict) -> dict:
        workspace = chosen["workspaceId"]
        base = f"/workspaces/{workspace}"
        meta = await self.json(API + base)
        if identifier(meta.get("id")) != workspace:
            raise FabricSelectionError("Fabric workspace identity mismatch.")
        items = await self.collection(base + "/items")
        candidates: dict[str, list[dict]] = {"lakehouses": [], "ontologies": []}
        for item in items:
            kind = {"Lakehouse": "lakehouses", "Ontology": "ontologies"}.get(item.get("type"))
            if kind:
                if identifier(item.get("workspaceId")) != workspace:
                    raise FabricSelectionError("Fabric item workspace mismatch.")
                candidates[kind].append({"id": identifier(item.get("id")),
                                         "name": str(item.get("displayName", ""))[:256]})
        return {"workspaceId": workspace, **candidates}

    async def resolve(self, chosen: dict, inventory: dict, *, require_sql: bool) -> Connection:
        workspace = chosen["workspaceId"]
        base = f"/workspaces/{workspace}"
        selected: dict[str, str | None] = {}
        for key, kind in (("lakehouseId", "lakehouses"), ("ontologyId", "ontologies")):
            choices = inventory[kind]
            if key not in chosen:
                if len(choices) > 1:
                    raise FabricSelectionError("Multiple Fabric items exist; select item IDs explicitly.")
                selected[key] = choices[0]["id"] if choices else None
            else:
                selected[key] = chosen[key]
            if selected[key] and not any(item["id"] == selected[key] for item in choices):
                raise FabricSelectionError("Selected item is not a readable item in this workspace.")
        lakehouse, ontology = selected["lakehouseId"], selected["ontologyId"]
        if not lakehouse and not ontology:
            raise FabricSelectionError("Select at least one readable Lakehouse or supported Ontology.")
        host, database, markers = "", "", []
        for item_id, plural, kind in ((lakehouse, "lakehouses", "Lakehouse"), (ontology, "ontologies", "Ontology")):
            if not item_id:
                continue
            meta = await self.json(f"{API}{base}/{plural}/{item_id}")
            if (identifier(meta.get("id")) != item_id or identifier(meta.get("workspaceId")) != workspace
                    or meta.get("type") != kind):
                raise FabricSelectionError("Fabric item metadata identity/type mismatch.")
            markers.append(bool(re.fullmatch(r"enterprise-iq-test:v1:[0-9a-f]{12}:[0-9a-f]{64}",
                                             str(meta.get("description", "")))))
            properties = meta.get("properties")
            if not isinstance(properties, dict):
                raise FabricSelectionError("Fabric item properties are unavailable.")
            if kind == "Ontology":
                if properties.get("generation") != 1:
                    raise FabricSelectionError("Only generation-1 ontology is supported. Choose SQL-only explicitly or a supported ontology.")
            else:
                endpoint = properties.get("sqlEndpointProperties", {})
                if not isinstance(endpoint, dict):
                    raise FabricSelectionError("Lakehouse SQL endpoint properties are unavailable.")
                host = endpoint.get("connectionString", "")
                database = meta.get("displayName", "")
                if (endpoint.get("provisioningStatus") != "Success" or not isinstance(host, str)
                        or not re.fullmatch(r"[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.datawarehouse\.fabric\.microsoft\.com", host)
                        or not isinstance(database, str) or not database or len(database) > 256
                        or any(ord(c) < 32 for c in database)):
                    raise FabricSelectionError("Lakehouse SQL endpoint/database is not ready or supported.")
                tables = await self.collection(f"{base}/lakehouses/{item_id}/tables")
                from enterprise_schema import TABLE_NAMES
                names = {str(table.get("name", "")).lower() for table in tables}
                if not {name.lower() for name in TABLE_NAMES} <= names:
                    raise FabricSelectionError("Selected Lakehouse does not expose the supported EnterpriseKG tables.")
        if require_sql and lakehouse:
            await self.verify_tokens(sql=True)
        return Connection(workspace, lakehouse, ontology, host, database,
                          "test-sample" if any(markers) else "existing", self.caller)


@asynccontextmanager
async def resolver_for(caller: Caller):
    import asyncio
    try:
        async with asyncio.timeout(40), httpx.AsyncClient(
            timeout=10, follow_redirects=False, trust_env=False,
        ) as client:
            resolver = Resolver(client, caller)
            await resolver.verify_tokens()
            yield resolver
    except TimeoutError:
        raise FabricSelectionError("Fabric workspace validation timed out.") from None


@asynccontextmanager
async def connection_scope(value: Any, headers: Mapping[str, str]):
    if value is None:
        yield
        return
    chosen = selection(value)
    caller = Caller.from_headers(headers)
    async with resolver_for(caller) as resolver:
        inventory = await resolver.inventory(chosen)
        connection = await resolver.resolve(chosen, inventory, require_sql=True)
    token = CURRENT.set(connection)
    try:
        yield
    finally:
        CURRENT.reset(token)


def context_headers(ctx: Any) -> Mapping[str, str]:
    request = ctx.request_context.request
    return request.headers if request is not None else {}
