from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ARTIFACT_MOUNT = "daemon_benchmark_artifacts:/var/lib/daemon/benchmark-artifacts"


def _compose_config() -> dict[str, Any]:
    data = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    return cast(dict[str, Any], data)


def test_backend_has_dedicated_persistent_benchmark_artifact_volume() -> None:
    compose = _compose_config()

    assert "daemon_benchmark_artifacts" in compose["volumes"]
    assert BENCHMARK_ARTIFACT_MOUNT in compose["services"]["backend"]["volumes"]


def test_benchmark_volume_is_independent_of_opencode_scratch_space() -> None:
    compose = _compose_config()
    backend_volumes = compose["services"]["backend"]["volumes"]

    assert all("/tmp/opencode" not in mount for mount in backend_volumes)
