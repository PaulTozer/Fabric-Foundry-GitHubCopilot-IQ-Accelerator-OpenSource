"""list_test_cases — read the consultancy evaluation set from
`agents/tests/test_cases.csv` with optional persona/level/code filters.
"""
from __future__ import annotations

import csv
from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import config


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def list_test_cases(persona: str | None = None,
                        level: str | None = None,
                        code: str | None = None) -> dict[str, Any]:
        """Return rows from `agents/tests/test_cases.csv` (60 FM questions
        across 9 personas × 5 difficulty levels). Filters are case-insensitive
        substring matches.

        Args:
            persona: e.g. "Account Manager", "Bid Manager", "Operations Director",
                     "BMS/IoT Engineer", "Mobile Engineer", "Asset & Compliance".
            level:   "Basic", "Intermediate", "Hard", "Very Hard", or "Extreme".
            code:    exact code (e.g. "AC-VH01") to fetch a single question.
        """
        if not config.TEST_CASES_CSV.exists():
            return {"error": f"{config.TEST_CASES_CSV} not found.", "rows": []}

        with config.TEST_CASES_CSV.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        def _match(row: dict[str, str]) -> bool:
            if code and row.get("Code", "").lower() != code.lower():
                return False
            if persona and persona.lower() not in row.get("Persona", "").lower():
                return False
            if level and level.lower() != row.get("Level", "").lower():
                return False
            return True

        filtered = [r for r in rows if _match(r)]
        return {
            "totalAvailable": len(rows),
            "returned":       len(filtered),
            "rows":           filtered,
        }
