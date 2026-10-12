"""Barrier I/O lifetime checks, including shutdown in an owned subprocess."""

from __future__ import annotations

import asyncio
import contextvars
import multiprocessing
import threading
from uuid import uuid4

import pytest

from orchestrator.services.deletion_journal import barrier
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.store import Journal, initialize


@pytest.mark.asyncio
async def test_io_preserves_generic_result_and_copied_context():
    context = contextvars.ContextVar("journal_io_context", default="unset")
    token = context.set("caller")
    result = object()

    def work():
        assert context.get() == "caller"
        context.set("thread")
        return result

    try:
        assert await barrier._io(work) is result
        assert context.get() == "caller"
    finally:
        context.reset(token)


class IOFailure(BaseException):
    """Also exercise the helper's BaseException, not just Exception, contract."""


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError("fictional I/O failure"), IOFailure()])
async def test_io_preserves_original_exception(failure):
    def work():
        raise failure

    with pytest.raises(type(failure)) as error:
        await barrier._io(work)
    assert error.value is failure


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_io_failure_then_propagates_cancellation():
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def work():
        started.set()
        try:
            assert release.wait(5)
            raise OSError("fictional I/O failure after cancellation")
        finally:
            finished.set()

    waiter = asyncio.create_task(barrier._io(work))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        waiter.cancel()
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.sleep(0)
        assert not waiter.done() and not finished.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    assert finished.is_set()


def _cancel_io_during_shutdown(
    path, identity, corrupt, started, cancelled, release, finished, cleaned
):
    async def run():
        with Journal(path, identity) as journal:
            scan = journal._scan

            def held_scan():
                # Journal.verify has already acquired its real filesystem lock.
                started.set()
                assert release.wait(10)
                return scan()

            journal._scan = held_scan

            def verify():
                try:
                    result = journal.verify()
                except JournalError as error:
                    assert corrupt and str(error) == "corrupt"
                    # verify's lock cleanup has run before this signal.
                    finished.set()
                    raise
                assert not corrupt
                finished.set()
                return result

            previous = asyncio.all_tasks()
            waiter = asyncio.create_task(barrier._io(verify))
            assert await asyncio.to_thread(started.wait, 5)
            for task in asyncio.all_tasks() - previous:
                task.cancel()
            cancelled.set()
            try:
                await waiter
            except asyncio.CancelledError:
                assert finished.is_set(), "cancellation abandoned live verification"
            else:
                pytest.fail("shutdown cancellation became successful startup")
        assert journal._dir == journal._lock == -1
        cleaned.set()

    # Natural exit also proves asyncio's task/executor shutdown completes.
    asyncio.run(run())


@pytest.mark.parametrize("corrupt", [False, True])
def test_all_task_shutdown_cancellation_waits_for_real_journal_cleanup(tmp_path, corrupt):
    path, identity = tmp_path / "journal", uuid4()
    initialize(path, identity)
    if corrupt:
        (path / "HEAD").write_bytes(b"{")
    context = multiprocessing.get_context("spawn")
    started, cancelled, release, finished, cleaned = [context.Event() for _ in range(5)]
    child = context.Process(
        target=_cancel_io_during_shutdown,
        args=(path, identity, corrupt, started, cancelled, release, finished, cleaned),
    )
    child.start()
    try:
        assert started.wait(5), "real verification never acquired its lock"
        assert cancelled.wait(5), "shutdown cancellation was not issued"
        assert not finished.is_set() and not cleaned.is_set()
        # Parent release works even if the old helper spins on the child's loop.
        release.set()
        child.join(10)
        assert finished.is_set(), "filesystem verification did not finish"
        assert child.exitcode == 0, "cleanup spun or failed after verification finished"
        assert cleaned.is_set(), "journal descriptors were not closed"
    finally:
        release.set()
        if child.is_alive():
            child.terminate()
            child.join(5)
        assert not child.is_alive(), "owned regression subprocess did not stop"
        child.close()
