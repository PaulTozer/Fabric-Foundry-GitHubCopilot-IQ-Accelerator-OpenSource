import asyncio
from types import SimpleNamespace

import pytest
from mcp_registry import SessionWorker


@pytest.mark.asyncio
async def test_cancelled_queue_entry_never_dispatches_and_stops_active_operation():
    started, stopped = asyncio.Event(), asyncio.Event()
    calls = []
    class Session:
        async def call_tool(self, name, arguments):
            calls.append(name)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
    async def open_session(entry, stack):
        return Session()
    worker = SessionWorker(SimpleNamespace(open_session=open_session), {})
    await worker.ready
    first = asyncio.create_task(worker.call_tool("active", {}))
    await started.wait()
    second = asyncio.create_task(worker.call_tool("expired-before-dispatch", {}))
    await asyncio.sleep(0)
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    with pytest.raises(RuntimeError):
        await first
    assert stopped.is_set() and calls == ["active"]
    await worker.close()


@pytest.mark.asyncio
async def test_close_preserves_caller_cancellation_without_interrupting_cleanup():
    started, finished = asyncio.Event(), asyncio.Event()
    async def cleanup():
        started.set()
        await asyncio.sleep(0.03)
        finished.set()
    async def open_session(entry, stack):
        stack.push_async_callback(cleanup)
        return object()
    worker = SessionWorker(SimpleNamespace(open_session=open_session), {})
    await worker.ready
    closing = asyncio.create_task(worker.close())
    await started.wait()
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert finished.is_set() and worker.task.done()


@pytest.mark.asyncio
async def test_cleanup_failure_is_logged_without_raw_transport_details(caplog):
    async def cleanup():
        raise OSError("credential-text-must-not-be-logged")
    async def open_session(entry, stack):
        stack.push_async_callback(cleanup)
        return object()
    worker = SessionWorker(SimpleNamespace(open_session=open_session), {})
    await worker.ready
    await worker.close()
    assert "cleanup failed (OSError)" in caplog.text
    assert "credential-text-must-not-be-logged" not in caplog.text
