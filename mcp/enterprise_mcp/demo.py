"""Independent stdio demo servers inspired by Kratos IQ contracts; synthetic only."""
from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP


def build_demo(kind: str) -> FastMCP:
    if kind not in {"fabric-iq", "foundry-iq", "work-iq"}:
        raise ValueError("Unknown demo server.")
    server = FastMCP(name=f"demo-{kind}")

    if kind == "fabric-iq":
        @server.tool()
        def fabric_iq_query_portfolio() -> dict:
            """Read a synthetic project portfolio. NOT a live Fabric connection."""
            return {"simulation_only": True, "projects": [
                {"project_id": "DEMO-001", "title": "Example Water Resilience",
                 "market": "Water", "status": "Active", "fee_currency": "GBP", "fee_budget": 100000},
                {"project_id": "DEMO-002", "title": "Example Rail Programme",
                 "market": "Transport", "status": "Active", "fee_currency": "GBP", "fee_budget": 250000},
            ]}
    elif kind == "foundry-iq":
        @server.tool()
        def foundry_iq_search_knowledge(query: str) -> dict:
            """Search synthetic delivery knowledge and return source/page citations."""
            return {"simulation_only": True, "documents": [{
                "id": "demo-delivery", "source_id": "demo-delivery",
                "title": "Example Delivery Guide", "content": "Confirm scope, owner and evidence before handover.",
                "citations": [{"source_id": "demo-delivery", "section": "Handover",
                               "page": 1, "title": "Example Delivery Guide",
                               "url": "/api/documents/demo-delivery?page=1"}],
            }]}

        @server.tool()
        def foundry_iq_get_document(document_id: str) -> dict:
            """Retrieve an allowlisted synthetic Markdown document."""
            if document_id != "demo-delivery":
                raise ValueError("Document not found.")
            return {"simulation_only": True, "id": document_id,
                    "url": "/api/documents/demo-delivery", "format": "markdown",
                    "content": "# Example Delivery Guide\n\nConfirm scope, owner and evidence before handover."}
    else:
        @server.tool()
        def work_iq_search(query: str) -> dict:
            """Search synthetic collaboration evidence, NOT Microsoft 365."""
            return {"simulation_only": True, "results": [
                {"source_id": "demo-meeting", "title": "Example handover meeting",
                 "participants": ["Example Project Lead", "Example Delivery Lead"],
                 "summary": "The team agreed to confirm scope and evidence.",
                 "url": "/api/documents/demo-meeting", "page": 1},
            ]}
    return server


if __name__ == "__main__":
    build_demo(sys.argv[1]).run(transport="stdio")
