"""Ontology tools for EnterpriseKG.

`describe_ontology()` — fetch the live `EnterpriseKG_Ontology` definition (entities,
relationships, table bindings) via Fabric REST `getDefinition`.

`query_ontology(gql)` — execute a GQL traversal via M4 (which has direct
ontology MCP access). The Fabric Ontology preview has no public REST endpoint
for ad-hoc GQL, so queries are dispatched through M4.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from .. import config
from ..auth import get_token as operator_token


def get_token(resource: str) -> str:
    from fabric_connection import CURRENT
    connection = CURRENT.get()
    return connection.caller.fabric_token if connection else operator_token(resource)


def _fabric_request(method: str, url: str, body: dict | None = None,
                    timeout: int = 60) -> tuple[int, dict, bytes]:
    from fabric_connection import CURRENT, FabricSelectionError
    connection = CURRENT.get()
    if connection:
        timeout = min(timeout, 10)
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != "api.fabric.microsoft.com"
                or parsed.fragment or not parsed.path.startswith(("/v1/workspaces/", "/v1/operations/"))):
            raise FabricSelectionError("Ontology metadata URL is outside the official Fabric API.")
    headers = {
        "Authorization": f"Bearer {get_token(config.FABRIC_TOKEN_RESOURCE)}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        if connection:
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    raise FabricSelectionError("Ontology metadata redirect refused.")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            r = opener.open(req, timeout=timeout)
        else:
            r = urllib.request.urlopen(req, timeout=timeout)  # noqa: S310
        with r:
            raw = r.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise FabricSelectionError("Ontology metadata exceeds the response limit.")
            return r.status, dict(r.headers), raw
    except urllib.error.HTTPError as e:
        with e:
            raw = e.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise FabricSelectionError("Ontology metadata error exceeds the response limit.") from None
            return e.code, dict(e.headers), raw


def _await_lro(initial_status: int, initial_headers: dict, initial_body: bytes,
               max_polls: int = 60) -> bytes:
    from fabric_connection import CURRENT, FabricSelectionError
    selected = CURRENT.get() is not None
    if selected:
        max_polls = min(max_polls, 5)
    if initial_status != 202:
        return initial_body
    loc = initial_headers.get("Location") or initial_headers.get("location")
    if not loc:
        if selected:
            raise FabricSelectionError("Selected ontology metadata operation has no polling location.")
        return initial_body
    headers = initial_headers
    for _ in range(max_polls):
        delay = int(headers.get("Retry-After") or 5)
        time.sleep(min(max(delay, 0), 2) if selected else delay)
        s2, h2, b2 = _fabric_request("GET", loc)
        if selected and s2 not in {200, 202}:
            raise FabricSelectionError("Selected ontology metadata polling request failed.")
        try:
            j = json.loads(b2)
        except (ValueError, UnicodeError):
            if selected:
                raise FabricSelectionError("Selected ontology metadata operation returned invalid JSON.") from None
            j = {}
        if selected and not isinstance(j, dict):
            raise FabricSelectionError("Selected ontology metadata operation returned an invalid object.")
        if j.get("status") in ("Succeeded", "Failed", "Canceled"):
            if selected and j.get("status") != "Succeeded":
                raise RuntimeError("Selected ontology metadata operation failed.")
            rl = h2.get("Location") or h2.get("location")
            if rl:
                status, _, final_body = _fabric_request("GET", rl)
                if selected and status != 200:
                    raise FabricSelectionError("Selected ontology metadata result request failed.")
                return final_body
            return b2
        headers = h2
    raise TimeoutError("Fabric LRO did not finish within the poll budget.")


def _fetch_ontology_definition() -> dict:
    from fabric_connection import CURRENT, FabricSelectionError
    connection = CURRENT.get()
    if connection and not connection.ontology:
        raise FabricSelectionError("No ontology was selected in this workspace.")
    workspace = connection.workspace if connection else config.WORKSPACE_ID
    ontology = connection.ontology if connection else config.ONTOLOGY_ID
    url = (
        f"{config.FABRIC_API}/v1/workspaces/{workspace}"
        f"/items/{ontology}/getDefinition"
    )
    status, headers, body = _fabric_request("POST", url, {})
    if connection and status not in {200, 202}:
        raise FabricSelectionError("Selected ontology metadata request failed or access was denied.")
    final = _await_lro(status, headers, body)
    return json.loads(final)


def register(mcp: FastMCP) -> None:

    def describe() -> dict[str, Any]:
        """Return the live EnterpriseKG_Ontology definition: entities, relationships,
        table bindings, and key/property bindings."""
        doc = _fetch_ontology_definition()
        parts = doc.get("definition", {}).get("parts", [])

        entities: list[dict] = []
        relationships: list[dict] = []
        bindings: list[dict] = []
        rel_contexts: list[dict] = []

        for p in parts:
            path = p.get("path", "")
            try:
                payload = json.loads(base64.b64decode(p["payload"]))
            except Exception:
                continue
            if path.startswith("EntityTypes/") and path.endswith("/definition.json"):
                props = payload.get("properties", [])
                key_ids = payload.get("entityIdParts", [])
                disp_id = payload.get("displayNamePropertyId")
                key_names = [next((p["name"] for p in props if p["id"] == kid), str(kid))
                             for kid in key_ids]
                disp_name = next((p["name"] for p in props if p["id"] == disp_id),
                                 str(disp_id))
                entities.append({
                    "id":            payload.get("id"),
                    "name":          payload.get("name"),
                    "keys":          key_names,
                    "displayName":   disp_name,
                    "propertyCount": len(props),
                    "properties":    [p.get("name") for p in props],
                })
            elif (path.startswith("RelationshipTypes/")
                  and path.endswith("/definition.json")):
                relationships.append({
                    "id":           payload.get("id"),
                    "name":         payload.get("name"),
                    "sourceTypeId": payload.get("source", {}).get("entityTypeId"),
                    "targetTypeId": payload.get("target", {}).get("entityTypeId"),
                })
            elif "DataBindings" in path:
                bindings.append({"path": path, "definition": payload})
            elif "RelationshipContextualizations" in path:
                rel_contexts.append({"path": path, "definition": payload})

        ent_by_id = {str(e["id"]): e for e in entities}
        for r in relationships:
            r["source"] = ent_by_id.get(str(r["sourceTypeId"]), {}).get("name")
            r["target"] = ent_by_id.get(str(r["targetTypeId"]), {}).get("name")

        from fabric_connection import CURRENT
        connection = CURRENT.get()
        return {
            "workspaceId":      connection.workspace if connection else config.WORKSPACE_ID,
            "ontologyId":       connection.ontology if connection else config.ONTOLOGY_ID,
            "partCount":        len(parts),
            "entities":         entities,
            "relationships":    relationships,
            "dataBindingCount": len(bindings),
            "relationshipContextualizationCount": len(rel_contexts),
        }

    @mcp.tool()
    async def describe_ontology(ctx: Context, fabric_selection: dict | None = None) -> dict[str, Any]:
        """Read the selected ontology definition, or the legacy operator default."""
        import asyncio

        from fabric_connection import connection_scope, context_headers
        async with connection_scope(fabric_selection, context_headers(ctx)):
            return await asyncio.to_thread(describe)

    @mcp.tool()
    async def query_ontology(gql: str, ctx: Context, fabric_selection: dict | None = None) -> dict[str, Any]:
        """Execute a GQL traversal against EnterpriseKG_Ontology via M4.

        Example GQL:
            MATCH (e:Engineer)-[:hasCertification]->(c:Certification)
            WHERE c.CertificationCode = 'GAS_CCN1'
              AND e.RegionCode = 'LON'
            RETURN e.EngineerName, c.ExpiryDate
            ORDER BY c.ExpiryDate ASC LIMIT 25

        Required env: PROJECT_ENDPOINT, MODEL_DEPLOYMENT_NAME, FABRIC_WORKSPACE_ID,
        FABRIC_ONTOLOGY_ID (or FABRIC_ONTOLOGY_MCP_URL).
        """
        import asyncio

        from fabric_connection import connection_scope, context_headers
        from fabric_ontology import query_ontology as query
        async with connection_scope(fabric_selection, context_headers(ctx)):
            return await asyncio.to_thread(query, gql.strip())
