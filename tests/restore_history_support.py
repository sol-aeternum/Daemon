"""Synthetic H1 records and initializer; never imported by application code."""

from pathlib import Path
from uuid import UUID
from typing import Literal
import os

from orchestrator.services.restore_history.model import (
    AcceptPrepare,
    Command,
    Cut,
    Identity,
    RestoreAssessed,
    RestoreBegin,
    SendPossible,
    StopIntent,
    canonical,
    key_check,
)

KEY = bytes(range(32))
HISTORY = UUID(int=100)
ZERO = bytes(32)


def accept():
    return Command(
        UUID(int=1),
        AcceptPrepare(
            UUID(int=10),
            0,
            UUID(int=20),
            UUID(int=21),
            UUID(int=22),
            UUID(int=23),
            1,
            1,
            "a" * 64,
            "b" * 64,
            "chat-canonical-3715-v1",
        ),
    )


def send():
    return Command(
        UUID(int=2),
        SendPossible(
            UUID(int=10),
            0,
            UUID(int=20),
            1,
            UUID(int=30),
            UUID(int=31),
            UUID(int=32),
            UUID(int=33),
            UUID(int=34),
            UUID(int=35),
            UUID(int=36),
            "completion",
        ),
    )


def stop():
    return Command(UUID(int=3), StopIntent(UUID(int=10), 0, UUID(int=20), "owner"))


def begin(seq=0, digest=ZERO):
    return Command(
        UUID(int=4),
        RestoreBegin(
            UUID(int=40),
            "a" * 64,
            "b" * 64,
            "c" * 64,
            "d" * 40,
            Cut(seq, digest.hex()),
            HISTORY,
            Cut(0, ZERO.hex()),
        ),
    )


def assessed(
    seq,
    digest,
    disposition: Literal["remain_blocked", "qualified_to_recheck"] = "qualified_to_recheck",
):
    return Command(
        UUID(int=5),
        RestoreAssessed(
            UUID(int=40),
            UUID(int=4),
            Cut(seq, digest.hex()),
            "e" * 64,
            disposition,
        ),
    )


def initialize_test_directory(path: Path):
    """Only test code can create the store; existing directories are refused."""
    path.mkdir(mode=0o2770)
    path.chmod(0o2770)
    identity = Identity(HISTORY, key_check(KEY, HISTORY))
    content = {
        "IDENTITY": canonical(identity.wire()),
        "HEAD": canonical({"v": 1, "history_id": str(HISTORY), "seq": 0, "digest": ZERO.hex()}),
        "LOG": b"",
        "OP_LOCK": b"",
        "APPEND_LOCK": b"",
        "RUNTIME_LOCK": b"",
    }
    for name, data in content.items():
        fd = os.open(path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o660)
        try:
            os.write(fd, data)
            os.fchmod(fd, 0o440 if name == "IDENTITY" else 0o660)
            os.fsync(fd)
        finally:
            os.close(fd)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return identity
