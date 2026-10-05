"""Owner-approved disposable PostgreSQL accounting tests; no production rows.

Creates a uniquely named DB on the existing local service, runs synthetic tests,
and drops only the database it successfully created. No provider calls are made.
Credentials are read in memory from the existing container and never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
CONTAINER = "daemon-postgres-1"
PREFIX = "daemon_embedding_test_"
TESTS = (
    "tests/test_entitlements_service.py",
    "tests/test_account_compute_postgres.py",
    "tests/test_embedding_account_postgres.py",
)


async def run(approved: bool) -> int:
    if not approved:
        raise ValueError("Requires --approved-isolated-db")
    inspected = json.loads(subprocess.check_output(["docker", "inspect", CONTAINER], text=True))
    if len(inspected) != 1 or inspected[0]["Name"] != "/" + CONTAINER:
        raise ValueError("Unexpected PostgreSQL service identity")
    info = inspected[0]
    ports = info["NetworkSettings"]["Ports"].get("5432/tcp", [])
    if not any(port["HostIp"] == "127.0.0.1" and port["HostPort"] == "5432" for port in ports):
        raise ValueError("Expected loopback-only PostgreSQL endpoint unavailable")
    variables = dict(item.split("=", 1) for item in info["Config"]["Env"] if "=" in item)
    user, password = variables.get("POSTGRES_USER"), variables.get("POSTGRES_PASSWORD")
    if not user or not password:
        raise ValueError("Existing test transport credentials unavailable")
    database = PREFIX + uuid.uuid4().hex
    if not re.fullmatch(PREFIX + r"[a-f0-9]{32}", database):
        raise ValueError("Invalid disposable database identity")
    # No production DB connection/query: container's createdb connects to the
    # maintenance DB. A failed creation never grants authority to drop anything.
    created = False
    try:
        subprocess.run(
            [
                "docker",
                "exec",
                "-e",
                f"TEST_DATABASE={database}",
                CONTAINER,
                "sh",
                "-c",
                'exec createdb -U "$POSTGRES_USER" -- "$TEST_DATABASE"',
            ],
            check=True,
        )
        created = True
        connection = await asyncpg.connect(
            host="127.0.0.1", port=5432, user=user, password=password, database=database
        )
        try:
            if await connection.fetchval("SELECT current_database()") != database:
                raise ValueError("Disposable database identity mismatch")
            if (
                await connection.fetchval(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
                )
                != 0
            ):
                raise ValueError("Disposable database is not empty")
        finally:
            await connection.close()
        from urllib.parse import quote

        env = dict(os.environ)
        env["ENTITLEMENTS_TEST_DATABASE_URL"] = (
            f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@127.0.0.1:5432/{database}"
        )
        result = subprocess.run(
            ["uv", "run", "--locked", "pytest", "-q", *TESTS],
            cwd=ROOT,
            env={**env, "PYTHONPATH": "."},
        )
        return result.returncode
    finally:
        if created:
            subprocess.run(
                [
                    "docker",
                    "exec",
                    "-e",
                    f"TEST_DATABASE={database}",
                    CONTAINER,
                    "sh",
                    "-c",
                    'exec dropdb -U "$POSTGRES_USER" -- "$TEST_DATABASE"',
                ],
                check=True,
            )
            print("Removed only the owned disposable accounting database.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-isolated-db", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.approved_isolated_db)))
