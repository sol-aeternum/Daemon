"""Fake-only Docker lifecycle driver tests; NO Docker, process or network here.

``FakeDocker`` simulates daemon state behind the pinned argv/env contract and
injects create/inspect/rm outcomes; ``FakeAttachLauncher`` stands in for the
owned ``docker start --attach --interactive`` raw-launch child, whose own
ownership is qualified separately. Config-directory checks use pytest's
private ``tmp_path`` only.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence

import pytest

from scripts.web_fetch_pilot_command import CommandLimitFailure, CommandResult
from scripts.web_fetch_pilot_container_policy import (
    IMAGE,
    OWNER_LABEL,
    RUN_LABEL,
    PreflightError,
    browser_create_arguments,
    create_arguments,
)
from scripts.web_fetch_pilot_docker import (
    BROWSER_NAME_PREFIX,
    DOCKER,
    GATEWAY_POLICY,
    ContainerPolicy,
    DOCKER_HOST,
    MAX_LISTED_IDS,
    DockerCleanupFailure,
    DockerLifecycleFailure,
    OfflineContainerDriver,
    OrphansPresent,
    OwnedNetwork,
    browser_policy,
    check_config_dir,
    docker_argv,
    parse_ids,
    parse_inspect,
    require_daemon,
)
from scripts.web_fetch_pilot_process import ProcessCleanupFailure, ProcessLaunchFailure
from tests.test_web_fetch_pilot_container_policy import COMMAND
from tests.test_web_fetch_pilot_container_policy import (
    PROFILE_PATH,
    browser_fixture,
    egress_network_fixture,
    network_fixture,
)
from tests.test_web_fetch_pilot_container_policy import fixture as policy_record

CONFIG_DIR = "/run/user/fake/daemon-pilot-docker-config"
NAMES = ["daemon-dns-offline-" + c * 24 for c in "cdef"]


def result(returncode: int = 0, stdout: bytes = b"") -> CommandResult:
    return CommandResult(returncode, stdout, b"", 0, 0)


def good_info() -> dict[str, object]:
    return {
        "OSType": "linux",
        "CgroupVersion": "2",
        "DefaultRuntime": "runc",
        "MemoryLimit": True,
        "SwapLimit": True,
        "PidsLimit": True,
        "CpuCfsQuota": True,
        "SecurityOptions": ["name=seccomp,profile=builtin", "name=cgroupns"],
    }


Hook = Callable[[tuple[str, ...]], Awaitable[CommandResult | None]]


class FakeDocker:
    """Fake daemon behind the pinned client; records every subcommand."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.containers: dict[str, dict] = {}
        self.info: object = good_info()
        self.hooks: dict[str, Hook] = {}
        self.record_factory: Callable[[], dict] = policy_record
        self.networks: dict[str, dict] = {}
        self.network_record_factory: Callable[[], dict] = network_fixture
        self._serial = 0

    async def run(
        self,
        argv: Sequence[str],
        env: Mapping[str, str],
        *,
        stdin: bytes = b"",
        timeout: float,
    ) -> CommandResult:
        assert tuple(argv[:3]) == (DOCKER, "--host", DOCKER_HOST)
        assert dict(env) == {"DOCKER_CONFIG": CONFIG_DIR}
        assert stdin == b"" and type(timeout) is float and 0 < timeout <= 15.0
        args = tuple(argv[3:])
        self.calls.append(args)
        hook = self.hooks.get(args[0])
        if hook is not None:
            outcome = await hook(args)
            if outcome is not None:
                return outcome
        return getattr(self, "do_" + args[0])(args)

    def subcommands(self) -> list[str]:
        return [call[0] for call in self.calls]

    def add(self, name: str, *, label: str | None = None) -> str:
        self._serial += 1
        identifier = f"{self._serial:064x}"
        record = self.record_factory()
        record["Id"] = identifier
        record["Name"] = "/" + name
        record["Config"]["Labels"] = {OWNER_LABEL: name if label is None else label}
        record["State"] = {"Status": "exited", "ExitCode": 0}
        self.containers[identifier] = record
        return identifier

    def do_info(self, args: tuple[str, ...]) -> CommandResult:
        assert args == ("info", "--format", "{{json .}}")
        return result(stdout=json.dumps(self.info).encode())

    def do_create(self, args: tuple[str, ...]) -> CommandResult:
        name = args[args.index("--name") + 1]
        if any(r["Name"] == "/" + name for r in self.containers.values()):
            return result(125)
        identifier = self.add(name)
        image = args.index(IMAGE)
        labels = dict(
            args[i + 1].split("=", 1) for i in range(image) if args[i] == "--label"
        )  # Exactly the labels the create vector requested, run token included.
        self.containers[identifier]["Config"]["Labels"] = labels
        self.containers[identifier]["Config"]["Cmd"] = list(args[image + 1 :])
        return result(stdout=(identifier + "\n").encode())

    def do_inspect(self, args: tuple[str, ...]) -> CommandResult:
        assert args[:3] == ("inspect", "--type", "container") and len(args) == 4
        record = self.containers.get(args[3])
        if record is None:
            return result(1, b"[]\n")
        return result(stdout=json.dumps([record]).encode())

    def do_ps(self, args: tuple[str, ...]) -> CommandResult:
        assert args[:3] == ("ps", "--all", "--no-trunc") and args[-2:] == ("--format", "{{.ID}}")
        kind, _, value = args[4].partition("=")
        if kind == "label":
            key, _, wanted = value.partition("=")
            assert key in (OWNER_LABEL, RUN_LABEL) and (key == OWNER_LABEL) == (not wanted)
            ids = [
                i
                for i, r in self.containers.items()
                if key in r["Config"]["Labels"]
                and (not wanted or r["Config"]["Labels"][key] == wanted)
            ]
        elif kind == "name":
            assert value.startswith("^/?") and value.endswith("$")
            ids = [i for i, r in self.containers.items() if r["Name"] == "/" + value[3:-1]]
        else:
            assert kind == "id"
            ids = [i for i in self.containers if i == value]
        return result(stdout="".join(i + "\n" for i in ids).encode())

    def add_network(self, name: str, labels: dict[str, str]) -> str:
        self._serial += 1
        identifier = f"{self._serial:064x}"
        record = self.network_record_factory()
        record.update(Id=identifier, Name=name, Labels=dict(labels))
        self.networks[identifier] = record
        return identifier

    def do_network(self, args: tuple[str, ...]) -> CommandResult:
        action = args[1]
        if action == "create":
            name = args[-1]
            if any(r["Name"] == name for r in self.networks.values()):
                return result(1)
            labels = dict(
                args[i + 1].split("=", 1) for i in range(len(args)) if args[i] == "--label"
            )
            return result(stdout=(self.add_network(name, labels) + "\n").encode())
        if action == "inspect":
            record = self.networks.get(args[2])
            return (
                result(1, b"[]\n")
                if record is None
                else result(stdout=json.dumps([record]).encode())
            )
        if action == "rm":
            return result(0 if self.networks.pop(args[2], None) is not None else 1)
        assert args[1:3] == ("ls", "--no-trunc") and args[-2:] == ("--format", "{{.ID}}")
        kind, _, value = args[4].partition("=")
        if kind == "label":
            key, _, wanted = value.partition("=")
            ids = [
                i
                for i, r in self.networks.items()
                if key in r["Labels"] and (not wanted or r["Labels"][key] == wanted)
            ]
        elif kind == "name":
            ids = [i for i, r in self.networks.items() if value in r["Name"]]  # Substring.
        else:
            assert kind == "id"
            ids = [i for i in self.networks if i == value]
        return result(stdout="".join(i + "\n" for i in ids).encode())

    def do_rm(self, args: tuple[str, ...]) -> CommandResult:
        assert args[:2] == ("rm", "--force") and len(args) == 3
        return result(0 if self.containers.pop(args[2], None) is not None else 1)


