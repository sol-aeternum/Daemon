"""Owned, TCP-disabled PG16 fixture using public binaries from the cached image.

No installation, Docker service, network, credentials, or production DB access.
Tool extraction and cluster output live in registered task resources, never /usr.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any

CONTAINER = "9aa9b80e8c782e1efddaa8a6839f525b58be5b85cca067db10b81c8ec0002bef"
PROGRAMS = ("postgres", "initdb", "pg_ctl", "psql", "pg_dump", "pg_restore")


def provision(tools: Path) -> None:
    """Complete the already-extracted public tool tree; never alter host tools."""
    marker = tools / "fixture-tools.json"
    if marker.exists():
        raise RuntimeError("Fixture tool provisioning already recorded; do not replay")
    binaries = tools / "usr/lib/postgresql/16/bin"
    # Debian's versioned share tree contains this one relative sample symlink.
    # Materialize its public target inside the same owned tool prefix.
    subprocess.run(
        [
            "docker",
            "cp",
            CONTAINER + ":/usr/share/postgresql/postgresql.conf.sample",
            str(tools / "usr/share/postgresql/postgresql.conf.sample"),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    library = tools / "native-libs"
    library.mkdir(mode=0o700, exist_ok=False)
    paths = [f"/usr/lib/postgresql/16/bin/{name}" for name in PROGRAMS]
    paths += ["/usr/lib/postgresql/16/lib/pgcrypto.so", "/usr/lib/postgresql/16/lib/vector.so"]
    result = subprocess.run(
        ["docker", "exec", CONTAINER, "ldd", *paths],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    libs = sorted(set(re.findall(r"(/[a-zA-Z0-9_./+-]+\.so(?:\.[0-9]+)*)", result.stdout)))
    if not libs or not any("ld-linux" in name for name in libs):
        raise RuntimeError("Cached tool dependency inventory incomplete")
    for name in libs:
        if not name.startswith(("/lib/", "/lib64/", "/usr/lib/")):
            raise RuntimeError("Unexpected public library path")
        subprocess.run(
            ["docker", "cp", "-L", f"{CONTAINER}:{name}", str(library / Path(name).name)],
            capture_output=True,
            check=True,
            timeout=30,
        )
    loader = next(library.glob("ld-linux*.so*"))
    for name in PROGRAMS:
        executable = binaries / name
        original = executable.with_name(name + ".binary")
        executable.rename(original)
        executable.write_text(
            "#!/bin/sh\nexec "
            + shlex.join([str(loader), "--library-path", str(library), str(original)])
            + ' "$@"\n'
        )
        executable.chmod(0o700)
    marker.write_text(json.dumps({"container": CONTAINER, "libraries": libs, "programs": PROGRAMS}))


class NativePostgres:
    def __init__(self, tools: Path, root: Path):
        self.bin = tools / "usr/lib/postgresql/16/bin"
        self.share = tools / "usr/share/postgresql/16"
        self.root = root
        self.fd: int | None = None
        self.socket = ""
        self.started = False

    def command(self, name: str, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        return subprocess.run(
            [str(self.bin / name), *args], capture_output=True, check=True, timeout=60, **kwargs
        )

    def start(self) -> None:
        if self.root.exists():
            raise RuntimeError("Fixture cluster path must be new")
        self.root.mkdir(mode=0o700)
        socket = self.root / "socket"
        socket.mkdir(mode=0o700)
        # A short, process-owned alias avoids PostgreSQL's Unix socket path limit;
        # all actual files remain in this registered SSD fixture directory.
        self.fd = os.open(socket, os.O_RDONLY | os.O_DIRECTORY)
        self.socket = f"/proc/{os.getpid()}/fd/{self.fd}"
        self.command(
            "initdb",
            "-D",
            str(self.root / "data"),
            "-L",
            str(self.share),
            "--no-locale",
            "--encoding=UTF8",
            "--auth=trust",
            "--username=bench",
        )
        self.started = True
        self.command(
            "pg_ctl",
            "-D",
            str(self.root / "data"),
            "-l",
            str(self.root / "server.log"),
            "-o",
            f"-k {self.socket} -c listen_addresses=''",
            "-w",
            "start",
        )

    def close(self) -> None:
        try:
            if self.started and (self.root / "data/postmaster.pid").exists():
                self.command("pg_ctl", "-D", str(self.root / "data"), "-m", "fast", "-w", "stop")
                self.started = False
        finally:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None

    def sql(self, text: str, *, database: str = "postgres") -> str:
        return self.command(
            "psql",
            "--no-psqlrc",
            "-h",
            self.socket,
            "-U",
            "bench",
            "-d",
            database,
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            text,
            text=True,
        ).stdout
