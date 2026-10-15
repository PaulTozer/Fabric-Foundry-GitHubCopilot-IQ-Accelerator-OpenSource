"""Shared Fabric tools and response helpers used by Copilot SDK and M6."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

# Shared Fabric Ontology MCP client — also used by M3 dispatcher in run_tests.
from fabric_ontology import (  # type: ignore[import-not-found]
    query_ontology as _query_ontology,
)

# Reuse the M3 SQL handler — read-only, deny-listed, capped at 200 rows.
from m3_sql_handler import execute_sql  # type: ignore[import-not-found]

# ─── Tunables ──────────────────────────────────────────────────────────────
MAX_TURNS              = 16        # outer responses-loop iterations
MAX_SQL_ATTEMPTS       = 8         # query_lakehouse calls per question
MAX_ONTOLOGY_ATTEMPTS  = 3         # query_ontology calls per question
MAX_CRITIQUE_RETRIES   = 2         # re-engage budget after a refusal
MAX_EMPTY_NUDGES       = 2         # retries when model returns empty (no msg, no call)
MAX_TOOL_OUTPUT_CHARS  = 30_000    # truncation guard for tool payloads
SAMPLE_VALUES_LIMIT    = 50

# ─── Direct Fabric Ontology MCP (no Search KB wrapper) ────────────────────
# Per https://learn.microsoft.com/fabric/iq/ontology/how-to-use-ontology-mcp-server
# (Endpoint config + helper functions live in fabric_ontology.py so the M3
#  dispatcher in run_tests.py can call the same code path.)

_REFUSAL_PATTERNS = (
    r"i couldn'?t",
    r"could not retrieve",
    r"technical issue",
    r"capacity[- ]limit",
    r"capacity exceeded",
    r"no matches in the current data",
    r"no matching .{0,30} (in the current|found|returned)",
    r"unable to (retrieve|return)",
    r"please try again shortly",
    r"deferred rather than executed",
)
_REFUSAL_REGEX = re.compile("|".join(_REFUSAL_PATTERNS), re.IGNORECASE)


# ─── System prompt — extracted from the markdown spec ──────────────────────
_DOCS_PATH = Path(__file__).resolve().parents[1] / "CopilotSDK_Instructions.md"


def _load_system_prompt() -> str:
    """Read the M4 instructions MD and return everything below the
    'System prompt (verbatim — used by the runner)' header.
    Falls back to the whole file if the marker isn't found."""
    if not _DOCS_PATH.exists():
        return _FALLBACK_SYSTEM_PROMPT
    text = _DOCS_PATH.read_text(encoding="utf-8")
    marker = "## System prompt (verbatim"
    idx = text.find(marker)
    if idx < 0:
        return text
    body = text[idx:]
    # Strip the "## System prompt..." header line and any blockquote intro.
    body = re.sub(r"^## System prompt.*?\n+", "", body, count=1, flags=re.DOTALL)
    body = re.sub(r"^> \*[^\n]*\n+", "", body, count=1)
    return body.strip()


_FALLBACK_SYSTEM_PROMPT = (
    "You are the EnterpriseKG analyst. Use the query_lakehouse tool to answer "
    "questions about Example Enterprise staff, projects, clients, capability, and "
    "finances, then call final_answer. Never invent rows. If a query "
    "returns zero rows, broaden the filter and retry before answering."
)


_SYSTEM_PROMPT = _load_system_prompt()

_ONTOLOGY_DISABLED_NOTE = (
    "\n\n---\n"
    "RUNTIME OVERRIDE: the `query_ontology` tool is DISABLED for this run "
    "(operator toggle in the WebUI). It has been removed from your tool "
    "list — do not attempt to call it. Skip the 'try ontology first' rule "
    "and answer the question using `schema_card`, `sample_values`, and "
    "`query_lakehouse` only."
)


def _build_instructions(ontology_enabled: bool) -> str:
    """Return the system prompt, optionally appending an override note when
    the ontology tool is disabled for this run."""
    if ontology_enabled:
        return _SYSTEM_PROMPT
    return _SYSTEM_PROMPT + _ONTOLOGY_DISABLED_NOTE


def _tools_for(ontology_enabled: bool) -> list[dict[str, Any]]:
    """Return the tool schema list filtered to drop `query_ontology` when
    the operator has toggled the ontology off."""
    from fabric_connection import CURRENT
    from fabric_readiness import fabric_readiness
    selected = CURRENT.get()
    readiness = fabric_readiness() if selected else {"sql": True, "ontology": True}
    return [tool for tool in _TOOL_SCHEMAS
            if (tool["name"] != "query_ontology" or (ontology_enabled and readiness["ontology"]))
            and (tool["name"] not in {"schema_card", "sample_values", "query_lakehouse"} or readiness["sql"])]