class FakeChannel:
    async def read(self, size: int, /) -> bytes:
        return b""

    async def write(self, data: bytes) -> int:
        return len(data)

    def close(self) -> None:
        pass


class FakeAttachSession:
    def __init__(self, launcher: FakeAttachLauncher) -> None:
        self._launcher = launcher
        self.stdin = FakeChannel()
        self.stdout = FakeChannel()
        self.stderr = FakeChannel()

    async def wait(self) -> int:
        return 0

    async def aclose(self) -> None:
        self._launcher.session_closed = True
        if self._launcher.fail_close:
            raise ProcessCleanupFailure("fake attach teardown uncertain")


class FakeAttachLauncher:
    def __init__(self) -> None:
        self.argv: tuple[str, ...] | None = None
        self.launch_error: BaseException | None = None
        self.fail_close = False
        self.closed = False
        self.aclosed = False
        self.session_closed = False

    async def launch(self, argv: Sequence[str], env: Mapping[str, str]) -> FakeAttachSession:
        assert dict(env) == {"DOCKER_CONFIG": CONFIG_DIR}
        self.argv = tuple(argv)
        if self.launch_error is not None:
            raise self.launch_error
        return FakeAttachSession(self)

    def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.aclosed = True


class Harness:
    def __init__(
        self, *, policy: ContainerPolicy = GATEWAY_POLICY, names: Sequence[str] = NAMES
    ) -> None:
        self.docker = FakeDocker()
        self.launchers: list[FakeAttachLauncher] = []
        self.launch_error: BaseException | None = None
        self.fail_close = False
        pending = iter(names)
        self.driver = OfflineContainerDriver(
            self.docker,
            self.attach,
            config_dir=CONFIG_DIR,
            name_factory=lambda: next(pending),
            policy=policy,
        )

    def attach(self) -> FakeAttachLauncher:
        launcher = FakeAttachLauncher()
        launcher.launch_error = self.launch_error
        launcher.fail_close = self.fail_close
        self.launchers.append(launcher)
        return launcher

    async def qualified(self, monkeypatch: pytest.MonkeyPatch) -> OfflineContainerDriver:
        monkeypatch.setattr("scripts.web_fetch_pilot_docker.check_config_dir", lambda path: None)
        await self.driver.preflight()
        self.docker.calls.clear()
        return self.driver


