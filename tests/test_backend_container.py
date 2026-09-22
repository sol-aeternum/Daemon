"""Opt-in container regressions: DAEMON_DOCKER_TESTS=1 pytest this file.

Build contexts contain dependency manifests and explicit runtime source trees;
only synthetic source is mounted, never credentials or production services.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.environ.get("DAEMON_DOCKER_TESTS") != "1",
    reason="Opt-in Docker build tests require DAEMON_DOCKER_TESTS=1",
)


@pytest.fixture
def build_context(tmp_path: Path) -> Path:
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(ROOT / name, tmp_path / name)
    shutil.copyfile(ROOT / "backend/Dockerfile", tmp_path / "Dockerfile")
    for name in ("orchestrator", "db", "config", "providers", "scripts", "migrations"):
        shutil.copytree(ROOT / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path


@pytest.mark.parametrize("invalid_lock", ["malformed", "stale"])
def test_backend_rejects_invalid_lock(build_context: Path, invalid_lock: str) -> None:
    """A deployment must not silently resolve new versions when its lock is invalid."""
    if invalid_lock == "malformed":
        (build_context / "uv.lock").write_text("invalid lockfile [\n")
        expected_error = "Failed to parse `uv.lock`"
    else:
        manifest = build_context / "pyproject.toml"
        manifest.write_text(manifest.read_text().replace('version = "0.1.0"', 'version = "0.1.1"'))
        expected_error = "needs to be updated"
    result = subprocess.run(
        ["docker", "build", "--network=host", str(build_context)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode != 0, "Backend build silently ignored invalid uv.lock"
    assert expected_error in result.stdout + result.stderr


def test_locked_runtime_survives_source_mount(build_context: Path, tmp_path: Path) -> None:
    """Compose's /app mount must retain locked dependencies and mounted Python code."""
    image = f"daemon-backend-audit:{uuid.uuid4().hex}"
    try:
        subprocess.run(
            ["docker", "build", "--network=host", "-t", image, str(build_context)],
            check=True,
            capture_output=True,
            timeout=1800,
        )
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network=none",
                "-e",
                "LITELLM_MODE=PRODUCTION",
                "-e",
                "LITELLM_LOCAL_MODEL_COST_MAP=True",
                "-e",
                "DAEMON_ENVIRONMENT=development",
                image,
                "python",
                "-c",
                "import orchestrator.main, orchestrator.worker.worker; "
                "from pathlib import Path; "
                "assert Path('/app/scripts/migrate.py').is_file(); "
                "assert list(Path('/app/migrations').glob('*.sql'))",
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        mounted = tmp_path / "mounted"
        (mounted / "orchestrator").mkdir(parents=True)
        (mounted / "orchestrator/__init__.py").write_text('SOURCE = "mounted"\n')
        probe = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--mount",
                f"type=bind,source={mounted},target=/app,readonly",
                image,
                "python",
                "-c",
                "import json, sys, shutil, orchestrator; "
                "from importlib.metadata import version; "
                "import asyncpg, arq, litellm; "
                "print(json.dumps([sys.prefix, shutil.which('uvicorn'), "
                "orchestrator.SOURCE, version('fastapi')]))",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        prefix, uvicorn, source, fastapi_version = json.loads(probe.stdout)
        assert prefix == "/opt/venv"
        assert uvicorn == "/opt/venv/bin/uvicorn"
        assert source == "mounted"
        lock = tomllib.loads((ROOT / "uv.lock").read_text())
        expected = next(p["version"] for p in lock["package"] if p["name"] == "fastapi")
        assert fastapi_version == expected
    finally:
        subprocess.run(["docker", "image", "rm", "-f", image], capture_output=True, timeout=60)
