"""
Tool-call handler for Method 3 (Foundry-direct).

The M3 Foundry agent has a single function tool 'execute_sql'. When the
runner sees a `requires_action` run with that tool call, it executes the
SQL against the Fabric lakehouse SQL analytics endpoint and feeds the
result rows back to the model.

Auth: Microsoft Entra (DefaultAzureCredential) via the
'Active Directory Default' authentication mechanism in pyodbc.
"""

from __future__ import annotations

import json
import os
import re
import struct
import time
from typing import Any

# pyodbc + ODBC Driver 18 are required at runtime. Install once:
#   winget install Microsoft.MicrosoftODBCDriverforSQLServer
#   pip install pyodbc azure-identity
import pyodbc
from azure.identity import DefaultAzureCredential

_TOKEN_RESOURCE     = "https://database.windows.net/.default"
_SQL_COPT_SS_ACCESS_TOKEN = 1256
MAX_ROWS = 200
QUERY_TIMEOUT_SEC = 60

_DENY_WORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|merge|truncate|grant|revoke|exec|"
    r"execute|into|openrowset|openquery|opendatasource|dbcc|backup|restore|use|"
    r"waitfor|shutdown)\b", re.IGNORECASE,
)

# Fabric Lakehouse SQL endpoints use a case-sensitive collation
# (Latin1_General_100_BIN2_UTF8), and the underlying delta tables are
# always lower-cased by Fabric on creation — so `ENT_Staff` produces a
# 42S02 "Invalid object name" error while `ent_staff` succeeds.
# Agent prompts and the M4 schema card keep the PascalCase form because
# it's more readable for the model; we normalise to lowercase here so
# every method (M3/M4/M6) is fixed in one place.
_ENT_TABLES = (
    "ENT_ProjectDisciplineStatistics",  # longest first — ordering matters for
    "ENT_ProjectFinancialCumulative",   #   the regex alternation (greedy match
    "ENT_PracticeNetworkMembers",       #   wins only when the longer literal
    "ENT_StaffCapability",              #   comes first in `|`-separated
    "ENT_ProjectStaff",                #   patterns).
    "ENT_Capability",
    "ENT_StaffSkill",
    "ENT_Programme",
    "ENT_Project",
    "ENT_Client",
    "ENT_Staff",
    "ENT_Skill",
)
# Match optional `[...]` brackets, optional `dbo.` schema, then the table
# name. Case-insensitive so `ENT_Staff`, `ent_staff`, `ENT_STAFF`
# all normalise to the canonical lower-case form.
_ENT_TABLE_RE = re.compile(
    r"(?ix)"                          # case-insensitive, verbose
    r"(?P<schema>\bdbo\s*\.\s*)?"     # optional dbo.
    r"\[?(?P<name>" + "|".join(_ENT_TABLES) + r")\]?"
)


def _normalise_table_names(sql: str) -> str:
    """Rewrite ENT_* table references to lower-case to match the underlying
    Fabric Lakehouse delta names (whose SQL endpoint uses a case-sensitive
    collation). Preserves any `dbo.` schema prefix.
    """
    def repl(m: re.Match[str]) -> str:
        schema = m.group("schema") or ""
        return f"{schema}{m.group('name').lower()}"
    return _ENT_TABLE_RE.sub(repl, sql)


def _connection_string() -> str:
    from fabric_connection import CURRENT
    connection = CURRENT.get()
    server = connection.sql_host if connection else os.environ.get("FABRIC_SQL_ENDPOINT")
    db = connection.sql_database if connection else os.environ.get("FABRIC_SQL_DATABASE", "EnterpriseKG_Lakehouse")
    if not server:
        raise RuntimeError("FABRIC_SQL_ENDPOINT not set in environment.")
    return (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server={server},1433;"
        f"Database={{{db.replace('}', '}}')}}};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )


def _access_token_struct() -> bytes:
    from fabric_connection import CURRENT, FabricSelectionError
    connection = CURRENT.get()
    if connection:
        if not connection.caller.sql_token:
            raise FabricSelectionError("Selected workspace requires delegated SQL sign-in; no identity fallback.")
        token = connection.caller.sql_token.encode("utf-16-le")
        return struct.pack("=i", len(token)) + token
    cred  = DefaultAzureCredential()
    token = cred.get_token(_TOKEN_RESOURCE).token.encode("utf-16-le")
    return struct.pack("=i", len(token)) + token


def _get_conn() -> pyodbc.Connection:
    attrs = {_SQL_COPT_SS_ACCESS_TOKEN: _access_token_struct()}
    conn = pyodbc.connect(_connection_string(), attrs_before=attrs)
    conn.timeout = QUERY_TIMEOUT_SEC
    return conn


def _is_safe(sql: str) -> tuple[bool, str]:
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 20_000:
        return False, "Refused: SQL must be nonempty text of at most 20000 characters."
    # Strip literals before checking comments/keywords; semicolons inside a
    # literal are data, not additional statements.
    s = re.sub(r"'(?:''|[^'])*'", "''", sql).strip().rstrip(";").strip()
    if "--" in s or "/*" in s or "*/" in s or ";" in s:
        return False, "Refused: SQL comments and multiple statements are not allowed."
    if not re.match(r"^(SELECT|WITH)\b", s, re.IGNORECASE):
        return False, "Refused: only read-only SELECT/CTE queries are allowed."
    if _DENY_WORDS.search(s):
        return False, "Refused: query contains a forbidden SQL operation."
    return True, ""


def execute_sql(query: str) -> dict[str, Any]:
    """Run the agent-supplied SQL and return a JSON-friendly dict."""
    ok, why = _is_safe(query or "")
    if not ok:
        return {"error": why}

    # Normalise PascalCase `ENT_*` table names to lower-case so the
    # case-sensitive Fabric Lakehouse SQL collation can resolve them.
    query = _normalise_table_names(query)

    started = time.time()
    conn = None
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute(query)
        rows = cur.fetchmany(MAX_ROWS + 1)
        cols = [c[0] for c in cur.description] if cur.description else []
        truncated = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        return {
            "columns":   cols,
            "rows":      [[_jsonify(v) for v in r] for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    except pyodbc.Error as e:
        return {"error": f"SQL error: {str(e)[:600]}", "elapsed_ms": int((time.time() - started) * 1000)}
    finally:
        if conn is not None:
            conn.close()


def _jsonify(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (int, float, bool, str)):
        return v
    return str(v)


def handle_tool_calls(tool_calls: list[Any]) -> list[dict[str, str]]:
    """Build tool_outputs for an Agents 'requires_action' run."""
    outputs: list[dict[str, str]] = []
    for call in tool_calls:
        if getattr(call, "type", None) != "function":
            outputs.append({"tool_call_id": call.id, "output": json.dumps({"error": "unsupported tool"})})
            continue
        fn = call.function
        if fn.name != "execute_sql":
            outputs.append({"tool_call_id": call.id, "output": json.dumps({"error": f"unknown function {fn.name}"})})
            continue
        try:
            args = json.loads(fn.arguments or "{}")
        except Exception:
            args = {}
        result = execute_sql(args.get("query", ""))
        outputs.append({"tool_call_id": call.id, "output": json.dumps(result, default=str)[:30000]})
    return outputs
