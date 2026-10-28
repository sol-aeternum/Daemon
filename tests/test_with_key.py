"""Key-scoped .env injection for commands run from worktrees."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import with_key

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/with_key.py"
SECRET = "sk-fixture-not-a-real-key"


def write_env(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def run_script(
    script: Path, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(script), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain", "plain"),
        ("  padded  ", "padded"),
        ("value # trailing comment", "value"),
        ("value#kept", "value#kept"),
        ("", ""),
        ("'single $literal \\n'", "single $literal \\n"),
        ("'quoted' # comment", "quoted"),
        ('"double \\"escaped\\" \\n line"', 'double "escaped" \n line'),
        ('"keeps # hash"', "keeps # hash"),
        ('"unknown \\q escape"', "unknown \\q escape"),
        ("'unterminated", None),
        ('"unterminated', None),
    ],
)
def test_parse_value(raw: str, expected: str | None) -> None:
    assert with_key.parse_value(raw) == expected


def test_read_keys_returns_only_requested_keys(tmp_path: Path) -> None:
    env_file = write_env(
        tmp_path / ".env",
        "# OPENROUTER_API_KEY=commented-out\n"
        "\n"
        "OPENROUTER_API_KEY=first\n"
        "export VOYAGE_API_KEY = 'voyage'\n"
        "OTHER_SECRET=unrelated\n"
        "OPENROUTER_API_KEY=second\n",
    )

    assert with_key.read_keys(env_file, ["OPENROUTER_API_KEY", "VOYAGE_API_KEY", "ABSENT"]) == {
        "OPENROUTER_API_KEY": "second",
        "VOYAGE_API_KEY": "voyage",
    }


def test_unterminated_quote_names_the_line_not_the_value(tmp_path: Path) -> None:
    env_file = write_env(tmp_path / ".env", f'A=1\nOPENROUTER_API_KEY="{SECRET}\n')

    with pytest.raises(with_key.WithKeyError) as raised:
        with_key.read_keys(env_file, ["OPENROUTER_API_KEY"])

    assert f"{env_file}:2: OPENROUTER_API_KEY" in str(raised.value)
    assert SECRET not in str(raised.value)


def test_unterminated_quote_on_an_unrequested_key_is_ignored(tmp_path: Path) -> None:
    env_file = write_env(tmp_path / ".env", 'OTHER="broken\nWANTED=ok\n')

    assert with_key.read_keys(env_file, ["WANTED"]) == {"WANTED": "ok"}


def test_calling_environment_wins_and_skips_the_file(tmp_path: Path) -> None:
    base = {"OPENROUTER_API_KEY": "from-shell", "PATH": "/bin"}

    resolved = with_key.resolve_environment(
        ["OPENROUTER_API_KEY"], tmp_path / "does-not-exist", base
    )

    assert resolved == base


def test_file_fills_keys_that_are_missing_or_empty(tmp_path: Path) -> None:
    env_file = write_env(tmp_path / ".env", "A=file-a\nB=file-b\nC=file-c\n")

    resolved = with_key.resolve_environment(
        ["A", "B", "C"], env_file, {"A": "shell-a", "B": "", "PATH": "/bin"}
    )

    assert resolved == {"A": "shell-a", "B": "file-b", "C": "file-c", "PATH": "/bin"}


def test_missing_and_empty_keys_are_reported_by_name(tmp_path: Path) -> None:
    env_file = write_env(tmp_path / ".env", f"PRESENT={SECRET}\nEMPTY=\n")

    with pytest.raises(with_key.WithKeyError, match=r"EMPTY, ABSENT not set in ") as raised:
        with_key.resolve_environment(["PRESENT", "EMPTY", "ABSENT"], env_file, {})

    assert SECRET not in str(raised.value)


def test_missing_env_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(with_key.WithKeyError, match="not found; pass --env-file"):
        with_key.resolve_environment(["A"], tmp_path / ".env", {})


def clean_environment(**extra: str) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"OPENROUTER_API_KEY", "VOYAGE_API_KEY"} and not key.startswith("GIT_")
    }
    return {**environment, **extra}


def test_script_injects_the_key_into_the_command(tmp_path: Path) -> None:
    env_file = write_env(tmp_path / ".env", f"OPENROUTER_API_KEY={SECRET}\nOTHER=nope\n")

    result = run_script(
        SCRIPT,
        "--env-file",
        str(env_file),
        "OPENROUTER_API_KEY",
        "--",
        sys.executable,
        "-c",
        "import os; print(os.environ['OPENROUTER_API_KEY'], 'OTHER' in os.environ)",
        env=clean_environment(),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{SECRET} False\n"


def test_script_failures_exit_nonzero_without_printing_values(tmp_path: Path) -> None:
    env_file = write_env(tmp_path / ".env", f"OPENROUTER_API_KEY={SECRET}\n")

    missing = run_script(
        SCRIPT, "--env-file", str(env_file), "VOYAGE_API_KEY", "--", "true", env=clean_environment()
    )
    unknown_command = run_script(
        SCRIPT,
        "--env-file",
        str(env_file),
        "OPENROUTER_API_KEY",
        "--",
        str(tmp_path / "no-such-command"),
        env=clean_environment(),
    )

    assert missing.returncode == 2
    assert "VOYAGE_API_KEY not set in" in missing.stderr
    assert unknown_command.returncode == 127
    assert "cannot run" in unknown_command.stderr
    for result in (missing, unknown_command):
        assert SECRET not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["OPENROUTER_API_KEY", "true"],
        ["OPENROUTER_API_KEY", "--"],
        ["--", "true"],
        ["not-a-key", "--", "true"],
    ],
)
def test_script_rejects_malformed_invocations(args: list[str]) -> None:
    result = run_script(SCRIPT, *args, env=clean_environment())

    assert result.returncode == 2
    assert "usage: with_key.py" in result.stderr


DECOY = "sk-decoy-from-another-repository"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_repository_selectors_cover_gits_own_list() -> None:
    listed = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"],
        capture_output=True,
        text=True,
        env=clean_environment(),
        check=True,
    ).stdout.split()

    assert listed
    assert set(listed) <= with_key.GIT_REPOSITORY_SELECTORS


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
@pytest.mark.parametrize(
    "inherited",
    [
        [],
        ["GIT_DIR"],
        ["GIT_COMMON_DIR"],
        ["GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"],
    ],
    ids=lambda names: "+".join(names) or "clean",
)
def test_worktree_reads_the_main_checkout_env(tmp_path: Path, inherited: list[str]) -> None:
    git_env = clean_environment(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

    def git(*args: str, cwd: Path) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
            cwd=cwd,
            env=git_env,
            check=True,
            capture_output=True,
        )

    main_checkout = tmp_path / "main"
    main_checkout.mkdir()
    git("init", "-q", cwd=main_checkout)
    git("commit", "-q", "--allow-empty", "-m", "init", cwd=main_checkout)
    worktree = tmp_path / "worktree"
    git("worktree", "add", "-q", str(worktree), cwd=main_checkout)
    write_env(main_checkout / ".env", f"OPENROUTER_API_KEY='{SECRET}'\n")
    script = worktree / "scripts/with_key.py"
    script.parent.mkdir()
    shutil.copyfile(SCRIPT, script)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    # An unrelated repository selected by the calling shell, as in a hook.
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    git("init", "-q", cwd=decoy)
    write_env(decoy / ".env", f"OPENROUTER_API_KEY={DECOY}\n")
    selectors = {
        "GIT_DIR": decoy / ".git",
        "GIT_COMMON_DIR": decoy / ".git",
        "GIT_WORK_TREE": decoy,
    }
    caller_env = {**git_env, **{name: str(selectors[name]) for name in inherited}}

    # Run the copy directly, from an unrelated cwd, to cover the shebang and
    # resolution from the script's own location.
    result = subprocess.run(
        [
            str(script),
            "OPENROUTER_API_KEY",
            "--",
            "sh",
            "-c",
            'printf "%s|%s\\n" "$OPENROUTER_API_KEY" "${GIT_DIR-}"',
        ],
        capture_output=True,
        text=True,
        env=caller_env,
        cwd=tmp_path,
        check=False,
    )

    assert not (worktree / ".env").exists()
    assert result.returncode == 0, result.stderr
    # The key comes from the script's own repository; the command still sees
    # the caller's environment unchanged.
    assert result.stdout == f"{SECRET}|{caller_env.get('GIT_DIR', '')}\n"
    assert DECOY not in result.stdout + result.stderr
