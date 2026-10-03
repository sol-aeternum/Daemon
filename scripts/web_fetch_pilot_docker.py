"""Pilot-only owned offline container lifecycle driver; no CLI or import I/O.

Owner decisions (2026-10-02):
- **Pinned daemon**: fixed ``/usr/bin/docker`` with ``--host
  unix:///run/docker.sock`` and an environment containing only a private,
  empty ``DOCKER_CONFIG`` directory (no ambient contexts, credential helpers,
  proxies or remote daemon). Daemon provenance is checked before any create.
- **create -> inspect -> start -ai**: short commands run through the bounded
  command runner; the policy preflight checks the created record before
  anything executes; ``docker start --attach --interactive <full-id>`` runs as
  one owned raw-launch child carrying the container's stdio. Killing that CLI
  never counts as stopping the container; removal is separate.
- **Orphans report and block**: an existing owner-labelled container refuses
  new work and is never removed by this process.

One allocation at a time. Ownership intent (the fresh unpredictable name) is
recorded **before** create, so a create with unknown outcome is resolved by
exact-name lookup plus independent identity inspection. Removal targets only
the retained full ID after re-inspected identity, then verifies absence.
Any uncertain teardown latches fatal: allocation retained visible, no reset,
no new starts. A late daemon-side create after an unknown create outcome is
caught by the next start's owner-label orphan check, not silently ignored.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from scripts.web_fetch_pilot_command import CommandLimitFailure, CommandResult
from scripts.web_fetch_pilot_container_policy import (
    OWNER_LABEL,
    RUN_LABEL,
    PreflightError,
    browser_create_arguments,
    browser_seccomp_option,
    create_arguments,
    require_browser_identity,
    require_identity,
    network_create_arguments,
    networked_create_arguments,
    require_network_identity,
    require_networked,
    require_networked_identity,
    require_offline_browser,
    require_offline_gateway,
    require_offline_network,
)
from scripts.web_fetch_pilot_process import (
    ProcessCleanupFailure,
    ProcessLaunchFailure,
    RawByteChannel,
)

DOCKER = "/usr/bin/docker"
DOCKER_HOST = "unix:///run/docker.sock"
COMMAND_SECONDS = 15.0  # Create/start-side commands.
CLEANUP_COMMAND_SECONDS = 10.0  # Each teardown command; at most six per allocation.
MAX_NAME_CANDIDATES = 2
MAX_LISTED_IDS = 16
NAME_PREFIX = "daemon-dns-offline-"
BROWSER_NAME_PREFIX = "daemon-browser-offline-"
_ID = re.compile(r"[0-9a-f]{64}\Z")
_RUN_TOKEN = re.compile(r"[0-9a-f]{32}\Z")


class DockerLifecycleFailure(RuntimeError):
    """Ordinary refusal; nothing this driver created remains (verified)."""


class OrphansPresent(DockerLifecycleFailure):
    """Owner-labelled containers exist; report and block, never auto-remove."""

    def __init__(self, ids: tuple[str, ...]) -> None:
        super().__init__(f"{len(ids)} owner-labelled container(s) present; owner disposition")
        self.ids = ids


class DockerCleanupFailure(RuntimeError):
    """Fatal ownership uncertainty: allocation retained visible, never resets."""


class CommandRunner(Protocol):
    async def run(
        self,
        argv: Sequence[str],
        env: Mapping[str, str],
        *,
        stdin: bytes = b"",
        timeout: float,
    ) -> CommandResult: ...


class AttachSession(Protocol):
    @property
    def stdin(self) -> RawByteChannel: ...

    @property
    def stdout(self) -> RawByteChannel: ...

    @property
    def stderr(self) -> RawByteChannel: ...

    async def wait(self) -> int: ...

    async def aclose(self) -> None: ...


class AttachLauncher(Protocol):
    async def launch(self, argv: Sequence[str], env: Mapping[str, str]) -> AttachSession: ...

    def close(self) -> None: ...

    async def aclose(self) -> None: ...


def docker_argv(*args: str) -> tuple[str, ...]:
    """Fixed client and daemon; never PATH, contexts or DOCKER_HOST."""
    return (DOCKER, "--host", DOCKER_HOST, *args)


def fresh_name() -> str:
    return NAME_PREFIX + secrets.token_hex(12)


def fresh_network_name() -> str:
    return "daemon-net-offline-" + secrets.token_hex(12)


def fresh_browser_name() -> str:
    return BROWSER_NAME_PREFIX + secrets.token_hex(12)


@dataclass(frozen=True)
class ContainerPolicy:
    """Trusted pure policy for one container role; never record-derived."""

    name_factory: Callable[[], str]
    create: Callable[[str, tuple[str, ...], str], tuple[str, ...]]
    preflight: Callable[[object, str, tuple[str, ...], str, str], str]
    identity: Callable[[object, str, str | None, str], str]


GATEWAY_POLICY = ContainerPolicy(
    fresh_name,
    lambda name, command, run: create_arguments(name, command, run_token=run),
    lambda record, name, command, identifier, run: require_offline_gateway(
        record, name, command, container_id=identifier, run_token=run
    ),
    lambda record, name, identifier, run: require_identity(
        record, name, container_id=identifier, run_token=run
    ),
)


def browser_policy(profile_path: str, profile: bytes) -> ContainerPolicy:
    """Browser role over the pinned profile bytes, verified here once.

    The CLI reads ``profile_path`` at create; the preflight compares the stored
    option with these verified bytes, so a file changed in between is refused.
    """
    option = browser_seccomp_option(profile)
    return ContainerPolicy(
        fresh_browser_name,
        lambda name, command, run: browser_create_arguments(
            name, command, profile_path, run_token=run
        ),
        lambda record, name, command, identifier, run: require_offline_browser(
            record, name, command, option, container_id=identifier, run_token=run
        ),
        lambda record, name, identifier, run: require_browser_identity(
            record, name, container_id=identifier, run_token=run
        ),
    )


def networked_policy(
    network: str, network_id: str, role: str, *, alias: str | None = None
) -> ContainerPolicy:
    """Gateway-limit role attached only to the owned internal network."""
    if role not in ("gateway", "fixture"):
        raise ValueError("networked role must be gateway or fixture")
    return ContainerPolicy(
        lambda: f"daemon-{role}-" + secrets.token_hex(12),
        lambda name, command, run: networked_create_arguments(
            name, command, network, run_token=run, alias=alias
        ),
        lambda record, name, command, identifier, run: require_networked(
            record,
            name,
            command,
            network,
            run_token=run,
            network_id=network_id,
            alias=alias,
            container_id=identifier,
        ),
        lambda record, name, identifier, run: require_networked_identity(
            record, name, container_id=identifier, run_token=run
        ),
    )


def _refuse(message: str) -> DockerLifecycleFailure:
    return DockerLifecycleFailure(message)


def require_daemon(info: object) -> None:
    """Pure provenance check of ``docker info --format '{{json .}}'``.

    The pinned unix socket establishes locality; this establishes the runtime
    and kernel features the policy limits depend on. It is not enforcement
    evidence: actual limits still need the native container gate.
    """
    if type(info) is not dict:
        raise _refuse("daemon provenance refused")
    for key, expected in (
        ("OSType", "linux"),
        ("CgroupVersion", "2"),
        ("DefaultRuntime", "runc"),
        ("MemoryLimit", True),
        ("SwapLimit", True),
        ("PidsLimit", True),
        ("CpuCfsQuota", True),
    ):
        value = info.get(key)
        if type(value) is not type(expected) or value != expected:
            raise _refuse("daemon provenance refused")
    options = info.get("SecurityOptions")
    if type(options) is not list or not any(_seccomp_enforcing(item) for item in options):
        raise _refuse("daemon provenance refused")


def _seccomp_enforcing(item: object) -> bool:
    """``name=seccomp`` with an explicit profile that is not ``unconfined``."""
    if not isinstance(item, str):
        return False
    fields: dict[str, str] = {}
    for part in item.split(","):
        key, _, value = part.partition("=")
        fields[key] = value
    profile = fields.get("profile", "")
    return fields.get("name") == "seccomp" and bool(profile) and profile != "unconfined"


def parse_ids(stdout: bytes) -> tuple[str, ...]:
    """Bounded newline-separated full container IDs; anything else refused."""
    try:
        text = stdout.decode("ascii")
    except UnicodeDecodeError:
        raise _refuse("container id listing refused") from None
    ids = tuple(line for line in text.split("\n") if line)
    if len(ids) > MAX_LISTED_IDS or any(_ID.fullmatch(item) is None for item in ids):
        raise _refuse("container id listing refused")
    if len(set(ids)) != len(ids) or text not in ("", "\n".join(ids) + "\n"):
        raise _refuse("container id listing refused")
    return ids


def parse_inspect(stdout: bytes) -> dict[str, object]:
    try:
        records = json.loads(stdout)
    except ValueError:
        raise _refuse("inspect record refused") from None
    if type(records) is not list or len(records) != 1 or type(records[0]) is not dict:
        raise _refuse("inspect record refused")
    return records[0]


def check_config_dir(path: str) -> None:
    """Private empty real directory owned by this user; refuse anything else."""
    if type(path) is not str or not os.path.isabs(path) or os.path.normpath(path) != path:
        raise _refuse("docker config directory refused")
    try:
        info = os.lstat(path)
        entries = os.listdir(path)
    except OSError:
        raise _refuse("docker config directory refused") from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or entries
    ):
        raise _refuse("docker config directory refused")


@dataclass(eq=False)
class _Allocation:
    name: str
    command: tuple[str, ...]
    container_id: str | None = None
    create_unknown: bool = False
    attach: AttachLauncher | None = None
    session: AttachSession | None = None
    final_state: tuple[str, int] | None = None
    teardown: asyncio.Task[None] | None = None
    released: bool = False


class AttachedContainer:
    """Caller view of one running owned container; stdio is the attach CLI.

    ``wait`` reports the attach CLI exit, not container-exit proof; teardown
    re-inspects ``State`` into ``final_state`` before removal.
    """

    def __init__(self, driver: OfflineContainerDriver, allocation: _Allocation) -> None:
        self._driver = driver
        self._allocation = allocation

    @property
    def name(self) -> str:
        return self._allocation.name

    @property
    def container_id(self) -> str:
        identifier = self._allocation.container_id
        if identifier is None:
            raise DockerCleanupFailure("container identity missing")
        return identifier

    @property
    def final_state(self) -> tuple[str, int] | None:
        return self._allocation.final_state

    def _session(self) -> AttachSession:
        session = self._allocation.session
        if session is None:
            raise DockerCleanupFailure("attach session missing")
        return session

    @property
    def stdin(self) -> RawByteChannel:
        return self._session().stdin

    @property
    def stdout(self) -> RawByteChannel:
        return self._session().stdout

    @property
    def stderr(self) -> RawByteChannel:
        return self._session().stderr

    async def wait(self) -> int:
        return await self._session().wait()

    async def aclose(self) -> None:
        await self._driver._release(self._allocation)


class OfflineContainerDriver:
    """One owned network-none container at a time over a pinned local daemon.

    Constructor is shape-only: no I/O, no event-loop binding. ``preflight``
    performs the config-directory and daemon provenance checks.
    """

    def __init__(
        self,
        runner: CommandRunner,
        attach_factory: Callable[[], AttachLauncher],
        *,
        config_dir: str,
        name_factory: Callable[[], str] | None = None,
        policy: ContainerPolicy = GATEWAY_POLICY,
        run_token: str | None = None,
    ) -> None:
        if not callable(getattr(runner, "run", None)) or not callable(attach_factory):
            raise ValueError("command runner and attach factory required")
        if type(config_dir) is not str or not os.path.isabs(config_dir):
            raise ValueError("absolute docker config directory required")
        if type(policy) is not ContainerPolicy:
            raise ValueError("trusted container policy required")
        if name_factory is None:
            name_factory = policy.name_factory
        if not callable(name_factory):
            raise ValueError("name factory required")
        if run_token is None:
            run_token = secrets.token_hex(16)  # This driver is its own run.
        if type(run_token) is not str or _RUN_TOKEN.fullmatch(run_token) is None:
            raise ValueError("128-bit lowercase hex run token required")
        self._run = run_token
        self._policy = policy
        self._runner = runner
        self._attach_factory = attach_factory
        self._env = {"DOCKER_CONFIG": config_dir}
        self._config_dir = config_dir
        self._name_factory = name_factory
        self._allocation: _Allocation | None = None
        self._qualified = False
        self._closing = False
        self._failed = False

    @property
    def cleanup_failed(self) -> bool:
        return self._failed

    @property
    def retained(self) -> tuple[str, str | None] | None:
        allocation = self._allocation
        if allocation is None:
            return None
        return allocation.name, allocation.container_id

    @property
    def pending_tasks(self) -> tuple[asyncio.Task[None], ...]:
        allocation = self._allocation
        if allocation is None or allocation.teardown is None or allocation.teardown.done():
            return ()
        return (allocation.teardown,)

    def _latch_fatal(self) -> None:
        self._failed = True
        self._closing = True

    async def _docker(self, *args: str, timeout: float = COMMAND_SECONDS) -> CommandResult:
        return await self._runner.run(docker_argv(*args), dict(self._env), timeout=timeout)

    async def preflight(self) -> None:
        """Private config directory and pinned-daemon provenance, before create."""
        if self._failed:
            raise DockerCleanupFailure("driver fatal; new allocation prohibited")
        check_config_dir(self._config_dir)
        result = await self._docker("info", "--format", "{{json .}}")
        if result.returncode != 0:
            raise _refuse("daemon provenance refused")
        try:
            info = json.loads(result.stdout)
        except ValueError:
            raise _refuse("daemon provenance refused") from None
        require_daemon(info)
        self._qualified = True

    @property
    def run_token(self) -> str:
        return self._run

    async def _listed(self, label_filter: str) -> tuple[str, ...]:
        result = await self._docker(
            "ps", "--all", "--no-trunc", "--filter", label_filter, "--format", "{{.ID}}"
        )
        if result.returncode != 0:
            raise _refuse("owner-label listing refused")
        return parse_ids(result.stdout)

    async def owned_ids(self) -> tuple[str, ...]:
        """Every owner-labelled container, from any run."""
        return await self._listed("label=" + OWNER_LABEL)

    async def foreign_ids(self) -> tuple[str, ...]:
        """Owner-labelled containers NOT stamped with this run's token."""
        everything = await self.owned_ids()
        ours = set(await self._listed("label=" + RUN_LABEL + "=" + self._run))
        return tuple(identifier for identifier in everything if identifier not in ours)

    async def start(self, command: tuple[str, ...]) -> AttachedContainer:
        """Create, preflight and attach one owned container; see module docs."""
        if self._failed:
            raise DockerCleanupFailure("driver fatal; new allocation prohibited")
        if self._closing:
            raise _refuse("driver closed")
        if not self._qualified:
            raise _refuse("preflight required before create")
        if self._allocation is not None:
            raise _refuse("allocation slot occupied")
        arguments = self._policy.create(self._name_factory(), command, self._run)  # Checked.
        name = arguments[arguments.index("--name") + 1]
        orphans = await self.foreign_ids()  # Other runs report and block; ours do not.
        if orphans:
            raise OrphansPresent(orphans)
        if self._allocation is not None or self._closing:
            raise _refuse("allocation slot occupied")
        allocation = _Allocation(name, command)
        self._allocation = allocation  # Ownership intent precedes create.
        failure: BaseException | None = None
        try:
            await self._create(allocation, arguments)
            await self._preflight_created(allocation)
            launcher = self._attach_factory()
            allocation.attach = launcher
            identifier = allocation.container_id
            if identifier is None:
                raise DockerCleanupFailure("container identity missing")
            allocation.session = await launcher.launch(
                docker_argv("start", "--attach", "--interactive", identifier), dict(self._env)
            )
        except BaseException as exc:
            failure = exc
        if failure is None:
            return AttachedContainer(self, allocation)
        teardown = self._start_teardown(allocation)
        if isinstance(failure, asyncio.CancelledError):
            raise failure  # Teardown stays driver-owned; aclose awaits it.
        await asyncio.shield(teardown)  # Fatal teardown raises here.
        if isinstance(failure, (DockerCleanupFailure, ProcessCleanupFailure)):
            self._latch_fatal()
            raise DockerCleanupFailure("start failed with ownership uncertainty") from None
        if isinstance(failure, DockerLifecycleFailure):
            raise failure
        if isinstance(failure, (PreflightError, CommandLimitFailure, ProcessLaunchFailure)):
            raise _refuse("start refused; owned container verified removed") from None
        raise failure

    async def _create(self, allocation: _Allocation, arguments: tuple[str, ...]) -> None:
        allocation.create_unknown = True  # Until a definite outcome is observed.
        result = await self._docker(*arguments)
        if result.returncode != 0:
            raise _refuse("docker create refused")
        ids = parse_ids(result.stdout)
        if len(ids) != 1:
            raise _refuse("docker create returned no single full id")
        allocation.container_id = ids[0]
        allocation.create_unknown = False

    async def _inspect(self, identifier: str, *, timeout: float) -> dict[str, object]:
        result = await self._docker("inspect", "--type", "container", identifier, timeout=timeout)
        if result.returncode != 0:
            raise _refuse("inspect refused")
        return parse_inspect(result.stdout)

    async def _preflight_created(self, allocation: _Allocation) -> None:
        identifier = allocation.container_id
        if identifier is None:
            raise DockerCleanupFailure("container identity missing")
        record = await self._inspect(identifier, timeout=COMMAND_SECONDS)
        self._policy.preflight(record, allocation.name, allocation.command, identifier, self._run)

    def _start_teardown(self, allocation: _Allocation) -> asyncio.Task[None]:
        if allocation.teardown is not None:
            return allocation.teardown
        task = asyncio.get_running_loop().create_task(
            self._teardown(allocation), name="pilot-docker-teardown"
        )
        allocation.teardown = task
        driver = self

        def settled(done: asyncio.Task[None]) -> None:
            # Backstop only: _teardown itself publishes release/fatal state with
            # no deferred step, so awaiting an already-finished teardown never
            # observes stale ownership. A cancelled teardown is fatal.
            if done.cancelled() or done.exception() is not None:
                driver._latch_fatal()  # Allocation stays visible in ``retained``.

        task.add_done_callback(settled)
        return task

    async def _teardown(self, allocation: _Allocation) -> None:
        """Remove only the identity-checked owned container, then close attach."""
        failure: BaseException | None = None
        try:
            await self._remove(allocation)
        except BaseException as exc:
            failure = exc
        launcher = allocation.attach
        if launcher is not None:
            launcher.close()
            try:
                if allocation.session is not None:
                    await allocation.session.aclose()
                await launcher.aclose()
            except BaseException as exc:
                failure = failure or exc
        if failure is not None:
            self._latch_fatal()  # Allocation stays visible in ``retained``.
            raise DockerCleanupFailure("owned container teardown uncertain") from None
        allocation.released = True
        if self._allocation is allocation:
            self._allocation = None

    async def _resolve_by_name(self, allocation: _Allocation) -> str | None:
        result = await self._docker(
            "ps",
            "--all",
            "--no-trunc",
            "--filter",
            "name=^/?" + allocation.name + "$",
            "--format",
            "{{.ID}}",
            timeout=CLEANUP_COMMAND_SECONDS,
        )
        if result.returncode != 0:
            raise DockerCleanupFailure("name resolution failed")
        candidates = parse_ids(result.stdout)
        if len(candidates) > MAX_NAME_CANDIDATES:
            raise DockerCleanupFailure("ambiguous owned container identity")
        owned: list[str] = []
        for candidate in candidates:
            record = await self._inspect(candidate, timeout=CLEANUP_COMMAND_SECONDS)
            try:
                owned.append(self._policy.identity(record, allocation.name, candidate, self._run))
            except PreflightError:
                continue  # Same-name foreign container: never ours, never removed.
        if len(owned) > 1:
            raise DockerCleanupFailure("ambiguous owned container identity")
        return owned[0] if owned else None

    async def _remove(self, allocation: _Allocation) -> None:
        identifier = allocation.container_id
        if identifier is None:
            if not allocation.create_unknown:
                return  # Create never ran.
            identifier = await self._resolve_by_name(allocation)
            if identifier is None:
                return  # Absent now; a late create is caught by the orphan check.
            allocation.container_id = identifier
        record = await self._inspect(identifier, timeout=CLEANUP_COMMAND_SECONDS)
        self._policy.identity(record, allocation.name, identifier, self._run)
        state = record.get("State")
        if type(state) is dict:
            status, code = state.get("Status"), state.get("ExitCode")
            if type(status) is str and type(code) is int:
                allocation.final_state = (status, code)
        removed = await self._docker("rm", "--force", identifier, timeout=CLEANUP_COMMAND_SECONDS)
        if removed.returncode != 0:
            raise DockerCleanupFailure("owned container removal refused")
        listed = await self._docker(
            "ps",
            "--all",
            "--no-trunc",
            "--filter",
            "id=" + identifier,
            "--format",
            "{{.ID}}",
            timeout=CLEANUP_COMMAND_SECONDS,
        )
        if listed.returncode != 0 or parse_ids(listed.stdout):
            raise DockerCleanupFailure("owned container still present after removal")

    async def _release(self, allocation: _Allocation) -> None:
        if allocation.released:
            if self._failed:
                raise DockerCleanupFailure("driver cleanup failed")
            return
        teardown = self._start_teardown(allocation)
        await asyncio.shield(teardown)
        if self._failed:
            raise DockerCleanupFailure("driver cleanup failed")

    def close(self) -> None:
        """Refuse new starts; stop the attach CLI (removal needs ``aclose``)."""
        self._closing = True
        allocation = self._allocation
        if allocation is not None and allocation.attach is not None:
            allocation.attach.close()

    async def aclose(self) -> None:
        """Remove any owned allocation; fatal DockerCleanupFailure on uncertainty."""
        self.close()
        allocation = self._allocation
        if allocation is not None:
            await self._release(allocation)
        if self._failed:
            raise DockerCleanupFailure("driver cleanup failed; allocation retained visible")


