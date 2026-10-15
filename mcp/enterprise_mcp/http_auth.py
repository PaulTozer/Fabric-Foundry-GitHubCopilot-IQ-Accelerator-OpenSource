"""Service authentication independent of the caller's delegated Fabric token."""
from __future__ import annotations

import hmac
import time
from collections import deque

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class ServiceTokenMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        if token and len(token) < 32:
            raise ValueError("MCP_SERVICE_TOKEN must contain at least 32 characters.")
        self.app = app
        self.token = token.encode()
        self.requests: dict[tuple[str, str], deque[float]] = {}
        self.active = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self.token:
            health = scope["path"] == "/healthz" and scope["method"] == "GET"
            supplied = [value for name, value in scope["headers"] if name == b"x-iq-service-token"]
            if not health and (len(supplied) != 1 or not hmac.compare_digest(supplied[0], self.token)):
                await JSONResponse({"error": "Service authentication required."}, status_code=401)(
                    scope, receive, send,
                )
                return
        limited = scope["type"] == "http" and scope["path"] in {
            "/discover", "/fabric/workspace", "/models", "/ask", "/ask/stream",
        }
        if not limited:
            await self.app(scope, receive, send)
            return
        owner = next((value.decode("ascii", errors="ignore") for name, value in scope["headers"]
                      if name == b"x-iq-owner"), "service")
        discovery = scope["path"] in {"/discover", "/fabric/workspace"}
        key = (owner[:64], "discover" if discovery else "turn")
        now = time.monotonic()
        for expired in [key for key, values in self.requests.items() if not values or values[-1] < now - 60]:
            del self.requests[expired]
        if key not in self.requests and len(self.requests) >= 2048:
            await JSONResponse({"error": "Service request capacity reached; retry shortly."}, status_code=503)(scope, receive, send)
            return
        values = self.requests.setdefault(key, deque())
        while values and values[0] < now - 60:
            values.popleft()
        if len(values) >= (6 if discovery else 60):
            await JSONResponse({"error": "Request limit reached; wait one minute before retrying."}, status_code=429)(scope, receive, send)
            return
        if self.active >= 8:
            await JSONResponse({"error": "Service is busy; retry shortly."}, status_code=503)(scope, receive, send)
            return
        values.append(now)
        self.active += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.active -= 1