def assert_released(h: Harness) -> None:
    assert h.driver.retained is None
    assert h.driver.pending_tasks == ()
    assert not h.driver.cleanup_failed
    assert all(launcher.closed and launcher.aclosed for launcher in h.launchers)


def test_constructor_shape_only_and_refusals() -> None:
    h = Harness()
    assert h.docker.calls == [] and h.launchers == []
    with pytest.raises(ValueError):
        OfflineContainerDriver(object(), h.attach, config_dir=CONFIG_DIR)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        OfflineContainerDriver(h.docker, h.attach, config_dir="relative")
    with pytest.raises(ValueError):
        OfflineContainerDriver(h.docker, None, config_dir=CONFIG_DIR)  # type: ignore[arg-type]


def test_docker_argv_is_pinned_client_and_daemon() -> None:
    assert docker_argv("ps") == ("/usr/bin/docker", "--host", "unix:///run/docker.sock", "ps")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("OSType", "windows"),
        ("CgroupVersion", "1"),
        ("CgroupVersion", 2),
        ("DefaultRuntime", "runsc"),
        ("MemoryLimit", False),
        ("SwapLimit", False),
        ("PidsLimit", None),
        ("CpuCfsQuota", 1),
        ("SecurityOptions", ["name=apparmor"]),
        ("SecurityOptions", None),
        ("SecurityOptions", ["name=seccomp,profile=unconfined"]),
        ("SecurityOptions", ["name=seccomp"]),
        ("SecurityOptions", ["name=seccompx,profile=builtin"]),
    ],
)
def test_require_daemon_refuses_each_missing_provenance_field(key: str, value: object) -> None:
    require_daemon(good_info())
    info = good_info()
    info[key] = value
    with pytest.raises(DockerLifecycleFailure):
        require_daemon(info)
    with pytest.raises(DockerLifecycleFailure):
        require_daemon([good_info()])


def test_parse_ids_and_inspect_are_strict() -> None:
    a, b = "a" * 64, "b" * 64
    assert parse_ids(b"") == ()
    assert parse_ids(f"{a}\n{b}\n".encode()) == (a, b)
    for bad in (
        a.encode(),  # Missing trailing newline.
        f"{a}\n{a}\n".encode(),
        f"{a.upper()}\n".encode(),
        b"abc\n",
        f"{a}\n\n".encode(),
        f" {a}\n".encode(),
        b"\xff\n",
        "".join(f"{i:064x}\n" for i in range(MAX_LISTED_IDS + 1)).encode(),
    ):
        with pytest.raises(DockerLifecycleFailure):
            parse_ids(bad)
    assert parse_inspect(b'[{"Id": "x"}]') == {"Id": "x"}
    for bad in (b"{}", b"[]", b"[{}, {}]", b"[1]", b"not json"):
        with pytest.raises(DockerLifecycleFailure):
            parse_inspect(bad)