class OwnedNetwork:
    """One disposable internal network for one run; same fatal contract as containers.

    Single use. Ownership intent (fresh name) precedes ``network create``; an
    unknown create outcome is resolved by exact name plus identity inspection.
    The policy preflight runs on the created record. Teardown re-inspects
    identity, removes only the retained full ID and verifies absence; it runs as
    an owned task that caller cancellation never cancels. Any uncertain step
    latches fatal and keeps the network visible in ``retained``. Containers on
    the network must be removed first; a refused removal is fatal, not retried.
    """

    def __init__(
        self,
        runner: CommandRunner,
        *,
        config_dir: str,
        run_token: str,
        name_factory: Callable[[], str] = fresh_network_name,
    ) -> None:
        if not callable(getattr(runner, "run", None)) or not callable(name_factory):
            raise ValueError("command runner and name factory required")
        if type(config_dir) is not str or not os.path.isabs(config_dir):
            raise ValueError("absolute docker config directory required")
        if type(run_token) is not str or _RUN_TOKEN.fullmatch(run_token) is None:
            raise ValueError("128-bit lowercase hex run token required")
        self._runner = runner
        self._env = {"DOCKER_CONFIG": config_dir}
        self._run = run_token
        self._name_factory = name_factory
        self._name: str | None = None
        self._id: str | None = None
        self._create_unknown = False
        self._used = False
        self._released = False
        self._failed = False
        self._teardown: asyncio.Task[None] | None = None

    @property
    def cleanup_failed(self) -> bool:
        return self._failed

    @property
    def name(self) -> str | None:
        return self._name

    @property
    def network_id(self) -> str | None:
        return self._id

    @property
    def retained(self) -> tuple[str, str | None] | None:
        if self._name is None or self._released:
            return None
        return self._name, self._id

    async def _docker(self, *args: str, timeout: float = COMMAND_SECONDS) -> CommandResult:
        return await self._runner.run(docker_argv(*args), dict(self._env), timeout=timeout)

    async def _listed(
        self, network_filter: str, *, timeout: float = COMMAND_SECONDS
    ) -> tuple[str, ...]:
        result = await self._docker(
            "network",
            "ls",
            "--no-trunc",
            "--filter",
            network_filter,
            "--format",
            "{{.ID}}",
            timeout=timeout,
        )
        if result.returncode != 0:
            raise _refuse("network listing refused")
        return parse_ids(result.stdout)

    async def owned_ids(self) -> tuple[str, ...]:
        return await self._listed("label=" + OWNER_LABEL)

    async def foreign_ids(self) -> tuple[str, ...]:
        everything = await self.owned_ids()
        ours = set(await self._listed("label=" + RUN_LABEL + "=" + self._run))
        return tuple(identifier for identifier in everything if identifier not in ours)

    async def _inspect(self, identifier: str, *, timeout: float) -> dict[str, object]:
        result = await self._docker("network", "inspect", identifier, timeout=timeout)
        if result.returncode != 0:
            raise _refuse("network inspect refused")
        return parse_inspect(result.stdout)

    async def create(self) -> str:
        """Create, preflight and return the full network ID; see class docs."""
        if self._failed:
            raise DockerCleanupFailure("network owner fatal; new allocation prohibited")
        if self._used:
            raise _refuse("network owner is single use")
        self._used = True
        name = self._name_factory()
        arguments = network_create_arguments(name, self._run)  # Shape-checked.
        orphans = await self.foreign_ids()
        if orphans:
            raise OrphansPresent(orphans)
        self._name = name
        self._create_unknown = True  # Ownership intent precedes create.
        failure: BaseException | None = None
        try:
            result = await self._docker(*arguments)
            if result.returncode != 0:
                raise _refuse("network create refused")
            ids = parse_ids(result.stdout)
            if len(ids) != 1:
                raise _refuse("network create returned no single full id")
            self._id = ids[0]
            self._create_unknown = False
            record = await self._inspect(self._id, timeout=COMMAND_SECONDS)
            require_offline_network(record, name, self._run, network_id=self._id)
            return self._id
        except BaseException as exc:
            failure = exc
        teardown = self._start_teardown()
        if isinstance(failure, asyncio.CancelledError):
            raise failure  # Teardown stays owned; aclose awaits it.
        try:
            await asyncio.shield(teardown)
        except asyncio.CancelledError:
            raise
        except BaseException:
            raise DockerCleanupFailure("network create failed with ownership uncertainty") from None
        if isinstance(failure, (DockerLifecycleFailure, PreflightError, CommandLimitFailure)):
            raise _refuse("network create refused; owned network verified removed") from None
        raise failure

    def _start_teardown(self) -> asyncio.Task[None]:
        if self._teardown is None:
            task = asyncio.get_running_loop().create_task(
                self._remove_all(), name="pilot-network-teardown"
            )
            owner = self

            def settled(done: asyncio.Task[None]) -> None:
                if done.cancelled() or done.exception() is not None:
                    owner._failed = True  # Backstop; _remove_all publishes state itself.

            task.add_done_callback(settled)
            self._teardown = task
        return self._teardown

    async def _resolve_by_name(self, name: str) -> str | None:
        candidates = await self._listed("name=" + name, timeout=CLEANUP_COMMAND_SECONDS)
        if len(candidates) > MAX_NAME_CANDIDATES:
            raise DockerCleanupFailure("ambiguous owned network identity")
        owned: list[str] = []
        for candidate in candidates:
            record = await self._inspect(candidate, timeout=CLEANUP_COMMAND_SECONDS)
            try:
                owned.append(
                    require_network_identity(record, name, self._run, network_id=candidate)
                )
            except PreflightError:
                continue  # Similar or same-name foreign network: never removed.
        if len(owned) > 1:
            raise DockerCleanupFailure("ambiguous owned network identity")
        return owned[0] if owned else None

    async def _remove_all(self) -> None:
        try:
            name = self._name
            identifier = self._id
            if name is not None and identifier is None and self._create_unknown:
                identifier = await self._resolve_by_name(name)
                self._id = identifier
            if name is not None and identifier is not None:
                record = await self._inspect(identifier, timeout=CLEANUP_COMMAND_SECONDS)
                require_network_identity(record, name, self._run, network_id=identifier)
                removed = await self._docker(
                    "network", "rm", identifier, timeout=CLEANUP_COMMAND_SECONDS
                )
                if removed.returncode != 0:
                    raise DockerCleanupFailure("owned network removal refused")
                if await self._listed("id=" + identifier, timeout=CLEANUP_COMMAND_SECONDS):
                    raise DockerCleanupFailure("owned network still present after removal")
        except BaseException:
            self._failed = True  # Published synchronously; retained stays visible.
            raise
        self._released = True

    async def aclose(self) -> None:
        """Remove the owned network; fatal DockerCleanupFailure on uncertainty."""
        self._used = True
        if not self._released and not self._failed:
            try:
                await asyncio.shield(self._start_teardown())
            except asyncio.CancelledError:
                raise
            except BaseException:
                self._failed = True  # _remove_all already latched; never ordinary.
        if self._failed:
            raise DockerCleanupFailure("network cleanup failed; network retained visible")
