"""Browser-container entrypoint for the offline pilot; never run on the host.

Staged into the read-only, network-none browser container by the reviewed
bootstrap in ``scripts/web_fetch_pilot_browser_payload.py`` and imported there
under this exact module name. Playwright is imported lazily inside the
container; host tests exercise only the pure helpers here.

One run, one browser: framed IPC owns duplicated stdin/stdout descriptors and
fd 1 is redirected to stderr, so no child or stray print can corrupt frames.
A local relay listens only on ``127.0.0.1``; Chromium uses it as an explicit
proxy with implicit loopback bypass removed, its sandbox on, a fresh context,
service workers and downloads disabled and HTTPS verification intact. The
entrypoint checks isolation, the ``chrome://sandbox`` status and a synthetic
extraction BEFORE navigating the single trusted URL, then hands one bounded
RESULT to ``Relay.finish_result``. Diagnostics go to stderr only, bounded; the
supervisor never interprets them. Exit codes are the supervisor's signal:

- 0: RESULT committed after every check passed (not article success).
- 1: RESULT not committed or relay cleanup failed.
- 3: isolation refused; 4: sandbox/synthetic check refused (no navigation).
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import sys
from dataclasses import dataclass
from urllib.parse import urlsplit

EXECUTABLE = "/home/appuser/.cache/ms-playwright/chromium-1200/chrome-linux64/chrome"
MODES = ("ordinary", "basic-stealth")
RUN_KEYS = frozenset(
    {"original_url", "allowed_hosts", "mode", "extraction_version", "deadline_seconds"}
)
MAX_DEADLINE = 40.0  # Inside the 45-second run; leaves room for RESULT and teardown.
CONTENT_LIMIT = 1_000_000  # Bytes; with chunk tags and final, under the 1 MiB quota.
TITLE_LIMIT = 512
DIAGNOSTIC_LIMIT = 8192
EXIT_OK, EXIT_RESULT, EXIT_ISOLATION, EXIT_SANDBOX = 0, 1, 3, 4
SYNTHETIC_HTML = (
    "<html><head><title>Daemon offline reader</title></head><body><article>"
    "<h1>Offline fixture</h1><p>Expected synthetic article.</p></article></body></html>"
)
SYNTHETIC_LINES = ("Offline fixture", "Expected synthetic article.")
SANDBOX_REQUIRED = (
    "Layer 1 Sandbox\tNamespace",
    "PID namespaces\tYes",
    "Network namespaces\tYes",
    "Seccomp-BPF sandbox\tYes",
    "You are adequately sandboxed.",
)
EXTRACT_JS = """() => {
  const node = document.querySelector('article') || document.querySelector('main')
    || document.body;
  return node ? String(node.innerText).slice(0, 1100000) : '';
}"""


class EntrypointRefused(RuntimeError):
    def __init__(self, code: int) -> None:
        super().__init__(f"entrypoint refused with exit {code}")
        self.code = code


@dataclass(frozen=True)
class RunConfig:
    original_url: str
    allowed_hosts: frozenset[str]
    mode: str
    extraction_version: str
    deadline_seconds: float


def url_host(url: object) -> str | None:
    if type(url) is not str or len(url) > 2048:
        return None
    try:
        parts = urlsplit(url)
        port = parts.port  # Raises ValueError for a malformed port.
    except ValueError:
        return None
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443):
        return None
    return parts.hostname


def parse_run(raw: object) -> RunConfig:
    """Strict trusted run values from the supervisor's bundle."""
    if type(raw) is not dict or set(raw) != RUN_KEYS:
        raise ValueError("run configuration refused")
    hosts = raw["allowed_hosts"]
    if type(hosts) is not list or not 0 < len(hosts) <= 40:
        raise ValueError("run configuration refused")
    if any(type(host) is not str or not host or host != host.lower() for host in hosts):
        raise ValueError("run configuration refused")
    allowed = frozenset(hosts)
    if url_host(raw["original_url"]) not in allowed:
        raise ValueError("run configuration refused")
    if raw["mode"] not in MODES:
        raise ValueError("run configuration refused")
    version = raw["extraction_version"]
    if type(version) is not str or not version.strip() or len(version) > 128:
        raise ValueError("run configuration refused")
    deadline = raw["deadline_seconds"]
    if type(deadline) is not float or not 1.0 <= deadline <= MAX_DEADLINE:
        raise ValueError("run configuration refused")
    return RunConfig(raw["original_url"], allowed, raw["mode"], version, deadline)


def final_record(run: RunConfig, status: str, final_url: str, title: str) -> bytes:
    """Exact collector schema; bounded title; never error text."""
    if status not in ("success", "blocked", "error"):
        raise ValueError("unknown status")
    record = {
        "status": status,
        "original_url": run.original_url,
        "final_url": final_url,
        "title": title[:TITLE_LIMIT],
        "extraction_version": run.extraction_version,
    }
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 4096:
        raise ValueError("final record too large")
    return encoded


def classify(
    run: RunConfig, failed: bool, final_url: str, title: str, text: str
) -> tuple[bytes, bytes]:
    """Pure (content, final) decision; failure never carries content."""
    if failed:
        return b"", final_record(run, "error", run.original_url, "")
    if url_host(final_url) not in run.allowed_hosts:
        return b"", final_record(run, "blocked", run.original_url, "")
    body = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if not body:
        return b"", final_record(run, "error", final_url, "")
    content = body.encode("utf-8")[:CONTENT_LIMIT].decode("utf-8", "ignore").encode("utf-8")
    return content, final_record(run, "success", final_url, title)