def test_config_dir_must_be_private_empty_real_directory(tmp_path) -> None:
    good = tmp_path / "config"
    good.mkdir(mode=0o700)
    check_config_dir(str(good))
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o700)
    os.chmod(loose, 0o750)
    full = tmp_path / "full"
    full.mkdir(mode=0o700)
    (full / "config.json").write_text("{}")
    link = tmp_path / "link"
    link.symlink_to(good)
    plain = tmp_path / "file"
    plain.write_text("")
    for bad in (
        "relative/config",
        str(tmp_path) + "/config/../config",
        str(tmp_path / "missing"),
        str(loose),
        str(full),
        str(link),
        str(plain),
    ):
        with pytest.raises(DockerLifecycleFailure):
            check_config_dir(bad)


@pytest.mark.asyncio
async def test_start_requires_preflight_and_bad_provenance_never_qualifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness()
    with pytest.raises(DockerLifecycleFailure, match="preflight"):
        await h.driver.start(COMMAND)
    assert h.docker.calls == []
    monkeypatch.setattr("scripts.web_fetch_pilot_docker.check_config_dir", lambda path: None)
    h.docker.info = {**good_info(), "CgroupVersion": "1"}
    with pytest.raises(DockerLifecycleFailure, match="provenance"):
        await h.driver.preflight()
    with pytest.raises(DockerLifecycleFailure, match="preflight"):
        await h.driver.start(COMMAND)
    assert h.docker.subcommands() == ["info"]


@pytest.mark.asyncio
async def test_create_inspect_attach_then_identity_checked_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness()
    driver = await h.qualified(monkeypatch)
    container = await driver.start(COMMAND)
    identifier = container.container_id
    assert h.docker.calls[2] == create_arguments(NAMES[0], COMMAND, run_token=driver.run_token)
    assert h.docker.subcommands() == ["ps", "ps", "create", "inspect"]
    assert h.launchers[0].argv == docker_argv("start", "--attach", "--interactive", identifier)
    assert driver.retained == (NAMES[0], identifier)
    assert await container.wait() == 0
    h.docker.calls.clear()
    await container.aclose()
    assert h.docker.calls == [
        ("inspect", "--type", "container", identifier),
        ("rm", "--force", identifier),
        ("ps", "--all", "--no-trunc", "--filter", "id=" + identifier, "--format", "{{.ID}}"),
    ]
    assert h.docker.containers == {}
    assert container.final_state == ("exited", 0)
    assert h.launchers[0].session_closed
    assert_released(h)
    await container.aclose()  # Repeated release is a no-op.
    assert len(h.docker.calls) == 3


@pytest.mark.asyncio
async def test_orphans_report_and_block_and_are_never_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness()
    driver = await h.qualified(monkeypatch)
    orphan = h.docker.add(NAMES[3])
    with pytest.raises(OrphansPresent) as raised:
        await driver.start(COMMAND)
    assert raised.value.ids == (orphan,)
    assert h.docker.subcommands() == ["ps", "ps"]
    assert orphan in h.docker.containers
    assert driver.retained is None and not driver.cleanup_failed


@pytest.mark.asyncio
async def test_create_refused_and_absent_by_name_is_ordinary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness()
    driver = await h.qualified(monkeypatch)

    async def refuse(args: tuple[str, ...]) -> CommandResult:
        return result(125)

    h.docker.hooks["create"] = refuse
    with pytest.raises(DockerLifecycleFailure, match="create refused"):
        await driver.start(COMMAND)
    assert h.docker.subcommands() == ["ps", "ps", "create", "ps"]
    assert h.docker.calls[3][4] == "name=^/?" + NAMES[0] + "$"
    assert h.launchers == []
    assert_released(h)


@pytest.mark.asyncio
async def test_create_timeout_after_daemon_created_is_resolved_and_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness()
    driver = await h.qualified(monkeypatch)

    async def created_then_timeout(args: tuple[str, ...]) -> CommandResult:
        h.docker.do_create(args)
        raise CommandLimitFailure("command deadline exceeded; owned child stopped")

    h.docker.hooks["create"] = created_then_timeout
    with pytest.raises(DockerLifecycleFailure, match="verified removed"):
        await driver.start(COMMAND)
    assert h.docker.subcommands() == ["ps", "ps", "create", "ps", "inspect", "inspect", "rm", "ps"]
    assert h.docker.containers == {}
    assert_released(h)


