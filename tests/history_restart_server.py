"""Exec-child loopback server. Control/receipts are never exposed through HTTP."""

import asyncio
import json
from pathlib import Path
import socket
import sys
import time
from types import SimpleNamespace

from redis.asyncio import Redis
import uvicorn

from orchestrator.services.restore_history.model import HistoryError
from tests.history_ui_support import actual_auth_app


async def serve(host, execution, settings, pool, packet, patch, channel, release, entered, calls):
    loop = asyncio.get_running_loop()
    current = asyncio.current_task()
    assert current is not None
    redis = Redis(unix_socket_path=packet["redis"], decode_responses=True, socket_timeout=2)
    listener = socket.socket(fileno=packet["listener_fd"])
    listener.set_inheritable(False)
    assert listener.getsockname()[0] == "127.0.0.1"
    local = SimpleNamespace(
        settings=settings, host=host, execution=execution, w=pool, path=Path(packet["path"])
    )
    stream = asyncio.StreamReader()
    pipe, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(stream), sys.stdin.buffer
    )

    def lost_parent():
        loop.remove_reader(channel)
        host._closing = execution._closing = True
        execution.block()
        current.cancel()

    loop.add_reader(channel, lost_parent)
    serving = server = None
    try:
        assert (await redis.info("server"))["run_id"] == packet["redis_run"]
        app = await actual_auth_app(local, patch, redis, packet["origin"], initialize_setup=False)
        server = uvicorn.Server(
            uvicorn.Config(app.app, log_level="warning", access_log=False, lifespan="off")
        )
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        async with asyncio.timeout(10):
            while not server.started:
                if serving.done():
                    serving.result()
                await asyncio.sleep(0.01)
        print(json.dumps({"stage": "serving"}), flush=True)
        while True:
            command = await stream.readline()
            if command == b"entered\n":
                print(
                    json.dumps({"task": await asyncio.wait_for(asyncio.shield(entered), 15)}),
                    flush=True,
                )
            elif command in (b"release\n", b"joined\n"):
                if command == b"release\n":
                    release.set()
                result = await asyncio.gather(*execution._jobs)
                print(json.dumps({"result": result, "dispatches": calls()}), flush=True)
            elif command == b"retire\n":
                try:
                    await host.retire_original(deadline=time.monotonic() + 8)
                except HistoryError as error:
                    print(json.dumps({"refused": error.code}), flush=True)
                    if host._closing:
                        return False
                else:
                    return True
            elif command == b"close\n":
                return False
            else:
                raise RuntimeError("launcher channel unavailable")
    finally:
        loop.remove_reader(channel)
        pipe.close()
        if server is not None:
            server.should_exit = True
        try:
            if serving is not None:
                await asyncio.wait_for(serving, 10)
        finally:
            listener.close()
            await redis.aclose()