# ─── Tool schemas (Responses-API function-tool format) ─────────────────────
_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "schema_card",
        "description": (
            "Return the compact EnterpriseKG_Lakehouse schema card (entities, "
            "columns, types, enums, key constraints). The card is also "
            "included in your system prompt — call this only if you need a "
            "fresh copy mid-execution."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "type": "function",
        "name": "sample_values",
        "description": (
            "Return up to 50 distinct non-null values from one column of one "
            "ENT_* table. Use this to discover what literal strings are "
            "actually in the data before putting one in a WHERE clause "
            "(e.g. SectorName, ClientSectorName, RoleName)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table":       {"type": "string", "description": "ENT_* table name (no schema prefix)."},
                "column":      {"type": "string", "description": "Column to sample."},
                "like_filter": {"type": "string", "description": "Optional substring filter (no %s — added automatically)."},
            },
            "required": ["table", "column"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "query_ontology",
        "description": (
            "★ PRIMARY TOOL — try this BEFORE writing any SQL. Ask the EnterpriseKG "
            "Fabric ontology a natural-language question by calling the public "
            "Fabric Ontology MCP server's `search_ontology` tool directly "
            "(documented at https://learn.microsoft.com/fabric/iq/ontology/how-to-use-ontology-mcp-server). "
            "The ontology already encodes the joins between Staff, Expertise, "
            "StaffExpertise, SkillsNetwork, Project, and Client — so use this "
            "for any business question that names those entities, including "
            "filters like 'in the Transport sector', 'for client X', 'reports "
            "to Y', 'holds the BIM Coordinator role'. Returns the natural-"
            "language `answer` plus `raw` (the JSON rows the ontology "
            "returned). Capped at 3 calls per question. If Fabric returns "
            "'Failed to translate NL query', reword once or fall back to "
            "query_lakehouse. If Fabric returns 'Request is blocked by the "
            "upstream service', record the unblock time in your final_answer "
            "caveats and stop calling."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question":  {"type": "string",
                               "description": "Natural-language question. Pass the user's words; Fabric does NL→ontology translation server-side."},
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "query_lakehouse",
        "description": (
            "FALLBACK tool. Use ONLY when query_ontology can't answer. Execute "
            "T-SQL against the EnterpriseKG_Lakehouse SQL endpoint. Read-only — "
            "INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/MERGE/TRUNCATE/GRANT/"
            "REVOKE/EXEC are rejected. Returns at most 200 rows "
            "(`truncated=true` if more were available). You have a maximum of "
            "8 such calls per question. Use this for: aggregations (SUM/COUNT/"
            "percentile/window), fee and margin analysis, latest financial "
            "snapshot per project, per-discipline headcount from "
            "ENT_ProjectDisciplineStatistics, and "
            "any case where the ontology returned a translation failure."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql":     {"type": "string",
                            "description": "Valid T-SQL. Use TOP not LIMIT, "
                                           "[Current] for that reserved word, "
                                           "TRY_CAST for varchar→numeric."},
                "purpose": {"type": "string",
                            "description": "One-line annotation of what this "
                                           "query is for. Required. Logged in "
                                           "the trace."},
            },
            "required": ["sql", "purpose"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "simulate_crowd_flow",
        "description": (
            "Run a pedestrian-dynamics evacuation simulation (JuPedSim's "
            "CollisionFreeSpeedModel). Use this for engineering questions "
            "about crowd movement that ADS data cannot answer — "
            "evacuation times, where queues form, two-exit vs one-exit "
            "comparisons, peak crowd density. "
            "\n\nIMPORTANT: when the user describes a SPECIFIC geometry "
            "(e.g. '15 m × 15 m lobby with a 2 m door and 1,000 people', "
            "or an attached floor-plan image), build a `custom_scenario` "
            "JSON dict for those exact dimensions and pass it. DO NOT pick "
            "a pre-canned scenario whose width is different and then "
            "width-scale the result by hand — always re-run the simulation "
            "on the real geometry so the density heatmap and egress curve "
            "reflect what the user actually asked about. "
            "\n\nThree pre-canned scenarios remain available for the common "
            "shapes: `lobby_evacuation` (20m × 15m, one 3m south exit), "
            "`corridor_flow` (30m × 3m, east exit), `two_exit_hall` "
            "(25m × 25m, two 2m exits). "
            "\n\nReturns total egress time, peak density (people/m²), "
            "per-exit throughput, and an egress curve (list of "
            "[t, agents_remaining]). The egress curve and density heatmap "
            "PNG plots are rendered automatically and appended to your "
            "final answer server-side — DO NOT try to embed them yourself. "
            "Your job is to describe the numerical results and explain "
            "what the user will see in the plots."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scenario": {
                    "type": "string",
                    "enum": ["lobby_evacuation", "corridor_flow", "two_exit_hall"],
                    "description": (
                        "Pre-canned scenario name. Use ONLY when the "
                        "user's dimensions match a pre-canned shape; "
                        "otherwise pass `custom_scenario` instead."
                    ),
                },
                "custom_scenario": {
                    "type": "object",
                    "description": (
                        "Bespoke geometry for the user's actual room. "
                        "Mutually exclusive with `scenario` — supply one or "
                        "the other. All coordinates are in metres."
                    ),
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Short label used in plot titles.",
                        },
                        "summary": {
                            "type": "string",
                            "description": "One-line human description.",
                        },
                        "walkable": {
                            "type": "array",
                            "description": (
                                "Outer walkable polygon as [[x,y], …], "
                                "min 3 vertices, in metres. For a rectangular "
                                "room WxH use [[0,0],[W,0],[W,H],[0,H]]."
                            ),
                            "items": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 2, "maxItems": 2,
                            },
                            "minItems": 3,
                        },
                        "spawn_area": {
                            "type": "array",
                            "description": (
                                "Polygon where agents start, in metres. "
                                "Must lie INSIDE `walkable` — inset by ~0.5 m "
                                "from each wall so agents don't spawn clipping."
                            ),
                            "items": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 2, "maxItems": 2,
                            },
                            "minItems": 3,
                        },
                        "exits": {
                            "type": "array",
                            "description": (
                                "List of exit polygons. Each is a thin "
                                "rectangle hugging the wall it punches "
                                "through, e.g. for a 2 m door centred on "
                                "the south wall of a 15 m room: "
                                "[[6.5,0],[8.5,0],[8.5,0.3],[6.5,0.3]]."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label":   {"type": "string"},
                                    "polygon": {
                                        "type": "array",
                                        "items": {
                                            "type": "array",
                                            "items": {"type": "number"},
                                            "minItems": 2, "maxItems": 2,
                                        },
                                        "minItems": 3,
                                    },
                                },
                                "required": ["label", "polygon"],
                            },
                            "minItems": 1,
                        },
                    },
                    "required": ["name", "walkable", "spawn_area", "exits"],
                },
                "agent_count": {
                    "type": "integer",
                    "description": (
                        "Number of pedestrians, 1–2000 (default 50). "
                        "Use the exact figure the user asked for — do "
                        "NOT silently downscale a 1,000-person evacuation "
                        "to fit a smaller default."
                    ),
                },
                "max_time_sec": {
                    "type": "number",
                    "description": (
                        "Cap on sim duration in seconds, 5–900 (default 120). "
                        "Pick generously: a ~1.8 m door clears roughly "
                        "1.5 people/sec, so 1,000 people through one door "
                        "needs ~700 s + headroom — raise this above the "
                        "default for large crowds or narrow exits."
                    ),
                },
                "seed": {
                    "type": "integer",
                    "description": "RNG seed for spawn placement (default 1). Same seed + same args = deterministic numerics.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "final_answer",
        "description": (
            "Terminal — call this exactly once, with the user-facing answer. "
            "Do not output free text after calling final_answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "markdown": {
                    "type": "string",
                    "description": "User-facing answer: one-line summary, "
                                   "then a markdown table.",
                },
                "scope_filters": {
                    "type": "string",
                    "description": "Concise prose listing the filters applied "
                                   "(e.g. 'Active projects; sector = Transport; "
                                   "started within last 24 months').",
                },
                "caveats": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Caveats — always include the mixed-currency "
                                   "caveat for TotalInvoiced answers and the "
                                   "no-numeric-expertise-level caveat for any "
                                   "level-N question.",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "high = all criteria grounded; medium = one "
                                   "criterion substituted; low = partial result.",
                },
                "queries_run": {
                    "type": "integer",
                    "description": "Count of query_lakehouse calls you made.",
                },
            },
            "required": ["markdown", "scope_filters", "caveats",
                          "confidence", "queries_run"],
            "additionalProperties": False,
        },
    },
]


