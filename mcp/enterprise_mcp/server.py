"""FastMCP server factory — registers all EnterpriseKG tools (M4 + M6 only)."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import config
from . import resources as resources_mod
from .tools import agents as agents_tool
from .tools import crowd as crowd_tool
from .tools import lakehouse as lakehouse_tool
from .tools import ontology as ontology_tool
from .tools import test_cases as test_cases_tool

SERVER_NAME = "fabric-foundry-githubcopilot-iq"

INSTRUCTIONS = """\
EnterpriseKG MCP server. Surfaces the EnterpriseKG_Ontology, EnterpriseKG_Lakehouse, the
consultancy evaluation set, and Copilot SDK BYOK (M4), Council (M6), Work IQ (M7).
M4 requires COPILOT_MODEL, COPILOT_BASE_URL, server-side credentials and the
Copilot runtime. M6 needs PROJECT_ENDPOINT/MODEL_DEPLOYMENT_NAME; M7 also
needs a provisioned Foundry Work IQ agent.

Domain (generic enterprise consultancy):
  Staff, Skill, Capability, PracticeNetwork, Client, Programme, Project.

Discovery:
  describe_ontology() — entities, relationships, table bindings (live).
  list_test_cases()   — consultancy evaluation questions, optional persona/level filter.

Querying:
  query_ontology(gql)         — GQL via the Fabric ontology MCP (M4/M6 internal).
  query_lakehouse(sql)        — read-only T-SQL against EnterpriseKG_Lakehouse.
  ask_agent(question, method) — delegate to M4, M6 or M7 (default M4).
  simulate_crowd_flow(...)    — JuPedSim evacuation simulation.

Resources:
  enterprise-iq://ontology/definition          — live ontology JSON
  enterprise-iq://ontology/definition.cached   — same, 10-min cache
  enterprise-iq://test-cases.csv                — raw question CSV
  enterprise-iq://test-cases.json               — parsed rows
  enterprise-iq://test-cases/{code}             — one case by Code

Prefer the lakehouse for portfolio totals, fee snapshots, skills and staffing;
use the ontology for Staff/Skill/Capability/Project/Client traversals."""


def build_server() -> FastMCP:
    config.load_dotenv()
    mcp = FastMCP(name=SERVER_NAME, instructions=INSTRUCTIONS)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @mcp.tool()
    def health() -> dict[str, object]:
        """Quick environment check — returns workspace IDs and which env vars
        are populated. Safe to call without any side effects."""
        config.load_dotenv()
        import os

        from fabric_readiness import fabric_data_mode
        return {
            "server":      SERVER_NAME,
            "fabricDataMode": fabric_data_mode(),
            "workspaceId": os.environ.get("FABRIC_WORKSPACE_ID", ""),
            "lakehouseId": os.environ.get("FABRIC_LAKEHOUSE_ID", ""),
            "ontologyId":  os.environ.get("FABRIC_ONTOLOGY_ID", ""),
            "env":         config.env_summary([
                "PROJECT_ENDPOINT",
                "COPILOT_MODEL",
                "COPILOT_BASE_URL",
                "COPILOT_PROVIDER",
                "MODEL_DEPLOYMENT_NAME",
                "MODEL_DEPLOYMENT_NAME_FAST",
                "MODEL_DEPLOYMENT_NAME_PRO",
                "FABRIC_SQL_ENDPOINT",
                "FABRIC_SQL_DATABASE",
                "FABRIC_WORKSPACE_ID",
                "FABRIC_LAKEHOUSE_ID",
                "FABRIC_ONTOLOGY_ID",
                "FABRIC_ONTOLOGY_MCP_URL",
            ]),
        }

    ontology_tool.register(mcp)
    lakehouse_tool.register(mcp)
    agents_tool.register(mcp)
    test_cases_tool.register(mcp)
    crowd_tool.register(mcp)
    resources_mod.register(mcp)
    return mcp