@pytest.mark.asyncio
async def test_same_name_foreign_container_is_never_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness()
    driver = await h.qualified(monkeypatch)

    async def foreign_conflict(args: tuple[str, ...]) -> CommandResult:
        h.docker.add(NAMES[0], label="someone-else")
        return result(125)

    h.docker.hooks["create"] = foreign_conflict
    with pytest.raises(DockerLifecycleFailure):
        await driver.start(COMMAND)
    assert "rm" not in h.docker.subcommands()
    assert len(h.docker.containers) == 1
    assert_released(h)


@pytest.mark.asyncio
async def test_policy_mismatch_after_create_removes_owned_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness()
    driver = await h.qualified(monkeypatch)

    async def bridge_network(args: tuple[str, ...]) -> CommandResult:
        outcome = h.docker.do_create(args)
        for record in h.docker.containers.values():
            record["HostConfig"]["NetworkMode"] = "bridge"
        return outcome

    h.docker.hooks["create"] = bridge_network
    with pytest.raises(DockerLifecycleFailure, match="verified removed"):
        await driver.start(COMMAND)
    assert h.launchers == []  # Nothing executes before the preflight passes.
    assert h.docker.containers == {}
    assert_released(h)


@pytest.mark.asyncio
async def test_attach_launch_failure_removes_container_and_closes_attach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness()
    h.launch_error = ProcessLaunchFailure("native backend refused launch; no owned handle")
    driver = await h.qualified(monkeypatch)
    with pytest.raises(DockerLifecycleFailure, match="verified removed"):
        await driver.start(COMMAND)
    assert h.docker.containers == {}
    assert_released(h)


@pytest.mark.asyncio
async def test_removal_refused_is_fatal_retained_visible_and_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness()
    driver = await h.qualified(monkeypatch)
    container = await driver.start(COMMAND)

    async def refuse(args: tuple[str, ...]) -> CommandResult:
        return result(1)

    h.docker.hooks["rm"] = refuse
    with pytest.raises(DockerCleanupFailure):
        await container.aclose()
    assert driver.cleanup_failed
    assert driver.retained == (NAMES[0], container.container_id)
    assert h.launchers[0].closed and h.launchers[0].aclosed  # Attach still stopped.
    calls = len(h.docker.calls)
    with pytest.raises(DockerCleanupFailure):
        await driver.start(COMMAND)
    with pytest.raises(DockerCleanupFailure):
        await driver.aclose()
    assert len(h.docker.calls) == calls
    assert container.container_id in h.docker.containers


@pytest.mark.asyncio
async def test_still_present_after_successful_rm_is_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness()
    driver = await h.qualified(monkeypatch)
    container = await driver.start(COMMAND)

    async def pretend(args: tuple[str, ...]) -> CommandResult:
        return result(0)

    h.docker.hooks["rm"] = pretend
    with pytest.raises(DockerCleanupFailure):
        await container.aclose()
    assert driver.cleanup_failed and driver.retained is not None


@pytest.mark.asyncio
async def test_identity_change_at_teardown_is_fatal_and_never_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness()
    driver = await h.qualified(monkeypatch)
    container = await driver.start(COMMAND)
    h.docker.containers[container.container_id]["Image"] = "sha256:" + "0" * 64
    with pytest.raises(DockerCleanupFailure):
        await container.aclose()
    assert "rm" not in h.docker.subcommands()
    assert container.container_id in h.docker.containers
    assert driver.cleanup_failed


@pytest.mark.asyncio
async def test_cleanup_command_timeout_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness()
    driver = await h.qualified(monkeypatch)
    container = await driver.start(COMMAND)

    async def timeout(args: tuple[str, ...]) -> CommandResult:
        raise CommandLimitFailure("command deadline exceeded; owned child stopped")

    h.docker.hooks["inspect"] = timeout
    with pytest.raises(DockerCleanupFailure):
        await container.aclose()
    assert driver.cleanup_failed
    assert h.launchers[0].aclosed


