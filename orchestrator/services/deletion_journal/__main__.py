"""Offline NEW-only initializer. No repair, replay, delete or prune commands."""

import sys
from pathlib import Path
from uuid import UUID

from .errors import JournalError
from .store import initialize


def main() -> int:
    try:
        if len(sys.argv) != 4 or sys.argv[1] != "init":
            raise JournalError("invalid_config")
        initialize(Path(sys.argv[2]), UUID(sys.argv[3]))
    except (JournalError, ValueError):
        print("journal_initialization_failed", file=sys.stderr)
        return 1
    print("journal_initialized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
