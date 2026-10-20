"""Owned config and fake Docker readback regressions; never invokes Docker."""

from __future__ import annotations

import asyncio
import io
import os
import tarfile
from pathlib import Path

import pytest

from scripts.web_fetch_pilot_config_snapshot import ConfigSnapshot, require_config_archive
from scripts.web_fetch_pilot_container_policy import OWNER_LABEL, RUN_LABEL, PreflightError
from scripts.web_fetch_pilot_docker import (
    DockerCleanupFailure,
    DockerLifecycleFailure,
    OfflineContainerDriver,
    c4ai_policy,
)
from tests.test_web_fetch_pilot_container_policy import (
    ALIAS,
    API,
    NET,
    NET_ID,
    OVERRIDE_PATH,
    PROFILE_PATH,
    RUN,
    c4ai_fixture,
    pinned_profile,
)
from tests.test_web_fetch_pilot_docker import CONFIG_DIR, FakeAttachLauncher, FakeDocker, result

CONTENT = OVERRIDE_PATH.read_bytes()


def archive(
    content: bytes = CONTENT,
    *,
    name: str = "config.yml",
    kind: bytes = tarfile.REGTYPE,
    extra: bool = False,
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as stream:
        entry = tarfile.TarInfo(name)
        entry.size, entry.type = len(content), kind
        if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            entry.linkname = "/foreign"
        stream.addfile(entry, io.BytesIO(content))
        if extra:
            stream.addfile(tarfile.TarInfo("extra"))
    return output.getvalue()


@pytest.mark.parametrize(
    "variant",
    [
        "valid",
        "mismatch",
        "symlink",
        "hardlink",
        "extra",
        "path",
        "truncated",
        "oversize",
        "missing_end",
    ],
)
def test_archive_is_single_bounded_regular_config(variant: str) -> None:
    variants = {
        "valid": archive(),
        "mismatch": archive(b"wrong"),
        "symlink": archive(kind=tarfile.SYMTYPE),
        "hardlink": archive(kind=tarfile.LNKTYPE),
        "extra": archive(extra=True),
        "path": archive(name="../config.yml"),
        "truncated": archive()[:600],
        "oversize": archive() + b"\0" * 65536,
        "missing_end": archive()[: 512 + ((len(CONTENT) + 511) // 512) * 512],
    }
    if variant == "valid":
        require_config_archive(variants[variant], CONTENT)
    else:
        with pytest.raises(PreflightError):
            require_config_archive(variants[variant], CONTENT)


def test_snapshot_is_private_exclusive_and_readonly(tmp_path: Path) -> None:
    snapshot = ConfigSnapshot(str(tmp_path), CONTENT)
    snapshot.create()
    assert Path(snapshot.path).read_bytes() == CONTENT
    assert Path(snapshot.path).stat().st_mode & 0o777 == 0o444
    assert Path(snapshot.path).parent.stat().st_mode & 0o777 == 0o700
    snapshot.verify()
    snapshot.cleanup()
    snapshot.cleanup()
    assert not Path(snapshot.path).parent.exists()


@pytest.mark.parametrize("mutate", ["content", "replace", "symlink", "hardlink"])
def test_snapshot_detects_mutation_and_preserves_foreign_inodes(
    tmp_path: Path, mutate: str
) -> None:
    snapshot = ConfigSnapshot(str(tmp_path), CONTENT)
    snapshot.create()
    path = Path(snapshot.path)
    original = path.parent / "original"
    if mutate == "content":
        path.chmod(0o600)
        path.write_bytes(b"different")
    elif mutate == "hardlink":
        os.link(path, original)
    else:
        path.rename(original)
        if mutate == "replace":
            path.write_bytes(CONTENT)
            path.chmod(0o444)
        else:
            path.symlink_to(original)
    with pytest.raises((PreflightError, OSError)):
        snapshot.verify()
    if mutate in ("replace", "symlink", "hardlink"):
        with pytest.raises(PreflightError):
            snapshot.cleanup()
        assert path.exists()  # Never delete foreign replacement / unknown extra.
        if mutate != "hardlink":
            path.unlink()
            original.rename(path)
        else:
            original.unlink()
    snapshot.cleanup()


def test_no_symlink_ancestor_even_with_same_owned_directory(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    snapshot = ConfigSnapshot(str(link), CONTENT)
    with pytest.raises(OSError):
        snapshot.create()
    snapshot.cleanup()
    assert not list(real.iterdir())


@pytest.mark.parametrize("change", ["ancestor_symlink", "directory_replace"])
def test_directory_substitution_is_refused_without_deleting_replacements(
    tmp_path: Path, change: str
) -> None:
    parent = tmp_path / "inputs"
    parent.mkdir(mode=0o700)
    snapshot = ConfigSnapshot(str(parent), CONTENT)
    snapshot.create()
    path = Path(snapshot.path)
    if change == "ancestor_symlink":
        saved = tmp_path / "saved"
        parent.rename(saved)
        parent.symlink_to(saved, target_is_directory=True)
    else:
        saved = parent / "saved"
        path.parent.rename(saved)
        path.parent.mkdir(mode=0o700)
    with pytest.raises((PreflightError, OSError)):
        snapshot.verify()
    with pytest.raises((PreflightError, OSError)):
        snapshot.cleanup()
    if change == "ancestor_symlink":
        parent.unlink()
        saved.rename(parent)
    else:
        path.parent.rmdir()
        saved.rename(path.parent)
    snapshot.cleanup()


def test_partial_stage_write_failure_closes_owned_descriptors(tmp_path: Path, monkeypatch) -> None:
    snapshot = ConfigSnapshot(str(tmp_path), CONTENT)
    fds = set(os.listdir("/proc/self/fd"))
    monkeypatch.setattr("scripts.web_fetch_pilot_config_snapshot.os.write", lambda *a: 0)
    with pytest.raises(PreflightError):
        snapshot.create()
    snapshot.cleanup()
    assert set(os.listdir("/proc/self/fd")) == fds
    assert not list(tmp_path.iterdir())


def test_bad_config_bytes_never_allocate_files(tmp_path: Path) -> None:
    with pytest.raises(PreflightError):
        ConfigSnapshot(str(tmp_path), b"unreviewed")
    assert not list(tmp_path.iterdir())


class FakeReaderDocker(FakeDocker):
    def __init__(self) -> None:
        super().__init__()
        self.mounted: Path | None = None

    def do_create(self, args: tuple[str, ...]):
        self._serial += 1
        identifier = f"{self._serial:064x}"
        name = args[args.index("--name") + 1]
        record = c4ai_fixture(sandboxed=True)
        source = args[args.index("--mount") + 1].split("source=", 1)[1].split(",", 1)[0]
        self.mounted = Path(source)
        record.update(Id=identifier, Name="/" + name, State={"Status": "created", "Running": False})
        record["Config"]["Labels"] = {OWNER_LABEL: name, RUN_LABEL: RUN}
        record["Mounts"][-1]["Source"] = source
        self.containers[identifier] = record
        return result(stdout=(identifier + "\n").encode())

    def do_cp(self, args: tuple[str, ...]):
        identifier = args[1].split(":", 1)[0]
        assert args == ("cp", identifier + ":/app/config.yml", "-")
        assert self.containers[identifier]["State"]["Status"] == "created"
        assert self.mounted is not None
        return result(stdout=archive(self.mounted.read_bytes()))


def reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, content: bytes = CONTENT):
    monkeypatch.setattr("scripts.web_fetch_pilot_docker.check_config_dir", lambda _: None)
    docker = FakeReaderDocker()
    launched = []

    def attach():
        launcher = FakeAttachLauncher()
        launched.append(launcher)
        return launcher

    # Fake argv/env still expects CONFIG_DIR; staging uses its private parent.
    policy = c4ai_policy(
        NET,
        NET_ID,
        ALIAS,
        api_token=API,
        sandboxed=True,
        profile_path=str(PROFILE_PATH),
        profile=pinned_profile(),
        config=content,
    )
    driver = OfflineContainerDriver(
        docker, attach, config_dir=CONFIG_DIR, policy=policy, run_token=RUN
    )
    driver._config_dir = str(tmp_path / "docker-config")
    return docker, driver, launched


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["edit", "replace"])
async def test_source_changed_after_policy_construction_consumes_original_snapshot(
    tmp_path: Path, monkeypatch, change: str
) -> None:
    source = tmp_path / "source.yml"
    source.write_bytes(CONTENT)
    docker, driver, launched = reader(tmp_path, monkeypatch, content=source.read_bytes())
    if change == "replace":
        source.rename(tmp_path / "old.yml")
    source.write_bytes(b"not reviewed")
    await driver.preflight()
    container = await driver.start(())
    assert docker.mounted is not None and docker.mounted != source
    assert docker.mounted.read_bytes() == CONTENT and len(launched) == 1
    assert driver.retained_config == str(docker.mounted)
    assert docker.subcommands().index("cp") > docker.subcommands().index("create")
    await container.aclose()
    await driver.aclose()
    assert not docker.mounted.exists() and driver.retained is None
    assert driver.retained_config is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "readback",
        "cp_failure",
        "transport",
        "wrong_state",
        "wrong_mount",
        "wrong_id",
        "stage_edit",
        "stage_replace",
    ],
)
async def test_bad_consumed_input_or_identity_prevents_start(
    tmp_path: Path, monkeypatch, fault: str
) -> None:
    docker, driver, launched = reader(tmp_path, monkeypatch)

    async def cp(args):
        if fault == "readback":
            return result(stdout=archive(b"not reviewed"))
        if fault == "cp_failure":
            return result(1)
        if fault == "transport":
            raise DockerLifecycleFailure("synthetic transport error")
        return None

    async def create(args):
        outcome = docker.do_create(args)
        record = next(iter(docker.containers.values()))
        if fault == "wrong_state":
            record["State"] = {"Status": "running", "Running": True}
        if fault == "wrong_mount":
            record["Mounts"][-1]["Source"] = "/foreign"
        if fault == "wrong_id":
            record["Id"] = "f" * 64
        assert docker.mounted is not None
        if fault == "stage_edit":
            docker.mounted.chmod(0o600)
            docker.mounted.write_bytes(b"changed")
        if fault == "stage_replace":
            docker.mounted.rename(docker.mounted.with_suffix(".original"))
            docker.mounted.write_bytes(CONTENT)
            docker.mounted.chmod(0o444)
        return outcome

    docker.hooks.update(cp=cp, create=create)
    await driver.preflight()
    expected = (
        DockerCleanupFailure if fault in ("wrong_id", "stage_replace") else DockerLifecycleFailure
    )
    with pytest.raises(expected):
        await driver.start(())
    assert not launched
    if fault in ("wrong_id", "stage_replace"):
        assert driver.retained is not None and driver.cleanup_failed
        assert docker.mounted is not None and docker.mounted.exists()
        # Test-owned cleanup only, after restoring exact original identity.
        allocation = driver._allocation
        assert allocation is not None and allocation.snapshot is not None
        if fault == "stage_replace":
            docker.mounted.unlink()
            docker.mounted.with_suffix(".original").rename(docker.mounted)
        allocation.snapshot.cleanup()
    else:
        assert driver.retained is None and not driver.cleanup_failed
        assert docker.mounted is not None and not docker.mounted.exists()