@pytest.mark.asyncio
async def test_attach_teardown_failure_is_fatal_even_after_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness()
    h.fail_close = True
    driver = await h.qualified(monkeypatch)
    container = await driver.start(COMMAND)
    with pytest.raises(DockerCleanupFailure):
        await container.aclose()
    assert h.docker.containers == {}
    assert driver.cleanup_failed


@pytest.mark.asyncio
async def test_cancel_during_create_keeps_teardown_owned_and_removes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness()
    driver = await h.qualified(monkeypatch)
    entered = asyncio.Event()

    async def created_then_stall(args: tuple[str, ...]) -> CommandResult:
        h.docker.do_create(args)
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    h.docker.hooks["create"] = created_then_stall
    start = asyncio.create_task(driver.start(COMMAND))
    await asyncio.wait_for(entered.wait(), 1)
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start
    await asyncio.wait_for(driver.aclose(), 1)
    assert h.docker.containers == {}
    assert_released(h)
    with pytest.raises(DockerLifecycleFailure, match="closed"):
        await driver.start(COMMAND)


@pytest.mark.asyncio
async def test_single_allocation_slot_and_driver_aclose_removes_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness()
    driver = await h.qualified(monkeypatch)
    container = await driver.start(COMMAND)
    calls = len(h.docker.calls)
    with pytest.raises(DockerLifecycleFailure, match="occupied"):
        await driver.start(COMMAND)
    assert len(h.docker.calls) == calls
    await driver.aclose()
    assert container.container_id not in h.docker.containers
    assert_released(h)


@pytest.mark.asyncio
async def test_finished_failed_teardown_is_never_reported_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness()
    driver = await h.qualified(monkeypatch)
    entered = asyncio.Event()

    async def created_then_stall(args: tuple[str, ...]) -> CommandResult:
        h.docker.do_create(args)
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def refuse(args: tuple[str, ...]) -> CommandResult:
        return result(1)

    h.docker.hooks["create"] = created_then_stall
    h.docker.hooks["rm"] = refuse
    start = asyncio.create_task(driver.start(COMMAND))
    await asyncio.wait_for(entered.wait(), 1)
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start
    allocation = driver._allocation
    assert allocation is not None and allocation.teardown is not None
    if not allocation.teardown.done():
        await asyncio.wait({allocation.teardown})
    assert allocation.teardown.done()  # Finished; aclose follows with no loop turn.
    with pytest.raises(DockerCleanupFailure):
        await driver.aclose()
    assert driver.cleanup_failed
    assert driver.retained is not None
    assert len(h.docker.containers) == 1


BROWSER_NAMES = [BROWSER_NAME_PREFIX + c * 24 for c in "0123"]


def pinned_browser_policy() -> ContainerPolicy:
    return browser_policy(str(PROFILE_PATH), PROFILE_PATH.read_bytes())


@pytest.mark.asyncio
async def test_browser_policy_creates_preflights_attaches_and_removes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(policy=pinned_browser_policy(), names=BROWSER_NAMES)
    h.docker.record_factory = browser_fixture
    driver = await h.qualified(monkeypatch)
    container = await driver.start(COMMAND)
    assert h.docker.calls[2] == browser_create_arguments(
        BROWSER_NAMES[0], COMMAND, str(PROFILE_PATH), run_token=driver.run_token
    )
    identifier = container.container_id
    assert h.launchers[0].argv == docker_argv("start", "--attach", "--interactive", identifier)
    await container.aclose()
    assert h.docker.containers == {}
    assert_released(h)


@pytest.mark.asyncio
async def test_browser_policy_refuses_gateway_shaped_record_and_removes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(policy=pinned_browser_policy(), names=BROWSER_NAMES)
    h.docker.record_factory = policy_record  # Gateway limits, no browser seccomp.
    driver = await h.qualified(monkeypatch)
    with pytest.raises(DockerLifecycleFailure, match="verified removed"):
        await driver.start(COMMAND)
    assert h.launchers == [] and h.docker.containers == {}
    assert_released(h)


