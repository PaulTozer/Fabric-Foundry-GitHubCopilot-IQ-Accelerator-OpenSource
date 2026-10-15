"""Resource IDs, env loading, and path helpers for the EnterpriseKG MCP server."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable


def _discover_repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "agents" / "tests" / "run_tests.py").is_file():
            return candidate
    return here.parents[2]


REPO_ROOT = _discover_repo_root()
AGENTS_DIR = REPO_ROOT / "agents"
TESTS_DIR = AGENTS_DIR / "tests"
TEST_CASES_CSV = TESTS_DIR / "test_cases.csv"

_PATH_ADDITIONS = [str(TESTS_DIR), str(REPO_ROOT)]
for p in _PATH_ADDITIONS:
    if p not in sys.path:
        sys.path.insert(0, p)


_PLACEHOLDER = ""


def _id_from_env(name: str) -> str:
    return os.environ.get(name, _PLACEHOLDER)


WORKSPACE_ID = _id_from_env("FABRIC_WORKSPACE_ID")
LAKEHOUSE_ID = _id_from_env("FABRIC_LAKEHOUSE_ID")
ONTOLOGY_ID  = _id_from_env("FABRIC_ONTOLOGY_ID")

FABRIC_API = "https://api.fabric.microsoft.com"
FABRIC_TOKEN_RESOURCE = "https://api.fabric.microsoft.com"
SQL_TOKEN_RESOURCE = "https://database.windows.net/.default"


def load_dotenv() -> None:
    candidates = [
        TESTS_DIR / ".env",
        AGENTS_DIR / ".env",
        REPO_ROOT / ".env",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            for raw in p.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
        except OSError:
            continue


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def require_env(*names: str) -> dict[str, str]:
    missing: list[str] = []
    out: dict[str, str] = {}
    for n in names:
        v = os.environ.get(n)
        if not v:
            missing.append(n)
        else:
            out[n] = v
    if missing:
        raise RuntimeError(
            f"Missing required env vars: {', '.join(missing)}. "
            f"Set them in agents/tests/.env or the process environment."
        )
    return out


def env_summary(keys: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in keys:
        v = os.environ.get(k)
        if v is None:
            out[k] = "(unset)"
        elif any(s in k.lower() for s in ("token", "secret", "key", "password")):
            out[k] = f"(set, len={len(v)})"
        else:
            out[k] = v
    return out
