"""Owned ephemeral-loopback fixtures; review before first execution.

Only listeners/clients created by these fixtures are used. No public addresses,
DNS, existing-service probes, processes, browsers or containers are involved.
"""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from scripts.web_fetch_pilot_acceptor import LoopbackAcceptor
from scripts.web_fetch_pilot_io import SocketConnection


@contextmanager
def listener() -> Iterator[LoopbackAcceptor]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(4)
        acceptor = LoopbackAcceptor(sock)
        try:
            yield acceptor
        finally:
            acceptor.close()


async def retrieve(task: asyncio.Task[SocketConnection | None]) -> None:
    if not task.done():
        task.cancel()
    results = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)
    # An accept may have completed before a connect/assertion failed, without the
    # caller yet assigning its result. Retrieve and close that transferred socket.
    for result in results:
        if isinstance(result, SocketConnection):
            result.close()


@pytest.mark.asyncio
async def test_real_acceptor_peer_io_and_transferred_socket_lifetime() -> None:
    baseline = set(os.listdir("/proc/self/fd"))
    tasks = set(asyncio.all_tasks())
    loop = asyncio.get_running_loop()
    with listener() as acceptor, socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.setblocking(False)
        accepting = asyncio.create_task(acceptor.accept())
        connection = None
        try:
            await asyncio.wait_for(loop.sock_connect(client, acceptor.address), 1)
            connection = await asyncio.wait_for(accepting, 1)
            assert connection is not None
            assert connection.peer_ip == "127.0.0.1"
            assert connection.getpeername() == client.getsockname()
            acceptor.close()  # The transferred socket must remain independently owned.
            assert await connection.write(b"fixture") == 7
            assert await asyncio.wait_for(loop.sock_recv(client, 7), 1) == b"fixture"
            await loop.sock_sendall(client, b"reply")
            assert await asyncio.wait_for(connection.read(5), 1) == b"reply"
            await connection.shutdown_write()
            assert await asyncio.wait_for(loop.sock_recv(client, 1), 1) == b""
        finally:
            await retrieve(accepting)
            if connection is not None:
                connection.close()
    await asyncio.sleep(0)
    assert set(os.listdir("/proc/self/fd")) == baseline
    assert set(asyncio.all_tasks()) == tasks


@pytest.mark.asyncio
async def test_real_acceptor_close_wakes_idle_wait_and_releases_listener() -> None:
    baseline = set(os.listdir("/proc/self/fd"))
    tasks = set(asyncio.all_tasks())
    with listener() as acceptor:
        accepting = asyncio.create_task(acceptor.accept())
        try:
            await asyncio.sleep(0)
            assert acceptor._accepting
            acceptor.close()
            assert await asyncio.wait_for(accepting, 1) is None
            assert acceptor._listener.fileno() == -1
            assert await acceptor.accept() is None
        finally:
            await retrieve(accepting)
    await asyncio.sleep(0)
    assert set(os.listdir("/proc/self/fd")) == baseline
    assert set(asyncio.all_tasks()) == tasks


@pytest.mark.asyncio
async def test_real_acceptor_cancel_unregisters_and_next_accept_still_works() -> None:
    baseline = set(os.listdir("/proc/self/fd"))
    tasks = set(asyncio.all_tasks())
    loop = asyncio.get_running_loop()
    with listener() as acceptor, socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        first = asyncio.create_task(acceptor.accept())
        second = None
        connection = None
        try:
            await asyncio.sleep(0)
            assert acceptor._accepting
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert not acceptor._accepting
            assert acceptor._listener.fileno() >= 0
            client.setblocking(False)
            second = asyncio.create_task(acceptor.accept())
            await asyncio.wait_for(loop.sock_connect(client, acceptor.address), 1)
            connection = await asyncio.wait_for(second, 1)
            assert connection is not None and connection.getpeername() == client.getsockname()
        finally:
            await retrieve(first)
            if second is not None:
                await retrieve(second)
            if connection is not None:
                connection.close()
    await asyncio.sleep(0)
    assert set(os.listdir("/proc/self/fd")) == baseline
    assert set(asyncio.all_tasks()) == tasks
