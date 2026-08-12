from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
DEPENDABOT_CONFIG = REPO_ROOT / ".github" / "dependabot.yml"
RENOVATE_CONFIG = REPO_ROOT / "renovate.json"
PINNED_REMOTE_ACTION = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _collect_uses(value: Any) -> list[str]:
    if isinstance(value, dict):
        uses: list[str] = []
        for key, child in value.items():
            if key == "uses" and isinstance(child, str):
                uses.append(child)
            else:
                uses.extend(_collect_uses(child))
        return uses
    if isinstance(value, list):
        return [uses for child in value for uses in _collect_uses(child)]
    return []


def test_all_remote_github_actions_are_pinned_by_commit() -> None:
    invalid: dict[str, list[str]] = {}
    workflow_paths = sorted((*WORKFLOWS_DIR.glob("*.yml"), *WORKFLOWS_DIR.glob("*.yaml")))
    for workflow_path in workflow_paths:
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        remote_actions = [
            uses for uses in _collect_uses(workflow) if not uses.startswith(("./", "docker://"))
        ]
        mutable_actions = [
            uses for uses in remote_actions if PINNED_REMOTE_ACTION.fullmatch(uses) is None
        ]
        if mutable_actions:
            invalid[workflow_path.name] = mutable_actions

    assert not invalid, f"GitHub Actions must use full commit SHAs: {invalid}"


def test_dependabot_tracks_github_action_updates() -> None:
    config = yaml.safe_load(DEPENDABOT_CONFIG.read_text(encoding="utf-8"))
    assert config["version"] == 2
    github_actions_updates = [
        update
        for update in config["updates"]
        if update.get("package-ecosystem") == "github-actions"
    ]
    assert github_actions_updates == [
        {
            "package-ecosystem": "github-actions",
            "directory": "/",
            "schedule": {"interval": "weekly"},
        }
    ]

    renovate = json.loads(RENOVATE_CONFIG.read_text(encoding="utf-8"))
    github_actions_rules = [
        rule for rule in renovate["packageRules"] if rule.get("matchManagers") == ["github-actions"]
    ]
    assert github_actions_rules == [{"matchManagers": ["github-actions"], "enabled": False}]
