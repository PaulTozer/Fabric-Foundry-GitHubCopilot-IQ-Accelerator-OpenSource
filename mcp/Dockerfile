# EnterpriseIQ MCP server — Streamable HTTP container image.
#
# Build context: REPO ROOT (EnterpriseIQ/) so we can include agents/tests/ alongside mcp/.
#   docker build -f EnterpriseIQ/mcp/Dockerfile -t enterprise_mcp-mcp EnterpriseIQ
# Run:    docker run --rm -p 8765:8765 -e PROJECT_ENDPOINT=... enterprise_mcp-mcp
#
# Auth: DefaultAzureCredential. In Azure, the Container App's Managed Identity must have:
#   - Microsoft Fabric workspace role (Member/Contributor) on "Enterprise KG"
#   - Lakehouse + ontology read permissions
#   - Foundry data agent invocation permissions (PROJECT_ENDPOINT)
FROM python:3.11-slim-bookworm

WORKDIR /app

# ── ODBC Driver 18 for SQL (Fabric SQL endpoint) ──
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl gnupg apt-transport-https \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list \
        | tee /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc \
    && rm -rf /var/lib/apt/lists/*

# ── Python deps (cached unless pyproject changes) ──
COPY mcp/pyproject.toml ./pyproject.toml
COPY mcp/enterprise_mcp/ ./enterprise_mcp/
RUN pip install --no-cache-dir '.[crowd]' \
    && python -m copilot download-runtime

# ── Source: MCP package + agents/tests/ helpers it imports at runtime ──
COPY enterprise_schema.py ./enterprise_schema.py
COPY agents/tests/         ./agents/tests/
COPY crowd/                ./crowd/
COPY agents/CopilotSDK_Instructions.md ./agents/CopilotSDK_Instructions.md
COPY agents/Method6_Council_Instructions.md              ./agents/Method6_Council_Instructions.md
COPY agents/Method7_WorkIQ_Instructions.md ./agents/Method7_WorkIQ_Instructions.md

# Pre-warm matplotlib + jupedsim cold start
RUN python -c "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt; fig, ax = plt.subplots(); ax.plot([0,1],[0,1]); fig.savefig('/tmp/_warm.png'); import jupedsim; print('warmed')" && rm -f /tmp/_warm.png

# Streamable-HTTP transport on 8765 by default.
EXPOSE 8765
ENV ENTERPRISEKG_HOST=0.0.0.0 \
    ENTERPRISEKG_PORT=8765 \
    PYTHONUNBUFFERED=1

CMD ["sh", "-c", "python -m enterprise_mcp --http --host ${ENTERPRISEKG_HOST} --port ${ENTERPRISEKG_PORT}"]
