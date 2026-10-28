"""Owned synthetic POSIX mechanics only; no registry, provisioning or fence proof."""

import base64
import errno
import fcntl
import json
import multiprocessing
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from cryptography.fernet import Fernet

from orchestrator import artifact_staging as staging
from orchestrator.artifact_envelope import MAX_CONTENT_BYTES, ArtifactBinding, ArtifactEnvelopeCodec

NAMESPACE = "a" * 32
INTENT = "b" * 32
BINDING = ArtifactBinding("account_a", "resource_a", "version_a", 4, "generated_file")
CONTENT = b"fictional-private-content\nvalue,2\n"
OSPath = str | bytes | os.PathLike[str] | os.PathLike[bytes]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "store"
    # Fixture-only provisioning. No application provisioning/journal API exists.
    for part in (
        root,
        root / "live",
        root / "live" / NAMESPACE,
        root / "live" / NAMESPACE / "staging",
        root / "private",
        root / "private" / NAMESPACE,
    ):
        part.mkdir(mode=0o700)
    lock = root / "private" / NAMESPACE / ".prepare.lock"
    lock.touch(mode=0o600)
    return root


@pytest.fixture
def codec() -> ArtifactEnvelopeCodec:
    return ArtifactEnvelopeCodec(Fernet(Fernet.generate_key()))


def paths(root: Path, intent: str = INTENT) -> tuple[Path, Path]:
    return root / "live" / NAMESPACE / "staging" / intent, root / "private" / NAMESPACE / intent


def mutation_facts(path: Path) -> tuple[int, ...]:
    info = path.lstat()
    # Reads/enumeration may update atime, even though no content or names mutate.
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def prepare(
    root: Path, codec: ArtifactEnvelopeCodec, content: bytes = CONTENT
) -> staging.StagedArtifact:
    return staging.prepare_artifact(root, codec, BINDING, NAMESPACE, INTENT, content)


def test_encrypted_pair_and_duplicate_are_not_publication(
    root: Path, codec: ArtifactEnvelopeCodec
) -> None:
    result = prepare(root, codec)
    stage, private = paths(root)
    token = stage.read_bytes()
    assert result.outcome == "prepared_uncommitted"
    assert result.encrypted_size == len(token)
    assert CONTENT not in token
    assert private.read_bytes() == token
    assert codec.decode(BINDING, token) == CONTENT
    assert stage.stat().st_ino == private.stat().st_ino
    assert stage.stat().st_nlink == 2
    assert stat.S_IMODE(stage.stat().st_mode) == 0o600
    before = (mutation_facts(stage), mutation_facts(private), token)
    assert prepare(root, codec).outcome == "duplicate_uncommitted"
    assert (mutation_facts(stage), mutation_facts(private), stage.read_bytes()) == before
    with pytest.raises(staging.ArtifactStageError, match="^conflict$"):
        prepare(root, codec, b"other bytes")
    assert stage.read_bytes() == token


def test_contract_vectors(root: Path, codec: ArtifactEnvelopeCodec) -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/artifact_continuity/contract.json").read_text()
    )
    vectors = []
    for item in fixture["inputs"][:2]:
        content = (
            base64.b64decode(item["content_base64"])
            if "content_base64" in item
            else item["submitted_text"].encode()
        )
        vectors.append(
            (
                item["owner_id"],
                item["resource_id"],
                item["version_id"],
                item["representation"],
                content,
            )
        )
    output = fixture["output"]
    vectors.extend(
        (
            output["owner_id"],
            output["resource_id"],
            v["version_id"],
            v["representation"],
            v["text"].encode(),
        )
        for v in output["versions"]
    )
    for index, (owner, resource, version, representation, content) in enumerate(vectors):
        binding = ArtifactBinding(owner, resource, version, 4, representation)
        intent = f"{index + 1:032x}"
        staging.prepare_artifact(root, codec, binding, NAMESPACE, intent, content)
        stage, private = paths(root, intent)
        assert codec.decode(binding, private.read_bytes()) == content
        assert stage.stat().st_ino == private.stat().st_ino


@pytest.mark.parametrize("bad", ["", "../escape", "A" * 32, "x" * 32, "a" * 33])
def test_invalid_ids_have_no_effect(root: Path, codec: ArtifactEnvelopeCodec, bad: str) -> None:
    for namespace, intent in ((bad, INTENT), (NAMESPACE, bad)):
        with pytest.raises(staging.ArtifactStageError, match="^invalid_input$"):
            staging.prepare_artifact(root, codec, BINDING, namespace, intent, CONTENT)
    assert not any(path.exists() for path in paths(root))


