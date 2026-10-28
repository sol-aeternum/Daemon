#!/usr/bin/env -S python3 -I
"""Run a command with named keys taken from the main checkout's .env.

Worktrees have no .env of their own. This finds the main checkout through git's
common directory, reads only the keys named on the command line, and execs the
command with them added to its environment. Values are never printed.

    scripts/with_key.py OPENROUTER_API_KEY VOYAGE_API_KEY -- <command> [args...]

A key already set (non-empty) in the calling environment is kept as is.
Standard library only, so it runs without a synced virtualenv.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

KEY_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ASSIGNMENT = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)")
INLINE_COMMENT = re.compile(r"\s+#")
DOUBLE_QUOTED_ESCAPES = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}


class WithKeyError(Exception):
    """A key could not be resolved. Messages name keys and paths, never values."""


def parse_value(raw: str) -> str | None:
    """Decode one dotenv value; None means an unterminated (multi-line) quote."""
    raw = raw.strip()
    if raw.startswith("'"):
        end = raw.find("'", 1)
        return None if end < 0 else raw[1:end]
    if raw.startswith('"'):
        chars: list[str] = []
        index = 1
        while index < len(raw):
            char = raw[index]
            if char == '"':
                return "".join(chars)
            if char == "\\" and index + 1 < len(raw):
                following = raw[index + 1]
                chars.append(DOUBLE_QUOTED_ESCAPES.get(following, char + following))
                index += 2
                continue
            chars.append(char)
            index += 1
        return None
    return INLINE_COMMENT.split(raw, maxsplit=1)[0]


def read_keys(env_file: Path, keys: Iterable[str]) -> dict[str, str]:
    """Return the requested keys found in env_file; later assignments win."""
    wanted = set(keys)
    found: dict[str, str] = {}
    lines = env_file.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, start=1):
        match = ASSIGNMENT.fullmatch(line)
        if match is None or match[1] not in wanted:
            continue
        value = parse_value(match[2])
        if value is None:
            raise WithKeyError(
                f"{env_file}:{number}: {match[1]} has an unterminated quote "
                "(multi-line values are not supported)"
            )
        found[match[1]] = value
    return found


def default_env_file() -> Path:
    """The .env beside the main checkout's .git, from any worktree of the repo."""
    try:
        common_dir = subprocess.run(
            [
                "git",
                "-C",
                str(Path(__file__).resolve().parent),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise WithKeyError("cannot locate the main checkout with git; pass --env-file") from exc
    return Path(common_dir).parent / ".env"


def resolve_environment(
    keys: Sequence[str], env_file: Path | None, base: Mapping[str, str]
) -> dict[str, str]:
    """Return base plus the keys it lacks, loaded from env_file."""
    missing = [key for key in keys if not base.get(key)]
    if not missing:
        return dict(base)
    path = env_file if env_file is not None else default_env_file()
    if not path.is_file():
        raise WithKeyError(f"{path} not found; pass --env-file")
    loaded = read_keys(path, missing)
    absent = [key for key in missing if not loaded.get(key)]
    if absent:
        raise WithKeyError(f"{', '.join(absent)} not set in {path}")
    return {**base, **loaded}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="with_key.py",
        usage="%(prog)s [--env-file PATH] KEY [KEY ...] -- COMMAND [ARG ...]",
        description="Run COMMAND with KEYs from the main checkout's .env.",
    )
    parser.add_argument("--env-file", type=Path, help="read keys from this file instead")
    parser.add_argument("keys", nargs="+", metavar="KEY")
    split = args.index("--") if "--" in args else len(args)
    options = parser.parse_args(args[:split])
    command = args[split + 1 :]
    if not command:
        parser.error("give the command after --")
    invalid = [key for key in options.keys if not KEY_NAME.fullmatch(key)]
    if invalid:
        parser.error(f"invalid key name: {', '.join(invalid)}")

    try:
        environment = resolve_environment(options.keys, options.env_file, os.environ)
    except WithKeyError as exc:
        print(f"with_key: {exc}", file=sys.stderr)
        return 2
    try:
        os.execvpe(command[0], command, environment)
    except OSError as exc:
        print(f"with_key: cannot run {command[0]}: {exc.strerror}", file=sys.stderr)
        return 127
    return 0  # unreachable: execvpe replaces this process


if __name__ == "__main__":
    sys.exit(main())
