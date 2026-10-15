"""Entry point: `python -m enterprise_mcp [--http] [--port N]`."""
from __future__ import annotations

import argparse
import os
import sys

from .http_auth import ServiceTokenMiddleware
from .server import build_server


def main() -> None:
    parser = argparse.ArgumentParser(prog="enterprise_mcp")
    parser.add_argument("--http", action="store_true",
                        help="Serve over Streamable HTTP instead of stdio.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    mcp = build_server()
    if args.http:
        service_token = os.getenv("MCP_SERVICE_TOKEN", "")
        if args.host not in {"127.0.0.1", "::1", "localhost"} and not service_token:
            parser.error("Set MCP_SERVICE_TOKEN before exposing the HTTP service beyond loopback.")
        try:
            import uvicorn
        except ImportError:
            sys.exit("uvicorn is required for --http mode: pip install uvicorn")
        mcp.settings.host = args.host
        mcp.settings.port = args.port

        if os.getenv("ENTERPRISEKG_STATEFUL") not in {"1", "true", "True"}:
            mcp.settings.stateless_http = True
            mcp.settings.json_response = True

        if os.getenv("ENTERPRISEKG_DISABLE_DNS_REBINDING_PROTECTION") in {"1", "true", "True"}:
            mcp.settings.transport_security.enable_dns_rebinding_protection = False
        elif allow := os.getenv("ENTERPRISEKG_ALLOWED_HOSTS"):
            hosts = [h.strip() for h in allow.split(",") if h.strip()]
            mcp.settings.transport_security.allowed_hosts = hosts
            mcp.settings.transport_security.allowed_origins = [
                f"https://{h}" for h in hosts
            ] + [f"http://{h}" for h in hosts]

        app = ServiceTokenMiddleware(mcp.streamable_http_app(), service_token)
        uvicorn.run(app, host=args.host, port=args.port,
                    log_level="info")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
