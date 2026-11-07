"""Real TLS and proxy parity for the production inference transport seam (#681).

Owner-requested qualification: the production client (``inference_transport.Handler``)
and LiteLLM's ordinary ``AsyncHTTPHandler`` are driven over a real loopback TLS
server with a CA we generate. Trust reaches both clients the way production
supplies it - ``SSL_CERT_FILE`` resolved by ``get_ssl_configuration`` inside
``AsyncHTTPHandler.create_client`` - never by disabling verification. The seam
replaces only the client's unstarted transport, so parity here is a check of
observable behaviour: content, request bytes/headers on the wire, TCP connection
reuse, certificate refusal, exact status, stream failure and charge/release rows.

Failure rows are the production classifier in ``inference_transport.Evidence``:
row 1 local validation, row 2 ``dispatch_not_sent``, row 3
``dispatch_provider_rejected``, row 4 charged/unknown (no release path).

Proxy configuration status (established, not assumed): the repository has no
``HTTP_PROXY``/``HTTPS_PROXY``/``ALL_PROXY``/``trust_env``/``aiohttp_trust_env``
setting in ``docker-compose.yml``, ``.env.example``, ``orchestrator/config.py``
or ``docs/``; proxies are therefore NOT a supported Daemon configuration.
LiteLLM's aiohttp pool nevertheless resolves environment proxies per request
(``AIOHTTP_TRUST_ENV``/``disable_aiohttp_trust_env``), identically with the seam
on and off, and this file proves that routing parity plus its accounting.

Certificates: generated with ``cryptography`` (a locked dependency whose wheel
is ``cp311-abi3``, so this works on Python 3.11 without an ``openssl`` binary)
in ``tmp_path`` and never committed.
"""

from __future__ import annotations

import asyncio
import contextvars
import datetime as dt
import ipaddress
import json
import socket
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import aiohttp
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler, get_ssl_configuration

import litellm

from orchestrator.inference_transport import Attempt, Evidence, Forwarder, Handler, current
from tests.inference_transport_support import offline_only

__all__ = ["offline_only"]  # Reuse the enforced loopback/.invalid network fixture.

MODEL = "openrouter/openai/gpt-4.1-mini"
MESSAGE = {"role": "user", "content": "offline tls proof"}


# --------------------------------------------------------------------------- PKI


def _key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _name(common: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])


def _pem_cert(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _pem_key(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


@dataclass
class PKI:
    """One session CA; every trusted case uses the same anchor production would."""

    ca_path: Path
    ca_cert: x509.Certificate
    ca_key: ec.EllipticCurvePrivateKey
    root: Path
    issued: int = 0

    @classmethod
    def create(cls, root: Path) -> "PKI":
        now = dt.datetime.now(dt.UTC)
        ca_key = _key()
        ca_name = _name("offline-session-ca")
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(ca_name)
            .issuer_name(ca_name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=30))
            .not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        ca_path = root / "ca.pem"
        ca_path.write_bytes(_pem_cert(ca_cert))
        return cls(ca_path=ca_path, ca_cert=ca_cert, ca_key=ca_key, root=root)

    def server(self, kind: str = "valid") -> tuple[Path, Path, Path]:
        self.issued += 1
        now = dt.datetime.now(dt.UTC)
        directory = self.root / f"server-{self.issued}-{kind}"
        directory.mkdir()
        server_key = _key()
        if kind == "mismatch":
            san = x509.SubjectAlternativeName([x509.DNSName("wrong.invalid")])
            not_before, not_after = now - dt.timedelta(days=1), now + dt.timedelta(days=1)
        elif kind == "expired":
            san = x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            )
            not_before, not_after = now - dt.timedelta(days=10), now - dt.timedelta(days=1)
        else:
            assert kind == "valid"
            san = x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            )
            not_before, not_after = now - dt.timedelta(days=1), now + dt.timedelta(days=1)
        server_cert = (
            x509.CertificateBuilder()
            .subject_name(_name(f"offline-server-{kind}"))
            .issuer_name(self.ca_cert.subject)
            .public_key(server_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before)
            .not_valid_after(not_after)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(self.ca_key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()),
                critical=False,
            )
            .add_extension(san, critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(self.ca_key, hashes.SHA256())
        )
        cert_path, key_path = directory / "server.pem", directory / "server.key"
        cert_path.write_bytes(_pem_cert(server_cert))
        key_path.write_bytes(_pem_key(server_key))
        return self.ca_path, cert_path, key_path


