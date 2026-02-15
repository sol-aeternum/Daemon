#!/usr/bin/env python3

import os
import subprocess
from datetime import datetime
from pathlib import Path


def main() -> int:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    output_dir = Path(os.getenv("BACKUP_DIR", "backups"))
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"daemon_backup_{timestamp}.dump"

    command = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--file",
        str(output_path),
        database_url,
    ]

    result = subprocess.run(command, check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
