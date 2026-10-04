"""Isolated disk fixtures; no provider, deployed runtime, or inference actions."""

from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
import subprocess
import sys
import threading
import uuid

import pytest

from orchestrator.artifacts import artifact_owner_namespace, resolve_owned_artifact
from orchestrator.speech import cache, stream_cache as sc
from orchestrator.speech.contracts import SpeechRequest


@pytest.fixture
def spec():
    owner = uuid.uuid4()
    speech = SpeechRequest("A short fictional cache fixture.")
    identity = {
        "provider": "kokoro",
        "model": "fictional-model",
        "voice": speech.voice,
        "speed": speech.speed,
        "format": "mp3",
        "mime": "audio/mpeg",
        "sample_rate": 24000,
        "rendering": sc.RENDERING,
    }
    return owner, sc.progressive_filename("kokoro", "fictional-model", speech), identity


def complete(size=5, **changes):
    return {
        "frames": 1,
        "bytes": size,
        "source_seconds": 1.0,
        "encoded_seconds": 1.1,
        "synthesis_seconds": 0.5,
        "audio_path": None,
        "cache_available": False,
        **changes,
    }


def reserve(root, spec):
    result = sc.reserve_audio(root, *spec)
    assert result is not None
    return result


def publish(root, spec, content=b"audio"):
    result = reserve(root, spec)
    result.append(content)
    assert result.commit(complete(len(content)))
    return result


def disk_files(root):
    return [p for p in root.rglob("*") if p.is_file() and p.name != ".lock"]


@pytest.mark.parametrize("retirement", ["expiry", "eviction"])
def test_owner_churn_stays_bounded_and_preserves_live_empty_namespace(
    tmp_path, spec, monkeypatch, retirement
):
    # More owners than even the normal root enumeration bound; a small file
    # budget exercises eviction while one reservation remains live throughout.
    original_limit = cache.CACHE_FILES
    count = 2 * original_limit + 16
    monkeypatch.setattr(cache, "CACHE_FILES", 5)
    live = reserve(tmp_path, spec)
    live_directory = tmp_path / artifact_owner_namespace(spec[0])
    original = live_directory.stat().st_ino
    try:
        for _ in range(count):
            owner = uuid.uuid4()
            cache.store_audio(tmp_path, owner, "buffered.wav", b"clip")
            path = cache.cached_audio(tmp_path, owner, "buffered.wav")
            assert path is not None
            if retirement == "expiry":
                os.utime(path, (0, 0))
            assert live_directory.stat().st_ino == original
        live.append(b"audio")
        assert live.commit(complete())
        hit = sc.open_cached_audio(tmp_path, *spec)
        assert hit is not None
        hit.close()
        later_owner = uuid.uuid4()
        cache.store_audio(tmp_path, later_owner, "later.opus", b"complete")
        assert cache.cached_audio(tmp_path, later_owner, "later.opus") is not None
        # A new reservation needs three free slots; restore the normal budget
        # after exercising pressure, rather than demanding speculative eviction.
        monkeypatch.setattr(cache, "CACHE_FILES", original_limit)
        subsequent = reserve(tmp_path, (uuid.uuid4(), spec[1], spec[2]))
        subsequent.abort()
    finally:
        live.abort()


def test_empty_namespace_cleanup_preserves_unknown_contents_and_directories(tmp_path, spec):
    empty = tmp_path / artifact_owner_namespace(uuid.uuid4())
    occupied = tmp_path / artifact_owner_namespace(uuid.uuid4())
    unknown = tmp_path / "operator-notes"
    for directory in (empty, occupied, unknown):
        directory.mkdir()
    (occupied / "unrecognized.data").write_bytes(b"preserve")
    live = reserve(tmp_path, spec)
    try:
        assert not empty.exists()
        assert (occupied / "unrecognized.data").read_bytes() == b"preserve"
        assert unknown.is_dir()
    finally:
        live.abort()


def test_invalid_journal_prevents_even_empty_namespace_cleanup(tmp_path, spec):
    empty = tmp_path / artifact_owner_namespace(uuid.uuid4())
    empty.mkdir()
    manager = tmp_path / ".progressive"
    manager.mkdir()
    (manager / ("a" * 32 + ".lease")).write_bytes(b"")
    assert sc.reserve_audio(tmp_path, *spec) is None
    assert empty.is_dir()


