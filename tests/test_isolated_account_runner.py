"""Offline containment tests; never invokes Docker or connects to PostgreSQL."""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import run_isolated_account_tests as runner


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "approval", "port", "create", "identity"])
async def test_disposable_database_ownership_guard_and_cleanup(monkeypatch, failure):
    inspection = [
        {
            "Name": "/daemon-postgres-1",
            "NetworkSettings": {
                "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5432"}]}
            },
            "Config": {
                "Env": ["POSTGRES_USER=fictional", "POSTGRES_PASSWORD=synthetic-not-a-secret"]
            },
        }
    ]
    if failure == "port":
        inspection[0]["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostIp"] = "0.0.0.0"
    calls = []
    database = []

    def inspect(command, **kwargs):
        assert command == ["docker", "inspect", "daemon-postgres-1"]
        return json.dumps(inspection)

    def execute(command, **kwargs):
        calls.append(command)
        if command[0] == "docker":
            assert command[1] == "exec" and command[4] == "daemon-postgres-1"
            name = command[3].split("=", 1)[1]
            assert name.startswith(runner.PREFIX) and len(name) == len(runner.PREFIX) + 32
            if "createdb" in command[-1]:
                if failure == "create":
                    raise subprocess.CalledProcessError(1, command)
                database.append(name)
            else:
                assert "dropdb" in command[-1] and database == [name]
        else:
            assert command == ["uv", "run", "--locked", "pytest", "-q", *runner.TESTS]
            assert kwargs["env"]["ENTITLEMENTS_TEST_DATABASE_URL"].endswith("/" + database[0])
            assert "synthetic-not-a-secret" not in " ".join(command)
        return SimpleNamespace(returncode=0)

    async def connect(**kwargs):
        assert kwargs["database"] == database[0] and kwargs["host"] == "127.0.0.1"
        connection = AsyncMock()
        connection.fetchval.side_effect = ["wrong" if failure == "identity" else database[0], 0]
        return connection

    monkeypatch.setattr(runner.subprocess, "check_output", inspect)
    monkeypatch.setattr(runner.subprocess, "run", execute)
    monkeypatch.setattr(runner.asyncpg, "connect", connect)
    if failure:
        with pytest.raises((ValueError, subprocess.CalledProcessError)):
            await runner.run(failure != "approval")
        if failure in ("approval", "port"):
            assert calls == []
        elif failure == "create":
            assert len(calls) == 1  # Failed creation never authorizes a drop.
        else:
            assert len(calls) == 2  # Own DB cleaned, no tests run after mismatch.
    else:
        assert await runner.run(True) == 0
        assert len(calls) == 3