@pytest.fixture(scope="module")
def pki(tmp_path_factory: pytest.TempPathFactory) -> PKI:
    return PKI.create(tmp_path_factory.mktemp("tls-pki"))


def server_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path, key_path)
    return context


# --------------------------------------------------------------- TLS peers


@dataclass
class Peer:
    mode: str = "ok"
    accepts: int = 0
    bodies: list[bytes] = field(default_factory=list)
    headers: list[list[bytes]] = field(default_factory=list)
    writers: list[asyncio.StreamWriter] = field(default_factory=list)
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    received: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    server: asyncio.Server | None = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.accepts += 1
        self.writers.append(writer)
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        try:
            while True:
                try:
                    header = await reader.readuntil(b"\r\n\r\n")
                except (asyncio.IncompleteReadError, ConnectionError):
                    return
                self.headers.append(header.split(b"\r\n"))
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in header.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                body = await reader.readexactly(length)
                self.bodies.append(body)
                self.received.set()
                if not await self.respond(body, writer):
                    return
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, ssl.SSLError):
                pass
            self.tasks.discard(task)

    async def respond(self, body: bytes, writer: asyncio.StreamWriter) -> bool:
        if self.mode == "close":
            return False
        if self.mode == "read_timeout":
            await self.release.wait()
            return False
        streaming = json.loads(body).get("stream", False)
        if self.mode == "error_before":
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
            )
            await writer.drain()
            writer.transport.abort()
            return False
        status = int(self.mode) if self.mode.isdigit() else 200
        chunk: dict[str, Any] = {
            "id": "offline",
            "object": "chat.completion.chunk" if streaming else "chat.completion",
            "created": 1,
            "model": "openai/gpt-4.1-mini",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "ok"} if streaming else None,
                    "message": None if streaming else {"role": "assistant", "content": "ok"},
                    "finish_reason": None if streaming else "stop",
                }
            ],
        }
        if status != 200:
            output = json.dumps({"error": {"message": "offline", "code": status}}).encode()
            content_type = "application/json"
        elif streaming:
            output = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
            content_type = "text/event-stream"
        else:
            output = json.dumps(chunk).encode()
            content_type = "application/json"
        writer.write(
            f"HTTP/1.1 {status} Offline\r\nContent-Type: {content_type}\r\n"
            f"Content-Length: {len(output)}\r\nSet-Cookie: proof=no\r\n\r\n".encode()
            + output
        )
        await writer.drain()
        return True


@asynccontextmanager
async def tls_peer(
    cert_path: Path, key_path: Path, mode: str = "ok"
) -> AsyncIterator[tuple[Peer, str]]:
    state = Peer(mode)
    state.server = await asyncio.start_server(
        state.handle,
        "127.0.0.1",
        0,
        ssl=server_context(cert_path, key_path),
    )
    port = state.server.sockets[0].getsockname()[1]
    try:
        yield state, f"https://127.0.0.1:{port}/v1"
    finally:
        state.release.set()
        state.server.close()
        for writer in state.writers:
            writer.close()
        await state.server.wait_closed()
        if state.tasks:
            await asyncio.gather(*state.tasks)


# --------------------------------------------------------------- proxies


