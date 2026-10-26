"""Install/audit policy and full local wrapper contracts, using owned tool stubs.

These tests never install dependencies, query a registry or create a real PR.
They are not evidence of a successful online vulnerability audit.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
MAIN_INSTALL = "npm ci --prefix frontend --no-audit --no-fund --offline"
NESTED_INSTALL = (
    "npm ci --prefix vendor/braces-test-tools --ignore-scripts --offline --no-audit --no-fund"
)
HOSTED_NESTED_INSTALL = "npm ci --prefix vendor/braces-test-tools --ignore-scripts"
LOCAL_BRACES = "npm --prefix frontend run security:braces:offline"
AUDIT_FLAGS = "--offline=false --registry=https://registry.npmjs.org --audit-level=high"
MAIN_AUDIT = f"npm audit {AUDIT_FLAGS}"
NESTED_AUDIT = f"npm audit --prefix vendor/braces-test-tools {AUDIT_FLAGS}"
BRACES_COMMANDS = [
    "node scripts/check-braces-security.cjs",
    "node --test scripts/check-braces-security-negative.cjs",
    "node --test scripts/watch-braces-upstream.test.cjs",
    NESTED_INSTALL,
    NESTED_AUDIT,
    "node vendor/braces-test-tools/node_modules/mocha/bin/mocha.js --no-config --no-package "
    '--require ./scripts/braces-upstream-loader.cjs --reporter dot "vendor/braces/test/*.js"',
]
HOSTED_BRACES_COMMANDS = BRACES_COMMANDS[:3] + [HOSTED_NESTED_INSTALL] + BRACES_COMMANDS[4:]
GATES = [
    ("backend", "ruff-check", "blocking", "uv run ruff check ."),
    ("backend", "ruff-format", "blocking", "uv run ruff format --check ."),
    ("backend", "basedpyright", "blocking", "uv run basedpyright --level error"),
    (
        "backend",
        "bandit-high",
        "blocking",
        "uv run bandit -r orchestrator providers scripts tests -lll",
    ),
    ("backend", "bandit", "inventory", "uv run bandit -r orchestrator providers scripts tests"),
    ("backend", "pip-audit", "blocking", "uv run pip-audit"),
    ("backend", "pytest", "blocking", "python -I -S -B scripts/run_managed_backend_pytest.py -q"),
    ("frontend", "npm-ci", "blocking", MAIN_INSTALL),
    ("frontend", "braces-security", "blocking", LOCAL_BRACES),
    ("frontend", "hmr-protocol", "blocking", "npm --prefix frontend run test:hmr"),
    ("frontend", "type-check", "blocking", "npm --prefix frontend run type-check"),
    ("frontend", "lint", "blocking", "npm --prefix frontend run lint"),
    ("frontend", "format-check", "blocking", "npm --prefix frontend run format:check"),
    ("frontend", "audit-ci", "blocking", "npm --prefix frontend run audit:ci"),
    ("frontend", "cache-fixture-python", "blocking", "uv sync --locked"),
    ("frontend", "test-run", "blocking", "npm --prefix frontend run test:run"),
    (
        "frontend",
        "cache-fixture-ownership",
        "blocking",
        "uv run pytest -q frontend/e2e/cache_fixture_ownership.py",
    ),
    (
        "frontend",
        "http-cache-browser",
        "blocking",
        "npm --prefix frontend run test:browser:cache",
    ),
    ("frontend", "hmr-browser", "blocking", "npm --prefix frontend run test:hmr:browser"),
    ("frontend", "build", "blocking", "npm --prefix frontend run build"),
    ("aggregate", "feature-matrix", "blocking", "python scripts/lint_feature_matrix.py"),
    ("aggregate", "pre-commit", "blocking", "uv run pre-commit run --all-files"),
]


def test_each_install_and_audit_has_exact_separate_policy() -> None:
    scripts = json.loads((ROOT / "frontend/package.json").read_text())["scripts"]
    assert scripts["audit:ci"] == MAIN_AUDIT
    assert scripts["security:braces:offline"].split(" && ") == BRACES_COMMANDS
    assert scripts["security:braces"].split(" && ") == HOSTED_BRACES_COMMANDS
    # Exact argv equality also excludes fix, ignore/approval overrides on the main
    # install, extra registries, weaker severity and changed integrity/Mocha steps.
    for audit in (MAIN_AUDIT, NESTED_AUDIT):
        args = shlex.split(audit)
        assert args[:2] == ["npm", "audit"]
        assert "fix" not in args
        assert args[-3:] == shlex.split(AUDIT_FLAGS)


def test_only_nested_install_differs_between_local_and_hosted_chains() -> None:
    scripts = json.loads((ROOT / "frontend/package.json").read_text())["scripts"]
    hosted = scripts["security:braces"].split(" && ")
    local = scripts["security:braces:offline"].split(" && ")
    assert len(hosted) == len(local) == 6
    assert [index for index, pair in enumerate(zip(hosted, local)) if pair[0] != pair[1]] == [3]
    assert hosted[3] == HOSTED_NESTED_INSTALL
    assert local[3] == hosted[3] + " --offline --no-audit --no-fund"


def test_hosted_cold_main_cache_does_not_force_nested_offline_install() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["frontend"]["steps"]
    setup = next(step for step in steps if step["name"] == "Set up Node.js")
    install = next(step for step in steps if step["name"] == "Install frontend dependencies")
    braces = next(
        step
        for step in steps
        if step["name"] == "Vendored braces integrity and security regressions"
    )
    assert setup["with"]["cache-dependency-path"] == "frontend/package-lock.json"
    assert install["run"] == "npm ci"
    assert braces["run"] == "npm run security:braces"
    assert steps.index(setup) < steps.index(install) < steps.index(braces)
    main = json.loads((ROOT / "frontend/package-lock.json").read_text())["packages"]
    nested = json.loads((ROOT / "frontend/vendor/braces-test-tools/package-lock.json").read_text())[
        "packages"
    ]

    def identities(packages):
        return {
            (package["resolved"], package["integrity"])
            for path, package in packages.items()
            if path
        }

    assert len(nested) - 1 == 29
    assert len(identities(nested) - identities(main)) == 14
    scripts = json.loads((ROOT / "frontend/package.json").read_text())["scripts"]
    assert scripts["security:braces"].split(" && ")[3] == HOSTED_NESTED_INSTALL
    assert scripts["security:braces:offline"].split(" && ")[3] == NESTED_INSTALL


def test_full_gate_table_retains_order_count_and_classification() -> None:
    source = (ROOT / "scripts/local_ci.sh").read_text()
    table = source.split("GATE_TABLE <<'EOF'", 1)[1].split("\n", 1)[1].split("\nEOF", 1)[0]
    actual = [tuple(line.split("|", 3)) for line in table.splitlines()]
    assert actual == GATES


def owned_checkout(tmp_path: Path, fail_command: str = "") -> tuple[Path, dict[str, str], Path]:
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    (root / "frontend").mkdir()
    for name in ("local_ci.sh", "pr_create.sh"):
        shutil.copyfile(ROOT / "scripts" / name, root / "scripts" / name)
        (root / "scripts" / name).chmod(0o700)
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "commands"
    for name in ("uv", "npm", "npx", "python", "node", "gh"):
        tool = tools / name
        tool.write_text(
            '#!/bin/sh\ncommand="$(basename "$0") $*"\n'
            'printf \'%s\\n\' "$command" >> "$COMMAND_LOG"\n'
            'if [ "$command" = "$FAIL_COMMAND" ]; then exit 41; fi\n'
        )
        tool.chmod(0o700)
    return (
        root,
        {
            "PATH": f"{tools}:{os.defpath}",
            "COMMAND_LOG": str(log),
            "FAIL_COMMAND": fail_command,
        },
        log,
    )


def expected_gate_commands() -> list[str]:
    return [
        command.removeprefix("PYTHONPATH=. ").removeprefix("(cd frontend && ").removesuffix(")")
        for _, _, _, command in GATES
    ]


@pytest.mark.parametrize(
    ("failure", "expected_exit", "summary"),
    [
        ("", 0, "blocking failures:    0"),
        (MAIN_INSTALL, 1, "frontend/npm-ci (exit=41)"),
        ("npm --prefix frontend run audit:ci", 1, "frontend/audit-ci (exit=41)"),
        (LOCAL_BRACES, 1, "frontend/braces-security (exit=41)"),
        (
            "uv run bandit -r orchestrator providers scripts tests",
            0,
            "backend/bandit (exit=41)",
        ),
    ],
)
def test_noarg_runner_runs_every_gate_and_propagates_failures(
    tmp_path: Path, failure: str, expected_exit: int, summary: str
) -> None:
    root, env, log = owned_checkout(tmp_path, failure)
    result = subprocess.run(
        ["bash", str(root / "scripts/local_ci.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == expected_exit, result.stdout + result.stderr
    assert log.read_text().splitlines() == expected_gate_commands()
    assert summary in result.stdout


@pytest.mark.parametrize(
    "failure", ["", MAIN_INSTALL, "npm --prefix frontend run audit:ci", LOCAL_BRACES]
)
def test_pr_wrapper_uses_full_noarg_runner_before_forwarding_only_gh_args(
    tmp_path: Path, failure: str
) -> None:
    root, env, log = owned_checkout(tmp_path, failure)
    result = subprocess.run(
        ["bash", str(root / "scripts/pr_create.sh"), "--", "--title", "synthetic", "--draft"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == (1 if failure else 0), result.stdout + result.stderr
    assert log.read_text().splitlines() == expected_gate_commands() + (
        [] if failure else ["gh pr create --title synthetic --draft"]
    )
    if failure:
        assert "refusing to call `gh pr create`" in result.stdout


@pytest.mark.parametrize("script_name", ["security:braces", "security:braces:offline"])
@pytest.mark.parametrize("failed_step", range(6))
def test_braces_chain_stops_at_each_failed_install_audit_or_regression(
    tmp_path: Path, failed_step: int, script_name: str
) -> None:
    commands = BRACES_COMMANDS if script_name.endswith(":offline") else HOSTED_BRACES_COMMANDS
    expected = [" ".join(shlex.split(command)) for command in commands]
    root, env, log = owned_checkout(tmp_path, expected[failed_step])
    script = json.loads((ROOT / "frontend/package.json").read_text())["scripts"][script_name]
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=root / "frontend",
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 41, result.stdout + result.stderr
    assert log.read_text().splitlines() == expected[: failed_step + 1]


@pytest.mark.parametrize("script_name", ["security:braces", "security:braces:offline"])
def test_both_braces_chains_run_every_step_on_success(tmp_path: Path, script_name: str) -> None:
    commands = BRACES_COMMANDS if script_name.endswith(":offline") else HOSTED_BRACES_COMMANDS
    root, env, log = owned_checkout(tmp_path)
    script = json.loads((ROOT / "frontend/package.json").read_text())["scripts"][script_name]
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=root / "frontend",
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert log.read_text().splitlines() == [" ".join(shlex.split(command)) for command in commands]
