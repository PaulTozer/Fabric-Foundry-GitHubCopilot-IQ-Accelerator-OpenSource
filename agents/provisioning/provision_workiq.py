"""
EnterpriseKG Work IQ provisioner (Method 7).

Wires the Foundry project to **Microsoft 365 Work IQ** (preview) so the M7
agent can ground answers in the signed-in user's email, calendar, OneDrive,
SharePoint, Teams, and people graph via the Foundry Agent Service's hosted
`work_iq_preview` tool.

See https://learn.microsoft.com/azure/foundry/agents/how-to/tools/work-iq

Architecture:

    Foundry agent  enterprise-kg-m7-workiq  (kind = prompt)
        +-- type: work_iq_preview
              project_connection_id = enterprise-kg-workiq-connection

    Project connection (RemoteA2A, OAuth2)
              target   = https://workiq.svc.cloud.microsoft/a2a/
              authType = OAuth2
              ClientId / ClientSecret = your Entra app reg
              scopes   = api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask,
                         offline_access
              OBO chain caller-user-identity -> WorkIQAgent.Ask

Manual one-off prerequisites (Global Administrator only — see
infra/README.md "Work IQ (M7) tenant setup"):

  1. Provision the Work IQ service principal in your tenant (Graph Explorer).
    2. Register an Entra app (e.g. "EnterpriseKG-WorkIQ"), single-tenant.
  3. Add Delegated permission WorkIQAgent.Ask on API "Work IQ"
     (app id fdcc1f02-fc51-4226-8753-f668596af7f7) and grant admin consent.
  4. Mint a client secret.
  5. Add the OAuth redirect URI Foundry returns after this script creates
     the connection (we print it).
  6. Confirm every user who will invoke M7 has a Microsoft 365 Copilot
     licence assigned.

Run order:
    python provision_workiq.py                  # connection + agent + env
    python provision_workiq.py --steps conn     # one of: conn agent
    python provision_workiq.py --steps agent
    python provision_workiq.py --print-env      # update .env only

Auth: DefaultAzureCredential (run ``az login`` first). Acquires:
  * https://management.azure.com/.default -> ARM connection PUT
  * https://ai.azure.com/.default         -> Foundry agent POST

Required env (read from agents/tests/.env or the shell):
  AZURE_SUBSCRIPTION_ID, FOUNDRY_RESOURCE_GROUP, FOUNDRY_ACCOUNT_NAME,
  AZURE_TENANT_ID, WORKIQ_CLIENT_ID, WORKIQ_CLIENT_SECRET

Optional overrides:
  FOUNDRY_PROJECT_NAME       default EnterpriseKG
    MODEL_DEPLOYMENT_NAME      default gpt-5.4
  WORKIQ_CONNECTION_NAME     default enterprise-kg-workiq-connection
  M7_AGENT_NAME              default enterprise-kg-m7-workiq
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Tiny env loader (match the rest of the repo — no python-dotenv hard dep)
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_ENV_FILE = _THIS_DIR.parent / "tests" / ".env"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_env_file(_ENV_FILE)


def _required_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise SystemExit(
            f"ERROR: required env var {key!r} is not set. "
            f"Add it to agents/tests/.env (see agents/tests/.env.example) "
            f"or export it in the shell before running."
        )
    return val


# ---------------------------------------------------------------------------
# Configuration (sourced from env; do NOT hardcode tenant IDs or secrets)
# ---------------------------------------------------------------------------
SUBSCRIPTION_ID    = _required_env("AZURE_SUBSCRIPTION_ID")
RESOURCE_GROUP     = _required_env("FOUNDRY_RESOURCE_GROUP")
ACCOUNT_NAME       = _required_env("FOUNDRY_ACCOUNT_NAME")
PROJECT_NAME       = os.environ.get("FOUNDRY_PROJECT_NAME", "EnterpriseKG")
PROJECT_ENDPOINT   = os.environ.get(
    "PROJECT_ENDPOINT",
    f"https://{ACCOUNT_NAME.lower()}.services.ai.azure.com/api/projects/{PROJECT_NAME}",
)
MODEL_DEPLOYMENT   = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-5.4")

CONNECTION_API_VERSION = "2025-04-01-preview"
AGENTS_API_VERSION     = "v1"

DEFAULT_CONN_NAME     = "enterprise-kg-workiq-connection"
DEFAULT_M7_AGENT_NAME = "enterprise-kg-m7-workiq"
WORKIQ_A2A_TARGET     = "https://workiq.svc.cloud.microsoft/a2a/"
WORKIQ_SCOPES         = [
    "api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask",
    "offline_access",
]

PROJECT_RESOURCE_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}"
    f"/resourceGroups/{RESOURCE_GROUP}"
    f"/providers/Microsoft.CognitiveServices/accounts/{ACCOUNT_NAME}"
    f"/projects/{PROJECT_NAME}"
)

HERE             = Path(__file__).resolve().parent
AGENTS_DIR       = HERE.parent
ENV_PATH         = AGENTS_DIR / "tests" / ".env"
M7_INSTRUCTIONS  = AGENTS_DIR / "Method7_WorkIQ_Instructions.md"


# ---------------------------------------------------------------------------
# Config resolver — collects everything the steps below need
# ---------------------------------------------------------------------------
def _resolve_config() -> dict[str, Any]:
    tenant_id     = _required_env("AZURE_TENANT_ID")
    client_id     = _required_env("WORKIQ_CLIENT_ID")
    client_secret = _required_env("WORKIQ_CLIENT_SECRET")
    return {
        "tenant_id":     tenant_id,
        "client_id":     client_id,
        "client_secret": client_secret,
        "conn_name":     os.environ.get("WORKIQ_CONNECTION_NAME", DEFAULT_CONN_NAME),
        "m7_agent_name": os.environ.get("M7_AGENT_NAME", DEFAULT_M7_AGENT_NAME),
        "model":         os.environ.get("MODEL_DEPLOYMENT_NAME", MODEL_DEPLOYMENT),
        "target":        WORKIQ_A2A_TARGET,
        "scopes":        WORKIQ_SCOPES,
        "auth_url":      f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize",
        "token_url":     f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
    }


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _http():
    try:
        import requests
        return requests
    except ImportError:
        sys.exit("ERROR: requests not installed. pip install requests")


def _token(scope: str) -> str:
    cache = _token.__dict__.setdefault("_cache", {})
    if scope in cache:
        return cache[scope]
    from azure.identity import DefaultAzureCredential
    cred = DefaultAzureCredential()
    tok = cred.get_token(scope).token
    cache[scope] = tok
    return tok


def _check(resp, label: str) -> dict:
    if resp.status_code >= 400:
        msg = (resp.text or "")[:1500]
        sys.exit(
            f"\nERROR: {label} failed with HTTP {resp.status_code}\n"
            f"  URL:  {resp.request.url}\n"
            f"  Body: {msg}\n"
        )
    if not resp.text:
        return {}
    try:
        return resp.json()
    except Exception:
        return {"_raw": resp.text[:500]}


# ---------------------------------------------------------------------------
# Step 1 — Foundry RemoteA2A project connection (ARM, OAuth2)
# ---------------------------------------------------------------------------
def step_connection(cfg: dict) -> dict:
    requests = _http()
    url = (
        f"https://management.azure.com{PROJECT_RESOURCE_ID}"
        f"/connections/{cfg['conn_name']}?api-version={CONNECTION_API_VERSION}"
    )
    body = {
        "name": cfg["conn_name"],
        "type": "Microsoft.MachineLearningServices/workspaces/connections",
        "properties": {
            "authType":         "OAuth2",
            "group":            "ServicesAndApps",
            "category":         "RemoteA2A",
            "target":           cfg["target"],
            "isSharedToAll":    True,
            "sharedUserList":   [],
            "TokenUrl":         cfg["token_url"],
            "AuthorizationUrl": cfg["auth_url"],
            "RefreshUrl":       cfg["token_url"],
            "Scopes":           cfg["scopes"],
            "Credentials": {
                "ClientId":     cfg["client_id"],
                "ClientSecret": cfg["client_secret"],
            },
            "metadata": {"ApiType": "Azure"},
        },
    }
    arm_token = _token("https://management.azure.com/.default")
    print(f"[con] PUT {url}")
    r = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {arm_token}",
            "Content-Type":  "application/json",
        },
        json=body,
        timeout=60,
    )
    body_out = _check(r, "Create Work IQ A2A connection")

    redirect = (
        (body_out.get("properties") or {}).get("oauthRedirectUrl") or "<not returned>"
    )
    print(f"[con] OK   {cfg['conn_name']} -> {cfg['target']}")
    print(f"[con] OAuth redirect URI to add to your Entra app registration:")
    print(f"        {redirect}")
    return body_out


# ---------------------------------------------------------------------------
# Step 2 — Foundry M7 agent (prompt agent with work_iq_preview tool)
# ---------------------------------------------------------------------------
def _load_m7_instructions() -> str:
    if not M7_INSTRUCTIONS.exists():
        sys.exit(
            f"ERROR: {M7_INSTRUCTIONS} not found. The M7 instructions MD must "
            "exist before provisioning the agent."
        )
    return M7_INSTRUCTIONS.read_text(encoding="utf-8")


def step_agent(cfg: dict) -> dict:
    requests = _http()
    create_url  = f"{PROJECT_ENDPOINT}/agents?api-version={AGENTS_API_VERSION}"
    version_url = (
        f"{PROJECT_ENDPOINT}/agents/{cfg['m7_agent_name']}/versions"
        f"?api-version={AGENTS_API_VERSION}"
    )
    instructions = _load_m7_instructions()

    # Foundry expects the full ARM resource id of the connection here.
    connection_resource_id = (
        f"{PROJECT_RESOURCE_ID}/connections/{cfg['conn_name']}"
    )

    body = {
        "name": cfg["m7_agent_name"],
        "definition": {
            "model":        cfg["model"],
            "instructions": instructions,
            "tools": [{
                "type":                  "work_iq_preview",
                "project_connection_id": connection_resource_id,
            }],
            "kind": "prompt",
        },
    }
    foundry_token = _token("https://ai.azure.com/.default")
    headers = {
        "Authorization": f"Bearer {foundry_token}",
        "Content-Type":  "application/json",
    }

    # Create the agent; if it already exists (409), post a new version so
    # re-running this script refreshes the instructions / tool wiring.
    print(f"[agt] POST {create_url}")
    r = requests.post(create_url, headers=headers, json=body, timeout=60)
    if r.status_code == 409:
        print(f"[agt] exists -> POST {version_url}")
        version_body = {"definition": body["definition"]}
        r = requests.post(version_url, headers=headers, json=version_body, timeout=60)
        body_out = _check(r, "Create new version of M7 agent")
    else:
        body_out = _check(r, "Create M7 agent")
    print(
        f"[agt] OK   {cfg['m7_agent_name']} "
        f"version={body_out.get('version', '?')} id={body_out.get('id', '?')}"
    )
    return body_out


# ---------------------------------------------------------------------------
# Env writer — update agents/tests/.env (idempotent)
# ---------------------------------------------------------------------------
def write_env(cfg: dict) -> None:
    """Replace any pre-existing M7 keys; append a section header for new ones."""
    desired = {
        "M7_AGENT_NAME":          cfg["m7_agent_name"],
        "WORKIQ_CONNECTION_NAME": cfg["conn_name"],
    }
    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        replaced = False
        for k in desired:
            if line.startswith(f"{k}="):
                out.append(f"{k}={desired[k]}")
                seen.add(k)
                replaced = True
                break
        if not replaced:
            out.append(line)

    missing = [k for k in desired if k not in seen]
    if missing:
        if out and out[-1].strip():
            out.append("")
        out.append("# --- Work IQ (provision_workiq.py) ---")
        for k in missing:
            out.append(f"{k}={desired[k]}")

    while out and not out[-1].strip():
        out.pop()
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[env] wrote {ENV_PATH}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ALL_STEPS = ("conn", "agent")
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--steps", nargs="+", choices=ALL_STEPS, default=list(ALL_STEPS),
                   help="Subset of provisioning steps to run (default: all).")
    p.add_argument("--print-env", action="store_true",
                   help="Only update agents/tests/.env (no Foundry calls).")
    args = p.parse_args()

    cfg = _resolve_config()

    if args.print_env:
        write_env(cfg)
        return 0

    print("\nProvisioning Work IQ (M7) wiring")
    print(f"  Tenant      : {cfg['tenant_id']}")
    print(f"  Connection  : {cfg['conn_name']}  -> {cfg['target']}")
    print(f"  M7 agent    : {cfg['m7_agent_name']}")
    print(f"  Model       : {cfg['model']}")
    print(f"  Steps       : {' '.join(args.steps)}\n")

    if "conn" in args.steps:
        step_connection(cfg)
    if "agent" in args.steps:
        step_agent(cfg)

    write_env(cfg)
    print(
        "\nNext:\n"
        "  1. Copy the OAuth redirect URI from the [con] output above and add\n"
        "     it as a 'Web' platform redirect URI on your Entra app registration.\n"
        "  2. Confirm every user has a Microsoft 365 Copilot licence assigned.\n"
        "  3. Smoke test from the WebUI: select 'M7 \u00b7 Work IQ' in the\n"
        "     settings cog, then ask 'Summarise my recent emails about\n"
        "     <a project you actually work on>.'\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
