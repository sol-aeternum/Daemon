"""Real collision refusal: own only the synthetic listener created in this test."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import subprocess
from threading import Thread
from typing import Any

import pytest


@pytest.mark.parametrize("port", [3142, 3143, 3141])
def test_foreign_cache_listener_never_receives_warmup_or_cleanup(port):
    calls = []

    class Foreign(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.command)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"nonce":"foreign"}')

        def do_POST(self):
            calls.append(self.command)
            self.send_response(204)
            self.end_headers()

        def log_message(self, format: str, *args: Any):
            pass

    # Actual exclusive bind: collision with an existing listener is an error,
    # never a skip, adoption, probe or attempt to terminate another producer.
    foreign = HTTPServer(("127.0.0.1", port), Foreign, bind_and_activate=False)
    foreign.allow_reuse_address = False
    foreign.server_bind()
    foreign.server_activate()
    thread = Thread(target=foreign.serve_forever, daemon=True)
    thread.start()
    root = Path(__file__).resolve().parents[1]
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "TMPDIR", "UV_PROJECT_ENVIRONMENT")
        if key in os.environ
    }
    try:
        result = subprocess.run(
            ["node", "e2e/cache-fixture-setup.mjs"],
            cwd=root / "frontend",
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert result.returncode != 0, result.stdout + result.stderr
        assert "EADDRINUSE" in result.stderr or "Address already in use" in result.stderr
        assert calls == []  # Even a successful status is never used as ownership.
        assert thread.is_alive()
        assert foreign.socket.getsockname() == ("127.0.0.1", port)
    finally:
        foreign.shutdown()
        foreign.server_close()
        thread.join(timeout=2)
