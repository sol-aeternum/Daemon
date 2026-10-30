"""Uncalled store-owned POSIX publication mechanics, never database authority.

Only the trusted registry supplies independently authorised immutable bindings.
No directories, identities, plaintext temporaries, deletions or cleanup are made.
Reads are passive; repair is explicit and never overwrites conflicting evidence.
"""

from __future__ import annotations

import ctypes
import fcntl
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from orchestrator.artifact_envelope import (
    MAX_CONTENT_BYTES,
    ArtifactBinding,
    ArtifactEnvelopeCodec,
    ArtifactEnvelopeError,
)
from orchestrator.artifact_staging import (
    ArtifactStageError,
    _exists,
    _file,
    _Handles,
    _identity,
    prepare_artifact,
)


class StorageError(ValueError):
    """Content-free outcomes only; never expose paths, bytes or cipher errors."""

    def __init__(self, code: str = "uncertain") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class StoredBinding:
    artifact: ArtifactBinding
    namespace: str
    intent: str
    public_name: str
    measured_bytes: int
    sha256: str

    def validate(self) -> None:
        self.artifact.validate()
        if (
            any(
                type(v) is not str or re.fullmatch(r"[0-9a-f]{32}", v) is None
                for v in (self.namespace, self.intent, self.public_name)
            )
            or type(self.measured_bytes) is not int
            or not 0 <= self.measured_bytes <= MAX_CONTENT_BYTES
            or type(self.sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None
        ):
            raise StorageError("invalid")


def _rename_no_replace(source: int, name: str, target: int, public: str) -> None:
    try:
        rename = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError):
        raise StorageError("unsupported") from None
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source, name.encode("ascii"), target, public.encode("ascii"), 1) != 0:
        # Includes EEXIST: preserve both names; fresh repair verifies actual state.
        raise OSError(ctypes.get_errno(), "rename_refused")


def _access(
    root: Path,
    codec: ArtifactEnvelopeCodec,
    binding: StoredBinding,
    *,
    sync: bool,
    repair: bool,
    require_stage: bool = False,
) -> bytes:
    binding.validate()
    if not isinstance(root, Path) or not root.is_absolute() or ".." in root.parts:
        raise StorageError("invalid")
    try:
        with _Handles() as handles:
            base = handles.root(root)
            live_root = handles.directory(base, "live")
            live = handles.directory(live_root, binding.namespace)
            staging = handles.directory(live, "staging")
            private_root = handles.directory(base, "private")
            private = handles.directory(private_root, binding.namespace)
            lock = handles.open(".prepare.lock", os.O_RDWR | os.O_NONBLOCK, private)
            handles.revalidate(base, private, lock)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                handles.revalidate(base, private, lock)
                stage = _exists(staging, binding.intent)
                public = _exists(live, binding.public_name)
                if (stage and public) or (require_stage and (not stage or public)):
                    raise StorageError()
                links = 1 + int(stage) + int(public)
                fd = handles.open(binding.intent, os.O_RDONLY | os.O_NONBLOCK, private)
                saved = os.fstat(fd)
                device = os.fstat(private).st_dev
                _file(saved, links, device)

                def check_names() -> None:
                    names = [(private, binding.intent)]
                    if stage:
                        names.append((staging, binding.intent))
                    if public:
                        names.append((live, binding.public_name))
                    for parent, name in names:
                        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                        _file(current, links, device)
                        if (
                            _identity(current) != _identity(saved)
                            or current.st_size != saved.st_size
                        ):
                            raise StorageError()
                    handles.revalidate(base, private, lock)

                check_names()
                remaining = saved.st_size
                chunks: list[bytes] = []
                while remaining:
                    chunk = os.read(fd, min(remaining, 65536))
                    if not chunk:
                        raise StorageError()
                    chunks.append(chunk)
                    remaining -= len(chunk)
                if os.read(fd, 1):
                    raise StorageError()
                content = codec.decode(binding.artifact, b"".join(chunks))
                if (
                    len(content) != binding.measured_bytes
                    or hashlib.sha256(content).hexdigest() != binding.sha256
                ):
                    raise StorageError()
                current = os.fstat(fd)
                if (saved.st_mtime_ns, saved.st_ctime_ns) != (
                    current.st_mtime_ns,
                    current.st_ctime_ns,
                ):
                    raise StorageError()
                check_names()
                if sync:
                    os.fsync(fd)
                    os.fsync(private)
                    os.fsync(staging)
                    os.fsync(live)
                    check_names()
                if repair and not public:
                    if stage:
                        _rename_no_replace(staging, binding.intent, live, binding.public_name)
                        stage, public = False, True
                    else:
                        os.link(
                            binding.intent,
                            binding.public_name,
                            src_dir_fd=private,
                            dst_dir_fd=live,
                            follow_symlinks=False,
                        )
                        public, links = True, 2
                    os.fsync(live)
                    os.fsync(staging)
                    os.fsync(private)
                    check_names()
                return content
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    except (OSError, ArtifactStageError, ArtifactEnvelopeError):
        raise StorageError() from None


def prepare_verified(
    root: Path,
    codec: ArtifactEnvelopeCodec,
    binding: StoredBinding,
    content: bytes,
) -> None:
    binding.validate()
    if (
        type(content) is not bytes
        or len(content) != binding.measured_bytes
        or hashlib.sha256(content).hexdigest() != binding.sha256
    ):
        raise StorageError("invalid")
    try:
        prepare_artifact(root, codec, binding.artifact, binding.namespace, binding.intent, content)
    except ArtifactStageError:
        raise StorageError() from None
    # Duplicate staging is merely an observation: authenticate and sync again.
    _access(root, codec, binding, sync=True, repair=False, require_stage=True)


def read_verified(root: Path, codec: ArtifactEnvelopeCodec, binding: StoredBinding) -> bytes:
    return _access(root, codec, binding, sync=False, repair=False)


def repair_verified(root: Path, codec: ArtifactEnvelopeCodec, binding: StoredBinding) -> None:
    _access(root, codec, binding, sync=True, repair=True)
