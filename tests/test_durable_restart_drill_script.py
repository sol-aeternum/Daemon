"""Safety preflight of scripts/durable_restart_drill.sh, with a stub ``docker``.

The drill's teardown runs ``docker compose down -v``, which deletes a
project's volumes, so it must only ever tear down a project it created. These
tests run the real script against a stub on PATH (no Docker needed) and check
that it refuses, without any Compose mutation, whenever ownership cannot be
established, and that a failed teardown fails the run.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess  # nosec B404
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "durable_restart_drill.sh"

# The drill runs Compose under ``env -i``, so the stub reads its settings
# from a file beside it rather than from the environment.
STUB = r"""#!/usr/bin/env bash
source "$(dirname "$0")/stub.conf"
echo "$*" >> "$STUB_LOG"
if [[ "$1" == compose ]]; then
  if [[ "$2" == version ]]; then echo 2.30.0; exit 0; fi
  case " $* " in
    *" down "*) [[ "${STUB_DOWN:-ok}" == fail ]] && exit 1; exit 0 ;;
    *" build "*) exit 1 ;;  # never build anything for real
  esac
  exit 0
fi
if [[ "$2" == ls ]]; then
  [[ "${STUB_LIST:-}" == fail ]] && exit 1
  [[ "${STUB_LIST:-}" == "exists-$1" ]] && echo 0123abcd
  exit 0
fi
exit 0
"""

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture
def drill(tmp_path: Path):
    root = tmp_path / "checkout"  # a checkout without .env
    (root / "scripts").mkdir(parents=True)
    script = root / "scripts" / SCRIPT.name
    shutil.copy2(SCRIPT, script)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(STUB)
    stub.chmod(0o755)
    temp = tmp_path / "tmp"
    temp.mkdir()
    log = tmp_path / "docker-calls.log"
    log.touch()

    def run(**extra: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        stub_settings = {k: v for k, v in extra.items() if k.startswith("STUB_")}
        (bin_dir / "stub.conf").write_text(
            f"STUB_LOG='{log}'\n" + "".join(f"{k}='{v}'\n" for k, v in stub_settings.items())
        )
        env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path),
            "TMPDIR": str(temp),
            "DRILL_LOG": str(tmp_path / "drill.log"),
            "DRILL_PROJECT": "drill-under-test",
            **{k: v for k, v in extra.items() if not k.startswith("STUB_")},
        }
        result = subprocess.run(  # nosec B603
            ["bash", str(script)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return result, log.read_text().splitlines()

    return run, temp


def _compose_mutations(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith("compose -p")]


def test_refuses_when_docker_cannot_list_the_project(drill) -> None:
    run, temp = drill
    result, calls = run(STUB_LIST="fail")
    assert result.returncode == 2
    assert "could not list" in result.stderr
    assert _compose_mutations(calls) == []
    assert not (temp / "drill-under-test.drill-lock").exists()


@pytest.mark.parametrize("kind", ["container", "volume", "network"])
def test_refuses_a_project_that_already_exists(drill, kind: str) -> None:
    run, _ = drill
    result, calls = run(STUB_LIST=f"exists-{kind}")
    assert result.returncode == 2
    assert f"already has a {kind}" in result.stderr
    assert _compose_mutations(calls) == []


def test_refuses_a_project_another_run_holds(drill) -> None:
    run, temp = drill
    (temp / "drill-under-test.drill-lock").mkdir()
    result, calls = run()
    assert result.returncode == 2
    assert "another drill holds" in result.stderr
    assert calls == []  # Docker is not even asked


def test_refuses_the_live_project_name(drill) -> None:
    run, _ = drill
    result, calls = run(DRILL_PROJECT="daemon")
    assert result.returncode == 2
    assert calls == []


def test_a_failed_teardown_fails_the_run_with_recovery_steps(drill) -> None:
    run, temp = drill
    result, calls = run(STUB_DOWN="fail")
    assert result.returncode == 1
    assert "teardown failed" in result.stdout
    assert "docker compose -p drill-under-test down -v" in result.stdout
    assert "ALL PASS" not in result.stdout
    recovery = [line for line in result.stdout.splitlines() if line.startswith("recovery:")]
    assert recovery and "--env-file" in recovery[0] and "--project-directory" in recovery[0]
    retained = list(temp.glob("*/drill.env"))
    assert len(retained) == 1
    assert retained[0].stat().st_mode & 0o777 == 0o600
    # Only the project this run reserved was torn down, and it is released.
    assert any(" build " in f" {c} " for c in _compose_mutations(calls))
    downs = [c for c in _compose_mutations(calls) if " down " in f" {c} "]
    assert downs and all("-p drill-under-test " in c for c in downs)
    assert not (temp / "drill-under-test.drill-lock").exists()


@pytest.mark.parametrize("hostile", [False, True])
def test_generated_ownership_key_is_canonical_independent_and_not_inherited(drill, hostile):
    run, temp = drill
    inherited = (
        {
            "DAEMON_REDIS_ACCOUNT_HASH_KEY": "HOSTILE_HASH_KEY",
            "DAEMON_ENCRYPTION_KEY": "HOSTILE_CIPHER_KEY",
            "DAEMON_AUTH_PEPPER": "HOSTILE_AUTH_KEY",
            "OPENROUTER_API_KEY": "HOSTILE_PROVIDER_KEY",
            "MOCK_LLM": "false",
            "DATABASE_URL": "postgresql://hostile.invalid/db",
        }
        if hostile
        else {}
    )
    # Retain the generated fixture via the existing teardown-failure harness;
    # the Docker stub never starts a container or reaches a provider.
    result, _ = run(STUB_DOWN="fail", **inherited)
    assert result.returncode == 1
    files = list(temp.glob("*/drill.env"))
    assert len(files) == 1 and files[0].stat().st_mode & 0o777 == 0o600
    values = dict(line.split("=", 1) for line in files[0].read_text().splitlines())
    key = values["DAEMON_REDIS_ACCOUNT_HASH_KEY"]
    # Keep assertions boolean-only: pytest must not display secret values.
    assert bool(re.fullmatch(r"[A-Za-z0-9_-]{43}", key))
    raw = base64.urlsafe_b64decode(key + "=")
    assert len(raw) == 32
    assert bool(base64.urlsafe_b64encode(raw).decode().rstrip("=") == key)
    assert bool(raw != base64.urlsafe_b64decode(values["DAEMON_ENCRYPTION_KEY"]))
    assert bool(key != values["DAEMON_AUTH_PEPPER"])
    assert bool(values["MOCK_LLM"] == "true")
    assert "DATABASE_URL" not in values
    assert all(
        not value for name, value in values.items() if name.endswith("API_KEY") or name == "FAL_KEY"
    )
    assert all("HOSTILE_" not in value for value in values.values())
    assert all(
        value not in result.stdout + result.stderr
        for value in (key, values["DAEMON_ENCRYPTION_KEY"], values["DAEMON_AUTH_PEPPER"])
    )
