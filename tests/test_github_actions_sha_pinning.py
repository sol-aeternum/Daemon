from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


WORKFLOWS_DIR = Path(".github/workflows")
DEPENDABOT_CONFIG = Path(".github/dependabot.yml")
SHA_REF = re.compile(r"^[0-9a-f]{40}$")


def _walk_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        values: list[str] = []
        for key, nested in value.items():
            if key == "uses" and isinstance(nested, str):
                values.append(nested)
            values.extend(_walk_values(nested))
        return values
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(_walk_values(item))
        return values
    return []


def test_github_action_references_are_pinned_to_shas() -> None:
    violations: list[str] = []
    for workflow_path in sorted(WORKFLOWS_DIR.glob("*.y*ml")):
        workflow = yaml.safe_load(workflow_path.read_text()) or {}
        for action_ref in _walk_values(workflow):
            if action_ref.startswith(("./", "docker://")):
                continue
            if "@" not in action_ref:
                violations.append(f"{workflow_path}: {action_ref} has no ref")
                continue
            _, ref = action_ref.rsplit("@", 1)
            if not SHA_REF.fullmatch(ref):
                violations.append(f"{workflow_path}: {action_ref} is not SHA-pinned")

    assert not violations, "\n".join(violations)


def test_dependabot_tracks_github_actions_updates() -> None:
    config = yaml.safe_load(DEPENDABOT_CONFIG.read_text()) or {}
    updates = config.get("updates", [])

    assert {
        "package-ecosystem": "github-actions",
        "directory": "/",
        "schedule": {"interval": "weekly"},
    } in updates
