"""Exercise installed frameworks through the supported launch preparation."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

SECRET = "PRIVATE-RUNTIME-CONTENT-ACCOUNT-TOKEN"


def child(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=45, check=False
    )


def assert_safe(result: subprocess.CompletedProcess[str]) -> list[dict]:
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert SECRET not in result.stderr
    assert "Traceback" not in result.stderr
    return [json.loads(line) for line in result.stderr.splitlines()]


@pytest.mark.parametrize("interrupt", [False, True])
def test_supported_backend_launch_real_access_and_exception_output(interrupt: bool):
    result = child(f"""
import asyncio, http.client, os, signal, sys, types
from orchestrator import runtime
secret = {SECRET!r}
original_import = runtime.importlib.import_module
def importing(name, *args, **kwargs):
    module = original_import(name, *args, **kwargs)
    if name == 'uvicorn':
        old_config, old_server = module.Config, module.Server
        app_module = types.ModuleType('orchestrator.main')
        async def app(scope, receive, send):
            if scope['type'] == 'lifespan':
                while True:
                    message = await receive()
                    if message['type'] == 'lifespan.startup':
                        await send({{'type': 'lifespan.startup.complete'}})
                    else:
                        await send({{'type': 'lifespan.shutdown.complete'}})
                        return
            raise RuntimeError(secret)
        app_module.app = app
        sys.modules['orchestrator.main'] = app_module
        def config(*args, **kwargs):
            kwargs.update(host='127.0.0.1', port=0)
            return old_config(*args, **kwargs)
        class Server(old_server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                port = self.servers[0].sockets[0].getsockname()[1]
                async def exercise():
                    def request():
                        conn = http.client.HTTPConnection('127.0.0.1', port)
                        conn.request('GET', '/' + secret + '?token=' + secret, headers={{'Authorization': secret}})
                        response = conn.getresponse()
                        assert response.status == 500
                        response.read()
                        conn.close()
                    await asyncio.to_thread(request)
                    if {interrupt!r}:
                        os.kill(os.getpid(), signal.SIGINT)
                    else:
                        self.should_exit = True
                asyncio.create_task(exercise())
        module.Config, module.Server = config, Server
    return module
runtime.importlib.import_module = importing
runtime.launch('backend')
""")
    if interrupt:
        assert result.returncode == -2
        assert result.stdout == ""
        assert SECRET not in result.stderr
        assert "Traceback" not in result.stderr
        output = [json.loads(line) for line in result.stderr.splitlines()]
    else:
        output = assert_safe(result)
    assert output[0]["event"] == "service_start"
    assert any(item["event"] == "legacy_log" and item["level"] == "ERROR" for item in output)
    assert output[-1]["event"] == ("failure" if interrupt else "service_stop")


@pytest.mark.parametrize("fails", [False, True])
def test_supported_worker_prepare_real_arq_argument_result_and_failure_logs(fails: bool):
    result = child(f"""
import asyncio, logging, sys
from unittest.mock import AsyncMock
from orchestrator import runtime
secret = {SECRET!r}
original_import = runtime.importlib.import_module
def importing(name, *args, **kwargs):
    module = original_import(name, *args, **kwargs)
    if name == 'orchestrator.worker.worker':
        from arq.worker import func
        from arq.jobs import serialize_job, deserialize_result
        from arq.utils import timestamp_ms
        worker = module.worker
        async def sample(ctx, content):
            assert content == secret
            if {fails!r}:
                error = RuntimeError(secret)
                error.extra = {{'content': secret}}
                raise error
            return {{'content': secret}}
        worker.functions['sample'] = func(sample)
        payload = serialize_job('sample', (secret,), {{}}, None, timestamp_ms())
        class Pipe:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def get(self, *args): pass
            def incr(self, *args): pass
            def expire(self, *args): pass
            def zrem(self, *args): pass
            async def execute(self):
                return [payload, 1, None, False] if worker.allow_abort_jobs else [payload, 1, None]
        class Redis:
            def pipeline(self, **kwargs): return Pipe()
        worker._pool = Redis()
        worker.finish_job = AsyncMock()
        worker.after_job_end = None
        def run():
            worker.loop.run_until_complete(worker.run_job(secret, timestamp_ms()))
            assert worker.jobs_failed == int({fails!r})
            assert worker.jobs_complete == int(not {fails!r})
            worker.finish_job.assert_awaited_once()
            # The output boundary must not rewrite the stored audit/result payload.
            data = worker.finish_job.call_args.args[2]
            if data is not None:
                result = deserialize_result(data)
                assert result.success == (not {fails!r})
                assert secret in str(result.result)
            worker.loop.close()
        module.main = run
    return module
runtime.importlib.import_module = importing
runtime.launch('worker')
""")
    output = assert_safe(result)
    starts = [i for i, item in enumerate(output) if item["event"] == "service_start"]
    assert len(starts) == 1
    assert all(item["event"] == "legacy_log" for item in output[: starts[0]])
    assert any(item["event"] == "legacy_log" for item in output)
    if fails:
        assert any(item.get("level") == "ERROR" for item in output)
    assert output[-1]["event"] == "service_stop"


def test_installed_litellm_handlers_rehomed_and_routing_reconfigure_safe():
    result = child(f"""
import logging, sys
from orchestrator import runtime, safe_logging
with runtime.initialization(sys.stderr):
    import litellm
    from orchestrator import routing_log
safe_logging.configure(sys.stderr, 'DEBUG')
for name in ('LiteLLM', 'LiteLLM Router', 'LiteLLM Proxy'):
    logger = logging.getLogger(name)
    assert not logger.handlers and logger.propagate
    logger.setLevel(logging.DEBUG)
    logger.debug({SECRET!r})
routing_log._configure()
routing_log._configure()
owned = [h for h in routing_log.logger.handlers if getattr(h, '_daemon_routing', False)]
assert len(owned) == 1 and isinstance(owned[0], safe_logging.SafeHandler)
assert owned[0].stream is sys.stderr
routing_log.logger.warning({SECRET!r})
""")
    output = assert_safe(result)
    assert len(output) == 4
    assert all(item["event"] == "legacy_log" for item in output)


def test_asyncio_failure_handler_does_not_render_context_or_crash_process():
    result = child(f"""
import asyncio, sys
from orchestrator import safe_logging
safe_logging.configure(sys.stderr)
async def run():
    loop = asyncio.get_running_loop()
    safe_logging.protect_loop(loop)
    loop.call_exception_handler({{'message': {SECRET!r}, 'exception': RuntimeError({SECRET!r})}})
    safe_logging.event('service_stop', role='worker')
asyncio.run(run())
""")
    output = assert_safe(result)
    assert [item["event"] for item in output] == ["failure", "service_stop"]


@pytest.mark.parametrize("role", ["backend", "worker"])
def test_interrupt_exit_status_is_preserved_without_traceback(role: str):
    code = f"""
from orchestrator import runtime
def prepare(role, stream):
    def run():
        raise KeyboardInterrupt({SECRET!r})
    return run, 'INFO'
runtime.launch({role!r}, prepare)
"""
    result = child(code)
    baseline = child("raise KeyboardInterrupt('baseline')")
    assert result.returncode == baseline.returncode == -2
    assert result.stdout == ""
    assert SECRET not in result.stderr
    assert "Traceback" not in result.stderr
    assert json.loads(result.stderr.splitlines()[-1])["event"] == "failure"
