from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_SENTINEL = "DAEMON_PARENT_DOTENV_SENTINEL"


def test_pytest_does_not_load_parent_checkout_dotenv(tmp_path: Path) -> None:
    parent_checkout = tmp_path / "parent-checkout"
    nested_worktree = parent_checkout / ".worktrees" / "test-run"
    nested_worktree.mkdir(parents=True)
    (parent_checkout / ".env").write_text(f"{_SENTINEL}=leaked\n", encoding="utf-8")

    child_env = os.environ.copy()
    child_env.pop(_SENTINEL, None)
    child_env["LITELLM_MODE"] = "DEV"
    child_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    child_env["DAEMON_CONFTEST_PATH"] = str(Path(__file__).with_name("conftest.py"))

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, runpy; "
                "runpy.run_path(os.environ['DAEMON_CONFTEST_PATH']); "
                "import litellm; "
                f"assert os.environ.get('{_SENTINEL}') is None"
            ),
        ],
        cwd=nested_worktree,
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