def test_symlink_namespace_is_never_followed_or_removed_by_cleanup(tmp_path, spec):
    external = tmp_path / "operator-directory"
    external.mkdir()
    namespace = tmp_path / artifact_owner_namespace(uuid.uuid4())
    namespace.symlink_to(external, target_is_directory=True)
    assert sc.reserve_audio(tmp_path, *spec) is None
    assert namespace.is_symlink()
    assert external.is_dir()


@pytest.mark.parametrize("replacement", ["directory", "symlink", "missing", "nonempty-race"])
def test_empty_namespace_revalidation_preserves_replacements(tmp_path, monkeypatch, replacement):
    namespace = artifact_owner_namespace(uuid.uuid4())
    target = tmp_path / namespace
    with (
        cache.cache_lock(tmp_path) as root,
        sc._directory(root, ".progressive", create=True) as manager,
    ):
        ledger = sc._Ledger(root, manager)
        target.mkdir()
        expected = sc._inode(target.stat())
        if replacement == "nonempty-race":
            original_rmdir = os.rmdir

            def raced_rmdir(name, *, dir_fd):
                (target / "new-data").write_bytes(b"preserve")
                return original_rmdir(name, dir_fd=dir_fd)

            monkeypatch.setattr(sc.os, "rmdir", raced_rmdir)
            ledger._remove_empty_owner(namespace, expected)
            assert (target / "new-data").read_bytes() == b"preserve"
        else:
            preserved = tmp_path / "preserved-original"
            target.rename(preserved)
            if replacement == "directory":
                target.mkdir()
            elif replacement == "symlink":
                target.symlink_to(preserved, target_is_directory=True)
            if replacement == "missing":
                ledger._remove_empty_owner(namespace, expected)
                assert not target.exists()
            else:
                with pytest.raises(OSError):
                    ledger._remove_empty_owner(namespace, expected)
                assert target.exists()
            assert preserved.is_dir()


def test_render_identity_and_complete_only_private_stage(tmp_path, spec):
    owner, filename, identity = spec
    speech = SpeechRequest("A short fictional cache fixture.")
    assert filename != cache.audio_filename("kokoro", "fictional-model", speech)
    assert filename == cache.audio_filename(
        "kokoro", "fictional-model", speech, rendering=sc.RENDERING
    )
    result = reserve(tmp_path, spec)
    result.append(b"audio")
    assert sc.open_cached_audio(tmp_path, *spec) is None
    for private in (tmp_path / ".progressive").iterdir():
        assert resolve_owned_artifact(tmp_path, owner, private.name) is None
        assert private.stat().st_mode & 0o777 == 0o600
    assert resolve_owned_artifact(tmp_path, owner, ".progressive") is None
    assert result.commit(complete())
    hit = sc.open_cached_audio(tmp_path, *spec)
    assert hit is not None
    assert hit.file.read(2) + hit.file.read(3) == b"audio"
    assert hit.complete == complete()
    hit.close()
    hit.close()
    assert sc.open_cached_audio(tmp_path, uuid.uuid4(), filename, identity) is None
    assert sc.open_cached_audio(tmp_path, owner, filename, {**identity, "model": "other"}) is None
    assert len(disk_files(tmp_path)) == 2
    metadata = next((tmp_path / ".progressive").iterdir()).read_text()
    assert speech.text not in metadata and str(owner) not in metadata
    result.abort()
    result.abort()
    assert result.commit(complete())
    assert resolve_owned_artifact(tmp_path, owner, filename) is not None


def test_pinned_read_survives_eviction_and_metadata_follows(tmp_path, spec, monkeypatch):
    publish(tmp_path, spec)
    hit = sc.open_cached_audio(tmp_path, *spec)
    assert hit is not None
    monkeypatch.setattr(cache, "CACHE_FILES", 2)
    cache.store_audio(tmp_path, spec[0], "buffered.wav", b"new")
    assert hit.file.read() == b"audio"
    hit.close()
    assert resolve_owned_artifact(tmp_path, spec[0], spec[1]) is None
    assert not list((tmp_path / ".progressive").iterdir())


