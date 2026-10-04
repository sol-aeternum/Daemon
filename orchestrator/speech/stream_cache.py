"""Private complete-only speech cache with one cross-process occupancy ledger.

The manager is outside all servable owner namespaces. Root flock sections cover
transitions, not synthesis. A separately opened, OS-locked lease protects each
writer until its synchronous append/commit/abort actually exits. Callers must own
and await thread tails; cancelling an asyncio waiter does not cancel disk I/O.

Recovery is tested for process crashes, not storage power loss. Inconsistent or
unknown disk state fails closed without a broad purge. No raw speech text is kept.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
from typing import Any, BinaryIO
from uuid import UUID

from orchestrator.artifacts import (
    ArtifactOwnerError,
    artifact_owner_namespace,
    is_artifact_owner_namespace,
)
from orchestrator.speech import cache
from orchestrator.speech.contracts import MAX_AUDIO_BYTES, SpeechRequest

_cache_warning_reported = False


def _warn_cache_unavailable() -> None:
    global _cache_warning_reported
    if not _cache_warning_reported:
        _cache_warning_reported = True
        logging.getLogger(__name__).error("speech_cache_state_unavailable")


RENDERING = "speech-mp3-progressive-v1"
CONTROL_BYTES = 4096
# Stage, immutable lease journal, and immutable completion manifest. No rewrites
# or unbudgeted control temporaries: EXCL manifest creation is the publish journal.
RESERVATION_BYTES = MAX_AUDIO_BYTES + 2 * CONTROL_BYTES
RESERVATION_FILES = 3
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_CREATE_FLAGS = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
_ENTRY = re.compile(r"([0-9a-f]{32})\.(lease|stage|manifest)\Z")
_FILENAME = re.compile(r"[0-9a-f]{64}\.mp3\Z")
_IDENTITY = frozenset(
    {"version", "provider", "model", "voice", "speed", "format", "mime", "sample_rate", "rendering"}
)
_COMPLETE = frozenset(
    {
        "frames",
        "bytes",
        "source_seconds",
        "encoded_seconds",
        "synthesis_seconds",
        "audio_path",
        "cache_available",
    }
)


def progressive_filename(provider: str, model: str, speech: SpeechRequest) -> str:
    return cache.audio_filename(provider, model, speech, rendering=RENDERING)


def _regular(value: os.stat_result) -> None:
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise OSError("unsafe speech cache file")


def _inode(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _open_directory(path: Path, *, create: bool = False) -> int:
    """Walk every component without following even an ancestor symlink."""
    absolute = Path(os.path.abspath(path))
    descriptor = os.open(absolute.anchor, _DIR_FLAGS)
    try:
        for part in absolute.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def _directory(parent: int, name: str, *, create: bool = False) -> Iterator[int]:
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
    descriptor = os.open(name, _DIR_FLAGS, dir_fd=parent)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _names(descriptor: int) -> list[str]:
    # Bound enumeration itself, not just the returned list allocation.
    names: list[str] = []
    with os.scandir(descriptor) as entries:
        for entry in entries:
            if len(names) >= 2 * cache.CACHE_FILES + 8:
                raise OSError("speech cache directory exceeds scan bound")
            names.append(entry.name)
    return names


def _number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _identity(value: dict) -> dict:
    if type(value) is not dict or value.keys() not in (_IDENTITY, _IDENTITY - {"version"}):
        raise ArtifactOwnerError("invalid speech cache identity")
    result = {"version": 1, **value}
    if type(result["version"]) is not int or result["version"] != 1:
        raise ArtifactOwnerError("invalid speech cache identity")
    for key in ("provider", "model"):
        if not isinstance(result[key], str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", result[key]
        ):
            raise ArtifactOwnerError("invalid speech cache identity")
    if (
        result["voice"] != "daemon-default"
        or not _number(result["speed"])
        or not 0.5 <= result["speed"] <= 2
        or result["format"] != "mp3"
        or result["mime"] != "audio/mpeg"
        or type(result["sample_rate"]) is not int
        or result["sample_rate"] != 24000
        or result["rendering"] != RENDERING
    ):
        raise ArtifactOwnerError("invalid speech cache identity")
    result["speed"] = float(result["speed"])
    return result


def _complete(value: dict, size: int) -> dict:
    if type(value) is not dict or value.keys() != _COMPLETE:
        raise ArtifactOwnerError("invalid speech cache completion")
    if (
        type(value["bytes"]) is not int
        or value["bytes"] != size
        or not 0 < size <= MAX_AUDIO_BYTES
        or type(value["frames"]) is not int
        or not 0 < value["frames"] <= 16384
        or not value["frames"] <= size <= value["frames"] * 65532
        or not all(
            _number(value[key])
            for key in ("source_seconds", "encoded_seconds", "synthesis_seconds")
        )
        or not 0 < value["source_seconds"] <= 300
        or not value["encoded_seconds"] <= 300.15
        or not 0 <= value["encoded_seconds"] - value["source_seconds"] <= 0.15 + 1e-9
        or not 0 <= value["synthesis_seconds"] <= 120
        or value["audio_path"] is not None
        or type(value["cache_available"]) is not bool
        or value["cache_available"]
    ):
        raise ArtifactOwnerError("invalid speech cache completion")
    return dict(value)


def _unique(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactOwnerError("duplicate speech cache journal field")
        result[key] = value
    return result


def _read_json(manager: int, name: str) -> tuple[dict, os.stat_result]:
    descriptor = os.open(name, _READ_FLAGS, dir_fd=manager)
    try:
        info = os.fstat(descriptor)
        _regular(info)
        if info.st_mode & 0o077 or not 0 < info.st_size <= CONTROL_BYTES:
            raise OSError("invalid speech cache journal")
        payload = os.read(descriptor, CONTROL_BYTES + 1)
        if len(payload) != info.st_size or os.fstat(descriptor).st_size != info.st_size:
            raise OSError("speech cache journal changed while reading")
        value = json.loads(payload, object_pairs_hook=_unique)
        if type(value) is not dict:
            raise OSError("invalid speech cache journal")
        return value, info
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        count = os.write(descriptor, view)
        if count <= 0:
            raise OSError("short speech cache write")
        view = view[count:]


def _encoded(value: dict) -> bytes:
    content = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    if len(content) > CONTROL_BYTES:
        raise OSError("speech cache journal exceeds bound")
    return content


def _validate_journal(value: dict, *, manifest: bool) -> dict:
    fields = {"schema", "owner", "filename", "identity", "device", "inode"}
    fields |= {"complete", "created"} if manifest else set()
    if value.keys() != fields or type(value["schema"]) is not int or value["schema"] != 1:
        raise OSError("invalid speech cache journal")
    if not isinstance(value["owner"], str) or not is_artifact_owner_namespace(value["owner"]):
        raise OSError("invalid speech cache owner")
    if not isinstance(value["filename"], str) or not _FILENAME.fullmatch(value["filename"]):
        raise OSError("invalid speech cache filename")
    if any(type(value[key]) is not int or value[key] < 0 for key in ("device", "inode")):
        raise OSError("invalid speech cache inode")
    if _identity(value["identity"]) != value["identity"]:
        raise OSError("noncanonical speech cache identity")
    if manifest:
        if not _number(value["created"]) or not 0 < value["created"] <= time.time():
            raise OSError("invalid speech cache timestamp")
        _complete(value["complete"], value["complete"]["bytes"])
    return value


def _matching(info: os.stat_result, value: dict) -> bool:
    return _inode(info) == (value["device"], value["inode"])


def _stat_file(directory: int, name: str) -> os.stat_result | None:
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None
    _regular(info)
    return info


@dataclass
class _Clip:
    owner: str
    name: str
    info: os.stat_result
    manifest: str | None = None
    journal: dict | None = None
    metadata_size: int = 0
    protected: bool = False
    manifest_info: os.stat_result | None = None


class _Ledger:
    """Opened only under root flock; validates everything before recovery/pruning."""

    def __init__(self, root: int, manager: int):
        self.root = root
        self.manager = manager
        self.clips: dict[tuple[str, str], _Clip] = {}
        self.used_bytes = 0
        self.used_files = 0
        self._scan()

    def _scan(self) -> None:
        try:
            self._scan_validated()
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise OSError("invalid speech cache ledger") from exc

    def _scan_validated(self) -> None:
        for name in _names(self.root):
            if not is_artifact_owner_namespace(name):
                continue
            with _directory(self.root, name) as owner:
                for filename in _names(owner):
                    info = _stat_file(owner, filename)
                    if info is None:
                        raise OSError("speech cache changed during scan")
                    self.clips[name, filename] = _Clip(name, filename, info)
                    self.used_bytes += info.st_size
                    self.used_files += 1

        groups: dict[str, dict[str, tuple[dict | None, os.stat_result]]] = {}
        for name in _names(self.manager):
            match = _ENTRY.fullmatch(name)
            if match is None:
                raise OSError("unknown speech cache manager entry")
            token, kind = match.groups()
            if kind == "stage":
                info = _stat_file(self.manager, name)
                if info is None or info.st_mode & 0o077 or info.st_size > MAX_AUDIO_BYTES:
                    raise OSError("invalid speech cache stage")
                groups.setdefault(token, {})[kind] = None, info
            else:
                value, info = _read_json(self.manager, name)
                _validate_journal(value, manifest=kind == "manifest")
                groups.setdefault(token, {})[kind] = value, info

        # No mutation before all manager inputs and pair relationships validate.
        keys: set[tuple[str, str]] = set()
        for group in groups.values():
            lease = group.get("lease")
            manifest = group.get("manifest")
            stage = group.get("stage")
            if lease is None and (manifest is None or stage is not None):
                raise OSError("orphan speech cache stage")
            journal = (lease or manifest)[0]  # type: ignore[index]
            assert journal is not None
            if manifest is not None and lease is not None:
                if any(manifest[0][key] != journal[key] for key in journal):  # type: ignore[index]
                    raise OSError("speech cache journals disagree")
            if stage is not None and not _matching(stage[1], journal):
                raise OSError("speech cache stage inode changed")
            clip = self.clips.get((journal["owner"], journal["filename"]))
            published = clip is not None and _matching(clip.info, journal)
            if published:
                assert clip is not None
                if manifest is None or stage is not None:
                    raise OSError("inconsistent speech cache publication")
                self._validate_clip(clip, manifest[0])  # type: ignore[arg-type]
                key = (clip.owner, clip.name)
                if key in keys:
                    raise OSError("duplicate speech cache manifest")
                keys.add(key)
            elif lease is not None and stage is None:
                raise OSError("speech cache pair missing")
            elif lease is None and clip is not None:
                raise OSError("speech cache published inode changed")

        for token, group in groups.items():
            lease = group.get("lease")
            manifest = group.get("manifest")
            stage = group.get("stage")
            journal = (lease or manifest)[0]  # type: ignore[index]
            assert journal is not None
            clip = self.clips.get((journal["owner"], journal["filename"]))
            published = clip is not None and _matching(clip.info, journal)
            if lease is not None:
                descriptor = os.open(token + ".lease", _READ_FLAGS, dir_fd=self.manager)
                try:
                    if _inode(os.fstat(descriptor)) != _inode(lease[1]):
                        raise OSError("speech cache lease changed")
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        # Charge the reservation once, not its same partial twice.
                        if published:
                            assert clip is not None and manifest is not None
                            self.used_bytes -= clip.info.st_size
                            self.used_files -= 1
                            clip.manifest = token + ".manifest"
                            clip.journal = manifest[0]
                            clip.metadata_size = manifest[1].st_size
                            clip.manifest_info = manifest[1]
                            clip.protected = True
                        self.used_bytes += RESERVATION_BYTES
                        self.used_files += RESERVATION_FILES
                        continue
                    current = _stat_file(self.manager, token + ".lease")
                    if current is None or _inode(current) != _inode(os.fstat(descriptor)):
                        raise OSError("speech cache lease replaced")
                    if not published:
                        if stage is not None:
                            self._unlink_exact(self.manager, token + ".stage", stage[1])
                        if manifest is not None:
                            self._unlink_exact(self.manager, token + ".manifest", manifest[1])
                    self._unlink_exact(self.manager, token + ".lease", current)
                finally:
                    os.close(descriptor)
            elif not published:
                # An eviction can crash after unlinking audio. Reclaim only the
                # validated orphan manifest, never a different same-key audio.
                assert manifest is not None
                self._unlink_exact(self.manager, token + ".manifest", manifest[1])
            if published:
                assert clip is not None and manifest is not None
                clip.manifest = token + ".manifest"
                clip.journal = manifest[0]
                clip.metadata_size = manifest[1].st_size
                clip.manifest_info = manifest[1]
                self.used_bytes += clip.metadata_size
                self.used_files += 1

        now = time.time()
        for clip in list(self.clips.values()):
            timestamp = clip.journal["created"] if clip.journal else clip.info.st_mtime
            if (
                not clip.protected
                and Path(clip.name).suffix in (".mp3", ".opus", ".wav")
                and now - timestamp > cache.CACHE_TTL_SECONDS
            ):
                self.evict(clip)

    @staticmethod
    def _validate_clip(clip: _Clip, manifest: dict) -> None:
        if clip.info.st_mode & 0o077 or clip.info.st_size != manifest["complete"]["bytes"]:
            raise OSError("invalid complete speech cache file")
        _complete(manifest["complete"], clip.info.st_size)

    @staticmethod
    def _unlink_exact(directory: int, name: str, expected: os.stat_result) -> None:
        current = _stat_file(directory, name)
        if current is None or _inode(current) != _inode(expected):
            raise OSError("speech cache unlink target changed")
        os.unlink(name, dir_fd=directory)

    def evict(self, clip: _Clip) -> None:
        if clip.protected:
            raise OSError("speech cache publication still reserved")
        with _directory(self.root, clip.owner) as owner:
            self._unlink_exact(owner, clip.name, clip.info)
        if clip.manifest:
            if clip.manifest_info is None:
                raise OSError("speech cache manifest identity missing")
            self._unlink_exact(self.manager, clip.manifest, clip.manifest_info)
        del self.clips[clip.owner, clip.name]
        self.used_bytes -= clip.info.st_size + clip.metadata_size
        self.used_files -= 1 + int(clip.manifest is not None)

    def fits(self, size: int, files: int) -> bool:
        return (
            self.used_bytes + size <= cache.CACHE_BYTES
            and self.used_files + files <= cache.CACHE_FILES
        )


@dataclass
class CachedSpeech:
    file: BinaryIO
    complete: dict

    def close(self) -> None:
        self.file.close()


def open_cached_audio(
    root: Path, owner: UUID | str, filename: str, identity: dict
) -> CachedSpeech | None:
    try:
        namespace = artifact_owner_namespace(str(owner))
        expected = _identity(identity)
        if not _FILENAME.fullmatch(filename):
            return None
        with (
            cache.cache_lock(root) as root_fd,
            _directory(root_fd, ".progressive", create=True) as manager,
        ):
            ledger = _Ledger(root_fd, manager)
            clip = ledger.clips.get((namespace, filename))
            if (
                clip is None
                or clip.journal is None
                or clip.journal["identity"] != expected
                or time.time() - clip.journal["created"] > cache.CACHE_TTL_SECONDS
            ):
                return None
            with _directory(root_fd, namespace) as directory:
                descriptor = os.open(filename, _READ_FLAGS, dir_fd=directory)
            try:
                info = os.fstat(descriptor)
                _regular(info)
                if _inode(info) != _inode(clip.info) or info.st_size != clip.info.st_size:
                    raise OSError("speech cache read target changed")
                output = os.fdopen(descriptor, "rb")
            except BaseException:
                os.close(descriptor)
                raise
            return CachedSpeech(output, dict(clip.journal["complete"]))
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        _warn_cache_unavailable()
        return None


class CacheReservation:
    """Synchronous serialized writer. No lifecycle method may outlive its lease."""

    def __init__(
        self,
        root: Path,
        token: str,
        journal: dict,
        lease: int,
        stage: int,
        root_inode: tuple[int, int],
        manager_inode: tuple[int, int],
    ):
        self._root = root
        self._token = token
        self._journal = journal
        self._lease = lease
        self._stage = stage
        self._root_inode = root_inode
        self._manager_inode = manager_inode
        self._size = 0
        self._guard = threading.RLock()
        self._closed = False
        self._committed = False
        self._manifest_inode: tuple[int, int] | None = None

    def append(self, data: bytes) -> None:
        with self._guard:
            if self._closed or type(data) is not bytes or not data:
                raise OSError("inactive or invalid speech cache append")
            if self._size + len(data) > MAX_AUDIO_BYTES:
                raise OSError("speech cache output exceeds reservation")
            _write_all(self._stage, data)
            self._size += len(data)

    def _check_directories(self, root: int, manager: int) -> None:
        if (
            _inode(os.fstat(root)) != self._root_inode
            or _inode(os.fstat(manager)) != self._manager_inode
        ):
            raise OSError("speech cache directory changed")

    def commit(self, complete: dict) -> bool:
        with self._guard:
            if self._closed:
                return self._committed
            try:
                validated = _complete(complete, self._size)
                info = os.fstat(self._stage)
                if not _matching(info, self._journal) or info.st_size != self._size:
                    raise OSError("speech cache staged bytes changed")
                os.fsync(self._stage)
                with (
                    cache.cache_lock(self._root) as root,
                    _directory(root, ".progressive") as manager,
                ):
                    self._check_directories(root, manager)
                    ledger = _Ledger(root, manager)
                    if not ledger.fits(0, 0):
                        raise OSError("speech cache capacity changed")
                    existing = ledger.clips.get((self._journal["owner"], self._journal["filename"]))
                    if existing is not None:
                        if (
                            existing.journal is None
                            or existing.journal["identity"] != self._journal["identity"]
                        ):
                            raise OSError("speech cache destination already occupied")
                        # Never replace an earlier complete inode for the same key.
                        self._discard(root, manager)
                        self._committed = True
                    else:
                        stage = _stat_file(manager, self._token + ".stage")
                        lease = _stat_file(manager, self._token + ".lease")
                        if (
                            stage is None
                            or lease is None
                            or not _matching(stage, self._journal)
                            or _inode(lease) != _inode(os.fstat(self._lease))
                        ):
                            raise OSError("speech cache reservation changed")
                        manifest = {**self._journal, "complete": validated, "created": time.time()}
                        descriptor = os.open(
                            self._token + ".manifest", _CREATE_FLAGS, 0o600, dir_fd=manager
                        )
                        try:
                            self._manifest_inode = _inode(os.fstat(descriptor))
                            _write_all(descriptor, _encoded(manifest))
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)
                        os.fsync(manager)
                        with _directory(root, self._journal["owner"]) as owner:
                            # Root lock prevents cooperating same-key insertion.
                            if _stat_file(owner, self._journal["filename"]) is not None:
                                raise OSError("speech cache destination changed")
                            os.rename(
                                self._token + ".stage",
                                self._journal["filename"],
                                src_dir_fd=manager,
                                dst_dir_fd=owner,
                            )
                            os.fsync(owner)
                            published = _stat_file(owner, self._journal["filename"])
                            if (
                                published is None
                                or not _matching(published, self._journal)
                                or published.st_size != self._size
                            ):
                                raise OSError("speech cache publication could not be verified")
                        # Manifest first, MP3 last; lease still held through transition.
                        _Ledger._unlink_exact(manager, self._token + ".lease", lease)
                        os.fsync(manager)
                        self._committed = True
                self._close()
                return True
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                self.abort()
                return False

    def _discard(self, root: int, manager: int) -> None:
        self._check_directories(root, manager)
        # Never unlink owner audio here: a commit may have reached rename already.
        stage = _stat_file(manager, self._token + ".stage")
        published = False
        with _directory(root, self._journal["owner"]) as owner:
            audio = _stat_file(owner, self._journal["filename"])
            published = audio is not None and _matching(audio, self._journal)
        if stage is not None:
            if not _matching(stage, self._journal):
                raise OSError("speech cache stage replaced")
            _Ledger._unlink_exact(manager, self._token + ".stage", stage)
        if not published:
            manifest = _stat_file(manager, self._token + ".manifest")
            if manifest is not None:
                if _inode(manifest) != self._manifest_inode:
                    raise OSError("speech cache manifest replaced")
                _Ledger._unlink_exact(manager, self._token + ".manifest", manifest)
        lease = _stat_file(manager, self._token + ".lease")
        if lease is not None:
            if _inode(lease) != _inode(os.fstat(self._lease)):
                raise OSError("speech cache lease replaced")
            _Ledger._unlink_exact(manager, self._token + ".lease", lease)

    def _close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                os.close(self._stage)
            finally:
                os.close(self._lease)

    def abort(self) -> None:
        with self._guard:
            if self._closed:
                return
            try:
                with (
                    cache.cache_lock(self._root) as root,
                    _directory(root, ".progressive") as manager,
                ):
                    self._discard(root, manager)
            except (OSError, ValueError):
                # Unknown/replaced paths remain for diagnosis, never unsafe purge.
                pass
            finally:
                self._close()


def reserve_audio(
    root: Path, owner: UUID | str, filename: str, identity: dict
) -> CacheReservation | None:
    lease = stage = None
    created: list[tuple[str, os.stat_result]] = []
    try:
        namespace = artifact_owner_namespace(str(owner))
        expected = _identity(identity)
        if not _FILENAME.fullmatch(filename):
            return None
        with (
            cache.cache_lock(root) as directory,
            _directory(directory, ".progressive", create=True) as manager,
        ):
            ledger = _Ledger(directory, manager)
            if not ledger.fits(RESERVATION_BYTES, RESERVATION_FILES):
                return None
            with _directory(directory, namespace, create=True):
                pass
            token = secrets.token_hex(16)
            try:
                lease = os.open(token + ".lease", _CREATE_FLAGS, 0o600, dir_fd=manager)
                created.append((token + ".lease", os.fstat(lease)))
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                stage = os.open(token + ".stage", _CREATE_FLAGS, 0o600, dir_fd=manager)
                info = os.fstat(stage)
                created.append((token + ".stage", info))
                journal = {
                    "schema": 1,
                    "owner": namespace,
                    "filename": filename,
                    "identity": expected,
                    "device": info.st_dev,
                    "inode": info.st_ino,
                }
                _write_all(lease, _encoded(journal))
                os.fsync(lease)
                os.fsync(manager)
                reservation = CacheReservation(
                    root,
                    token,
                    journal,
                    lease,
                    stage,
                    _inode(os.fstat(directory)),
                    _inode(os.fstat(manager)),
                )
                lease = stage = None  # Descriptors now belong to the reservation.
                return reservation
            except BaseException:
                for name, info in reversed(created):
                    _Ledger._unlink_exact(manager, name, info)
                raise
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        _warn_cache_unavailable()
        return None
    finally:
        if stage is not None:
            os.close(stage)
        if lease is not None:
            os.close(lease)


def _store_buffered(root: Path, owner: object, filename: str, content: bytes) -> None:
    """Legacy completed eviction policy, now funding its temporary-write peak."""
    if not filename or filename != os.path.basename(filename) or filename in (".", ".."):
        raise ArtifactOwnerError("artifact filename is unsafe")
    namespace = artifact_owner_namespace(str(owner))
    with (
        cache.cache_lock(root) as directory,
        _directory(directory, ".progressive", create=True) as manager,
    ):
        ledger = _Ledger(directory, manager)
        # Evict only completed recognized speech, never unknown files/reservations.
        clips = sorted(
            (
                clip
                for clip in ledger.clips.values()
                if not clip.protected and Path(clip.name).suffix in (".mp3", ".opus", ".wav")
            ),
            key=lambda clip: clip.info.st_mtime,
        )
        while not ledger.fits(len(content), 1) and clips:
            ledger.evict(clips.pop(0))
        if not ledger.fits(len(content), 1):
            raise OSError("speech cache capacity reserved")
        existing = ledger.clips.get((namespace, filename))
        if existing is not None and existing.protected:
            raise OSError("speech cache publication still reserved")
        with _directory(directory, namespace, create=True) as owner_fd:
            current = _stat_file(owner_fd, filename)
            if (current is None) != (existing is None) or (
                current is not None
                and existing is not None
                and _inode(current) != _inode(existing.info)
            ):
                raise ArtifactOwnerError("artifact destination changed")
            temporary = f".{filename}.{secrets.token_hex(16)}.tmp"
            descriptor = os.open(temporary, _CREATE_FLAGS, 0o600, dir_fd=owner_fd)
            info = os.fstat(descriptor)
            try:
                _write_all(descriptor, content)
                # Preserve legacy atomic replacement when the funded peak fits:
                # a failed temporary write must not destroy the previous clip.
                before_publish = _stat_file(owner_fd, filename)
                if (before_publish is None) != (current is None) or (
                    before_publish is not None
                    and current is not None
                    and _inode(before_publish) != _inode(current)
                ):
                    raise ArtifactOwnerError("artifact destination changed")
                os.rename(temporary, filename, src_dir_fd=owner_fd, dst_dir_fd=owner_fd)
                if existing is not None and existing.manifest is not None:
                    if existing.manifest_info is None:
                        raise OSError("speech cache manifest identity missing")
                    _Ledger._unlink_exact(manager, existing.manifest, existing.manifest_info)
            finally:
                os.close(descriptor)
                remaining = _stat_file(owner_fd, temporary)
                if remaining is not None:
                    _Ledger._unlink_exact(owner_fd, temporary, info)


def _cached_buffered_path(root: Path, owner: object, filename: str) -> Path | None:
    if (
        not filename
        or filename != os.path.basename(filename)
        or Path(filename).suffix not in (".mp3", ".opus", ".wav")
    ):
        return None
    try:
        namespace = artifact_owner_namespace(str(owner))
        with (
            cache.cache_lock(root) as directory,
            _directory(directory, ".progressive", create=True) as manager,
        ):
            ledger = _Ledger(directory, manager)
            clip = ledger.clips.get((namespace, filename))
            if (
                clip is not None
                and time.time() - (clip.journal["created"] if clip.journal else clip.info.st_mtime)
                <= cache.CACHE_TTL_SECONDS
            ):
                return root / namespace / filename
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        _warn_cache_unavailable()
    return None
