"""Inject the EnterpriseKG_Lakehouse default-lakehouse binding into
_load_demo_data.ipynb, upload it as a Notebook item in the 'Enterprise KG'
workspace, and trigger a RunNotebook job. Polls until terminal.

Required env vars:
    FABRIC_WORKSPACE_ID   GUID of the 'Enterprise KG' workspace
    FABRIC_LAKEHOUSE_ID   GUID of EnterpriseKG_Lakehouse
"""
from __future__ import annotations
import base64, json, os, subprocess, sys, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))

WORKSPACE_ID   = os.environ["FABRIC_WORKSPACE_ID"]
LAKEHOUSE_ID   = os.environ["FABRIC_LAKEHOUSE_ID"]
LAKEHOUSE_NAME = os.environ.get("FABRIC_LAKEHOUSE_NAME", "EnterpriseKG_Lakehouse")
NOTEBOOK_NAME  = os.environ.get("NOTEBOOK_NAME", "Load_EnterpriseKG_DemoData")
NB_PATH        = os.environ.get("NOTEBOOK_PATH", os.path.join(ROOT, "_load_demo_data.ipynb"))

with open(NB_PATH, "r", encoding="utf-8") as f:
    notebook = json.load(f)

# Inject Fabric lakehouse dependency + kernel info
notebook.setdefault("metadata", {})
notebook["metadata"].setdefault("kernel_info",   {"name": "synapse_pyspark"})
notebook["metadata"].setdefault("kernelspec",    {"name": "synapse_pyspark",
                                                  "display_name": "Synapse PySpark"})
notebook["metadata"].setdefault("language_info", {"name": "python"})
notebook["metadata"]["dependencies"] = {
    "lakehouse": {
        "default_lakehouse":              LAKEHOUSE_ID,
        "default_lakehouse_name":         LAKEHOUSE_NAME,
        "default_lakehouse_workspace_id": WORKSPACE_ID,
    }
}

nb_bytes = json.dumps(notebook).encode("utf-8")
nb_b64 = base64.b64encode(nb_bytes).decode("ascii")
print(f"Notebook source size: {len(nb_bytes)/1024/1024:.2f} MiB")
print(f"Base64 payload size:  {len(nb_b64)/1024/1024:.2f} MiB")

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
notebook_id = None
if status == 200 and isinstance(body, dict):
    for item in body.get("value", []):
        if item.get("displayName") == NOTEBOOK_NAME:
            notebook_id = item.get("id")
            print(f"Notebook '{NOTEBOOK_NAME}' already exists (id={notebook_id}).")
            # Replace its definition so it picks up the latest payload
            update_url = (f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}"
                          f"/notebooks/{notebook_id}/updateDefinition")
            s_u, h_u, b_u = http("POST", update_url, {"definition": definition})
            print("Update definition status:", s_u)
            if s_u == 202:
                op_url = h_u.get("Location") or h_u.get("location")
                retry_after = int(h_u.get("Retry-After") or 5)
                print(f"  LRO polling {op_url}")
                for _ in range(60):
                    time.sleep(retry_after)
                    s2, h2, b2 = http("GET", op_url)
                    state = b2.get("status") if isinstance(b2, dict) else None
                    print(f"  state={state}")
                    if state in ("Succeeded", "Failed", "Canceled"):
                        if state != "Succeeded":
                            print("UpdateDefinition failed:", b2); sys.exit(1)
                        break
            elif s_u not in (200, 201):
                print("UpdateDefinition body:", b_u); sys.exit(1)
            break

if notebook_id is None:
    # ── 2) create the notebook (async LRO) ──────────────────────────────────
    status, headers, body = http("POST", list_url, create_body)
    print("Create notebook status:", status)
    if status in (200, 201) and isinstance(body, dict):
        notebook_id = body.get("id")
    elif status == 202:
        op_url = headers.get("Location") or headers.get("location")
        retry_after = int(headers.get("Retry-After") or 5)
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
            print("Create failed:", b2); sys.exit(1)
        result_url = last_headers.get("Location") or last_headers.get("location")
        if result_url:
            s3, h3, b3 = http("GET", result_url)
            if isinstance(b3, dict):
                notebook_id = b3.get("id")
    else:
        print("Create body:", body); sys.exit(1)

    if not notebook_id:
        s, h, b = http("GET", list_url)
        if isinstance(b, dict):
            for item in b.get("value", []):
                if item.get("displayName") == NOTEBOOK_NAME:
                    notebook_id = item.get("id"); break

if not notebook_id:
    print("Could not resolve notebook id — aborting."); sys.exit(1)

print(f"Notebook id: {notebook_id}")

# ── 3) trigger the notebook run ─────────────────────────────────────────────
run_url = (f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}"
           f"/items/{notebook_id}/jobs/instances?jobType=RunNotebook")
status, headers, body = http("POST", run_url, {})
print("Run notebook status:", status)
job_loc = headers.get("Location") or headers.get("location")
print("Job location:", job_loc)
if not job_loc:
    print("No job location — body:", body); sys.exit(1)

# ── 4) poll until terminal (data load can take many minutes) ────────────────
for i in range(360):
    time.sleep(10)
    s2, h2, b2 = http("GET", job_loc)
    state = b2.get("status") if isinstance(b2, dict) else None
    print(f"[{i:>3}] job state = {state}")
    if state in ("Completed", "Failed", "Cancelled", "Deduped"):
        print(json.dumps(b2, indent=2)[:3000])
        if state != "Completed":
            sys.exit(2)
        break

print("Done.")