def test_invalid_content_before_open(
    root: Path, codec: ArtifactEnvelopeCodec, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> int:
        pytest.fail("invalid input reached filesystem")

    monkeypatch.setattr(staging.os, "open", forbidden)
    for binding, content in (
        (BINDING, b"x" * (MAX_CONTENT_BYTES + 1)),
        (replace(BINDING, representation="metadata_only"), CONTENT),
    ):
        with pytest.raises(staging.ArtifactStageError, match="^invalid_input$"):
            staging.prepare_artifact(root, codec, binding, NAMESPACE, INTENT, content)


@pytest.mark.parametrize(
    "component", ["live", "namespace", "staging", "private", "private_namespace", "lock"]
)
def test_missing_or_symlink_component_refused(
    root: Path, codec: ArtifactEnvelopeCodec, component: str
) -> None:
    selected = {
        "live": root / "live",
        "namespace": root / "live" / NAMESPACE,
        "staging": paths(root)[0].parent,
        "private": root / "private",
        "private_namespace": paths(root)[1].parent,
        "lock": paths(root)[1].parent / ".prepare.lock",
    }[component]
    moved = selected.with_name(selected.name + "-saved")
    selected.rename(moved)
    with pytest.raises(staging.ArtifactStageError):
        prepare(root, codec)
    assert not selected.exists()  # no lazy provisioning
    selected.symlink_to(moved, target_is_directory=moved.is_dir())
    with pytest.raises(staging.ArtifactStageError):
        prepare(root, codec)
    assert not list(root.rglob(INTENT))


def test_symlink_root_and_ancestor_refused(root: Path, codec: ArtifactEnvelopeCodec) -> None:
    alias = root.parent / "alias"
    alias.symlink_to(root.parent, target_is_directory=True)
    for candidate in (alias / "store", root.parent / "root_alias"):
        if candidate.name == "root_alias":
            candidate.symlink_to(root, target_is_directory=True)
        with pytest.raises(staging.ArtifactStageError):
            prepare(candidate, codec)
    assert not paths(root)[0].exists()


@pytest.mark.parametrize(
    "kind", ["stage", "private", "symlink", "fifo", "directory", "extra_link", "corrupt"]
)
def test_existing_evidence_preserved(root: Path, codec: ArtifactEnvelopeCodec, kind: str) -> None:
    stage, private = paths(root)
    if kind in {"extra_link", "corrupt"}:
        prepare(root, codec)
        if kind == "extra_link":
            os.link(stage, root / "foreign_link")
        else:
            private.write_bytes(b"corrupt synthetic ciphertext")
    elif kind == "stage":
        stage.touch(mode=0o600)
    elif kind == "private":
        private.touch(mode=0o600)
    elif kind == "symlink":
        stage.symlink_to(root / "missing")
    elif kind == "fifo":
        os.mkfifo(stage, 0o600)
        os.link(stage, private)
    else:
        stage.mkdir()
    before = {str(p.relative_to(root)): mutation_facts(p) for p in root.rglob("*")}
    with pytest.raises(staging.ArtifactStageError, match="^needs_reconciliation$"):
        prepare(root, codec)
    assert {str(p.relative_to(root)): mutation_facts(p) for p in root.rglob("*")} == before


@pytest.mark.parametrize("target", ["root", "namespace", "lock"])
def test_permissions_refused(root: Path, codec: ArtifactEnvelopeCodec, target: str) -> None:
    selected = {
        "root": root,
        "namespace": paths(root)[0].parent,
        "lock": paths(root)[1].parent / ".prepare.lock",
    }[target]
    selected.chmod(0o777)
    with pytest.raises(staging.ArtifactStageError):
        prepare(root, codec)
    assert not paths(root)[0].exists()


def test_write_sync_close_link_order(
    root: Path, codec: ArtifactEnvelopeCodec, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = []
    original_write, original_sync, original_close, original_link = (
        os.write,
        os.fsync,
        os.close,
        os.link,
    )
    write_fd = -1

    def write(fd: int, data: bytes) -> int:
        nonlocal write_fd
        write_fd = fd
        events.append("write")
        return original_write(fd, data[:17])

    def sync(fd: int) -> None:
        events.append("file_sync" if stat.S_ISREG(os.fstat(fd).st_mode) else "directory_sync")
        original_sync(fd)

    def close(fd: int) -> None:
        if fd == write_fd:
            events.append("close")
        original_close(fd)

    def link(
        src: OSPath,
        dst: OSPath,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        events.append("link")
        original_link(
            src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd, follow_symlinks=follow_symlinks
        )

    monkeypatch.setattr(staging.os, "write", write)
    monkeypatch.setattr(staging.os, "fsync", sync)
    monkeypatch.setattr(staging.os, "close", close)
    monkeypatch.setattr(staging.os, "link", link)
    prepare(root, codec)
    assert events.count("write") > 1
    assert (
        events.index("file_sync")
        < events.index("close")
        < events.index("directory_sync")
        < events.index("link")
    )
    assert events[-1] == "directory_sync" or "directory_sync" in events[events.index("link") + 1 :]


@pytest.mark.parametrize(
    "fault",
    [
        "write",
        "zero_write",
        "file_sync",
        "directory_sync",
        "close",
        "link",
        "link_after",
        "private_sync",
    ],
)
def test_faults_preserve_evidence_and_close_handles(
    root: Path, codec: ArtifactEnvelopeCodec, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    original_open, original_close, original_write, original_sync, original_link = (
        os.open,
        os.close,
        os.write,
        os.fsync,
        os.link,
    )
    owned: set[int] = set()
    faulted = False
    sync_count = 0

    def fail() -> None:
        nonlocal faulted
        if not faulted:
            faulted = True
            raise OSError(errno.EIO, "synthetic-private-path-and-content")

    def opened(path: OSPath, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        fd = original_open(path, flags, mode, dir_fd=dir_fd)
        owned.add(fd)
        return fd

    def close(fd: int) -> None:
        is_stage = stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_size > 0
        original_close(fd)
        owned.discard(fd)
        if fault == "close" and is_stage:
            fail()  # Linux ambiguous-close model: descriptor already released.

    def write(fd: int, data: bytes) -> int:
        if fault == "write":
            fail()
        if fault == "zero_write":
            return 0
        return original_write(fd, data)

    def sync(fd: int) -> None:
        nonlocal sync_count
        sync_count += 1
        if (fault, sync_count) in {("file_sync", 1), ("directory_sync", 2), ("private_sync", 3)}:
            fail()
        original_sync(fd)

    def link(
        src: OSPath,
        dst: OSPath,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        if fault == "link":
            fail()
        original_link(
            src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd, follow_symlinks=follow_symlinks
        )
        if fault == "link_after":
            fail()

    with monkeypatch.context() as patch:
        patch.setattr(staging.os, "open", opened)
        patch.setattr(staging.os, "close", close)
        patch.setattr(staging.os, "write", write)
        patch.setattr(staging.os, "fsync", sync)
        patch.setattr(staging.os, "link", link)
        with pytest.raises(staging.ArtifactStageError, match="^needs_reconciliation$"):
            prepare(root, codec)
    assert not owned
    stage, private = paths(root)
    assert stage.exists()
    if fault in {"link_after", "private_sync"}:
        assert private.exists()
        # New call cannot reinterpret an unacknowledged pair as fresh preparation.
        assert prepare(root, codec).outcome == "duplicate_uncommitted"
    else:
        with pytest.raises(staging.ArtifactStageError, match="^needs_reconciliation$"):
            prepare(root, codec)


def test_source_swap_detected_without_cleanup(
    root: Path, codec: ArtifactEnvelopeCodec, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_link = os.link
    stage, private = paths(root)

    def link(
        src: OSPath,
        dst: OSPath,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        stage.rename(stage.with_name("original"))
        stage.write_bytes(b"substituted")
        stage.chmod(0o600)
        original_link(
            src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd, follow_symlinks=follow_symlinks
        )

    monkeypatch.setattr(staging.os, "link", link)
    with pytest.raises(staging.ArtifactStageError, match="^needs_reconciliation$"):
        prepare(root, codec)
    assert private.read_bytes() == b"substituted"
    assert stage.with_name("original").exists()


def test_retirement_during_write_never_returns_prepared(
    root: Path, codec: ArtifactEnvelopeCodec, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_write = os.write

    def write(fd: int, data: bytes) -> int:
        count = original_write(fd, data)
        (root / "live" / NAMESPACE).rename(root / "frozen")
        return count

    monkeypatch.setattr(staging.os, "write", write)
    with pytest.raises(staging.ArtifactStageError, match="^needs_reconciliation$"):
        prepare(root, codec)
    assert (root / "frozen" / "staging" / INTENT).exists()
    assert not paths(root)[1].exists()
    assert not (root / "live" / NAMESPACE).exists()


def test_concurrent_calls_are_nonblocking_and_serialized(
    root: Path, codec: ArtifactEnvelopeCodec, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()
    original_write = os.write

    def write(fd: int, data: bytes) -> int:
        entered.set()
        assert release.wait(5)
        return original_write(fd, data)

    monkeypatch.setattr(staging.os, "write", write)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(prepare, root, codec)
        try:
            assert entered.wait(5)
            with pytest.raises(staging.ArtifactStageError, match="^busy$"):
                prepare(root, codec)
        finally:
            release.set()
        assert future.result(timeout=5).outcome == "prepared_uncommitted"


def _crash_prepare(root: Path, key: bytes, after_link: bool) -> None:
    if after_link:
        original = os.link

        def link(
            src: OSPath,
            dst: OSPath,
            *,
            src_dir_fd: int | None = None,
            dst_dir_fd: int | None = None,
            follow_symlinks: bool = True,
        ) -> None:
            original(
                src,
                dst,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )
            os._exit(71)

        os.link = link
    else:
        original_write = os.write

        def write(fd: int, data: bytes) -> int:
            original_write(fd, data[:17])
            os._exit(71)

        os.write = write
    prepare(root, ArtifactEnvelopeCodec(Fernet(key)))


@pytest.mark.parametrize("after_link", [False, True])
def test_owned_process_interruption_preserves_candidates(root: Path, after_link: bool) -> None:
    key = Fernet.generate_key()
    process = multiprocessing.get_context("fork").Process(
        target=_crash_prepare, args=(root, key, after_link)
    )
    process.start()
    try:
        process.join(5)
        assert process.exitcode == 71
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
        process.close()
    assert paths(root)[0].exists()
    codec = ArtifactEnvelopeCodec(Fernet(key))
    if after_link:
        assert prepare(root, codec).outcome == "duplicate_uncommitted"
    else:
        with pytest.raises(staging.ArtifactStageError, match="^needs_reconciliation$"):
            prepare(root, codec)
    # The dead process released its exact lock; no process-global cleanup needed.
    with (paths(root)[1].parent / ".prepare.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_maximum_content(root: Path, codec: ArtifactEnvelopeCodec) -> None:
    content = b"m" * MAX_CONTENT_BYTES
    result = prepare(root, codec, content)
    assert result.outcome == "prepared_uncommitted"
    assert codec.decode(BINDING, paths(root)[1].read_bytes()) == content


def test_cross_device_and_unsupported_refusal(
    root: Path, codec: ArtifactEnvelopeCodec, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = os.fstat
    private_inode = paths(root)[1].parent.stat().st_ino

    def different_device(fd: int) -> os.stat_result:
        info = original(fd)
        if info.st_ino != private_inode:
            return info
        fields = list(info)
        fields[2] = info.st_dev + 1
        return os.stat_result(fields)

    with monkeypatch.context() as patch:
        patch.setattr(staging.os, "fstat", different_device)
        with pytest.raises(staging.ArtifactStageError, match="^unavailable$"):
            prepare(root, codec)
    monkeypatch.setattr(staging, "fcntl", None)
    with pytest.raises(staging.ArtifactStageError, match="^unsupported$"):
        prepare(root, codec)
    assert not any(path.exists() for path in paths(root))


def test_replaced_lock_is_not_admission(
    root: Path, codec: ArtifactEnvelopeCodec, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = fcntl.flock
    lock_path = paths(root)[1].parent / ".prepare.lock"

    def replaced(fd: int, operation: int) -> None:
        original(fd, operation)
        lock_path.rename(lock_path.with_name("old-lock"))
        lock_path.touch(mode=0o600)

    monkeypatch.setattr(staging.fcntl, "flock", replaced)
    with pytest.raises(staging.ArtifactStageError, match="^needs_reconciliation$"):
        prepare(root, codec)
    assert not paths(root)[0].exists()


def test_complete_pair_wrong_binding_denies_without_writes(
    root: Path, codec: ArtifactEnvelopeCodec
) -> None:
    prepare(root, codec)
    before = paths(root)[0].read_bytes()
    with pytest.raises(staging.ArtifactStageError, match="^needs_reconciliation$"):
        staging.prepare_artifact(
            root, codec, replace(BINDING, account_id="other"), NAMESPACE, INTENT, CONTENT
        )
    assert paths(root)[0].read_bytes() == before