@dataclass
class Proxy:
    reject: bool = False
    connects: list[bytes] = field(default_factory=list)
    tunnels: int = 0
    server: asyncio.Server | None = None
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        remote_writer: asyncio.StreamWriter | None = None
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            self.connects.append(header)
            if self.reject:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                return
            target = header.split(b" ")[1].decode()
            host, _, port = target.rpartition(":")
            remote_reader, remote_writer = await asyncio.open_connection(host, int(port))
            self.tunnels += 1
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()

            async def pump(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
                try:
                    while chunk := await source.read(65536):
                        sink.write(chunk)
                        await sink.drain()
                except (ConnectionError, asyncio.IncompleteReadError):
                    pass
                finally:
                    sink.close()

            await asyncio.gather(pump(reader, remote_writer), pump(remote_reader, writer))
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if remote_writer is not None:
                remote_writer.close()
                try:
                    await remote_writer.wait_closed()
                except ConnectionError:
                    pass
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.tasks.discard(task)


@asynccontextmanager
async def connect_proxy(reject: bool = False) -> AsyncIterator[tuple[Proxy, str]]:
    state = Proxy(reject)
    state.server = await asyncio.start_server(state.handle, "127.0.0.1", 0)
    port = state.server.sockets[0].getsockname()[1]
    try:
        yield state, f"http://127.0.0.1:{port}"
    finally:
        state.server.close()
        await state.server.wait_closed()
        if state.tasks:
            await asyncio.gather(*state.tasks)


# --------------------------------------------------------------- calls


async def exchange(
    base: str, stream: bool, client: Any, **overrides: Any
) -> tuple[str | None, BaseException | None]:
    call: dict[str, Any] = {
        "model": MODEL,
        "messages": [MESSAGE],
        "api_base": base,
        "api_key": "fictional-offline-key",
        "max_tokens": 8,
        "num_retries": 0,
        "timeout": 0.4,
        "stream": stream,
        "provider": {"order": ["OpenAI"], "allow_fallbacks": False},
        "extra_body": {"reasoning": {"effort": "low", "exclude": False}},
        "client": client,
    }
    if stream:
        call["stream_options"] = {"include_usage": True}
    call.update(overrides)
    try:
        result = cast(Any, await asyncio.wait_for(litellm.acompletion(**call), timeout=5))
        if not stream:
            return result.choices[0].message.content, None
        text = ""
        try:
            async for chunk in cast(Any, result):
                if chunk.choices:
                    text += chunk.choices[0].delta.content or ""
        finally:
            await cast(Any, result).aclose()
        return text, None
    except BaseException as exc:  # the failure IS the observation
        return None, exc


async def dispatch(
    base: str, stream: bool, handler: Handler, evidence: Evidence, **overrides: Any
) -> tuple[str | None, BaseException | None]:
    token = current.set(evidence)
    try:
        task = asyncio.create_task(
            exchange(base, stream, handler, **overrides), context=contextvars.copy_context()
        )
        return await task
    finally:
        current.reset(token)


@asynccontextmanager
async def clients() -> AsyncIterator[tuple[Handler, AsyncHTTPHandler]]:
    """Production seam and its ordinary LiteLLM baseline, built the same way."""
    handler = Handler()
    baseline = AsyncHTTPHandler(timeout=0.4)
    try:
        assert isinstance(handler.client._transport, Forwarder)
        yield handler, baseline
    finally:
        await baseline.close()
        await handler.shutdown()


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "SSL_VERIFY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "AIOHTTP_TRUST_ENV",
        "DISABLE_AIOHTTP_TRUST_ENV",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)


def trusted(monkeypatch: pytest.MonkeyPatch, ca_path: Path) -> None:
    """The production trust route: SSL_CERT_FILE resolved by get_ssl_configuration."""
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_path))
    config = get_ssl_configuration(None)
    assert isinstance(config, ssl.SSLContext)
    assert config.verify_mode == ssl.CERT_REQUIRED
    assert config.check_hostname


def cause_chain(exc: BaseException) -> list[str]:
    """Exception chain names, for reporting only; never accounting evidence."""
    names: list[str] = []
    seen: set[int] = set()
    current_exc: BaseException | None = exc
    while current_exc is not None and id(current_exc) not in seen:
        seen.add(id(current_exc))
        names.append(type(current_exc).__name__)
        assert current_exc.__cause__ is not current_exc
        current_exc = current_exc.__cause__
    return names