def test_browser_policy_refuses_unpinned_profile_and_driver_refuses_untrusted_policy() -> None:
    raw = PROFILE_PATH.read_bytes()
    with pytest.raises(PreflightError):
        browser_policy(str(PROFILE_PATH), raw + b"\n")
    h = Harness()
    with pytest.raises(ValueError):
        OfflineContainerDriver(
            h.docker,
            h.attach,
            config_dir=CONFIG_DIR,
            policy=object(),  # type: ignore[arg-type]
        )
    assert pinned_browser_policy().name_factory().startswith(BROWSER_NAME_PREFIX)


@pytest.mark.asyncio
async def test_same_run_sibling_does_not_block_but_other_run_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(names=NAMES[:1])
    driver = await h.qualified(monkeypatch)
    sibling = h.docker.add(NAMES[3])
    h.docker.containers[sibling]["Config"]["Labels"][RUN_LABEL] = driver.run_token
    container = await driver.start(COMMAND)  # Same run: not an orphan.
    await container.aclose()
    assert sibling in h.docker.containers  # Never removed by this allocation.
    other = Harness(names=NAMES[1:2])
    other.docker = h.docker
    other.driver = OfflineContainerDriver(
        h.docker, other.attach, config_dir=CONFIG_DIR, name_factory=lambda: NAMES[1]
    )
    await other.qualified(monkeypatch)
    with pytest.raises(OrphansPresent) as raised:
        await other.driver.start(COMMAND)  # Different run token: report and block.
    assert raised.value.ids == (sibling,)
    assert sibling in h.docker.containers


def test_run_token_default_is_fresh_and_malformed_refused() -> None:
    h = Harness()
    first = OfflineContainerDriver(h.docker, h.attach, config_dir=CONFIG_DIR)
    second = OfflineContainerDriver(h.docker, h.attach, config_dir=CONFIG_DIR)
    assert len(first.run_token) == 32 and first.run_token != second.run_token
    shared = OfflineContainerDriver(h.docker, h.attach, config_dir=CONFIG_DIR, run_token="a" * 32)
    assert shared.run_token == "a" * 32
    for bad in ("A" * 32, "a" * 31, "", 5):
        with pytest.raises(ValueError):
            OfflineContainerDriver(
                h.docker,
                h.attach,
                config_dir=CONFIG_DIR,
                run_token=bad,  # type: ignore[arg-type]
            )


NET_RUN = "b" * 32
NET_NAMES = ["daemon-net-offline-" + c * 24 for c in "1234"]


def network_owner(docker: FakeDocker, name: str = NET_NAMES[0]) -> OwnedNetwork:
    return OwnedNetwork(docker, config_dir=CONFIG_DIR, run_token=NET_RUN, name_factory=lambda: name)


def network_calls(docker: FakeDocker) -> list[str]:
    return [call[1] for call in docker.calls if call[0] == "network"]


@pytest.mark.asyncio
async def test_network_create_preflight_then_identity_checked_removal() -> None:
    docker = FakeDocker()
    owner = network_owner(docker)
    identifier = await owner.create()
    assert network_calls(docker) == ["ls", "ls", "create", "inspect"]
    assert docker.calls[2][:2] == ("network", "create") and docker.calls[2][-1] == NET_NAMES[0]
    assert owner.retained == (NET_NAMES[0], identifier)
    await owner.aclose()
    assert network_calls(docker)[4:] == ["inspect", "rm", "ls"]
    assert docker.networks == {} and owner.retained is None and not owner.cleanup_failed
    await owner.aclose()  # Idempotent.
    with pytest.raises(DockerLifecycleFailure, match="single use"):
        await owner.create()


@pytest.mark.asyncio
async def test_foreign_run_network_reports_and_blocks_never_removed() -> None:
    docker = FakeDocker()
    foreign = docker.add_network(NET_NAMES[3], {OWNER_LABEL: NET_NAMES[3], RUN_LABEL: "c" * 32})
    sibling = docker.add_network(NET_NAMES[2], {OWNER_LABEL: NET_NAMES[2], RUN_LABEL: NET_RUN})
    owner = network_owner(docker)
    with pytest.raises(OrphansPresent) as raised:
        await owner.create()
    assert raised.value.ids == (foreign,)  # Same-run sibling is not an orphan.
    assert set(docker.networks) == {foreign, sibling}
    assert "create" not in network_calls(docker)


