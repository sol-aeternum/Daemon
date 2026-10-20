"""Stage E1 in-host tunnel/TLS fixtures: REVIEW BEFORE FIRST EXECUTION.

Pending name on purpose: pytest does not collect this module until the
reviewed bytes are renamed to ``test_web_fetch_pilot_gateway_tls_io.py``.

One process, real kernel objects only on loopback: a TLS client opens a real
TCP connection to the relay's real ``LoopbackAcceptor`` on ``127.0.0.1``,
the relay and gateway exchange frames over real pipes (``FDFrameIO`` over
``AsyncFD``), and the gateway applies its real manifest/address policy to
injected resolver answers. No DNS, no public socket, no container, no browser.

The ONLY test-only transport is ``LoopbackFixtureConnector`` (it lives here,
never in ``scripts/``): it records every numeric dial (the dial spy), accepts
only the single validated public candidate, and maps it to a local TLS
fixture. Because a loopback socket cannot truthfully report that candidate as
its peer, the connector presents the mapped peer to the gateway's peer check:
**peer identity on real sockets is not qualified here** (deferred to E3's
internal network). The TLS fixture uses a throwaway self-signed certificate
generated at test time in pytest's private ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import socket
import ssl
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from scripts.web_fetch_pilot_acceptor import LoopbackAcceptor
from scripts.web_fetch_pilot_gateway import Gateway, GatewayOutcome
from scripts.web_fetch_pilot_io import AsyncFD, FDFrameIO, SocketConnection
from scripts.web_fetch_pilot_relay import HTTP_OK, HTTP_REFUSED, Relay, RelayOutcome

HOST = "openai.com"  # Manifest host; never resolved or contacted.
CANDIDATE = "8.8.8.8"  # Injected public answer; mapped to loopback, never dialed.
GREETING = b"daemon-pilot-tls-ping"


def write_certificate(directory: Path, name: str) -> tuple[Path, Path]:
    """Throwaway self-signed EC certificate for ``name``; private tmp only."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / f"{name}.crt", directory / f"{name}.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    os.chmod(key_path, 0o600)
    return cert_path, key_path


class MappedPeer:
    """Real loopback SocketConnection presenting the validated candidate peer."""

    def __init__(self, connection: SocketConnection, candidate: str, port: int) -> None:
        assert connection.getpeername() == ("127.0.0.1", port)  # Truthful mapping only.
        self._connection = connection
        self._candidate = candidate

    @property
    def peer_ip(self) -> str:
        return self._candidate

    def getpeername(self) -> tuple[str, int]:
        return self._candidate, 443

    async def read(self, maxsize: int) -> bytes:
        return await self._connection.read(maxsize)

    async def write(self, data: bytes) -> int:
        return await self._connection.write(data)

    async def shutdown_write(self) -> None:
        await self._connection.shutdown_write()

    def close(self) -> None:
        self._connection.close()


@dataclass
class LoopbackFixtureConnector:
    """Test-only dial spy: only the validated candidate maps to the fixture."""

    port: int
    dials: list[tuple[str, int]] = field(default_factory=list)

    async def __call__(self, ip: str, port: int) -> MappedPeer:
        self.dials.append((ip, port))
        if (ip, port) != (CANDIDATE, 443):
            raise AssertionError("gateway dialed an unexpected numeric candidate")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setblocking(False)
            await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", self.port))
            return MappedPeer(SocketConnection(sock), ip, self.port)
        except BaseException:
            sock.close()
            raise


@dataclass
class Chain:
    relay_address: tuple[str, int]
    connector: LoopbackFixtureConnector
    resolutions: list[str]
    outcomes: list[object] = field(default_factory=list)


def fd_set() -> set[str]:
    return set(os.listdir("/proc/self/fd"))


