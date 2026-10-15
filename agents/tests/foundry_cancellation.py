"""Cooperative cancellation for the retained synchronous Foundry runners."""
from __future__ import annotations

import asyncio
from threading import Event
from typing import Any


class GuardedResponses:
    def __init__(self, responses: Any, cancelled: Event):
        self._responses = responses
        self._cancelled = cancelled

    def create(self, **kwargs: Any) -> Any:
        self.check()
        result = self._responses.create(**kwargs)
        self.check()
        return result

    def check(self) -> None:
        if self._cancelled.is_set():
            raise asyncio.CancelledError("Foundry request cancelled.")


class GuardedFoundryClient:
    def __init__(self, client: Any, cancelled: Event):
        self.responses = GuardedResponses(client.responses, cancelled)