def report(name: str, mode: str, evidence: Evidence, exc: BaseException | None) -> None:
    print(
        f"TLS {name} mode={mode} exception={type(exc).__name__} "
        f"text={str(exc)[-220:]!r} "
        f"chain={cause_chain(exc) if exc is not None else []} "
        f"charge={evidence.charge} attempts={evidence.attempts}"
    )


# --------------------------------------------------------------- parity


def test_shared_transport_verifies_by_default() -> None:
    """The seam must not ship a verification-off transport."""
    assert litellm.ssl_verify is True
    assert litellm.aiohttp_trust_env is False
    assert litellm.disable_aiohttp_trust_env is False
    handler = Handler()
    assert isinstance(handler.client._transport, Forwarder)
    assert handler.client._mounts == {}
    assert handler.pool is not None
    ssl_config = handler.pool._ssl_verify
    assert isinstance(ssl_config, ssl.SSLContext)
    assert ssl_config.verify_mode == ssl.CERT_REQUIRED
    assert ssl_config.check_hostname

    async def inspect() -> None:
        assert handler.pool is not None
        connector = handler.pool._get_valid_client_session().connector
        assert connector._ssl is not False
        assert connector._ssl is not None
        await handler.shutdown()

    # A default connector is built with that same context, not ssl=False.
    asyncio.run(inspect())


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_trusted_success_parity(
    stream: bool, pki: PKI, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca_path, cert_path, key_path = pki.server()
    trusted(monkeypatch, ca_path)
    async with tls_peer(cert_path, key_path) as (server, base):
        async with clients() as (handler, baseline):
            off_text, off_exc = await exchange(base, stream, baseline)
            assert server.accepts == 1
            evidence = Evidence()
            seam_text, seam_exc = await dispatch(base, stream, handler, evidence)
            assert server.accepts == 2
    assert off_exc is None and off_text == "ok"
    assert seam_exc is None and seam_text == "ok"
    # Same request bytes and headers on the wire, one per mode.
    assert len(server.bodies) == 2
    assert len(set(server.bodies)) == 1
    assert all(headers == server.headers[0] for headers in server.headers)
    assert len(evidence.attempts) == 1
    assert evidence.attempts[0].status == 200
    assert evidence.attempts[0].not_sent is False
    assert evidence.statuses == [200] and evidence.charge is True
    assert evidence.release_path(RuntimeError("ok")) is None


@pytest.mark.asyncio
async def test_connection_reuse_over_tls(pki: PKI, monkeypatch: pytest.MonkeyPatch) -> None:
    ca_path, cert_path, key_path = pki.server()
    trusted(monkeypatch, ca_path)
    evidence = Evidence()
    async with tls_peer(cert_path, key_path) as (server, base):
        async with clients() as (handler, baseline):
            for _ in range(2):
                text, exc = await exchange(base, False, baseline)
                assert exc is None and text == "ok"
            assert server.accepts == 1  # Baseline reused its TLS connection.
            for _ in range(2):
                text, exc = await dispatch(base, False, handler, evidence)
                assert exc is None and text == "ok"
            assert server.accepts == 2  # And so did the shared seam transport.
    assert [a.status for a in evidence.attempts] == [200, 200]
    assert evidence.charge is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["seam", "baseline"])
async def test_untrusted_ca_refused(mode: str, pki: PKI, monkeypatch: pytest.MonkeyPatch) -> None:
    """No SSL_CERT_FILE: the generated CA is untrusted, verification stays on."""
    _, cert_path, key_path = pki.server()
    async with tls_peer(cert_path, key_path) as (server, base):
        async with clients() as (handler, baseline):
            if mode == "seam":
                evidence = Evidence()
                text, exc = await dispatch(base, False, handler, evidence)
                assert text is None and exc is not None
                assert evidence.attempts
                attempt = evidence.attempts[0]
                assert attempt.not_sent is True
                assert attempt.status is None
                assert attempt.cause == "ClientConnectorCertificateError"
                # Evidence, not SDK metadata: that class is a ClientConnectorError.
                assert issubclass(
                    aiohttp.ClientConnectorCertificateError, aiohttp.ClientConnectorError
                )
                assert evidence.charge is False
                assert evidence.statuses == []
                assert evidence.release_path(exc) == "dispatch_not_sent"
            else:
                text, exc = await exchange(base, False, baseline)
                assert text is None and exc is not None
    # No request ever reached the peer: the handshake failed first.
    assert server.accepts == 0 and server.bodies == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["mismatch", "expired"])
