"""Live incident 2026-09-23: the API-logging middleware leaked open database
transactions and deadlocked the database for 30+ minutes.

pg_stat_activity showed three sessions stuck `idle in transaction` on
`INSERT INTO audit_dg_api_logs`, the oldest 36 minutes old, holding locks.
Six locks were ungranted and every test teardown
(`DELETE FROM iam_dg_users ...`, `UPDATE doc_dg_documents ...`) blocked
behind them, which looked like hung test runs rather than a database
problem.

Cause: dispatch() fired the write with a bare

    asyncio.create_task(_write_log(...))

keeping no reference to the returned Task. asyncio only holds a weak
reference to a running task, so it can be garbage-collected mid-await --
between the INSERT and the COMMIT the session's connection is left with an
open transaction and nothing runs the async context manager's cleanup.
There was also no timeout, so a write that never completed held its
transaction open indefinitely, and no bound on how many could be in flight.
"""
import asyncio

import pytest

import app.api_logging_middleware as mw


@pytest.mark.asyncio
async def test_spawned_log_task_is_strongly_referenced_until_done():
    """The bug: a task with no reference can be GC'd mid-transaction."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_write():
        started.set()
        await release.wait()

    task = mw._spawn_log_task(slow_write())
    await started.wait()

    assert task in mw._pending_log_tasks, (
        "the in-flight task is not referenced anywhere -- it can be "
        "garbage-collected mid-transaction, which is the leak"
    )

    release.set()
    await task
    await asyncio.sleep(0)

    assert task not in mw._pending_log_tasks, "finished tasks must not accumulate"


@pytest.mark.asyncio
async def test_a_hanging_write_is_abandoned_rather_than_holding_its_transaction(monkeypatch):
    """Without a timeout, one stuck write holds locks indefinitely."""
    monkeypatch.setattr(mw, "LOG_WRITE_TIMEOUT_SECONDS", 0.2)

    entered = asyncio.Event()
    closed = asyncio.Event()

    class HangingSession:
        def add(self, *_a, **_kw):
            pass

        async def commit(self):
            await asyncio.sleep(30)   # never returns

        async def __aenter__(self):
            entered.set()
            return self

        async def __aexit__(self, *exc):
            closed.set()
            return False

    monkeypatch.setattr(mw, "AsyncSessionLocal", lambda: HangingSession())

    # must return promptly instead of hanging forever
    await asyncio.wait_for(mw._write_log(method="GET", path="/x"), timeout=5)

    assert entered.is_set()
    assert closed.is_set(), (
        "the session context manager never exited -- its connection would be "
        "returned to the pool (or not) still inside a transaction"
    )


@pytest.mark.asyncio
async def test_in_flight_writes_are_bounded(monkeypatch):
    """A burst of traffic must not spawn unbounded concurrent transactions."""
    monkeypatch.setattr(mw, "MAX_IN_FLIGHT_LOG_WRITES", 3)
    release = asyncio.Event()

    async def blocked():
        await release.wait()

    spawned = [mw._spawn_log_task(blocked()) for _ in range(10)]
    live = [t for t in spawned if t is not None]

    assert len(live) <= 3, f"spawned {len(live)} concurrent log writes, cap is 3"

    release.set()
    for t in live:
        await t
