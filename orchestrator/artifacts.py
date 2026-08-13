"""Filesystem ownership helpers for generated artifacts."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import uuid


class ArtifactOwnerError(ValueError):
    """Raised when an artifact path cannot be safely scoped to a user."""


def artifact_owner_namespace(user_id: uuid.UUID | str | None) -> str:
    """Return the opaque, deterministic filesystem namespace for a user."""
    try:
        owner_id = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ArtifactOwnerError("authenticated artifact owner required") from exc

    return hashlib.sha256(owner_id.bytes).hexdigest()


def is_artifact_owner_namespace(value: str) -> bool:
    """Return whether a directory name has the canonical owner-namespace shape."""
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def user_artifact_directory(
    base_dir: Path,
    user_id: uuid.UUID | str | None,
    *,
    create: bool,
) -> Path:
    """Resolve a user's artifact directory without accepting a caller-supplied path."""
    namespace = artifact_owner_namespace(user_id)

    try:
        if create:
            base_dir.mkdir(parents=True, exist_ok=True)
        base_resolved = base_dir.resolve(strict=True)
        owner_dir = base_resolved / namespace
        if create:
            owner_dir.mkdir(mode=0o700, exist_ok=True)
        if owner_dir.is_symlink():
            raise ArtifactOwnerError("artifact owner namespace is unsafe")
        owner_resolved = owner_dir.resolve(strict=True)
        owner_resolved.relative_to(base_resolved)
        if not owner_resolved.is_dir():
            raise ArtifactOwnerError("artifact owner namespace is unavailable")
    except ArtifactOwnerError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise ArtifactOwnerError("artifact owner namespace is unavailable") from exc

    return owner_resolved


def resolve_owned_artifact(
    base_dir: Path,
    user_id: uuid.UUID | str | None,
    filename: str,
) -> Path | None:
    """Resolve a regular file only inside the authenticated user's namespace."""
    if not filename or filename != os.path.basename(filename):
        return None

    try:
        owner_dir = user_artifact_directory(base_dir, user_id, create=False)
        owner_root = os.fspath(owner_dir)
        owner_prefix = f"{owner_root}{os.sep}"
        candidate_value = os.path.normpath(os.path.join(owner_root, filename))
        if not candidate_value.startswith(owner_prefix):
            return None
        if os.path.dirname(candidate_value) != owner_root:
            return None

        candidate = Path(candidate_value)
        if candidate.is_symlink():
            return None
        candidate_resolved_value = os.path.realpath(candidate_value)
        if not candidate_resolved_value.startswith(owner_prefix):
            return None
        candidate_resolved = Path(candidate_resolved_value)
        return candidate_resolved if candidate_resolved.is_file() else None
    except (ArtifactOwnerError, OSError, RuntimeError, ValueError):
        return None


def write_owned_artifact(
    base_dir: Path,
    user_id: uuid.UUID | str | None,
    filename: str,
    content: bytes,
) -> Path:
    """Atomically write a regular file inside the authenticated namespace."""
    if not filename or filename != os.path.basename(filename):
        raise ArtifactOwnerError("artifact filename is unsafe")

    owner_dir = user_artifact_directory(base_dir, user_id, create=True)
    owner_root = os.fspath(owner_dir)
    owner_prefix = f"{owner_root}{os.sep}"
    destination_value = os.path.normpath(os.path.join(owner_root, filename))
    if not destination_value.startswith(owner_prefix):
        raise ArtifactOwnerError("artifact filename is unsafe")
    if os.path.dirname(destination_value) != owner_root:
        raise ArtifactOwnerError("artifact filename is unsafe")

    safe_name = os.path.basename(destination_value)
    temporary_name = f".{safe_name}.{uuid.uuid4().hex}.tmp"
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW

    directory_descriptor: int | None = None
    temporary_descriptor: int | None = None
    try:
        directory_descriptor = os.open(owner_dir, directory_flags)
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        temporary_descriptor = os.open(
            temporary_name,
            file_flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        with os.fdopen(temporary_descriptor, "wb") as output:
            temporary_descriptor = None
            output.write(content)
        os.replace(
            temporary_name,
            safe_name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise ArtifactOwnerError("artifact write failed") from exc
    finally:
        if temporary_descriptor is not None:
            try:
                os.close(temporary_descriptor)
            except OSError:
                pass
        if directory_descriptor is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except OSError:
                pass
            finally:
                try:
                    os.close(directory_descriptor)
                except OSError:
                    pass

    written = resolve_owned_artifact(base_dir, user_id, safe_name)
    if written is None:
        raise ArtifactOwnerError("artifact write could not be verified")
    return written