@pytest.mark.parametrize("mode", ["seam", "baseline"])
async def test_certificate_identity_failures_refused(
    kind: str, mode: str, pki: PKI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trusted CA, wrong hostname or expired certificate: refused in both modes."""
    ca_path, cert_path, key_path = pki.server(kind)
    trusted(monkeypatch, ca_path)
    async with tls_peer(cert_path, key_path) as (server, base):
        async with clients() as (handler, baseline):
            if mode == "seam":
                evidence = Evidence()
                text, exc = await dispatch(base, False, handler, evidence)
                assert text is None and exc is not None
                assert evidence.attempts and evidence.attempts[0].not_sent is True
                assert evidence.attempts[0].status is None
                assert evidence.charge is False
                assert evidence.release_path(exc) == "dispatch_not_sent"
            else:
                text, exc = await exchange(base, False, baseline)
                assert text is None and exc is not None
    assert server.accepts == 0 and server.bodies == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("stream", [False, True])
async def test_tls_provider_rejected_status(
    status: int, stream: bool, pki: PKI, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca_path, cert_path, key_path = pki.server()
    trusted(monkeypatch, ca_path)
    async with tls_peer(cert_path, key_path, mode=str(status)) as (server, base):
        async with clients() as (handler, baseline):
            text, exc = await exchange(base, stream, baseline)
            report(f"status-{status}-off", "baseline", Evidence(), exc)
            assert text is None and exc is not None
            evidence = Evidence()
            seam_text, seam_exc = await dispatch(base, stream, handler, evidence)
            report(f"status-{status}-seam", "seam", evidence, seam_exc)
            assert seam_text is None and seam_exc is not None
    report(f"status-{status}-final", "post", evidence, seam_exc)
    assert len(server.bodies) == 2
    assert evidence.statuses == [status]
    assert evidence.attempts[0].status == status
    assert evidence.attempts[0].not_sent is False
    assert evidence.charge is True
    # Row 3: provider rejection, not an unknown charge.
    assert evidence.release_path(seam_exc) == "dispatch_provider_rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["seam", "baseline"])
async def test_tls_read_timeout_charged(
    mode: str, pki: PKI, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca_path, cert_path, key_path = pki.server()
    trusted(monkeypatch, ca_path)
    async with tls_peer(cert_path, key_path, mode="read_timeout") as (server, base):
        async with clients() as (handler, baseline):
            if mode == "seam":
                evidence = Evidence()
                text, exc = await dispatch(base, False, handler, evidence, timeout=0.25)
                assert text is None and exc is not None
                assert server.bodies  # The peer received the request.
                assert evidence.attempts and evidence.attempts[0].not_sent is False
                assert evidence.attempts[0].status is None
                assert evidence.charge is True
                assert evidence.release_path(exc) is None  # Row 4: charged.
            else:
                text, exc = await exchange(base, False, baseline, timeout=0.25)
                assert text is None and exc is not None
                assert server.bodies
    assert len(server.bodies) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["seam", "baseline"])
async def test_tls_close_after_read_charged(
    mode: str, pki: PKI, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca_path, cert_path, key_path = pki.server()
    trusted(monkeypatch, ca_path)
    async with tls_peer(cert_path, key_path, mode="close") as (server, base):
        async with clients() as (handler, baseline):
            if mode == "seam":
                evidence = Evidence()
                text, exc = await dispatch(base, False, handler, evidence)
                assert text is None and exc is not None
                assert server.bodies
                assert evidence.charge is True
                assert evidence.release_path(exc) is None
            else:
                text, exc = await exchange(base, False, baseline)
                assert text is None and exc is not None
                assert server.bodies
    assert len(server.bodies) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["seam", "baseline"])
async def test_tls_stream_failure_after_200_not_rejection(
    mode: str, pki: PKI, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca_path, cert_path, key_path = pki.server()
    trusted(monkeypatch, ca_path)
    async with tls_peer(cert_path, key_path, mode="error_before") as (server, base):
        async with clients() as (handler, baseline):
            if mode == "seam":
                evidence = Evidence()
                text, exc = await dispatch(base, True, handler, evidence)
                assert text is None and exc is not None
                assert evidence.statuses == [200]
                assert evidence.charge is True
                # 200 is not a rejection row: a broken stream is charged.
                assert evidence.release_path(exc) is None
            else:
                text, exc = await exchange(base, True, baseline)
                assert text is None and exc is not None
    assert len(server.bodies) == 1


# --------------------------------------------------------------- proxies


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["seam", "baseline"])
async def test_proxy_tunnel_parity(mode: str, pki: PKI, monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTPS_PROXY: the seam must tunnel exactly where the baseline tunnels."""
    ca_path, cert_path, key_path = pki.server()
    trusted(monkeypatch, ca_path)
    async with connect_proxy() as (proxy, proxy_url):
        async with tls_peer(cert_path, key_path) as (server, base):
            async with clients() as (handler, baseline):
                if mode == "seam":
                    monkeypatch.setenv("HTTPS_PROXY", proxy_url)
                    evidence = Evidence()
                    text, exc = await dispatch(base, False, handler, evidence)
                    assert text == "ok" and exc is None
                    assert evidence.statuses == [200] and evidence.charge is True
                else:
                    monkeypatch.setenv("HTTPS_PROXY", proxy_url)
                    text, exc = await exchange(base, False, baseline)
                    assert text == "ok" and exc is None
    assert proxy.connects and proxy.tunnels == 1
    assert b"CONNECT 127.0.0.1:" in proxy.connects[0]
    assert len(server.bodies) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["seam", "baseline"])
