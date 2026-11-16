"""Owned B1 fixture process helpers. Never loaded by an application launcher."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import ipaddress
import hashlib
import json
import os
from pathlib import Path
import socket
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def reserve() -> socket.socket:
    """Retained inherited listeners eliminate check-then-bind reuse races."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.set_inheritable(True)
    return listener


def certificate(directory: Path) -> tuple[Path, Path]:
    """Ephemeral test certificate; no host trust store or live CA is involved."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "B1 owned fixture")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "fixture.crt", directory / "fixture-private"
    key_path.touch(mode=0o600)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def node_environment(directory: Path, origin: str, cert: Path) -> dict[str, str]:
    """Explicit allowlist, no process-environment enumeration or dotenv loading."""
    # Chromium service-worker script installation does not consistently inherit
    # Playwright ignoreHTTPSErrors. Pin the exception to this fixture certificate
    # only (public SPKI), rather than disabling browser certificate validation.
    public_key = x509.load_pem_x509_certificate(cert.read_bytes()).public_key()
    spki = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        # Playwright's existing cache lookup needs the real home, not its contents.
        "HOME": str(Path.home()),
        "LANG": "C.UTF-8",
        "TMPDIR": str(directory),
        "NODE_ENV": "production",
        "NODE_OPTIONS": f"--max-old-space-size=2048 --require={FRONTEND / 'e2e/b1-fixture-no-dotenv.cjs'}",
        "NEXT_TELEMETRY_DISABLED": "1",
        "NEXT_PUBLIC_API_URL": origin + "/daemon",
        "DAEMON_INTERNAL_API_URL": origin + "/daemon",
        "NODE_EXTRA_CA_CERTS": str(cert),
        "B1_TEST_CERT_SPKI": base64.b64encode(hashlib.sha256(spki).digest()).decode(),
    }


class JsonChild:
    """Private bounded JSON IPC. Exceptions never include frames or child logs."""

    def __init__(self, process: asyncio.subprocess.Process):
        self.process = process
        self.sequence = 0

    @classmethod
    async def start(cls, script: Path, args: list[str], env: dict[str, str], *, fds=()):
        process = await asyncio.create_subprocess_exec(
            "node",
            str(script),
            *args,
            cwd=str(FRONTEND),
            env=env,
            pass_fds=fds,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            # Application exceptions can include request context: never inherit.
            stderr=asyncio.subprocess.DEVNULL,
        )
        return cls(process)

    async def frame(self, *, timeout=60) -> dict[str, Any]:
        assert self.process.stdout is not None
        async with asyncio.timeout(timeout):
            while True:
                line = await self.process.stdout.readline()
                assert line, "B1 child exited before private receipt"
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeError):
                    continue  # Do not retain/publish incidental application logging.
                if isinstance(value, dict):
                    return value

    async def command(self, op: str, **kwargs) -> dict[str, Any]:
        self.sequence += 1
        ident = self.sequence
        assert self.process.stdin is not None
        self.process.stdin.write((json.dumps({"id": ident, "op": op, **kwargs}) + "\n").encode())
        await self.process.stdin.drain()
        frame = await self.frame(timeout=90)
        assert frame.get("id") == ident, "B1 IPC correlation failed"
        assert frame.get("ok") is True, (
            f"B1 browser/edge stage failed: {op}; {frame.get('reason', 'details suppressed')}"
        )
        return frame.get("result", frame.get("state", {}))

    async def stop(self) -> None:
        if self.process.returncode is not None:
            assert self.process.returncode == 0, "B1 child exited unsuccessfully"
            return
        assert self.process.stdin is not None
        self.process.stdin.write(b'{"op":"stop"}\n')
        await self.process.stdin.drain()
        # EOF is part of the private shutdown protocol: readline's iterator can
        # otherwise leave the browser child's stdin handle keeping Node alive.
        self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), 15)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()
            raise AssertionError("Owned B1 child exceeded shutdown grace") from None
        assert self.process.returncode == 0, "B1 owned child shutdown failed"


async def production_build(env: dict[str, str]) -> None:
    """Actual installed Next/Serwist build; no install, download or source copy."""
    process = await asyncio.create_subprocess_exec(
        "npm",
        "run",
        "build",
        cwd=str(FRONTEND),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 360)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    # Build has no auth input, but do not publish arbitrary environment diagnostics.
    if process.returncode:
        # Safe structural diagnostic only, not raw compiler/application output.
        lines = output.decode(errors="replace").splitlines()
        errors = sum("error" in line.lower() for line in lines)
        raise AssertionError(
            f"Owned production Next build failed (exit={process.returncode}, error_lines={errors})"
        )
    assert (FRONTEND / "public/sw.js").is_file(), "Production service worker was not emitted"
