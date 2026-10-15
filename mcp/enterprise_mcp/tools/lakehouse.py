"""Lakehouse tool — runs T-SQL against the Fabric SQL analytics endpoint
for EnterpriseKG_Lakehouse. Reuses the read-only `execute_sql` helper from
`agents/tests/m3_sql_handler.py`.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from .. import config as config  # ensure sys.path is set so we can import m3_sql_handler


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def query_lakehouse(sql: str, ctx: Context, fabric_selection: dict | None = None) -> dict[str, Any]:
        """Run T-SQL against the EnterpriseKG_Lakehouse SQL endpoint.

        Read-only: any query containing INSERT, UPDATE, DELETE, DROP, ALTER,
        CREATE, MERGE, TRUNCATE, GRANT, REVOKE, EXEC, EXECUTE is rejected.
        Returns at most 200 rows; sets `truncated=true` if more were available.

        Required env: FABRIC_SQL_ENDPOINT, FABRIC_SQL_DATABASE (defaults to
        EnterpriseKG_Lakehouse). Auth: DefaultAzureCredential (`az login` first).

        Example:
            SELECT TOP 10 ClientName, ContractCount, TotalAnnualValue
            FROM ent_client
            WHERE SectorName LIKE '%Healthcare%'
            ORDER BY TotalAnnualValue DESC
        """
        import asyncio

        from fabric_connection import connection_scope, context_headers
        from m3_sql_handler import execute_sql  # type: ignore[import-not-found]
        async with connection_scope(fabric_selection, context_headers(ctx)):
            return await asyncio.to_thread(execute_sql, sql)