@pytest.mark.asyncio
async def test_network_create_timeout_after_daemon_created_is_resolved_and_removed() -> None:
    docker = FakeDocker()

    async def created_then_timeout(args: tuple[str, ...]) -> CommandResult | None:
        if args[1] != "create":
            return None
        docker.do_network(args)
        raise CommandLimitFailure("command deadline exceeded; owned child stopped")

    docker.hooks["network"] = created_then_timeout
    owner = network_owner(docker)
    with pytest.raises(DockerLifecycleFailure, match="verified removed"):
        await owner.create()
    assert docker.networks == {} and owner.retained is None


@pytest.mark.asyncio
async def test_similar_or_same_name_foreign_network_is_never_removed() -> None:
    docker = FakeDocker()
    foreign = docker.add_network(NET_NAMES[0] + "-x", {OWNER_LABEL: "someone-else"})

    async def refuse(args: tuple[str, ...]) -> CommandResult | None:
        return result(1) if args[1] == "create" else None

    docker.hooks["network"] = refuse
    owner = network_owner(docker)
    with pytest.raises(DockerLifecycleFailure):
        await owner.create()
    assert foreign in docker.networks and "rm" not in network_calls(docker)


@pytest.mark.asyncio
async def test_network_policy_mismatch_removes_owned_network() -> None:
    docker = FakeDocker()

    async def no_isolation(args: tuple[str, ...]) -> CommandResult | None:
        if args[1] != "create":
            return None
        outcome = docker.do_network(args)
        for record in docker.networks.values():
            record["Options"] = {"com.docker.network.bridge.enable_ip_masquerade": "false"}
        return outcome

    docker.hooks["network"] = no_isolation
    owner = network_owner(docker)
    with pytest.raises(DockerLifecycleFailure, match="verified removed"):
        await owner.create()
    assert docker.networks == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["refused", "still-present"])
async def test_network_removal_uncertainty_is_fatal_and_retained(mode: str) -> None:
    docker = FakeDocker()
    owner = network_owner(docker)
    identifier = await owner.create()

    async def bad_rm(args: tuple[str, ...]) -> CommandResult | None:
        if args[1] != "rm":
            return None
        return result(1) if mode == "refused" else result(0)

    docker.hooks["network"] = bad_rm
    with pytest.raises(DockerCleanupFailure):
        await owner.aclose()
    assert owner.cleanup_failed and owner.retained == (NET_NAMES[0], identifier)
    assert identifier in docker.networks
    with pytest.raises(DockerCleanupFailure):
        await owner.aclose()
    with pytest.raises(DockerCleanupFailure):
        await owner.create()


@pytest.mark.asyncio
async def test_cancel_during_network_create_keeps_teardown_owned() -> None:
    docker = FakeDocker()
    entered = asyncio.Event()

    async def created_then_stall(args: tuple[str, ...]) -> CommandResult | None:
        if args[1] != "create":
            return None
        docker.do_network(args)
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    docker.hooks["network"] = created_then_stall
    owner = network_owner(docker)
    task = asyncio.create_task(owner.create())
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(owner.aclose(), 1)
    assert docker.networks == {} and owner.retained is None and not owner.cleanup_failed


def test_network_owner_constructor_refusals() -> None:
    docker = FakeDocker()
    for kwargs in (
        {"config_dir": "relative", "run_token": NET_RUN},
        {"config_dir": CONFIG_DIR, "run_token": "B" * 32},
    ):
        with pytest.raises(ValueError):
            OwnedNetwork(docker, **kwargs)  # type: ignore[arg-type]
    assert docker.calls == []


@pytest.mark.asyncio
async def test_egress_network_lifecycle_uses_egress_kind_preflight() -> None:
    docker = FakeDocker()
    docker.network_record_factory = egress_network_fixture
    name = "daemon-net-egress-" + "9" * 24
    owner = network_owner(docker, name)
    identifier = await owner.create()
    create = next(c for c in docker.calls if c[:2] == ("network", "create"))
    assert "--internal" not in create and "10.251.248.0/29" in create
    await owner.aclose()
    assert identifier not in docker.networks and owner.retained is None


@pytest.mark.asyncio
async def test_egress_name_with_internal_record_is_refused_and_removed() -> None:
    docker = FakeDocker()  # Daemon returns internal-shaped config for an egress name.
    owner = network_owner(docker, "daemon-net-egress-" + "a" * 24)
    with pytest.raises(DockerLifecycleFailure, match="verified removed"):
        await owner.create()
    assert docker.networks == {}