def test_unified_bytes_metadata_peak_and_no_speculative_eviction(tmp_path, spec, monkeypatch):
    owner = spec[0]
    cache.store_audio(tmp_path, owner, "keep.wav", b"x" * 20)
    monkeypatch.setattr(cache, "CACHE_BYTES", sc.RESERVATION_BYTES + 19)
    assert sc.reserve_audio(tmp_path, *spec) is None
    assert cache.cached_audio(tmp_path, owner, "keep.wav") is not None
    monkeypatch.setattr(cache, "CACHE_BYTES", sc.RESERVATION_BYTES + 20)
    result = reserve(tmp_path, spec)
    result.append(b"audio")
    # Same stage bytes are included in the reservation, not charged again.
    with cache.cache_lock(tmp_path) as root, sc._directory(root, ".progressive") as manager:
        ledger = sc._Ledger(root, manager)
        assert ledger.used_bytes == sc.RESERVATION_BYTES + 20
        assert ledger.used_files == sc.RESERVATION_FILES + 1
    cache.store_audio(tmp_path, owner, "replacement.wav", b"x" * 20)
    assert cache.cached_audio(tmp_path, owner, "keep.wav") is None
    with pytest.raises(OSError, match="capacity reserved"):
        cache.store_audio(tmp_path, owner, "too-large.wav", b"x" * 21)
    assert result.commit(complete())
    files = disk_files(tmp_path)
    assert sum(path.stat().st_size for path in files) <= cache.CACHE_BYTES


