"""Create the EnterpriseKG_Ontology item in the 'Enterprise KG' workspace.

Binds the entity types defined in `enterprise_schema.ENTITY_DEFS` to the
ENT_* Delta tables in `EnterpriseKG_Lakehouse`, and registers the
relationships from `enterprise_schema.RELATIONSHIPS`.

Adapted from EnterpriseIQ's `_create_ontology.py`. The Fabric REST payload
shape is the same — only the entity/relationship content is new.

Required env vars
-----------------
    FABRIC_WORKSPACE_ID    GUID of the Enterprise KG workspace
    FABRIC_LAKEHOUSE_ID    GUID of EnterpriseKG_Lakehouse

Optional
--------
    FABRIC_ONTOLOGY_NAME   Defaults to "EnterpriseKG_Ontology"
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from enterprise_schema import ENTITY_DEFS, RELATIONSHIPS


def build_ontology_parts(workspace_id: str, lakehouse_id: str, ontology_name: str) -> list[dict]:
    """Build documented generation-1 JSON parts without credentials or I/O."""
    WORKSPACE_ID = workspace_id
    LAKEHOUSE_ID = lakehouse_id
    ONTOLOGY_NAME = ontology_name
    _next_id = 9_500_000_000_000  # distinct base from EnterpriseKG to avoid id collisions
    def new_id() -> str:
        nonlocal _next_id
        _next_id += 1
        return str(_next_id)


    def new_guid() -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{workspace_id}/{lakehouse_id}/{ontology_name}/{new_id()}"))


    # ---- Build entity types ---------------------------------------------------
    entities: dict[str, dict] = {}
    for e in ENTITY_DEFS:
        entity_id = new_id()
        prop_ids: dict[str, str] = {}
        properties: list[dict] = []
        for (pname, vtype, _src) in e["props"]:
            pid = new_id()
            prop_ids[pname] = pid
            properties.append({
                "id":   pid,
                "name": pname,
                "redefines": None,
                "baseTypeNamespaceType": None,
                "valueType": vtype,
            })
        entities[e["name"]] = {
            "id":      entity_id,
            "name":    e["name"],
            "table":   e["table"],
            "keys":    e["keys"],
            "display": e["display"],
            "props":   prop_ids,
            "src_map": {pname: src for (pname, _vt, src) in e["props"]},
        }
        entities[e["name"]]["definition"] = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/ontology/entityType/1.0.0/schema.json",
            "id":                  entity_id,
            "namespace":           "usertypes",
            "baseEntityTypeId":    None,
            "name":                e["name"],
            "entityIdParts":       [prop_ids[k] for k in e["keys"]],
            "displayNamePropertyId": prop_ids[e["display"]],
            "namespaceType":       "Custom",
            "visibility":          "Visible",
            "properties":          properties,
            "timeseriesProperties": [],
            "untypedProperties": [],
        }


    # ---- Build relationship types --------------------------------------------
    relationships: list[dict] = []
    for r in RELATIONSHIPS:
        rid = new_id()
        relationships.append({
            "spec": r,
            "id":   rid,
            "definition": {
                "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/ontology/relationshipType/1.0.0/schema.json",
                "namespace":     "usertypes",
                "id":            rid,
                "name":          r["name"],
                "namespaceType": "Custom",
                "source": {"entityTypeId": entities[r["source"]]["id"]},
                "target": {"entityTypeId": entities[r["target"]]["id"]},
            },
        })


    # ---- Assemble Fabric "create item" payload --------------------------------
    def b64(obj) -> str:
        return base64.b64encode(json.dumps(obj).encode("utf-8")).decode("ascii")


    parts: list[dict] = []
    platform = {"metadata": {"type": "Ontology", "displayName": ONTOLOGY_NAME}}
    parts.append({"path": ".platform",        "payload": b64(platform), "payloadType": "InlineBase64"})
    parts.append({"path": "definition.json",  "payload": b64({}),       "payloadType": "InlineBase64"})

    for ent in entities.values():
        eid = ent["id"]
        parts.append({
            "path": f"EntityTypes/{eid}/definition.json",
            "payload": b64(ent["definition"]),
            "payloadType": "InlineBase64",
        })
        binding_id = new_guid()
        binding = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/ontology/dataBinding/1.0.0/schema.json",
            "id": binding_id,
            "dataBindingConfiguration": {
                "dataBindingType": "NonTimeSeries",
                "propertyBindings": [
                    {"sourceColumnName": ent["src_map"][p],
                     "targetPropertyId": ent["props"][p]}
                    for p in ent["props"].keys()
                ],
                "sourceTableProperties": {
                    "sourceType":      "LakehouseTable",
                    "workspaceId":     WORKSPACE_ID,
                    "itemId":          LAKEHOUSE_ID,
                    "sourceTableName": ent["table"].lower(),
                },
            },
        }
        parts.append({
            "path": f"EntityTypes/{eid}/DataBindings/{binding_id}.json",
            "payload": b64(binding),
            "payloadType": "InlineBase64",
        })

    for rel in relationships:
        rid    = rel["id"]
        spec   = rel["spec"]
        src_e  = entities[spec["source"]]
        tgt_e  = entities[spec["target"]]
        parts.append({
            "path": f"RelationshipTypes/{rid}/definition.json",
            "payload": b64(rel["definition"]),
            "payloadType": "InlineBase64",
        })
        ctx_id = new_guid()
        contextualization = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/ontology/contextualization/1.0.0/schema.json",
            "id": ctx_id,
            "dataBindingTable": {
                "workspaceId":     WORKSPACE_ID,
                "itemId":          LAKEHOUSE_ID,
                "sourceTableName": spec["table"].lower(),
                "sourceType":      "LakehouseTable",
            },
            "sourceKeyRefBindings": [
                {"sourceColumnName": col, "targetPropertyId": src_e["props"][prop]}
                for (col, prop) in spec["src_keys"]
            ],
            "targetKeyRefBindings": [
                {"sourceColumnName": col, "targetPropertyId": tgt_e["props"][prop]}
                for (col, prop) in spec["tgt_keys"]
            ],
        }
        parts.append({
            "path": f"RelationshipTypes/{rid}/Contextualizations/{ctx_id}.json",
            "payload": b64(contextualization),
            "payloadType": "InlineBase64",
        })


    return parts


# ---- REST plumbing --------------------------------------------------------
def az_token() -> str:
    out = subprocess.run(
        ["az", "account", "get-access-token",
         "--resource", "https://api.fabric.microsoft.com",
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, shell=True, check=True)
    return out.stdout.strip()


def _req(method: str, url: str, token: str, body: dict | None = None) -> tuple[int, dict, dict[str, str]]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            headers = dict(resp.headers)
            return resp.status, json.loads(raw) if raw.strip().startswith(("{", "[")) else {"raw": raw}, headers
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except Exception:
            return e.code, {"raw": raw}, dict(e.headers)


def main() -> None:
    WORKSPACE_ID = os.environ["FABRIC_WORKSPACE_ID"]
    LAKEHOUSE_ID = os.environ["FABRIC_LAKEHOUSE_ID"]
    ONTOLOGY_NAME = os.environ.get("FABRIC_ONTOLOGY_NAME", "EnterpriseKG_Ontology")
    parts = build_ontology_parts(WORKSPACE_ID, LAKEHOUSE_ID, ONTOLOGY_NAME)
    token = az_token()
    url_create = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/items"
    body = {
        "displayName": ONTOLOGY_NAME,
        "type":        "Ontology",
        "definition":  {"parts": parts},
    }
    print(f"Creating ontology '{ONTOLOGY_NAME}' in workspace {WORKSPACE_ID} "
          f"with {len(ENTITY_DEFS)} entities, {len(RELATIONSHIPS)} relationships, "
          f"{len(parts)} payload parts...")
    status, resp, headers = _req("POST", url_create, token, body)
    if status == 202:
        op_loc = headers.get("Location") or headers.get("location")
        print(f"Accepted — polling LRO at {op_loc}")
        while True:
            time.sleep(3)
            st, body2, _ = _req("GET", op_loc, token)
            state = (body2 or {}).get("status") if isinstance(body2, dict) else None
            print(f"  state={state}  http={st}")
            if state in ("Succeeded", "Failed"):
                print(json.dumps(body2, indent=2)[:2000])
                break
    elif status in (200, 201):
        print(f"Created. ID = {resp.get('id')}")
        print(json.dumps(resp, indent=2)[:2000])
    else:
        print(f"HTTP {status}")
        print(json.dumps(resp, indent=2)[:2000])
        sys.exit(2)


if __name__ == "__main__":
    main()
