"""Pure trusted-bundle builder and bootstrap literal for the offline browser run.

Owner decision (2026-10-03): the entrypoint reaches the read-only, network-none
browser container as ONE length-prefixed, strict JSON bundle on stdin. The
bootstrap reads exactly that many bytes from fd 0 with raw ``os.read`` (never a
buffered stdin that could read ahead into frames), stages the fixed module
files in its owned ``/tmp`` directory and statically imports the entrypoint,
which then owns the remaining stdin as the frame stream. Nothing here writes
stdout: fd 1 carries frames only.

This module performs no process, network, filesystem or container operation
and has no import-time side effects. Unlike the DNS bootstrap, the browser
interpreter keeps ``site`` (Playwright lives in the image's site-packages) but
stays isolated (``-I``: no environment-driven paths, no user site, no cwd).
"""

from __future__ import annotations

import json
import os
import struct
from collections.abc import Mapping

from scripts.web_fetch_pilot_browser_entrypoint import parse_run

MODULE_NAMES = (
    "web_fetch_pilot_acceptor.py",
    "web_fetch_pilot_browser_entrypoint.py",
    "web_fetch_pilot_core.py",
    "web_fetch_pilot_gateway.py",
    "web_fetch_pilot_io.py",
    "web_fetch_pilot_relay.py",
    "web_fetch_pilot_tunnels.py",
)
BUNDLE_CAP = 512 * 1024
FILE_CAP = 128 * 1024
PREFIX = struct.Struct(">I")
PYTHON = "/usr/local/bin/python"
# Matches the recorded browser sandbox evidence; -I ignores PYTHON* variables.
ENVIRONMENT = (
    "PATH=/usr/local/bin:/usr/bin:/bin",
    "LANG=C.UTF-8",
    "HOME=/tmp",
    "TMPDIR=/tmp",
    "XDG_CACHE_HOME=/tmp",
)


def make_bundle(modules: Mapping[str, bytes], run: Mapping[str, object]) -> bytes:
    """Length-prefixed strict JSON bundle of exact trusted module bytes + run."""
    if type(modules) is not dict or tuple(sorted(modules)) != MODULE_NAMES:
        raise ValueError("exact trusted module set required")
    texts: dict[str, str] = {}
    for name, data in modules.items():
        if type(data) is not bytes or not data or len(data) > FILE_CAP:
            raise ValueError("bounded trusted module bytes required")
        texts[name] = data.decode("utf-8")
    if type(run) is not dict:
        raise ValueError("run configuration refused")
    parse_run(dict(run))  # The same strict check the entrypoint applies.
    body = json.dumps(
        {"modules": texts, "run": run}, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")
    if len(body) > BUNDLE_CAP:
        raise ValueError("trusted bundle exceeds cap")
    return PREFIX.pack(len(body)) + body


def browser_command(python: str = PYTHON) -> tuple[str, ...]:
    """``/usr/bin/env -i`` arguments: fixed environment, isolated interpreter."""
    if type(python) is not str or not os.path.isabs(python) or "\x00" in python:
        raise ValueError("absolute interpreter required")
    return ("-i", *ENVIRONMENT, python, "-I", "-u", "-c", BOOTSTRAP_SOURCE)


BOOTSTRAP_SOURCE = r'''
"""Browser container bootstrap: one bundle from fd 0, then the entrypoint.

Never writes stdout (fd 1 carries frames). Failures exit 2 before the
entrypoint exists, with one fixed stderr line and no other output.
"""
import asyncio
import json
import os
import struct
import sys
import tempfile

sys.dont_write_bytecode = True
sys.stdout = sys.stderr
NAMES = (
    "web_fetch_pilot_acceptor.py",
    "web_fetch_pilot_browser_entrypoint.py",
    "web_fetch_pilot_core.py",
    "web_fetch_pilot_gateway.py",
    "web_fetch_pilot_io.py",
    "web_fetch_pilot_relay.py",
    "web_fetch_pilot_tunnels.py",
)
BUNDLE_CAP = 524288
FILE_CAP = 131072


def read_exact(count):
    chunks = []
    remaining = count
    while remaining:
        data = os.read(0, min(65536, remaining))
        if not data:
            raise ValueError("bundle truncated")
        chunks.append(data)
        remaining -= len(data)
    return b"".join(chunks)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def reject(value):
    raise ValueError("non-finite constant")


def load():
    (length,) = struct.unpack(">I", read_exact(4))
    if not 0 < length <= BUNDLE_CAP:
        raise ValueError("bundle length")
    raw = read_exact(length)
    value = json.loads(raw.decode("ascii"), object_pairs_hook=unique, parse_constant=reject)
    if type(value) is not dict or set(value) != {"modules", "run"}:
        raise ValueError("bundle fields")
    modules = value["modules"]
    if type(modules) is not dict or tuple(sorted(modules)) != NAMES:
        raise ValueError("bundle modules")
    for text in modules.values():
        if type(text) is not str or not text or len(text.encode("utf-8")) > FILE_CAP:
            raise ValueError("bundle module text")
    return modules, value["run"]


def stage(root, modules):
    package = os.path.join(root, "scripts")
    os.mkdir(package, 0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    for name, text in [("__init__.py", "")] + sorted(modules.items()):
        fd = os.open(os.path.join(package, name), flags, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))


def main():
    try:
        modules, run = load()
    except Exception:
        os.write(2, b'{"bootstrap":"bundle refused"}\n')
        return 2
    with tempfile.TemporaryDirectory(prefix="wfp-browser-", dir="/tmp") as root:
        if not os.path.realpath(root).startswith("/tmp/"):
            os.write(2, b'{"bootstrap":"stage refused"}\n')
            return 2
        try:
            stage(root, modules)
        except Exception:
            os.write(2, b'{"bootstrap":"stage refused"}\n')
            return 2
        sys.path.insert(0, root)
        import scripts.web_fetch_pilot_browser_entrypoint as entrypoint

        try:
            return asyncio.run(entrypoint.main(run))
        except Exception as exc:
            os.write(2, ('{"bootstrap":"%s"}\n' % type(exc).__name__).encode("ascii", "replace"))
            return 1


if __name__ == "__main__":
    sys.exit(main())
'''