# ─── Helpers ───────────────────────────────────────────────────────────────
_VALID_TABLES = {
    "ENT_Staff",
    "ENT_Capability",
    "ENT_Skill",
    "ENT_StaffCapability",
    "ENT_StaffSkill",
    "ENT_PracticeNetworkMembers",
    "ENT_Client",
    "ENT_Programme",
    "ENT_Project",
    "ENT_ProjectStaff",
    "ENT_ProjectFinancialCumulative",
    "ENT_ProjectDisciplineStatistics",
}


def _sample_values(table: str, column: str, like_filter: str = "") -> dict[str, Any]:
    """Run a SELECT DISTINCT TOP N on one column. Validates table name to keep
    user-supplied strings out of the SQL surface."""
    if table not in _VALID_TABLES:
        return {"error": f"Unknown table '{table}'. Valid: {sorted(_VALID_TABLES)}"}
    # Column name validation — alphanumerics, underscore, brackets only.
    if not re.fullmatch(r"\[?[A-Za-z_][A-Za-z0-9_]*\]?", column or ""):
        return {"error": f"Invalid column name '{column}'."}
    col = column if column.startswith("[") else f"[{column}]"
    if like_filter:
        # Escape single quotes by doubling.
        safe = like_filter.replace("'", "''")
        where = f"WHERE {col} IS NOT NULL AND {col} LIKE '%{safe}%'"
    else:
        where = f"WHERE {col} IS NOT NULL"
    sql = (
        f"SELECT TOP {SAMPLE_VALUES_LIMIT} {col} AS value, COUNT(*) AS occurrences "
        f"FROM {table} {where} "
        f"GROUP BY {col} ORDER BY occurrences DESC"
    )
    raw = execute_sql(sql)
    if "error" in raw:
        return raw
    return {
        "table":       table,
        "column":      column,
        "like_filter": like_filter or None,
        "values":      [r[0] for r in raw.get("rows", [])],
        "occurrences": [r[1] for r in raw.get("rows", [])],
        "row_count":   raw.get("row_count", 0),
    }


