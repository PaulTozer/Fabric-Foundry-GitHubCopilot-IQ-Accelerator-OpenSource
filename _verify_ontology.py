"""Fetch the live EnterpriseKG_Ontology definition from Fabric and dump it.

Usage:
    python _verify_ontology.py [--out _onto_dump.json]
"""
from __future__ import annotations
import argparse, base64, json, os, subprocess, sys, time
import urllib.request, urllib.error

WORKSPACE_ID = os.environ["FABRIC_WORKSPACE_ID"]
ONTOLOGY_ID  = os.environ["FABRIC_ONTOLOGY_ID"]


def az_token() -> str:
    out = subprocess.run(
        ["az", "account", "get-access-token",
         "--resource", "https://api.fabric.microsoft.com",
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, shell=True, check=True)
    return out.stdout.strip()


def _req(method: str, url: str, token: str) -> tuple[int, dict, dict[str, str]]:
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}, dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        return e.code, json.loads(raw) if raw else {}, dict(e.headers)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_onto_dump.json",
                    help="Where to write the decoded ontology JSON")
    args = ap.parse_args()

    token = az_token()
    url = (f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}"
           f"/ontologies/{ONTOLOGY_ID}/getDefinition")
    print(f"POST {url}")
    status, body, headers = _req("POST", url, token)
    if status == 202:
        op = headers.get("Location") or headers.get("location")
        print(f"LRO at {op}")
        while True:
            time.sleep(2)
            st, body2, hdrs2 = _req("GET", op, token)
            state = (body2 or {}).get("status")
            print(f"  state={state}  http={st}")
            if state in ("Succeeded", "Failed"):
                # Fabric returns the result URL in the Location header of the
                # successful GET, not as a "result" field in the body.
                result_url = (
                    hdrs2.get("Location")
                    or hdrs2.get("location")
                    or (body2 or {}).get("result")
                )
                if result_url:
                    _, body, _ = _req("GET", result_url, token)
                else:
                    body = body2
                break

    # Decode the base64 parts back into JSON for easy reading
    parts_out = []
    for p in (body.get("definition") or {}).get("parts", []):
        path     = p.get("path")
        payload  = p.get("payload")
        if payload:
            try:
                decoded = json.loads(base64.b64decode(payload).decode("utf-8"))
            except Exception:
                decoded = {"_raw": payload}
        else:
            decoded = None
        parts_out.append({"path": path, "payload": decoded})

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"definition": {"parts": parts_out}}, f, indent=2)
    print(f"Wrote {args.out}  ({len(parts_out)} parts)")

    # Brief summary on stdout
    entities = [p for p in parts_out if p["path"].startswith("EntityTypes/") and p["path"].endswith("/definition.json")]
    rels     = [p for p in parts_out if p["path"].startswith("RelationshipTypes/") and p["path"].endswith("/definition.json")]
    print(f"  entities:      {len(entities)}")
    for e in entities:
        print(f"    - {e['payload'].get('name')}")
    print(f"  relationships: {len(rels)}")
    for r in rels:
        print(f"    - {r['payload'].get('name')}")


if __name__ == "__main__":
    main()