def test_physical_slots_and_buffered_temporary_peak(tmp_path, spec, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_FILES", sc.RESERVATION_FILES - 1)
    assert sc.reserve_audio(tmp_path, *spec) is None
    monkeypatch.setattr(cache, "CACHE_FILES", sc.RESERVATION_FILES)
    result = reserve(tmp_path, spec)
    with pytest.raises(OSError, match="capacity reserved"):
        cache.store_audio(tmp_path, spec[0], "new.wav", b"a")
    assert len(disk_files(tmp_path)) == 2
    result.abort()
    # An old destination and its new temporary must both fit before replacing.
    monkeypatch.setattr(cache, "CACHE_BYTES", 10)
    cache.store_audio(tmp_path, spec[0], "replace.wav", b"123456")
    observations = []
    original = sc._write_all

    def observed(fd, data):
        original(fd, data)
        paths = disk_files(tmp_path)
        observations.append((sum(path.stat().st_size for path in paths), len(paths)))

    monkeypatch.setattr(sc, "_write_all", observed)
    cache.store_audio(tmp_path, spec[0], "replace.wav", b"abcdef")
    assert observations and all(size <= 10 and count <= 3 for size, count in observations)


def test_same_process_separately_opened_lease_is_not_recovered(tmp_path, spec):
    result = reserve(tmp_path, spec)
    lease = next((tmp_path / ".progressive").glob("*.lease"))
    with lease.open("rb") as another:
        with pytest.raises(BlockingIOError):
            fcntl.flock(another.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    other = reserve(tmp_path, spec)
    assert lease.exists()
    other.abort()
    result.abort()
    assert not list((tmp_path / ".progressive").iterdir())


_CHILD = r"""
import json, os, sys
from pathlib import Path
from orchestrator.speech import stream_cache as sc
root, owner, filename, identity, phase = sys.argv[1:]
root = Path(root)
r = sc.reserve_audio(root, owner, filename, json.loads(identity))
assert r is not None
r.append(b"audio")
if phase == "active":
    print("ready", flush=True)
    sys.stdin.read()
if phase in ("reserved", "active"):
    os._exit(23)
write = sc._write_all
rename = sc.os.rename
unlink = sc._Ledger._unlink_exact
original_open = sc.os.open
def crash_open(path, flags, *args, **kwargs):
    if str(path).endswith(".manifest") and flags & os.O_CREAT and phase == "before-manifest":
        os._exit(23)
    return original_open(path, flags, *args, **kwargs)
def crash_write(fd, data):
    if b'"complete"' in data and phase == "empty-manifest":
        os._exit(23)
    write(fd, data)
    if b'"complete"' in data and phase == "after-manifest":
        os._exit(23)
def crash_rename(*args, **kwargs):
    rename(*args, **kwargs)
    if phase == "after-rename":
        os._exit(23)
def crash_unlink(directory, name, expected):
    if name.endswith(".lease") and phase == "before-lease-cleanup":
        os._exit(23)
    unlink(directory, name, expected)
sc._write_all = crash_write
sc.os.open = crash_open
sc.os.rename = crash_rename
sc._Ledger._unlink_exact = staticmethod(crash_unlink)
assert r.commit({"frames":1,"bytes":5,"source_seconds":1.,"encoded_seconds":1.1,
                 "synthesis_seconds":.5,"audio_path":None,"cache_available":False})
os._exit(23)
"""


def child_args(root, spec, phase):
    return [
        sys.executable,
        "-c",
        _CHILD,
        str(root),
        str(spec[0]),
        spec[1],
        json.dumps(spec[2]),
        phase,
    ]


@pytest.mark.parametrize(
    "phase,published",
    [
        ("reserved", False),
        ("before-manifest", False),
        ("empty-manifest", False),
        ("after-manifest", False),
        ("after-rename", True),
        ("before-lease-cleanup", True),
        ("finished", True),
    ],
)
def test_process_crash_recovery_exact_pairs(tmp_path, spec, phase, published):
    child = subprocess.run(
        child_args(tmp_path, spec, phase), capture_output=True, text=True, timeout=15
    )
    assert child.returncode == 23, child.stderr
    if phase == "empty-manifest":
        # A crash after EXCL creation but before writing is deliberately fail-closed:
        # an empty manifest is not proof of owned complete metadata.
        original = sorted(path.name for path in (tmp_path / ".progressive").iterdir())
        assert sc.open_cached_audio(tmp_path, *spec) is None
        assert sc.reserve_audio(tmp_path, *spec) is None
        assert sorted(path.name for path in (tmp_path / ".progressive").iterdir()) == original
        return
    hit = sc.open_cached_audio(tmp_path, *spec)
    assert (hit is not None) is published
    if hit:
        assert hit.file.read() == b"audio"
        hit.close()
    assert not list((tmp_path / ".progressive").glob("*.lease"))
    assert not list((tmp_path / ".progressive").glob("*.stage"))
    assert len(list((tmp_path / ".progressive").iterdir())) == int(published)


def test_active_cross_process_lease_and_recovery(tmp_path, spec):
    child = subprocess.Popen(
        child_args(tmp_path, spec, "active"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None and child.stdout.readline() == "ready\n"
        lease = next((tmp_path / ".progressive").glob("*.lease"))
        other = reserve(tmp_path, spec)
        other.abort()
        assert lease.exists()
        assert child.stdin is not None
        child.stdin.close()
        assert child.wait(timeout=15) == 23
        result = reserve(tmp_path, spec)
        assert not lease.exists()
        result.abort()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=15)


def test_same_key_writers_deduplicate_without_replacing_inode(tmp_path, spec):
    first = reserve(tmp_path, spec)
    second = reserve(tmp_path, spec)
    first.append(b"audio")
    second.append(b"other")
    assert first.commit(complete())
    path = resolve_owned_artifact(tmp_path, spec[0], spec[1])
    assert path is not None
    inode = path.stat().st_ino
    assert second.commit(complete())
    assert path.stat().st_ino == inode and path.read_bytes() == b"audio"
    assert len(disk_files(tmp_path)) == 2


def test_dead_same_key_stage_never_deletes_later_complete(tmp_path, spec):
    child = subprocess.run(
        child_args(tmp_path, spec, "after-manifest"), capture_output=True, text=True, timeout=15
    )
    assert child.returncode == 23
    # Recovery of older speculative work precedes a new writer's publication.
    publish(tmp_path, spec, b"later")
    hit = sc.open_cached_audio(tmp_path, *spec)
    assert hit is not None and hit.file.read() == b"later"
    hit.close()


@pytest.mark.parametrize(
    "kind", ["root", "ancestor", "manager", "owner", "audio", "lease", "stage", "lock"]
)
def test_planted_symlinks_fail_closed_without_touching_target(tmp_path, spec, kind):
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "sentinel"
    sentinel.write_bytes(b"private")
    root = tmp_path / "cache"
    root.mkdir()
    active = None
    if kind == "root":
        root.rmdir()
        root.symlink_to(target, target_is_directory=True)
    elif kind == "ancestor":
        parent = tmp_path / "alias"
        parent.symlink_to(target, target_is_directory=True)
        root = parent / "cache"
    elif kind in ("manager", "owner"):
        (
            root / (".progressive" if kind == "manager" else artifact_owner_namespace(spec[0]))
        ).symlink_to(target, target_is_directory=True)
    elif kind == "lock":
        (root / ".lock").symlink_to(sentinel)
    elif kind == "audio":
        owner = root / artifact_owner_namespace(spec[0])
        owner.mkdir()
        (owner / spec[1]).symlink_to(sentinel)
    else:
        active = reserve(root, spec)
        entry = next((root / ".progressive").glob(f"*.{kind}"))
        entry.unlink()
        entry.symlink_to(sentinel)
    assert sc.reserve_audio(root, *spec) is None
    assert sc.open_cached_audio(root, *spec) is None
    with pytest.raises((OSError, ValueError)):
        cache.store_audio(root, spec[0], "new.wav", b"abc")
    if active:
        active.abort()
    assert sentinel.read_bytes() == b"private"
    assert list(target.iterdir()) == [sentinel]


@pytest.mark.parametrize("contents", [b"not-json", b"{}", b"{}" * 3000, b'{"schema":1,"schema":1}'])
def test_invalid_journal_never_purges_other_files(tmp_path, spec, contents):
    result = reserve(tmp_path, spec)
    manager = tmp_path / ".progressive"
    bad = manager / ("0" * 32 + ".lease")
    bad.write_bytes(contents)
    bad.chmod(0o600)
    before = {path.name: path.read_bytes() for path in manager.iterdir()}
    assert sc.reserve_audio(tmp_path, *spec) is None
    assert sc.open_cached_audio(tmp_path, *spec) is None
    with pytest.raises(OSError):
        cache.store_audio(tmp_path, spec[0], "new.wav", b"a")
    assert {path.name: path.read_bytes() for path in manager.iterdir()} == before
    result.abort()
    assert bad.exists()


def test_unknown_manager_entry_disables_cache_and_is_not_deleted(tmp_path, spec):
    result = reserve(tmp_path, spec)
    unknown = tmp_path / ".progressive" / "unknown"
    unknown.write_bytes(b"do not remove")
    assert sc.reserve_audio(tmp_path, *spec) is None
    result.abort()
    assert unknown.read_bytes() == b"do not remove"


@pytest.mark.parametrize(
    "field,value",
    [
        ("bytes", 4),
        ("bytes", True),
        ("frames", 0),
        ("frames", 16385),
        ("source_seconds", float("nan")),
        ("source_seconds", float("inf")),
        ("source_seconds", 300.001),
        ("source_seconds", 0),
        ("encoded_seconds", 0.9),
        ("encoded_seconds", 1.151),
        ("synthesis_seconds", float("nan")),
        ("synthesis_seconds", -1),
        ("synthesis_seconds", 120.001),
        ("cache_available", True),
        ("audio_path", "/generated-audio/other.mp3"),
    ],
)
def test_invalid_completion_discards_without_publishing(tmp_path, spec, field, value):
    result = reserve(tmp_path, spec)
    result.append(b"audio")
    assert not result.commit(complete(**{field: value}))
    assert sc.open_cached_audio(tmp_path, *spec) is None
    assert not disk_files(tmp_path)
    result.abort()


def test_exact_cap_finite_duration_boundary_and_append_guard(tmp_path, spec):
    result = reserve(tmp_path, spec)
    with pytest.raises(OSError):
        result.append(b"")
    block = b"a" * 64000
    for _ in range(250):
        result.append(block)
    with pytest.raises(OSError):
        result.append(b"b")
    assert result.commit(
        complete(
            sc.MAX_AUDIO_BYTES,
            frames=250,
            source_seconds=300.0,
            encoded_seconds=300.15,
            synthesis_seconds=120.0,
        )
    )
    hit = sc.open_cached_audio(tmp_path, *spec)
    assert hit is not None
    assert os.fstat(hit.file.fileno()).st_size == sc.MAX_AUDIO_BYTES
    hit.close()
    with pytest.raises(OSError):
        result.append(b"b")


def test_ttl_prunes_pair_and_unlocked_lease_recovery(tmp_path, spec, monkeypatch):
    publish(tmp_path, spec)
    original = sc.time.time()
    monkeypatch.setattr(sc.time, "time", lambda: original + cache.CACHE_TTL_SECONDS + 1)
    assert sc.open_cached_audio(tmp_path, *spec) is None
    assert not disk_files(tmp_path)


def test_blocked_thread_append_retains_lease_until_actual_exit(tmp_path, spec, monkeypatch):
    result = reserve(tmp_path, spec)
    entered, release = threading.Event(), threading.Event()
    original = sc._write_all

    def blocked(fd, data):
        if fd == result._stage:
            entered.set()
            assert release.wait(10)
        original(fd, data)

    monkeypatch.setattr(sc, "_write_all", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(result.append, b"audio")
        try:
            assert entered.wait(5)
            assert not writer.cancel()
            abort = pool.submit(result.abort)
            lease = next((tmp_path / ".progressive").glob("*.lease"))
            with lease.open("rb") as another:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(another.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert not abort.done()
            assert sc.open_cached_audio(tmp_path, *spec) is None
        finally:
            release.set()
        writer.result(timeout=5)
        abort.result(timeout=5)
    assert not disk_files(tmp_path)


def test_optional_write_error_and_constructor_cleanup(tmp_path, spec, monkeypatch):
    original = sc._write_all

    def failed(fd, data):
        raise OSError("fictional disk failure")

    monkeypatch.setattr(sc, "_write_all", failed)
    assert sc.reserve_audio(tmp_path, *spec) is None
    assert not disk_files(tmp_path)
    monkeypatch.setattr(sc, "_write_all", original)
    result = reserve(tmp_path, spec)
    monkeypatch.setattr(sc, "_write_all", failed)
    with pytest.raises(OSError):
        result.append(b"audio")
    result.abort()
    assert not disk_files(tmp_path)


def test_manifest_creation_peak_is_funded(tmp_path, spec, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_BYTES", sc.RESERVATION_BYTES)
    monkeypatch.setattr(cache, "CACHE_FILES", sc.RESERVATION_FILES)
    result = reserve(tmp_path, spec)
    block = b"x" * 64000
    for _ in range(250):
        result.append(block)
    peaks = []
    original = sc._write_all

    def observe(fd, data):
        original(fd, data)
        files = disk_files(tmp_path)
        peaks.append((len(files), sum(path.stat().st_size for path in files)))

    monkeypatch.setattr(sc, "_write_all", observe)
    assert result.commit(complete(sc.MAX_AUDIO_BYTES, frames=250))
    assert peaks and peaks[0][0] == 3
    assert all(count <= cache.CACHE_FILES and size <= cache.CACHE_BYTES for count, size in peaks)


def test_concurrent_same_key_commits_are_serialized_by_os_root_lock(tmp_path, spec):
    first, second = reserve(tmp_path, spec), reserve(tmp_path, spec)
    first.append(b"first")
    second.append(b"other")
    start = threading.Barrier(2)

    def commit_together(reservation):
        start.wait(timeout=5)
        return reservation.commit(complete())

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(commit_together, reservation) for reservation in (first, second)]
        assert all(future.result(timeout=10) for future in futures)
    hit = sc.open_cached_audio(tmp_path, *spec)
    assert hit is not None and hit.file.read() in (b"first", b"other")
    hit.close()
    assert len(disk_files(tmp_path)) == 2


def test_old_live_then_dead_lease_cannot_unlink_later_same_key_audio(tmp_path, spec):
    child = subprocess.Popen(
        child_args(tmp_path, spec, "active"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None and child.stdout.readline() == "ready\n"
        publish(tmp_path, spec, b"later")
        path = resolve_owned_artifact(tmp_path, spec[0], spec[1])
        assert path is not None
        inode = path.stat().st_ino
        assert child.stdin is not None
        child.stdin.close()
        assert child.wait(timeout=15) == 23
        hit = sc.open_cached_audio(tmp_path, *spec)
        assert hit is not None and hit.file.read() == b"later"
        hit.close()
        assert path.stat().st_ino == inode
        assert len(disk_files(tmp_path)) == 2
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=15)


def test_eviction_crash_reclaims_only_orphan_private_manifest(tmp_path, spec):
    publish(tmp_path, spec)
    program = r"""
import os, sys
from pathlib import Path
from orchestrator.speech import cache, stream_cache as sc
cache.CACHE_FILES = 2
unlink = sc._Ledger._unlink_exact
def crash(directory, name, expected):
    unlink(directory, name, expected)
    if name.endswith(".mp3"):
        os._exit(23)
sc._Ledger._unlink_exact = staticmethod(crash)
cache.store_audio(Path(sys.argv[1]), sys.argv[2], "buffered.wav", b"next")
"""
    child = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path), str(spec[0])],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert child.returncode == 23, child.stderr
    assert sc.open_cached_audio(tmp_path, *spec) is None
    assert not disk_files(tmp_path)
    reserve(tmp_path, spec).abort()


@pytest.mark.parametrize("change", ["inode", "size", "identity", "duration", "permissions"])
def test_invalid_completed_pair_fails_closed_without_deleting(tmp_path, spec, change):
    publish(tmp_path, spec)
    manifest = next((tmp_path / ".progressive").glob("*.manifest"))
    value = json.loads(manifest.read_bytes())
    audio = resolve_owned_artifact(tmp_path, spec[0], spec[1])
    assert audio is not None
    if change == "inode":
        # A later same-key replacement must never be removed as the old inode.
        replacement = audio.with_suffix(".new")
        replacement.write_bytes(b"other")
        replacement.chmod(0o600)
        replacement.replace(audio)
        # A mismatched published pair disables caching, never deletes later audio.
        assert sc.open_cached_audio(tmp_path, *spec) is None
        assert sc.reserve_audio(tmp_path, *spec) is None
        assert manifest.exists()
        assert audio.read_bytes() == b"other"
        return
    if change == "size":
        value["complete"]["bytes"] = 4
    elif change == "identity":
        value["identity"]["text"] = "never allowed in a journal"
    elif change == "duration":
        value["complete"]["encoded_seconds"] = float("inf")
    else:
        audio.chmod(0o644)
    manifest.write_bytes(json.dumps(value).encode())
    before = manifest.read_bytes(), audio.read_bytes()
    assert sc.open_cached_audio(tmp_path, *spec) is None
    assert sc.reserve_audio(tmp_path, *spec) is None
    assert (manifest.read_bytes(), audio.read_bytes()) == before


def test_large_manager_listing_is_bounded_and_never_purged(tmp_path, spec):
    manager = tmp_path / ".progressive"
    manager.mkdir()
    for i in range(2 * cache.CACHE_FILES + 10):
        (manager / f"unrecognized-{i}").touch()
    assert sc.reserve_audio(tmp_path, *spec) is None
    assert len(list(manager.iterdir())) == 2 * cache.CACHE_FILES + 10


def test_identity_rejects_raw_text_and_nonfinite_or_huge_speed(tmp_path, spec):
    for identity in (
        {**spec[2], "text": "private words"},
        {**spec[2], "speed": float("nan")},
        {**spec[2], "speed": 10**1000},
        {**spec[2], "version": True},
    ):
        assert sc.reserve_audio(tmp_path, spec[0], spec[1], identity) is None
    assert not disk_files(tmp_path)


def test_failed_manifest_write_does_not_publish_or_leak_reservation(tmp_path, spec, monkeypatch):
    result = reserve(tmp_path, spec)
    result.append(b"audio")
    original = sc._write_all

    def fail_manifest(fd, data):
        if b'"complete"' in data:
            os.write(fd, data[:20])
            raise OSError("fictional partial manifest write")
        original(fd, data)

    monkeypatch.setattr(sc, "_write_all", fail_manifest)
    assert not result.commit(complete())
    assert not disk_files(tmp_path)


def test_recovery_revalidates_lease_path_inode_after_lock(tmp_path, spec, monkeypatch):
    child = subprocess.run(
        child_args(tmp_path, spec, "reserved"), capture_output=True, text=True, timeout=15
    )
    assert child.returncode == 23, child.stderr
    manager = tmp_path / ".progressive"
    lease = next(manager.glob("*.lease"))
    stage = next(manager.glob("*.stage"))
    original = fcntl.flock
    replaced = False

    def replace_after_acquire(fd, operation):
        nonlocal replaced
        original(fd, operation)
        if operation == fcntl.LOCK_EX | fcntl.LOCK_NB and not replaced:
            replaced = True
            temporary = tmp_path / "replacement-lease"
            temporary.write_bytes(lease.read_bytes())
            temporary.chmod(0o600)
            temporary.replace(lease)

    monkeypatch.setattr(sc.fcntl, "flock", replace_after_acquire)
    assert sc.reserve_audio(tmp_path, *spec) is None
    assert replaced and lease.exists() and stage.read_bytes() == b"audio"


def test_future_completion_timestamp_fails_closed_without_deletion(tmp_path, spec):
    publish(tmp_path, spec)
    manifest = next((tmp_path / ".progressive").glob("*.manifest"))
    value = json.loads(manifest.read_bytes())
    value["created"] = sc.time.time() + 86400
    manifest.write_bytes(json.dumps(value).encode())
    before = {path.name: path.read_bytes() for path in disk_files(tmp_path)}
    assert sc.open_cached_audio(tmp_path, *spec) is None
    assert sc.reserve_audio(tmp_path, *spec) is None
    assert {path.name: path.read_bytes() for path in disk_files(tmp_path)} == before


def test_commit_rechecks_shared_budget_before_manifest_creation(tmp_path, spec, monkeypatch):
    result = reserve(tmp_path, spec)
    result.append(b"audio")
    monkeypatch.setattr(cache, "CACHE_BYTES", sc.RESERVATION_BYTES - 1)
    assert not result.commit(complete())
    assert not disk_files(tmp_path)


def test_root_path_inode_is_rechecked_after_root_flock(tmp_path, spec, monkeypatch):
    root = tmp_path / "cache"
    root.mkdir()
    original = fcntl.flock
    swapped = False

    def replace_root(fd, operation):
        nonlocal swapped
        original(fd, operation)
        if operation == fcntl.LOCK_EX and not swapped:
            swapped = True
            root.rename(tmp_path / "old-cache")
            root.mkdir()

    monkeypatch.setattr(sc.fcntl, "flock", replace_root)
    assert sc.reserve_audio(root, *spec) is None
    assert swapped and not list(root.iterdir())
    assert not (tmp_path / "old-cache" / ".progressive").exists()


def test_buffered_atomic_replacement_preserves_old_clip_on_write_failure(
    tmp_path, spec, monkeypatch
):
    cache.store_audio(tmp_path, spec[0], "replace.wav", b"old")
    path = cache.cached_audio(tmp_path, spec[0], "replace.wav")
    assert path is not None
    old_inode = path.stat().st_ino

    def failed(fd, content):
        os.write(fd, content[:1])
        raise OSError("fictional temporary write failure")

    monkeypatch.setattr(sc, "_write_all", failed)
    with pytest.raises(OSError):
        cache.store_audio(tmp_path, spec[0], "replace.wav", b"new")
    assert path.read_bytes() == b"old" and path.stat().st_ino == old_inode
    assert len(disk_files(tmp_path)) == 1


def test_buffered_replacement_removes_paired_manifest_only_after_success(tmp_path, spec):
    publish(tmp_path, spec)
    cache.store_audio(tmp_path, spec[0], spec[1], b"buffered")
    path = cache.cached_audio(tmp_path, spec[0], spec[1])
    assert path is not None and path.read_bytes() == b"buffered"
    assert not list((tmp_path / ".progressive").iterdir())
    assert sc.open_cached_audio(tmp_path, *spec) is None
