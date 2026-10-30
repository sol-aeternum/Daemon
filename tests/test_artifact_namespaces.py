"""Synthetic POSIX bootstrap failure and readiness checks, no DB authority oracle."""

import os
import multiprocessing
from pathlib import Path
from uuid import uuid4

import pytest

from orchestrator.artifact_namespaces import _topology
from orchestrator.services.deletion_journal.errors import JournalError


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    for child in ("live", "private"):
        (root / child).mkdir(mode=0o700)
    return root


@pytest.mark.parametrize("step", [1, 2, 3])
def test_partial_mkdir_preserved_then_completed(root, monkeypatch, step):
    namespace = uuid4().hex
    original = os.mkdir
    count = 0

    def mkdir(*args, **kwargs):
        nonlocal count
        count += 1
        original(*args, **kwargs)
        if count == step:
            raise OSError("synthetic")

    with monkeypatch.context() as patch:
        patch.setattr(os, "mkdir", mkdir)
        with pytest.raises(JournalError, match="unavailable"):
            _topology(root, namespace, create=True)
    _topology(root, namespace, create=True)
    _topology(root, namespace, create=False)
    assert (root / "private" / namespace / ".prepare.lock").read_bytes() == b""


@pytest.mark.parametrize("step", range(1, 8))
def test_each_sync_failure_preserved_and_resynced(root, monkeypatch, step):
    namespace = uuid4().hex
    original = os.fsync
    count = 0

    def sync(fd):
        nonlocal count
        count += 1
        if count == step:
            raise OSError("synthetic")
        original(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", sync)
        with pytest.raises(JournalError, match="unavailable"):
            _topology(root, namespace, create=True)
    assert (root / "live" / namespace).is_dir()
    _topology(root, namespace, create=True)


@pytest.mark.parametrize("missing", ["staging", "lock"])
def test_ready_missing_never_recreated(root, missing):
    namespace = uuid4().hex
    _topology(root, namespace, create=True)
    if missing == "staging":
        target = root / "live" / namespace / "staging"
        target.rmdir()
    else:
        target = root / "private" / namespace / ".prepare.lock"
        target.unlink()
    with pytest.raises(JournalError, match="unavailable"):
        _topology(root, namespace, create=False)
    assert not target.exists()


def test_unexpected_provisioning_file_preserved(root):
    namespace = uuid4().hex
    _topology(root, namespace, create=True)
    unexpected = root / "live" / namespace / "untracked"
    unexpected.write_bytes(b"synthetic")
    with pytest.raises(JournalError, match="recovery_required"):
        _topology(root, namespace, create=True)
    assert unexpected.read_bytes() == b"synthetic"


def test_no_runtime_namespace_imports():
    root = Path(__file__).resolve().parents[1] / "orchestrator"
    assert [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if p
        not in {
            root / "artifact_namespaces.py",
            root / "resource_store.py",
            root / "resource_inputs.py",
            root / "resource_catalog.py",
        }
        and "artifact_namespaces" in p.read_text()
    ] == []


@pytest.mark.parametrize("damage", ["symlink", "mode", "lock_data"])
def test_ready_unsafe_topology_refused(root, damage):
    namespace = uuid4().hex
    _topology(root, namespace, create=True)
    staging = root / "live" / namespace / "staging"
    if damage == "symlink":
        staging.rmdir()
        staging.symlink_to(root, target_is_directory=True)
    elif damage == "mode":
        staging.chmod(0o755)
    else:
        (root / "private" / namespace / ".prepare.lock").write_bytes(b"not-empty")
    with pytest.raises(JournalError, match="unavailable"):
        _topology(root, namespace, create=False)


def _die_at_sync(root, namespace):
    original = os.fsync

    def sync(fd):
        original(fd)
        os._exit(71)

    os.fsync = sync
    _topology(root, namespace, create=True)


def test_owned_process_death_preserves_provisioning_and_releases_lock(root):
    namespace = uuid4().hex
    job = multiprocessing.get_context("spawn").Process(target=_die_at_sync, args=(root, namespace))
    job.start()
    try:
        job.join(10)
        assert job.exitcode == 71
    finally:
        if job.is_alive():
            job.terminate()
            job.join(5)
    _topology(root, namespace, create=True)
    _topology(root, namespace, create=False)