def sandbox_ok(text: object) -> bool:
    if type(text) is not str:
        return False
    lines = {line.strip("\r") for line in text.splitlines()}
    return all(required in lines for required in SANDBOX_REQUIRED)


def synthetic_ok(text: object, title: object) -> bool:
    if type(text) is not str or title != "Daemon offline reader":
        return False
    return tuple(line.strip() for line in text.splitlines() if line.strip()) == SYNTHETIC_LINES


def diagnostic(record: dict[str, object]) -> None:
    """Bounded stderr line; never protocol output, never parsed by the supervisor."""
    line = json.dumps(record, sort_keys=True)[:DIAGNOSTIC_LIMIT]
    os.write(2, line.encode("utf-8", "replace") + b"\n")


def isolation_check() -> None:
    interfaces = sorted(os.listdir("/sys/class/net"))
    with open("/proc/net/route", "rb") as handle:
        routes = [row for row in handle.read(65536).split(b"\n")[1:] if row]
    if os.getuid() == 0 or interfaces != ["lo"] or routes:
        raise EntrypointRefused(EXIT_ISOLATION)


def own_frame_descriptors() -> tuple[int, int]:
    """Duplicate stdin/stdout for framed IPC; point fds 0/1 away from frames.

    ``os.dup`` returns non-inheritable descriptors, so no child can hold the
    frame stream; fd 1 becomes stderr and fd 0 becomes /dev/null.
    """
    frame_in, frame_out = os.dup(0), os.dup(1)
    null = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.dup2(null, 0)
        os.dup2(2, 1)
    finally:
        os.close(null)
    sys.stdout = sys.stderr
    return frame_in, frame_out


async def _browse(run: RunConfig, proxy_port: int, deadline: float) -> tuple[bytes, bytes]:
    # Container-only dependency (image site-packages), loaded by fixed name.
    async_playwright = importlib.import_module("playwright.async_api").async_playwright
    loop = asyncio.get_running_loop()
    async with async_playwright() as runtime:
        browser = await runtime.chromium.launch(
            executable_path=EXECUTABLE,
            headless=True,
            chromium_sandbox=True,
            proxy={"server": f"http://127.0.0.1:{proxy_port}", "bypass": "<-loopback>"},
            timeout=10000,
        )
        try:
            context = await browser.new_context(
                ignore_https_errors=False, accept_downloads=False, service_workers="block"
            )
            try:
                page = await context.new_page()
                if run.mode == "basic-stealth":
                    adapter = importlib.import_module("crawl4ai.browser_adapter").StealthAdapter()
                    if not adapter._stealth_available:
                        raise EntrypointRefused(EXIT_SANDBOX)
                    await adapter.apply_stealth(page)
                await page.goto("about:blank", timeout=5000)
                await page.set_content(SYNTHETIC_HTML, timeout=5000)
                synthetic = synthetic_ok(
                    await page.locator("article").inner_text(timeout=5000), await page.title()
                )
                await page.goto("chrome://sandbox", timeout=5000)
                sandbox = sandbox_ok(await page.locator("body").inner_text(timeout=5000))
                diagnostic({"mode": run.mode, "synthetic_ok": synthetic, "sandbox_ok": sandbox})
                if not (synthetic and sandbox):
                    raise EntrypointRefused(EXIT_SANDBOX)
                remaining_ms = max(1.0, (deadline - loop.time()) * 1000)
                try:
                    await page.goto(
                        run.original_url, wait_until="domcontentloaded", timeout=remaining_ms
                    )
                except Exception as exc:  # Category only; never error text in RESULT.
                    diagnostic({"navigation": type(exc).__name__})
                    return classify(run, True, run.original_url, "", "")
                title = await page.title()
                text = await page.evaluate(EXTRACT_JS)
                return classify(run, False, page.url, str(title), str(text))
            finally:
                await context.close()
        finally:
            await browser.close()


async def main(raw_run: object) -> int:
    """One bounded run; returns the process exit code (see module docstring)."""
    from scripts.web_fetch_pilot_acceptor import LoopbackAcceptor
    from scripts.web_fetch_pilot_io import AsyncFD, FDFrameIO
    from scripts.web_fetch_pilot_relay import Relay, RelayStatus

    run = parse_run(raw_run)
    frame_in, frame_out = own_frame_descriptors()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + run.deadline_seconds
    try:
        isolation_check()
    except EntrypointRefused as refused:
        diagnostic({"refused": "isolation"})
        return refused.code
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    acceptor = LoopbackAcceptor(listener)
    relay = Relay(
        FDFrameIO(AsyncFD(frame_in), AsyncFD(frame_out)),
        acceptor,
        deadline=run.deadline_seconds + 4.0,
    )
    relay_task = asyncio.create_task(relay.run())
    code = EXIT_OK
    try:
        async with asyncio.timeout_at(deadline):
            content, final = await _browse(run, acceptor.address[1], deadline)
    except EntrypointRefused as refused:
        code = refused.code
        content, final = b"", final_record(run, "error", run.original_url, "")
    except Exception as exc:
        diagnostic({"browse": type(exc).__name__})
        content, final = b"", final_record(run, "error", run.original_url, "")
    try:
        await relay.finish_result(content, final)
    except Exception as exc:
        diagnostic({"finish_result": type(exc).__name__})
    outcome = await relay_task
    diagnostic(
        {
            "relay_status": outcome.status.value,
            "relay_reason": outcome.reason.value,
            "opens": outcome.submitted_opens,
            "cleanup_failed": outcome.cleanup_failed,
        }
    )
    if outcome.status is not RelayStatus.RESULT_COMMITTED or outcome.cleanup_failed:
        return EXIT_RESULT
    return code
