"""Controlled native Docker lifecycle fixtures: REVIEW BEFORE FIRST EXECUTION.

Pending name on purpose: pytest does not collect this module until the
reviewed bytes are renamed to ``test_web_fetch_pilot_docker_io.py``; even then
it is opt-in (``DAEMON_DOCKER_TESTS=1``, the existing container-test switch)
so wildcard pilot runs never create containers.

Drives ``OfflineContainerDriver`` through the real ``CommandExecutor`` and
``RawLauncher`` over ``NativeBackend`` against the pinned local daemon, with
the pinned local image (``--pull never``), the policy's network-none,
read-only, cap-dropped, resource-limited create vector and inert coreutils
commands that bound themselves: ``timeout 20 cat`` and ``sleep 30``. No
shell, no Python inside the container, no DNS, no network, no browser and no
bootstrap payload.

Questions answered: pinned image presence; daemon provenance; whether
``docker create -i --attach stdin`` sets ``Config.StdinOnce`` (preflight fails
closed if not); attach stdio round trip with stdin EOF delivered to the
container; exact exit; identity-checked forced removal of a running container
and verified absence. Leftovers are never auto-removed by this fixture: any
remaining owner-labelled container is reported for owner disposition, as the
orphan policy requires. Execution must be owned by an external watchdog.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from scripts.web_fetch_pilot_command import CommandExecutor, CommandResult
from scripts.web_fetch_pilot_container_policy import (
    IMAGE,
    PreflightError,
    require_offline_gateway,
)
from scripts.web_fetch_pilot_docker import (
    DockerCleanupFailure,
    DockerLifecycleFailure,
    OfflineContainerDriver,
    docker_argv,
    fresh_name,
    parse_ids,
    parse_inspect,
)
from scripts.web_fetch_pilot_process import (
    NativeBackend,
    ProcessCleanupFailure,
    RawByteChannel,
    RawLauncher,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("DAEMON_DOCKER_TESTS") != "1",
    reason="Opt-in native Docker lifecycle gate requires DAEMON_DOCKER_TESTS=1",
)

CAT = ("-i", "/usr/bin/timeout", "20", "/bin/cat")
SLEEP = ("-i", "/bin/sleep", "30")
PING = b"daemon-pilot-docker-ping\n"


class RecordingRunner:
    """Real executor; records created IDs and the last inspect bytes only."""

    def __init__(self, executor: CommandExecutor) -> None:
        self._executor = executor
        self.subcommands: list[str] = []
        self.created: list[str] = []
        self.last_inspect: bytes | None = None

    async def run(
        self,
        argv: Sequence[str],
        env: Mapping[str, str],
        *,
        stdin: bytes = b"",
        timeout: float,
    ) -> CommandResult:
        outcome = await self._executor.run(argv, env, stdin=stdin, timeout=timeout)
        self.subcommands.append(argv[3])
        if argv[3] == "create" and outcome.returncode == 0:
            self.created.extend(parse_ids(outcome.stdout))
        if argv[3] == "inspect" and outcome.returncode == 0:
            self.last_inspect = outcome.stdout
        return outcome


def fd_set() -> set[str]:
    return set(os.listdir("/proc/self/fd"))


async def restored(fds: set[str], tasks: set[asyncio.Task[object]]) -> None:
    for _ in range(200):
        if fd_set() == fds and set(asyncio.all_tasks()) == tasks:
            break
        await asyncio.sleep(0.005)
    assert fd_set() == fds
    assert set(asyncio.all_tasks()) == tasks


def stdin_once_was_sole_refusal(
    raw: bytes, name: str, command: tuple[str, ...], identifier: str
) -> bool:
    """Diagnose by field without printing the record: was StdinOnce the cause?"""
    record = parse_inspect(raw)
    try:
        require_offline_gateway(record, name, command, container_id=identifier)
        return False
    except PreflightError:
        pass
    config = record.get("Config")
    if type(config) is not dict:
        return False
    config["StdinOnce"] = True
    try:
        require_offline_gateway(record, name, command, container_id=identifier)
    except PreflightError:
        return False
    return True


@asynccontextmanager
async def owned(
    config_dir: Path,
) -> AsyncIterator[tuple[OfflineContainerDriver, RecordingRunner, list[str]]]:
    """Real executor/attach launchers; teardown never auto-removes leftovers."""
    executor = CommandExecutor(NativeBackend())
    runner = RecordingRunner(executor)
    launchers: list[RawLauncher] = []
    names: list[str] = []

    def attach() -> RawLauncher:
        launcher = RawLauncher(NativeBackend(), launch_deadline=15.0, close_grace=2.0)
        launchers.append(launcher)
        return launcher

    def name() -> str:
        names.append(fresh_name())
        return names[-1]

    driver = OfflineContainerDriver(runner, attach, config_dir=str(config_dir), name_factory=name)
    try:
        async with asyncio.timeout(90):
            yield driver, runner, names
    finally:
        try:
            try:
                await driver.aclose()
            except DockerCleanupFailure:
                pass  # The body asserts cleanup state; leftovers stay for the owner.
        finally:
            for launcher in launchers:
                try:
                    await launcher.aclose()
                except ProcessCleanupFailure:
                    pass
            await executor.aclose()


def private_config(tmp_path: Path) -> Path:
    path = tmp_path / "docker-config"
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)
    return path


async def read_all(channel: RawByteChannel, cap: int = 64 * 1024) -> bytes:
    received = bytearray()
    while data := await channel.read(16 * 1024):
        received.extend(data)
        assert len(received) <= cap
    return bytes(received)


@pytest.mark.asyncio
async def test_native_docker_pinned_image_provenance_and_no_orphans(tmp_path: Path) -> None:
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    config = private_config(tmp_path)
    async with owned(config) as (driver, runner, _names):
        image = await runner.run(
            docker_argv("image", "inspect", "--format", "{{.Id}}", IMAGE),
            {"DOCKER_CONFIG": str(config)},
            timeout=15.0,
        )
        assert (image.returncode, image.stdout) == (0, (IMAGE + "\n").encode())
        await driver.preflight()
        assert await driver.owned_ids() == ()  # Report-and-block otherwise.
        assert "create" not in runner.subcommands
    assert not driver.cleanup_failed
    await restored(fds, tasks)


@pytest.mark.asyncio
async def test_native_docker_attach_roundtrip_stdin_eof_exit_and_removal(tmp_path: Path) -> None:
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    async with owned(private_config(tmp_path)) as (driver, runner, names):
        await driver.preflight()
        try:
            container = await driver.start(CAT)
        except DockerLifecycleFailure:
            if runner.last_inspect is not None and runner.created and names:
                if stdin_once_was_sole_refusal(
                    runner.last_inspect, names[-1], CAT, runner.created[-1]
                ):
                    pytest.fail("docker create left Config.StdinOnce unset; preflight refused")
            raise
        assert container.container_id == runner.created[-1]
        async with asyncio.timeout(30):
            await container.stdin.write(PING)
            container.stdin.close()  # EOF must reach the container via StdinOnce.
            out, err = await asyncio.gather(read_all(container.stdout), read_all(container.stderr))
            code = await container.wait()
        assert out == PING
        assert code == 0, (code, len(err))  # 124 would mean EOF never arrived.
        await container.aclose()
        assert container.final_state == ("exited", 0)
        assert await driver.owned_ids() == ()
        assert not driver.cleanup_failed and driver.retained is None
    await restored(fds, tasks)


@pytest.mark.asyncio
async def test_native_docker_forced_removal_of_running_container(tmp_path: Path) -> None:
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    config = private_config(tmp_path)
    async with owned(config) as (driver, runner, _names):
        await driver.preflight()
        container = await driver.start(SLEEP)
        identifier = container.container_id
        async with asyncio.timeout(15):
            while True:  # Attach start publishes before the daemon reports running.
                state = await runner.run(
                    docker_argv("inspect", "--type", "container", "--format", "{{.State.Status}}")
                    + (identifier,),
                    {"DOCKER_CONFIG": str(config)},
                    timeout=10.0,
                )
                assert state.returncode == 0
                if state.stdout == b"running\n":
                    break
                await asyncio.sleep(0.1)
        started = time.monotonic()
        await container.aclose()
        assert time.monotonic() - started < 25.0  # Removal, not the 30 s sleep.
        assert container.final_state is not None and container.final_state[0] == "running"
        assert await driver.owned_ids() == ()
        assert not driver.cleanup_failed and driver.retained is None
    await restored(fds, tasks)
