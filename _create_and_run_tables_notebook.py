"""Create a Fabric notebook in the 'Enterprise KG' workspace that bootstraps the 12
ENT_* Delta tables, attach it to EnterpriseKG_Lakehouse as the default lakehouse,
upload via the Fabric REST API, then trigger a RunNotebook job and poll until
it completes.

Required env vars:
    FABRIC_WORKSPACE_ID   GUID of the 'Enterprise KG' workspace
    FABRIC_LAKEHOUSE_ID   GUID of EnterpriseKG_Lakehouse

Run with:
    & "c:/python314/python.exe" .\_create_and_run_tables_notebook.py
"""
from __future__ import annotations
import base64, json, os, subprocess, sys, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from enterprise_schema import TABLES  # noqa: E402

WORKSPACE_ID   = os.environ["FABRIC_WORKSPACE_ID"]
LAKEHOUSE_ID   = os.environ["FABRIC_LAKEHOUSE_ID"]
LAKEHOUSE_NAME = os.environ.get("FABRIC_LAKEHOUSE_NAME", "EnterpriseKG_Lakehouse")
NOTEBOOK_NAME  = os.environ.get("NOTEBOOK_NAME", "Create_EnterpriseKG_Tables")


def _ddl_for(table_name: str, cols: list) -> str:
    lines = [f"CREATE TABLE IF NOT EXISTS {table_name.lower()} ("]
    col_lines = [f"  `{name}` {dtype}" for name, dtype in cols]
    lines.append(",\n".join(col_lines))
    lines.append(") USING DELTA")
    return "\n".join(lines)


cells = [
    {
        "cell_type": "markdown",
        "metadata":  {},
        "source": [
            "# EnterpriseKG Lakehouse — table bootstrap\n",
            "Creates the 12 ENT_* Delta tables in `EnterpriseKG_Lakehouse`.\n",
            "Safe to re-run (IF NOT EXISTS).",
        ],
    }
]
for name, cols in TABLES:
    ddl = _ddl_for(name, cols)
    cells.append({
        "cell_type": "code",
        "metadata":  {"language": "python"},
        "source": [f"spark.sql('''{ddl}''')"],
        "outputs": [],
        "execution_count": None,
    })

notebook = {
    "nbformat":       4,
    "nbformat_minor": 5,
    "cells": cells,
    "metadata": {
        "kernel_info":   {"name": "synapse_pyspark"},
        "kernelspec":    {"name": "synapse_pyspark", "display_name": "Synapse PySpark"},
        "language_info": {"name": "python"},
        "dependencies": {
            "lakehouse": {
                "default_lakehouse":              LAKEHOUSE_ID,
                "default_lakehouse_name":         LAKEHOUSE_NAME,
                "default_lakehouse_workspace_id": WORKSPACE_ID,
            }
        },
    },
}

nb_b64 = base64.b64encode(json.dumps(notebook).encode("utf-8")).decode("ascii")
definition = {
    "format": "ipynb",
    "parts": [
        {"path": "notebook-content.ipynb", "payload": nb_b64, "payloadType": "InlineBase64"}
    ],
}
create_body = {"displayName": NOTEBOOK_NAME, "definition": definition}


def az_token() -> str:
    out = subprocess.run(
        ["az", "account", "get-access-token", "--resource",
         "https://api.fabric.microsoft.com", "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, shell=True,
    )
    if out.returncode != 0:
        print("ERROR getting Fabric token:", out.stderr)
        sys.exit(1)
    return out.stdout.strip()


TOKEN = az_token()
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def http(method, url, body=None, expect_json=True):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=H, method=method)
    try:
        resp = urllib.request.urlopen(req)
        raw = resp.read()
        return resp.status, dict(resp.headers), (json.loads(raw) if raw and expect_json else raw)
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", errors="replace")
        return e.code, dict(e.headers), body_txt


# ── 1) check if notebook already exists ─────────────────────────────────────
list_url = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/notebooks"
status, headers, body = http("GET", list_url)
existing_id = None
if status == 200 and isinstance(body, dict):
    for item in body.get("value", []):
        if item.get("displayName") == NOTEBOOK_NAME:
            existing_id = item.get("id")
            break

if existing_id:
    print(f"Notebook '{NOTEBOOK_NAME}' already exists (id={existing_id}). Skipping create.")
    notebook_id = existing_id
else:
    # ── 2) create the notebook (async LRO) ──────────────────────────────────
    status, headers, body = http("POST", list_url, create_body)
    print("Create notebook status:", status)
    notebook_id = None

    if status in (200, 201) and isinstance(body, dict):
        notebook_id = body.get("id")
    elif status == 202:
        op_url = headers.get("Location") or headers.get("location")
        retry_after = int(headers.get("Retry-After") or headers.get("retry-after") or 5)
        print(f"  LRO polling {op_url}")
        state = None
        last_headers = headers
        for _ in range(60):
            time.sleep(retry_after)
            s2, h2, b2 = http("GET", op_url)
            last_headers = h2
            state = b2.get("status") if isinstance(b2, dict) else None
            print(f"  state={state}")
            if state in ("Succeeded", "Failed", "Canceled"):
                break
        if state != "Succeeded":
            print("Create failed:", b2)
            sys.exit(1)
        result_url = last_headers.get("Location") or last_headers.get("location")
        if result_url:
            s3, h3, b3 = http("GET", result_url)
            if isinstance(b3, dict):
                notebook_id = b3.get("id")
    else:
        print("Create body:", body)
        sys.exit(1)

    if not notebook_id:
        # fallback: re-list
        s, h, b = http("GET", list_url)
        if isinstance(b, dict):
            for item in b.get("value", []):
                if item.get("displayName") == NOTEBOOK_NAME:
                    notebook_id = item.get("id")
                    break

if not notebook_id:
    print("Could not resolve notebook id — aborting.")
    sys.exit(1)

print(f"Notebook id: {notebook_id}")

# ── 3) trigger the notebook run ─────────────────────────────────────────────
run_url = (f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}"
           f"/items/{notebook_id}/jobs/instances?jobType=RunNotebook")
status, headers, body = http("POST", run_url, {})
print("Run notebook status:", status)
job_loc = headers.get("Location") or headers.get("location")
print("Job location:", job_loc)
if not job_loc:
    print("No job location — body:", body)
    sys.exit(1)

# ── 4) poll until terminal ──────────────────────────────────────────────────
for i in range(120):
    time.sleep(5)
    s2, h2, b2 = http("GET", job_loc)
    state = b2.get("status") if isinstance(b2, dict) else None
    print(f"[{i:>3}] job state = {state}")
    if state in ("Completed", "Failed", "Cancelled", "Deduped"):
        print(json.dumps(b2, indent=2)[:2000])
        if state != "Completed":
            sys.exit(2)
        break

print("Done.")