async def test_proxy_connect_failure_charged(
    mode: str, pki: PKI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected CONNECT certifies nothing: no request is sent, but charge row 4."""
    ca_path, cert_path, key_path = pki.server()
    trusted(monkeypatch, ca_path)
    async with connect_proxy(reject=True) as (proxy, proxy_url):
        monkeypatch.setenv("HTTPS_PROXY", proxy_url)
        async with tls_peer(cert_path, key_path) as (server, base):
            async with clients() as (handler, baseline):
                if mode == "seam":
                    evidence = Evidence()
                    text, exc = await dispatch(base, False, handler, evidence)
                    assert text is None and exc is not None
                    assert proxy.connects
                    assert evidence.attempts
                    assert evidence.attempts[0].not_sent is False
                    assert evidence.charge is True
                    assert evidence.release_path(exc) is None
                else:
                    text, exc = await exchange(base, False, baseline)
                    assert text is None and exc is not None
                    assert proxy.connects
    assert server.accepts == 0 and server.bodies == []


# --------------------------------------------------------------- timeout row


def test_connect_timeout_without_evidence_is_charged() -> None:
    """A connect-timeout style outcome certifies nothing and is charged (row 4)."""
    evidence = Evidence(attempts=[Attempt(error="ConnectionTimeoutError", cause="TimeoutError")])
    assert evidence.charge is True
    assert evidence.statuses == []
    assert evidence.release_path(RuntimeError("connect timeout")) is None
    broken = Evidence(broken=True)
    assert broken.charge is True and broken.release_path(RuntimeError("x")) is None


@pytest.mark.xfail(
    strict=True, reason="reliable owned-loopback TLS connect timeout not established"
)
def test_tls_connect_timeout_unproved() -> None:
    pytest.fail("No external blackhole or mocked connector used")


def test_no_private_openssl_dependency(pki: PKI) -> None:
    """The generator is pure cryptography, so no openssl binary is required."""
    ca_path, cert_path, key_path = pki.server()
    assert ca_path.is_file() and cert_path.is_file() and key_path.is_file()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
    ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(str(ca_path))
