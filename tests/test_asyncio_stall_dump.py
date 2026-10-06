"""Synthetic coverage for tests/asyncio_stall_dump.py (issue #454 diagnostics)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_STALL_CASE = """
import asyncio

import pytest
import pytest_asyncio


async def _parked_forever_marker():
    # A bounded stand-in for a teardown that never completes.
    await asyncio.wait_for(asyncio.Event().wait(), timeout=4)


@pytest_asyncio.fixture
async def stalls_on_teardown():
    yield
    try:
        await _parked_forever_marker()
    except TimeoutError:
        pass


@pytest.mark.asyncio
async def test_case(stalls_on_teardown):
    assert True
"""


def _run(
    tmp_path: Path, threshold: str | None, *, capture: bool = False
) -> subprocess.CompletedProcess[str]:
    case = tmp_path / "test_stall_case.py"
    case.write_text(_STALL_CASE, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key != "DAEMON_PYTEST_ASYNCIO_DUMP_S"}
    env["PYTHONPATH"] = str(REPO_ROOT)
    if threshold is not None:
        env["DAEMON_PYTEST_ASYNCIO_DUMP_S"] = threshold
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            *([] if capture else ["-s"]),
            "-p",
            "no:cacheprovider",
            "-p",
            "tests.asyncio_stall_dump",
            "--rootdir",
            str(tmp_path),
            str(case),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_stalled_teardown_dumps_the_pending_await_chain(tmp_path: Path) -> None:
    proc = _run(tmp_path, "1")
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output
    assert "[asyncio-stall-dump] " in output
    assert "test_case [teardown]" in output
    assert "_parked_forever_marker" in output


def test_dump_escapes_pytest_output_capture(tmp_path: Path) -> None:
    # CI runs with capture on; teardown stderr would otherwise be swallowed.
    proc = _run(tmp_path, "1", capture=True)
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output
    assert "_parked_forever_marker" in output


def test_watchdog_is_inert_without_threshold(tmp_path: Path) -> None:
    proc = _run(tmp_path, None)
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output
    assert "asyncio-stall-dump" not in output


def test_await_chain_follows_nested_awaits_on_any_python() -> None:
    """The pre-3.14 fallback names every coroutine in a suspended task's chain."""
    import asyncio

    from tests.asyncio_stall_dump import await_chain

    async def innermost() -> None:
        await asyncio.Event().wait()

    async def middle() -> None:
        await innermost()

    async def outer() -> None:
        await middle()

    async def scenario() -> list[str]:
        task = asyncio.create_task(outer())
        await asyncio.sleep(0)
        try:
            return await_chain(task.get_coro())
        finally:
            task.cancel()

    chain = asyncio.run(scenario())
    names = [line.rsplit(" in ", 1)[-1] for line in chain]
    assert [name.split(".")[-1] for name in names[:3]] == ["outer", "middle", "innermost"]
