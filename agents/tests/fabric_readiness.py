"""Effective optional Fabric configuration, without contacting either service."""
import os


def fabric_readiness() -> dict[str, bool]:
    from fabric_connection import CURRENT
    connection = CURRENT.get()
    if connection:
        return {"sql": bool(connection.sql_host), "ontology": bool(connection.ontology)}
    ontology = bool(os.environ.get("FABRIC_ONTOLOGY_MCP_URL") or (
        os.environ.get("FABRIC_WORKSPACE_ID") and os.environ.get("FABRIC_ONTOLOGY_ID")
    ))
    sql = bool(os.environ.get("FABRIC_SQL_ENDPOINT"))
    return {"sql": sql, "ontology": ontology}


def fabric_data_mode() -> str:
    """Describe configured data provenance, not connectivity or permissions."""
    from fabric_connection import CURRENT
    connection = CURRENT.get()
    if connection:
        return connection.data_mode
    if os.environ.get("FABRIC_DATA_MODE") == "test-sample":
        return "test-sample"
    return "existing" if any(fabric_readiness().values()) else "unconfigured"
