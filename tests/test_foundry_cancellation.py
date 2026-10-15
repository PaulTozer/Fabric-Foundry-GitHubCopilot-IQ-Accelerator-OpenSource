import asyncio
from threading import Event
from types import SimpleNamespace

import pytest
from foundry_cancellation import GuardedFoundryClient


def test_cancelled_foundry_never_starts_another_request():
    cancelled = Event()
    calls = []
    client = GuardedFoundryClient(SimpleNamespace(responses=SimpleNamespace(
        create=lambda **kw: calls.append(kw))), cancelled)
    cancelled.set()
    with pytest.raises(asyncio.CancelledError):
        client.responses.create(input="question")
    assert calls == []


def test_cancellation_during_call_does_not_return_a_success():
    cancelled = Event()
    def create(**kwargs):
        cancelled.set()
        return SimpleNamespace(output_text="Must not be returned")
    client = GuardedFoundryClient(SimpleNamespace(responses=SimpleNamespace(create=create)), cancelled)
    with pytest.raises(asyncio.CancelledError):
        client.responses.create(input="question")
