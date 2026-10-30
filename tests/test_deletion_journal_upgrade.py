"""Offline promotion of owned synthetic POSIX journals; no runtime activation."""

from __future__ import annotations

import json
import multiprocessing
import os
from uuid import uuid4

import pytest

from orchestrator.services.deletion_journal import upgrade
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.store import Journal, initialize
from tests.deletion_journal_support import request


@pytest.fixture
def journal_dir(tmp_path):
    path = tmp_path / "journal"
    identity = uuid4()
    initialize(path, identity)
    return path, identity


@pytest.mark.parametrize("populated", [False, True])
def test_promote_preserves_frames_head_and_rejects_old_reader(journal_dir, populated):
    path, identity = journal_dir
    if populated:
        with Journal(path, identity) as journal:
            journal.append(request(), uuid4(), None, None)
    before = {name: (path / name).read_bytes() for name in ("entries.log", "HEAD", ".journal.lock")}
    old_inode = (path / "journal.meta").stat().st_ino
    upgrade.promote_container_v2(path, identity)
    assert json.loads((path / "journal.meta").read_bytes()) == {
        "version": 2,
        "journal_id": str(identity),
    }
    assert (path / "journal.meta").stat().st_ino != old_inode
    assert before == {name: (path / name).read_bytes() for name in before}
    with Journal(path, identity) as journal, pytest.raises(JournalError, match="corrupt"):
        journal.verify()
    with pytest.raises(JournalError, match="corrupt"):
        upgrade.promote_container_v2(path, identity)
    assert not (path / ".META.v2.pending").exists()


@pytest.mark.parametrize("damage", ["wrong_id", "torn_log", "head_pending", "upgrade_pending"])
def test_refuses_before_effect_and_preserves_evidence(journal_dir, damage):
    path, identity = journal_dir
    if damage == "wrong_id":
        identity = uuid4()
    elif damage == "torn_log":
        (path / "entries.log").write_bytes(b"\x00")
    else:
        name = ".HEAD.pending" if damage == "head_pending" else ".META.v2.pending"
        (path / name).write_bytes(b"{")
        (path / name).chmod(0o600)
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    with pytest.raises(JournalError, match="corrupt"):
        upgrade.promote_container_v2(path, identity)
    assert before == {p.name: p.read_bytes() for p in path.iterdir()}


@pytest.mark.parametrize("failure", ["write", "temp_sync", "replace", "directory_sync"])
def test_unknown_upgrade_preserves_failed_phase(journal_dir, monkeypatch, failure):
    path, identity = journal_dir
    original_sync = os.fsync
    original_replace = os.replace

    def write(fd, data):
        os.write(fd, data[:1])
        raise OSError("synthetic")

    def sync(fd):
        target = os.readlink(f"/proc/self/fd/{fd}")
        if (failure == "temp_sync" and target.endswith(".META.v2.pending")) or (
            failure == "directory_sync"
            and target == str(path)
            and json.loads((path / "journal.meta").read_bytes())["version"] == 2
        ):
            raise OSError("synthetic")
        original_sync(fd)

    def replace(src, dst, **kwargs):
        if src == ".META.v2.pending":
            raise OSError("synthetic")
        original_replace(src, dst, **kwargs)

    if failure == "write":
        monkeypatch.setattr(upgrade, "_write_all", write)
    elif failure == "replace":
        monkeypatch.setattr(os, "replace", replace)
    else:
        monkeypatch.setattr(os, "fsync", sync)
    with pytest.raises(JournalError, match="unknown_upgrade"):
        upgrade.promote_container_v2(path, identity)
    if failure == "directory_sync":
        assert json.loads((path / "journal.meta").read_bytes())["version"] == 2
    else:
        assert (path / ".META.v2.pending").exists()
        assert json.loads((path / "journal.meta").read_bytes())["version"] == 1
    assert (path / "entries.log").read_bytes() == b""
    with Journal(path, identity) as journal, pytest.raises(JournalError, match="corrupt"):
        journal.verify()


def test_metadata_substitution_after_temp_sync_is_denied(journal_dir, monkeypatch):
    path, identity = journal_dir
    original = upgrade._verify_promotion_paths

    def substituted(journal, pending):
        replacement = path / "replacement"
        replacement.write_bytes((path / "journal.meta").read_bytes())
        replacement.chmod(0o600)
        os.replace(replacement, path / "journal.meta")
        original(journal, pending)

    monkeypatch.setattr(upgrade, "_verify_promotion_paths", substituted)
    with pytest.raises(JournalError, match="unknown_upgrade"):
        upgrade.promote_container_v2(path, identity)
    assert (path / ".META.v2.pending").exists()
    assert json.loads((path / "journal.meta").read_bytes())["version"] == 1


def _crash_promotion(path, identity, phase):
    original_write = upgrade._write_all
    original_replace = os.replace
    original_sync = os.fsync

    def write(fd, data):
        if phase == "partial_write":
            os.write(fd, data[:1])
            os._exit(71)
        original_write(fd, data)

    def sync(fd):
        original_sync(fd)
        if phase == "temp_synced" and os.readlink(f"/proc/self/fd/{fd}").endswith(
            ".META.v2.pending"
        ):
            os._exit(71)

    def replace(src, dst, **kwargs):
        original_replace(src, dst, **kwargs)
        if phase == "replaced" and src == ".META.v2.pending":
            os._exit(71)

    upgrade._write_all = write
    os.fsync = sync
    os.replace = replace
    upgrade.promote_container_v2(path, identity)


@pytest.mark.parametrize("phase", ["partial_write", "temp_synced", "replaced"])
def test_process_death_preserves_fail_closed_state(journal_dir, phase):
    path, identity = journal_dir
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_promotion, args=(path, identity, phase)
    )
    process.start()
    try:
        process.join(10)
        assert process.exitcode == 71
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert (path / "entries.log").read_bytes() == b""
    assert (path / ".META.v2.pending").exists() == (phase != "replaced")
    with Journal(path, identity) as journal, pytest.raises(JournalError, match="corrupt"):
        journal.verify()


def test_lock_contention_refuses_without_temp(journal_dir, monkeypatch):
    from orchestrator.services.deletion_journal import store

    path, identity = journal_dir
    monkeypatch.setattr(store, "LOCK_WAIT_S", 0.01)
    with Journal(path, identity) as held, held._locked():
        with pytest.raises(JournalError, match="unavailable"):
            upgrade.promote_container_v2(path, identity)
    assert not (path / ".META.v2.pending").exists()
    with Journal(path, identity) as journal:
        assert journal.verify().entries == []


def test_preflight_retains_existing_complete_head_recovery(journal_dir):
    path, identity = journal_dir
    with Journal(path, identity) as journal:
        journal.append(request(), uuid4(), None, None)
    head = (path / "HEAD").read_bytes()
    frames = (path / "entries.log").read_bytes()
    (path / ".HEAD.pending").write_bytes(head)
    (path / ".HEAD.pending").chmod(0o600)
    upgrade.promote_container_v2(path, identity)
    assert not (path / ".HEAD.pending").exists()
    assert (path / "HEAD").read_bytes() == head
    assert (path / "entries.log").read_bytes() == frames