@asynccontextmanager
async def chain(
    tmp_path: Path, answers: Sequence[tuple[str, ...]], *, cert_name: str = HOST
) -> AsyncIterator[Chain]:
    """Owned loopback relay/gateway/TLS fixture; restores fds and tasks."""
    fds, tasks = fd_set(), set(asyncio.all_tasks())
    cert_path, key_path = write_certificate(tmp_path, cert_name)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    handlers: set[asyncio.Task[None]] = set()

    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        handlers.add(task)
        try:
            data = await asyncio.wait_for(reader.read(1024), 5)
            writer.write(b"echo:" + data)
            await writer.drain()
        except (ssl.SSLError, ConnectionError, TimeoutError):
            pass  # Client-side verification failure aborts the handshake.
        finally:
            writer.close()
            handlers.discard(task)

    server = await asyncio.start_server(echo, "127.0.0.1", 0, ssl=server_context)
    fixture_port = server.sockets[0].getsockname()[1]
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    pipes: list[int] = []
    relay_task: asyncio.Task[RelayOutcome] | None = None
    gateway_task: asyncio.Task[GatewayOutcome] | None = None
    relay_writer: AsyncFD | None = None
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        to_gateway = os.pipe2(os.O_CLOEXEC)
        pipes.extend(to_gateway)
        to_relay = os.pipe2(os.O_CLOEXEC)
        pipes.extend(to_relay)
        relay_writer = AsyncFD(to_gateway[1])
        relay_io = FDFrameIO(AsyncFD(to_relay[0]), relay_writer)
        gateway_io = FDFrameIO(AsyncFD(to_gateway[0]), AsyncFD(to_relay[1]))
        pipes.clear()  # AsyncFD now owns every pipe end.
        remaining = list(answers)
        resolutions: list[str] = []

        async def resolve(host: str) -> Sequence[str]:
            resolutions.append(host)
            return remaining.pop(0)

        connector = LoopbackFixtureConnector(fixture_port)
        acceptor = LoopbackAcceptor(listener)
        relay = Relay(relay_io, acceptor, deadline=10.0)
        gateway = Gateway((HOST,), (), gateway_io, resolve, connector, deadline=10.0)
        relay_task = asyncio.create_task(relay.run())
        gateway_task = asyncio.create_task(gateway.run())
        handle = Chain(acceptor.address, connector, resolutions)
        async with asyncio.timeout(8):
            yield handle
        relay_writer.close()  # Gateway input EOF; the gateway then closes its ends.
        handle.outcomes.extend(await asyncio.wait_for(asyncio.gather(relay_task, gateway_task), 5))
        assert not relay.pending_tasks and not gateway.pending_tasks
    finally:
        actors: list[asyncio.Task[RelayOutcome] | asyncio.Task[GatewayOutcome]] = [
            task for task in (relay_task, gateway_task) if task is not None
        ]
        for task in actors:
            if not task.done():
                task.cancel()
        if actors:
            await asyncio.wait(actors)
            for task in actors:
                if not task.cancelled():
                    task.exception()  # Retrieved; the body already asserted outcomes.
        for fd in pipes:  # Only ends never transferred to an AsyncFD owner.
            os.close(fd)
        listener.close()
        server.close()
        await server.wait_closed()
        await asyncio.wait_for(asyncio.gather(*handlers, return_exceptions=True), 2)
    for _ in range(200):
        if fd_set() == fds and set(asyncio.all_tasks()) == tasks:
            break
        await asyncio.sleep(0.005)
    assert fd_set() == fds
    assert set(asyncio.all_tasks()) == tasks


