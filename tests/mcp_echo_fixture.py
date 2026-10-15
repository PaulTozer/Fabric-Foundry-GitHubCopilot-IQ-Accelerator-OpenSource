"""Controlled transport fixture: echo input, no enterprise data or inference."""
import sys

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse, RedirectResponse

mcp = FastMCP("transport-fixture", host="127.0.0.1", port=int(sys.argv[2]), stateless_http=True)


@mcp.tool()
def echo(message: str) -> dict:
    return {"message": message}


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/unauthorized", methods=["GET", "POST", "DELETE"])
async def unauthorized(request):
    return JSONResponse({"error": "Authentication required."}, status_code=401)


@mcp.custom_route("/redirect", methods=["GET", "POST", "DELETE"])
async def redirect(request):
    return RedirectResponse("https://not-approved.example/mcp")


mcp.run(transport=sys.argv[1])