def _schema_card_payload() -> dict[str, Any]:
    """Return a compact, programmatic schema card for the EnterpriseKG_Lakehouse."""
    return {
        "tables": [
            {"name": "ENT_Staff", "rows": 900, "key": "StaffID",
              "label": "StaffName",
              "columns": ["StaffID","StaffUPN","StaffName","StaffStatus","Email",
                          "OfficeCode","OfficeName","CountryName","RegionName",
                          "PracticeName","DisciplineName","Grade","JobTitle",
                          "StartDate","LeaveDate","ManagerID","ManagerName",
                          "ManagerEmail","ReportsToUPN","MyPeoplePageURL"],
              "notes": ["StaffStatus IN ('Active','Leave','Secondment')",
                        "PracticeName IN ('Advisory','Buildings','Digital','Energy','Environment','Programme Management','Transport','Water')",
                        "line manager = ManagerName; a person's reports = WHERE ManagerID = <id>"]},
            {"name": "ENT_Capability", "rows": 10, "key": "CapabilityID",
              "label": "CapabilityName",
              "columns": ["CapabilityID","CapabilityCode","CapabilityName","MarketName",
                          "TechnicalArea","StrategicTheme","Billable"],
              "notes": ["MarketName IN ('Buildings','Energy','Environment','Transport','Water')",
                        "Billable is bit (1 = chargeable capability)"]},
            {"name": "ENT_Skill", "rows": 10, "key": "SkillID",
              "label": "SkillName",
              "columns": ["SkillID","SkillCode","SkillName","SkillCategory",
                          "RelatedCapabilityCode","DigitalSkill"],
              "notes": ["DigitalSkill is bit (1 = digital-delivery skill, e.g. BIM, digital twin)",
                        "join to staff via ENT_StaffSkill.SkillCode"]},
            {"name": "ENT_StaffCapability", "rows": 2741,
              "key": ["StaffID","CapabilityCode"],
              "label": "CapabilityName",
              "columns": ["StaffID","StaffName","StaffUPN","CapabilityCode",
                          "CapabilityName","ProficiencyLevel","YearsExperience",
                          "Accredited","PrimaryCapability"],
              "notes": ["ProficiencyLevel IN ('Foundation','Practitioner','Advanced','Expert')",
                        "Accredited / PrimaryCapability are bit flags"]},
            {"name": "ENT_StaffSkill", "rows": 2261,
              "key": ["StaffID","SkillCode"],
              "label": "SkillName",
              "columns": ["StaffID","StaffName","StaffUPN","SkillCode","SkillName",
                          "ProficiencyLevel","YearsExperience","PrimarySkill"],
              "notes": ["ProficiencyLevel IN ('Foundation','Practitioner','Advanced','Expert')",
                        "'advanced or expert' = ProficiencyLevel IN ('Advanced','Expert')"]},
            {"name": "ENT_PracticeNetworkMembers", "rows": 905,
              "key": ["PracticeNetworkID","StaffID"],
              "label": "PracticeNetworkName",
              "columns": ["PracticeNetworkID","PracticeNetworkName","StaffID",
                          "StaffEmail","StaffUPN","StaffName","Current"],
              "notes": ["[Current] is reserved — quote the brackets; 1 = active member",
                        "practice networks are communities of interest (e.g. digital, carbon)"]},
            {"name": "ENT_Client", "rows": 650, "key": "ClientID",
              "label": "ClientName",
              "columns": ["ClientID","ClientCode","ClientName","ClientStatusDesc",
                          "ClientSectorName","CountryName","RelationshipDirectorID",
                          "RelationshipDirectorName","RelationshipDirectorEmail",
                          "StrategicClient","FrameworkClient","ActiveProjectCount",
                          "TotalBacklogValue"],
              "notes": ["ClientStatusDesc IN ('Active','Dormant','Prospect')",
                        "ClientSectorName IN ('Buildings','Defence and Security','Energy','Environment and Society','Transport','Water')",
                        "StrategicClient / FrameworkClient are bit flags",
                        "relationship director / account lead = RelationshipDirectorName"]},
            {"name": "ENT_Programme", "rows": 220, "key": "ProgrammeID",
              "label": "ProgrammeName",
              "columns": ["ProgrammeID","ProgrammeCode","ProgrammeName","ClientID",
                          "ClientName","MarketName","RegionName","StartDate","EndDate",
                          "ProgrammeDirectorID","ProgrammeDirectorName","Status",
                          "TotalProgrammeValue"],
              "notes": ["a programme groups related projects for one client",
                        "Status examples: 'Active','Closing','Mobilising','On Hold'"]},
            {"name": "ENT_Project", "rows": 2600, "key": "ProjectID",
              "label": "ShortTitle",
              "columns": ["ProjectID","ProjectCode","ShortTitle","FullTitle","ClientID",
                          "ClientName","ProgrammeID","ProgrammeName","MarketName",
                          "SectorName","CountryName","RegionName","ProjectStatusDesc",
                          "StartDate","EndDate","ProjectDirectorID","ProjectDirectorName",
                          "ProjectManagerID","ProjectManagerName","CommercialManagerID",
                          "CommercialManagerName","FrameworkName","ContractModel",
                          "TotalFee","PercentComplete","BIMEnabled","DigitalTwinEnabled"],
              "notes": ["ProjectStatusDesc IN ('Active','Closing','Mobilising','On Hold')",
                        "SectorName IN ('Buildings','Defence and Security','Energy','Environment and Society','Transport','Water')",
                        "MarketName examples: Aviation, Defence, Energy Networks, Highways, Hydrogen, Ports, Property and Buildings, Rail, Resilience, Water Utilities",
                        "BIMEnabled / DigitalTwinEnabled are bit flags (1 = enabled)",
                        "project director = ProjectDirectorName; TotalFee is the headline fee"]},
            {"name": "ENT_ProjectStaff", "rows": 21987,
              "key": ["ProjectID","StaffID"],
              "label": "StaffName",
              "columns": ["ProjectID","ProjectCode","StaffID","StaffName","StaffUPN",
                          "RoleName","RoleType","DisciplineName","HoursBooked",
                          "ChargeabilityPct","StartDate","EndDate","Current"],
              "notes": ["[Current] is reserved — quote the brackets; 1 = currently assigned",
                        "staffed headcount by discipline = COUNT(DISTINCT StaffID) GROUP BY DisciplineName",
                        "ChargeabilityPct is the utilisation of that assignment"]},
            {"name": "ENT_ProjectFinancialCumulative", "rows": 10400,
              "key": ["ProjectID","AsOfDate"],
              "label": "ShortTitle",
              "columns": ["ProjectID","ShortTitle","AsOfDate","FeeBudget","FeeForecast",
                          "FeeEarned","FeeBilled","CostForecast","MarginForecastPct",
                          "ChangeValue","RiskValue"],
              "notes": ["cumulative snapshots — for the latest position take the MAX(AsOfDate) per ProjectID",
                        "MarginForecastPct is a percentage (e.g. 12.5 = 12.5%)",
                        "'margin below 12 percent' = MarginForecastPct < 12"]},
            {"name": "ENT_ProjectDisciplineStatistics", "rows": 11662,
              "key": ["ProjectID","DisciplineName"],
              "label": "ShortTitle",
              "columns": ["ProjectID","ShortTitle","DisciplineName","Headcount",
                          "HoursBooked","FeeContribution","CarbonSpecialists",
                          "DigitalSpecialists"],
              "notes": ["per-discipline breakdown for a project",
                        "digital specialists on a project = SUM(DigitalSpecialists)"]},
        ],
        "translation_rules_summary": [
            "active project = ProjectStatusDesc='Active'",
            "active staff = StaffStatus='Active'",
            "active client = ClientStatusDesc='Active'",
            "strategic client = StrategicClient = 1",
            "framework client = FrameworkClient = 1",
            "sector for projects/clients: SectorName / ClientSectorName LIKE '%X%'",
            "market = MarketName (Rail, Highways, Water Utilities, etc.)",
            "practice = ENT_Staff.PracticeName; headcount by practice = COUNT(*) GROUP BY PracticeName",
            "skill at advanced/expert = ENT_StaffSkill.ProficiencyLevel IN ('Advanced','Expert')",
            "fee budget / forecast / billed = ENT_ProjectFinancialCumulative (latest AsOfDate per project)",
            "margin = MarginForecastPct; worst margin = ORDER BY MarginForecastPct ASC",
            "BIM-enabled = BIMEnabled = 1; digital-twin-enabled = DigitalTwinEnabled = 1",
            "project director = ENT_Project.ProjectDirectorName",
            "relationship director / account lead = ENT_Client.RelationshipDirectorName",
            "person / client / project name = LIKE '%token%' on the most distinctive token",
            "latest financial snapshot: JOIN on (ProjectID, AsOfDate = MAX(AsOfDate))",
        ],
    }

