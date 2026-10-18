"""Fixture-only exclusive socket/control wrapper, outside all production middleware."""

import asyncio
import importlib
import json
import os
import re
import socket
import sys

import uvicorn


async def main():
    task, nonce = sys.argv[1:]
    if not re.fullmatch(r"[a-f0-9]{32}", nonce) or not re.fullmatch(r"[\w.-]{1,100}", task):
        raise RuntimeError("Missing fixture task/run identity")
    identity = {"task": task, "nonce": nonce}
    expected = {**identity, "role": "api", "host": "127.0.0.1", "port": 3143}
    listener = socket.socket()
    listener.bind(("127.0.0.1", 3143))
    listener.listen(128)
    armed = False
    stopping = False
    receipts = os.fdopen(3, "w", buffering=1)
    # This module sets the clean environment before importing production code.
    inner = importlib.import_module("cache-fixture-api").app

    async def gated(scope, receive, send):
        if scope["type"] == "lifespan":
            return await inner(scope, receive, send)
        if scope["type"] == "http":
            is_identity = scope["method"] == "GET" and scope["path"] == "/__fixture/identity"
            if is_identity or not armed or stopping:
                body = json.dumps(expected).encode() if is_identity else b"Fixture not armed"
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200 if is_identity else 503,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"cache-control", b"no-store"),
                        ],
                    }
                )
                return await send({"type": "http.response.body", "body": body})
        return await inner(scope, receive, send)

    server = uvicorn.Server(uvicorn.Config(gated, lifespan="off", log_level="warning"))

    async def commands():
        nonlocal armed, stopping
        reader = asyncio.StreamReader(limit=4096)
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin)
        try:
            while True:
                line = await reader.readline()
                if not line or len(line) > 4096:
                    break
                frame = json.loads(line)
                event = frame.pop("event")
                if frame != identity:
                    raise RuntimeError("Fixture command identity mismatch")
                if event == "shutdown":
                    break
                if event != "arm" or armed:
                    raise RuntimeError("Fixture command phase mismatch")
                armed = True
                receipts.write(json.dumps({**expected, "event": "armed"}) + "\n")
        finally:
            armed = False
            stopping = True
            server.should_exit = True

    command_task = asyncio.create_task(commands())
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        while not server.started:
            if serving.done() or command_task.done():
                raise RuntimeError("API fixture stopped before bind receipt")
            await asyncio.sleep(0.01)
        if listener.getsockname() != (expected["host"], expected["port"]):
            raise RuntimeError("Unexpected API listening address")
        receipts.write(json.dumps({**expected, "event": "bound"}) + "\n")
        done, _ = await asyncio.wait([serving, command_task], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=8)
    finally:
        command_task.cancel()
        await asyncio.gather(command_task, return_exceptions=True)
        listener.close()
        receipts.close()


if __name__ == "__main__":
    asyncio.run(main())
