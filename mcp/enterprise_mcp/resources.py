"""MCP resources for the EnterpriseKG server."""
from __future__ import annotations

import csv
import json
import threading
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import config
from .tools.ontology import _fetch_ontology_definition

_ONTO_LOCK = threading.Lock()
_ONTO_CACHE: dict[str, Any] = {"fetched_at": 0.0, "doc": None}
_ONTO_TTL_SEC = 600  # 10 min


def _cached_ontology() -> dict[str, Any]:
    from fabric_connection import CURRENT
    if CURRENT.get():
        return _fetch_ontology_definition()
    now = time.time()
    with _ONTO_LOCK:
        if _ONTO_CACHE["doc"] is not None and now - _ONTO_CACHE["fetched_at"] < _ONTO_TTL_SEC:
            return _ONTO_CACHE["doc"]
        doc = _fetch_ontology_definition()
        _ONTO_CACHE.update({"fetched_at": now, "doc": doc})
        return doc


def _load_test_cases_rows() -> list[dict[str, str]]:
    if not config.TEST_CASES_CSV.exists():
        return []
    with config.TEST_CASES_CSV.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def register(mcp: FastMCP) -> None:

    @mcp.resource(
        "enterprise-iq://ontology/definition",
        name="EnterpriseKG Ontology Definition (live)",
        description=(
            "Live JSON dump of the EnterpriseKG_Ontology Fabric item: entities, "
            "relationships, table bindings, and key/property bindings."
        ),
        mime_type="application/json",
    )
    def ontology_definition() -> str:
        return json.dumps(_fetch_ontology_definition(), indent=2)

    @mcp.resource(
        "enterprise-iq://ontology/definition.cached",
        name="EnterpriseKG Ontology Definition (10-min cache)",
        description="Same as enterprise-iq://ontology/definition, in-process 10-min cache.",
        mime_type="application/json",
    )
    def ontology_definition_cached() -> str:
        return json.dumps(_cached_ontology(), indent=2)

    @mcp.resource(
        "enterprise-iq://test-cases.csv",
        name="EnterpriseKG consultancy evaluation set (CSV)",
        description="Raw text of agents/tests/test_cases.csv (60 FM questions × 9 personas).",
        mime_type="text/csv",
    )
    def test_cases_csv() -> str:
        if not config.TEST_CASES_CSV.exists():
            return ""
        return config.TEST_CASES_CSV.read_text(encoding="utf-8")

    @mcp.resource(
        "enterprise-iq://test-cases.json",
        name="EnterpriseKG consultancy evaluation set (JSON)",
        description="Parsed array of {Persona, Code, Theme, Question, Level}.",
        mime_type="application/json",
    )
    def test_cases_json() -> str:
        return json.dumps(_load_test_cases_rows(), indent=2)

    @mcp.resource(
        "enterprise-iq://test-cases/{code}",
        name="EnterpriseKG test case by Code",
        description="Fetch a single test case by its Code (e.g. AC-VH01).",
        mime_type="application/json",
    )
    def test_case_by_code(code: str) -> str:
        rows = _load_test_cases_rows()
        match = next((r for r in rows if r.get("Code", "").lower() == code.lower()), {})
        return json.dumps(match, indent=2)