# ─── Direct Fabric Ontology MCP call ───────────────────────────────────────
# `_query_ontology`, `_fabric_user_token`, and `_condense_ontology_response`
# now live in fabric_ontology.py and are imported at the top of this module.


def _execute_tool(name: str, args: dict[str, Any], sql_state: dict) -> tuple[Any, bool]:
    """Run one tool. Returns (result_dict, is_terminal_final_answer)."""
    if name == "final_answer":
        # Stash + signal terminal. The caller drains the loop.
        sql_state["final_answer"] = args
        return {"ok": True}, True

    if name == "schema_card":
        return _schema_card_payload(), False

    if name == "sample_values":
        return _sample_values(
            table=args.get("table", ""),
            column=args.get("column", ""),
            like_filter=args.get("like_filter", "") or "",
        ), False

    if name == "query_lakehouse":
        sql_state["sql_attempts"] += 1
        if sql_state["sql_attempts"] > MAX_SQL_ATTEMPTS:
            return {
                "error": (
                    f"SQL attempt limit reached ({MAX_SQL_ATTEMPTS}). "
                    "Submit final_answer with the best result you have so far."
                )
            }, False
        purpose = (args.get("purpose") or "")[:200]
        sql_state["sql_log"].append({
            "n": sql_state["sql_attempts"], "purpose": purpose,
            "sql": (args.get("sql") or "")[:2000],
        })
        result = execute_sql(args.get("sql") or "")
        # Track outcomes for the outer critique loop.
        if "error" in result:
            sql_state["last_error"] = result["error"]
            sql_state["any_rows"] = sql_state.get("any_rows", False)
        else:
            rc = int(result.get("row_count") or 0)
            sql_state["last_row_count"] = rc
            sql_state["any_rows"] = sql_state.get("any_rows", False) or (rc > 0)
        return result, False

    if name == "query_ontology":
        if not sql_state.get("ontology_enabled", True):
            return {
                "error": (
                    "query_ontology is DISABLED for this run (operator "
                    "toggle). Use query_lakehouse + schema_card / "
                    "sample_values instead."
                )
            }, False
        from fabric_readiness import fabric_readiness
        if not fabric_readiness()["ontology"]:
            return {
                "error": (
                    "Fabric Ontology MCP not configured. Set "
                    "FABRIC_WORKSPACE_ID and FABRIC_ONTOLOGY_ID in "
                    "agents/tests/.env, or use query_lakehouse instead."
                )
            }, False
        sql_state["ontology_attempts"] = sql_state.get("ontology_attempts", 0) + 1
        if sql_state["ontology_attempts"] > MAX_ONTOLOGY_ATTEMPTS:
            return {
                "error": (
                    f"Ontology attempt limit reached ({MAX_ONTOLOGY_ATTEMPTS}). "
                    "Use query_lakehouse or call final_answer with what you have."
                )
            }, False
        question = (args.get("question") or "").strip()
        if not question:
            return {"error": "question is required"}, False
        result = _query_ontology(question)
        sql_state.setdefault("ontology_log", []).append({
            "n":        sql_state["ontology_attempts"],
            "question": question[:300],
            "ok":       "error" not in result and not result.get("is_error"),
        })
        if "error" not in result and (result.get("answer") or result.get("raw")):
            sql_state["any_rows"] = True
        return result, False

    if name == "simulate_crowd_flow":
        sql_state["crowd_attempts"] = sql_state.get("crowd_attempts", 0) + 1
        if sql_state["crowd_attempts"] > 3:
            return {
                "error": (
                    "simulate_crowd_flow attempt limit reached (3). "
                    "Call final_answer with the results you have."
                )
            }, False
        try:
            # Lazy import — the crowd package pulls in JuPedSim/matplotlib.
            from crowd import simulate as _simulate  # type: ignore[import-not-found]
            from crowd.visualize import (  # type: ignore[import-not-found]
                render_density_heatmap as _render_heatmap,
            )
            from crowd.visualize import (
                render_egress_curve as _render_egress,
            )
            from crowd.visualize import (
                to_base64 as _to_b64,
            )
        except ImportError as exc:
            return {"error": f"crowd-flow engine unavailable: {exc}"}, False
        # Resolve the geometry: a `custom_scenario` dict wins over a
        # pre-canned `scenario` string so the model can spin up bespoke
        # rooms for whatever the user actually asked about, rather than
        # falling back to a 20×15 lobby and width-scaling.
        custom = args.get("custom_scenario")
        scenario_arg: Any
        if isinstance(custom, dict) and custom:
            scenario_arg = custom
        else:
            scenario_arg = args.get("scenario") or "lobby_evacuation"
        try:
            sim_result = _simulate(
                scenario=scenario_arg,
                agent_count=int(args.get("agent_count") or 50),
                max_time_sec=float(args.get("max_time_sec") or 120.0),
                seed=int(args.get("seed") or 1),
            )
        except ValueError as exc:
            return {"error": f"simulation rejected: {exc}"}, False
        except Exception as exc:  # noqa: BLE001 — surface engine errors verbatim
            return {"error": f"simulation failed: {type(exc).__name__}: {exc}"}, False
        try:
            egress_b64 = _to_b64(_render_egress(sim_result))
            heatmap_b64 = _to_b64(_render_heatmap(sim_result))
        except Exception as exc:  # noqa: BLE001
            egress_b64 = ""
            heatmap_b64 = ""
            sim_result.notes.append(f"viz_error: {exc}")
        sql_state.setdefault("crowd_log", []).append({
            "n":              sql_state["crowd_attempts"],
            "scenario":       sim_result.scenario_name,
            "agent_count":    sim_result.agent_count,
            "egress_sec":     sim_result.total_egress_time_sec,
            "complete":       sim_result.everyone_egressed,
            "peak_density":   sim_result.peak_density_per_m2,
        })
        # The presence of a result counts as "any rows" so the outer critique
        # loop doesn't flag the answer as empty.
        sql_state["any_rows"] = True
        # Stash the rendered PNGs in sql_state so _format_final_answer can
        # splice them onto the model's markdown after the fact. We DO NOT
        # send the base64 strings back through the function_call_output
        # because the OpenAI Responses API stalls badly when ~30 KB of
        # base64 noise is fed back in as a tool result (10-minute hangs).
        artifacts = sql_state.setdefault("crowd_artifacts", [])
        if egress_b64:
            artifacts.append({
                "kind":    "egress_curve",
                "alt":     f"Egress curve — {sim_result.scenario_name}",
                "png_b64": egress_b64,
            })
        if heatmap_b64:
            artifacts.append({
                "kind":    "density_heatmap",
                "alt":     f"Peak density heatmap — {sim_result.scenario_name}",
                "png_b64": heatmap_b64,
            })
        return {
            "scenario_name":           sim_result.scenario_name,
            "summary":                 sim_result.summary,
            "agent_count":             sim_result.agent_count,
            "total_egress_time_sec":   sim_result.total_egress_time_sec,
            "everyone_egressed":       sim_result.everyone_egressed,
            "peak_density_per_m2":     sim_result.peak_density_per_m2,
            "mean_density_per_m2":     sim_result.mean_density_per_m2,
            "throughput_per_exit":     sim_result.throughput_per_exit,
            "egress_curve":            sim_result.egress_curve,
            # We deliberately do NOT include the base64 PNG payloads here.
            # Two reasons: (a) feeding 30+ KB of base64 back into the next
            # /responses call makes Foundry stall for ~10 min per attempt;
            # (b) the model otherwise hallucinates the marker text into a
            # broken `![alt](data:image/png;base64,<rendered: N chars>)`.
            # Instead we tell the model the charts exist as a flat list
            # of kinds and rely on _format_final_answer to splice the
            # real images onto the markdown server-side.
            "rendered_charts":         [a["kind"] for a in artifacts],
            "charts_note": (
                "The egress curve and density heatmap have already been "
                "rendered and will be appended to your final answer "
                "automatically. Do not attempt to embed them yourself — "
                "there is no base64 string for you to use. Just describe "
                "the numerical results in plain prose and a short markdown "
                "table."
            ) if artifacts else "no charts rendered",
            "seed":                    sim_result.seed,
            "notes":                   sim_result.notes,
        }, False

    return {"error": f"unknown tool '{name}'"}, False


