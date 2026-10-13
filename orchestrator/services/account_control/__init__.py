"""Uncalled account-control prerequisites; no request fence or runtime applier."""

from .guard import (
    CapturedGeneration,
    ContentWriteScope,
    WriteGuardRefusal,
    capture_generation,
    guard_content_write,
)

__all__ = [
    "CapturedGeneration",
    "ContentWriteScope",
    "WriteGuardRefusal",
    "capture_generation",
    "guard_content_write",
]