@pytest.mark.asyncio
async def test_cancellation_keeps_snapshot_until_confirmed_removal(
    tmp_path: Path, monkeypatch
) -> None:
    docker, driver, launched = reader(tmp_path, monkeypatch)
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def cp(args):
        entered.set()
        await asyncio.Future()

    async def rm(args):
        await proceed.wait()
        return None

    docker.hooks.update(cp=cp, rm=rm)
    await driver.preflight()
    task = asyncio.create_task(driver.start(()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert docker.mounted is not None and docker.mounted.exists() and not launched
    proceed.set()
    await driver.aclose()
    assert not docker.mounted.exists() and driver.retained is None


@pytest.mark.asyncio
async def test_unknown_create_and_failed_removal_retain_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    docker, driver, launched = reader(tmp_path, monkeypatch)

    async def create(args):
        docker.do_create(args)  # Daemon created it but client lost response.
        raise DockerLifecycleFailure("synthetic unknown create")

    async def rm(args):
        return result(1)

    docker.hooks.update(create=create, rm=rm)
    await driver.preflight()
    with pytest.raises(DockerCleanupFailure):
        await driver.start(())
    assert docker.mounted is not None and docker.mounted.exists() and not launched
    assert driver.retained is not None and driver.cleanup_failed
    with pytest.raises(DockerCleanupFailure):
        await driver.aclose()
    allocation = driver._allocation
    assert allocation is not None and allocation.snapshot is not None
    allocation.snapshot.cleanup()  # Fake-only test resources, no real container exists.


@pytest.mark.asyncio
async def test_unknown_create_resolves_owned_container_then_removes_input(
    tmp_path: Path, monkeypatch
) -> None:
    docker, driver, launched = reader(tmp_path, monkeypatch)

    async def create(args):
        docker.do_create(args)
        raise DockerLifecycleFailure("synthetic lost response")

    docker.hooks["create"] = create
    await driver.preflight()
    with pytest.raises(DockerLifecycleFailure):
        await driver.start(())
    assert not docker.containers and not launched
    assert docker.mounted is not None and not docker.mounted.exists()
    assert driver.retained is None and driver.retained_config is None


@pytest.mark.asyncio
async def test_stage_mutation_after_readback_still_prevents_start(
    tmp_path: Path, monkeypatch
) -> None:
    docker, driver, launched = reader(tmp_path, monkeypatch)

    async def cp(args):
        reply = docker.do_cp(args)
        assert docker.mounted is not None
        docker.mounted.chmod(0o600)
        docker.mounted.write_bytes(b"changed after readback")
        return reply

    docker.hooks["cp"] = cp
    await driver.preflight()
    with pytest.raises(DockerLifecycleFailure):
        await driver.start(())
    assert not launched and driver.retained is None