def _is_function_call(item: Any) -> bool:
    return getattr(item, "type", "") == "function_call"


def _accumulate_tokens(resp: Any, totals: dict) -> None:
    usage = getattr(resp, "usage", None)
    if not usage:
        return
    for src, dst in (("input_tokens", "PromptTokens"),
                      ("output_tokens", "CompletionTokens"),
                      ("total_tokens", "TotalTokens")):
        v = getattr(usage, src, None)
        if v is not None:
            totals[dst] = (totals.get(dst) or 0) + int(v)


def _resp_to_jsonable(resp: Any) -> dict:
    try:
        return resp.model_dump(mode="json")
    except Exception:
        try:
            return json.loads(resp.model_dump_json())
        except Exception:
            return {"_raw_repr": repr(resp)[:2000]}


def _extract_text(resp: Any) -> str:
    txt = getattr(resp, "output_text", None)
    if txt:
        return txt
    parts: list[str] = []
    for item in getattr(resp, "output", []) or []:
        if getattr(item, "type", "") == "message":
            for block in getattr(item, "content", []) or []:
                if getattr(block, "type", "") in ("output_text", "text"):
                    parts.append(getattr(block, "text", "") or "")
    return "\n".join(p for p in parts if p).strip()


def _format_final_answer(fa: dict[str, Any], sql_state: dict) -> str:
    """Render the structured final_answer as the user-facing markdown the
    runner will record in results CSV."""
    md       = fa.get("markdown", "").strip()
    scope    = (fa.get("scope_filters") or "").strip()
    caveats  = fa.get("caveats") or []
    conf     = fa.get("confidence") or "medium"
    nq       = fa.get("queries_run") or sql_state.get("sql_attempts", 0)

    # Belt-and-braces: even though the tool now tells the model not to
    # embed images, some models still hallucinate `![alt](data:image/png;
    # base64,<rendered: N chars>)` from the tool result. Strip any such
    # broken image lines from the model's markdown before we splice in
    # the real ones below.
    md = re.sub(
        r"!\[[^\]]*\]\(data:image/[^)]*\)\s*",
        "",
        md,
    ).strip()

    parts = [md]
    # Splice any crowd-flow visualisations the model produced as inline
    # data-URI images. We do this server-side because the base64 PNGs are
    # never round-tripped through the model (see simulate_crowd_flow above).
    artifacts = sql_state.get("crowd_artifacts") or []
    if artifacts:
        parts.append("\n\n---\n\n**Visualisations**\n")
        for art in artifacts:
            b64 = art.get("png_b64") or ""
            alt = art.get("alt") or art.get("kind") or "chart"
            if b64:
                parts.append(f"\n\n![{alt}](data:image/png;base64,{b64})")
    if scope:
        parts.append(f"\n\n**Scope**: {scope}")
    if caveats:
        bullets = "\n".join(f"- {c}" for c in caveats)
        parts.append(f"\n\n**Caveats**:\n{bullets}")
    parts.append(f"\n\n**Confidence**: `{conf}`  •  **SQL queries**: {nq}")
    return "".join(parts)