async def connect_tunnel(
    address: tuple[str, int], host: str = HOST
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, bytes]:
    reader, writer = await asyncio.open_connection(*address)
    writer.write(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
    await writer.drain()
    response = await reader.readuntil(b"\r\n\r\n")
    return reader, writer, response


async def close_stream(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except (ssl.SSLError, ConnectionError):
        pass


def outcomes(handle: Chain) -> tuple[RelayOutcome, GatewayOutcome]:
    relay, gateway = handle.outcomes
    assert isinstance(relay, RelayOutcome) and isinstance(gateway, GatewayOutcome)
    assert not relay.cleanup_failed and not gateway.cleanup_failed
    assert relay.pending_tasks == gateway.pending_tasks == 0
    return relay, gateway


@pytest.mark.asyncio
async def test_invalid_certificate_fails_in_verifying_client_through_real_tunnel(
    tmp_path: Path,
) -> None:
    async with chain(tmp_path, [(CANDIDATE,)]) as handle:
        reader, writer, response = await connect_tunnel(handle.relay_address)
        assert response == HTTP_OK
        with pytest.raises(ssl.SSLCertVerificationError):
            await writer.start_tls(ssl.create_default_context(), server_hostname=HOST)
        await close_stream(writer)
        assert handle.connector.dials == [(CANDIDATE, 443)]
    outcomes(handle)


@pytest.mark.asyncio
async def test_trusted_fixture_round_trip_and_matching_byte_counters(tmp_path: Path) -> None:
    async with chain(tmp_path, [(CANDIDATE,)]) as handle:
        reader, writer, response = await connect_tunnel(handle.relay_address)
        assert response == HTTP_OK
        trusted = ssl.create_default_context(cafile=tmp_path / f"{HOST}.crt")
        await writer.start_tls(trusted, server_hostname=HOST)
        writer.write(GREETING)
        await writer.drain()
        assert await asyncio.wait_for(reader.read(1024), 5) == b"echo:" + GREETING
        await close_stream(writer)
        assert handle.resolutions == [HOST]
    relay, gateway = outcomes(handle)
    assert gateway.read_bytes > len(GREETING) and gateway.written_bytes > len(GREETING)
    assert relay.data_received == gateway.read_bytes  # Encrypted TCP payload, both ways.
    assert relay.data_emitted == gateway.written_bytes


@pytest.mark.asyncio
async def test_trusted_certificate_for_another_name_still_fails_hostname_check(
    tmp_path: Path,
) -> None:
    async with chain(tmp_path, [(CANDIDATE,)], cert_name="other.example") as handle:
        reader, writer, response = await connect_tunnel(handle.relay_address)
        assert response == HTTP_OK
        trusted = ssl.create_default_context(cafile=tmp_path / "other.example.crt")
        with pytest.raises(ssl.SSLCertVerificationError):
            await writer.start_tls(trusted, server_hostname=HOST)
        await close_stream(writer)
    outcomes(handle)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        (CANDIDATE, "10.0.0.1"),  # Mixed public/private rejects the whole admission.
        ("169.254.169.254",),  # Metadata.
        ("127.0.0.1",),
        ("::ffff:8.8.8.8",),  # IPv4-mapped IPv6.
        ("2001:4860:4860::8888",),  # Public IPv6: live gateway is IPv4-only.
    ],
)
async def test_forbidden_answers_refused_before_any_dial(
    tmp_path: Path, answer: tuple[str, ...]
) -> None:
    async with chain(tmp_path, [answer]) as handle:
        _, writer, response = await connect_tunnel(handle.relay_address)
        assert response == HTTP_REFUSED
        await close_stream(writer)
        assert handle.resolutions == [HOST]
        assert handle.connector.dials == []
    outcomes(handle)


@pytest.mark.asyncio
async def test_rebinding_second_admission_private_answer_never_dialed(tmp_path: Path) -> None:
    async with chain(tmp_path, [(CANDIDATE,), ("10.0.0.1",)]) as handle:
        _, first, response = await connect_tunnel(handle.relay_address)
        assert response == HTTP_OK
        await close_stream(first)
        _, second, response = await connect_tunnel(handle.relay_address)
        assert response == HTTP_REFUSED
        await close_stream(second)
        assert handle.resolutions == [HOST, HOST]  # Each admission resolves afresh.
        assert handle.connector.dials == [(CANDIDATE, 443)]
    outcomes(handle)


@pytest.mark.asyncio
async def test_unlisted_host_refused_without_resolution_or_dial(tmp_path: Path) -> None:
    async with chain(tmp_path, []) as handle:
        _, writer, response = await connect_tunnel(handle.relay_address, "unlisted.example")
        assert response == HTTP_REFUSED
        await close_stream(writer)
        assert handle.resolutions == [] and handle.connector.dials == []
    outcomes(handle)