def _critique_message(sql_state: dict, answer_text: str) -> str | None:
    """Decide whether to re-engage the model after a final_answer.
    Returns the critique text to send back, or None to accept the answer.
    """
    if sql_state.get("critique_attempts", 0) >= MAX_CRITIQUE_RETRIES:
        return None

    text = (answer_text or "").lower()
    refusal = bool(_REFUSAL_REGEX.search(text))
    no_rows = not sql_state.get("any_rows", False)
    one_try = sql_state.get("sql_attempts", 0) <= 1

    # Trigger: looks like a refusal AND we barely tried.
    if refusal and one_try:
        return (
            "Your previous final_answer is a refusal but you only ran "
            f"{sql_state.get('sql_attempts', 0)} SQL query. "
            "The protocol requires you to broaden the filter and retry "
            "before refusing. Specifically: switch any exact `=` name match "
            "to multi-token `LIKE '%token%'`; if a date filter returned no "
            "rows, drop it; if a sector or practice filter returned no rows, "
            "use `sample_values` to see what's actually in the column. "
            "Try one or two more queries, then call final_answer again."
        )

    # Trigger: zero rows across ALL queries, no fallback attempted.
    if no_rows and sql_state.get("sql_attempts", 0) >= 1:
        return (
            "Every query you ran returned zero rows. Before refusing, "
            "broaden the filter: drop the strictest predicate (date range, "
            "exact sector, or exact name); if a literal is uncertain, call "
            "`sample_values(table, column)` to discover what's actually in "
            "the data; if you used `=` on a name, switch to multi-token "
            "`LIKE '%token%'`. Run another query, then call final_answer."
        )

    return None


class _FinalWrapper:
    """Tiny shim that quacks like an openai responses object enough for
    run_tests.py's _extract_text and status reading to work."""

    def __init__(self, text: str, usage: Any, sql_log: list[dict],
                  confidence: str | None) -> None:
        self.output_text = text
        self.usage       = usage
        self.status      = "completed"
        self.id          = f"m4-final-{int(time.time() * 1000)}"
        self.output      = []
        self.sql_log     = sql_log
        self.confidence  = confidence

    def model_dump(self, mode: str = "json") -> dict:
        return {
            "type":       "m4_final",
            "output_text": self.output_text,
            "status":     self.status,
            "sql_log":    self.sql_log,
            "confidence": self.confidence,
        }

    def model_dump_json(self) -> str:
        return json.dumps(self.model_dump(), default=str)
