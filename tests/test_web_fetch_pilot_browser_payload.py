"""Pure E2b tests: entrypoint helpers, bundle builder, bootstrap functions.

No browser, process, container or descriptor operation runs here. Bootstrap
functions are extracted from the literal's AST (definitions only, never its
top-level statements) and executed against an in-memory fake fd 0.
"""

from __future__ import annotations

import ast
import json
import struct
import sys
import types
from pathlib import Path

import pytest

from scripts.web_fetch_pilot_browser_entrypoint import (
    CONTENT_LIMIT,
    EXIT_BROWSE,
    EXIT_OK,
    EXIT_SANDBOX,
    STAGES,
    EntrypointRefused,
    browse_exit,
    SANDBOX_REQUIRED,
    SYNTHETIC_HTML,
    RunConfig,
    classify,
    final_record,
    parse_run,
    request_allowed,
    sandbox_ok,
    status_ok,
    synthetic_ok,
    url_host,
)
from scripts.web_fetch_pilot_browser_payload import (
    BOOTSTRAP_SOURCE,
    BUNDLE_CAP,
    ENVIRONMENT,
    MODULE_NAMES,
    PYTHON,
    browser_command,
    make_bundle,
)
from scripts.web_fetch_pilot_container_policy import browser_create_arguments
from scripts.web_fetch_pilot_core import Frame, FrameType
from scripts.web_fetch_pilot_results import ResultCollector

ROOT = Path(__file__).resolve().parents[1]
RUN = {
    "original_url": "https://openai.com/index/example/",
    "allowed_hosts": ["openai.com"],
    "mode": "ordinary",
    "extraction_version": "pilot-e2-innertext-1",
    "deadline_seconds": 30.0,
}
SANDBOX_TEXT = (
    "Sandbox Status\nLayer 1 Sandbox\tNamespace\nPID namespaces\tYes\n"
    "Network namespaces\tYes\nSeccomp-BPF sandbox\tYes\n"
    "Seccomp-BPF sandbox supports TSYNC\tYes\n\nYou are adequately sandboxed."
)


def modules() -> dict[str, bytes]:
    return {name: (ROOT / "scripts" / name).read_bytes() for name in MODULE_NAMES}


def run_config() -> RunConfig:
    return parse_run(dict(RUN))


def collector_accepts(content: bytes, final: bytes) -> bytes:
    collector = ResultCollector(RUN["original_url"], ("openai.com",), RUN["extraction_version"])
    body = 16 * 1024 - 1
    for index in range(0, len(content), body):
        assert collector.observe(
            Frame(FrameType.RESULT, 0, b"\x01" + content[index : index + body])
        )
    assert collector.observe(Frame(FrameType.RESULT, 0, b"\x02" + final))
    collector.end_of_stream()
    return collector.candidate().content


def test_entrypoint_import_is_host_safe_without_playwright() -> None:
    assert "playwright" not in sys.modules and "crawl4ai" not in sys.modules


def test_parse_run_accepts_exact_trusted_values_only() -> None:
    assert run_config().allowed_hosts == frozenset({"openai.com"})
    for key, value in (
        ("original_url", "http://openai.com/"),
        ("original_url", "https://evil.example/"),
        ("original_url", "https://user@openai.com/"),
        ("original_url", "https://openai.com:8443/"),
        ("original_url", "https://openai.com:bad/"),
        ("allowed_hosts", []),
        ("allowed_hosts", ["OpenAI.com"]),
        ("allowed_hosts", "openai.com"),
        ("mode", "patchright"),
        ("extraction_version", " "),
        ("deadline_seconds", 30),
        ("deadline_seconds", 41.0),
        ("deadline_seconds", 0.5),
    ):
        run = dict(RUN)
        run[key] = value
        with pytest.raises(ValueError):
            parse_run(run)
    with pytest.raises(ValueError):
        parse_run({**RUN, "extra": 1})
    with pytest.raises(ValueError):
        parse_run([RUN])


def test_url_host_requires_plain_https() -> None:
    assert url_host("https://openai.com/a?b") == "openai.com"
    for bad in ("chrome-error://chromewebdata/", "about:blank", 7, "https://a:b@openai.com/"):
        assert url_host(bad) is None


def test_classification_never_attaches_content_to_failure() -> None:
    run = run_config()
    content, final = classify(run, True, "chrome-error://chromewebdata/", "x", "page")
    assert content == b"" and json.loads(final)["status"] == "error"
    assert json.loads(final)["final_url"] == RUN["original_url"]
    assert collector_accepts(content, final) == b""
    content, final = classify(run, False, "https://evil.example/", "t", "text")
    assert content == b"" and json.loads(final)["status"] == "blocked"
    assert collector_accepts(content, final) == b""
    content, final = classify(run, False, "https://openai.com/x", "t", " \n \n")
    assert content == b"" and json.loads(final)["status"] == "error"


def test_success_content_is_normalized_bounded_and_collector_valid() -> None:
    run = run_config()
    content, final = classify(run, False, "https://openai.com/x", "Title", "  a \n\n b  ")
    assert content == b"a\nb" and json.loads(final)["status"] == "success"
    assert collector_accepts(content, final) == b"a\nb"
    huge = "é" * CONTENT_LIMIT  # Two bytes each: truncation must split cleanly.
    content, final = classify(run, False, "https://openai.com/x", "T" * 9000, huge)
    assert len(content) <= CONTENT_LIMIT and content.decode("utf-8")
    assert len(json.loads(final)["title"]) == 512
    assert collector_accepts(content, final) == content


def test_final_record_schema_is_exact() -> None:
    record = json.loads(final_record(run_config(), "error", RUN["original_url"], ""))
    assert set(record) == {"status", "original_url", "final_url", "title", "extraction_version"}
    with pytest.raises(ValueError):
        final_record(run_config(), "ok", RUN["original_url"], "")


def test_sandbox_and_synthetic_checks_match_recorded_evidence() -> None:
    assert sandbox_ok(SANDBOX_TEXT)
    for required in SANDBOX_REQUIRED:
        assert not sandbox_ok(SANDBOX_TEXT.replace(required, ""))
    assert not sandbox_ok(SANDBOX_TEXT.replace("PID namespaces\tYes", "PID namespaces\tNo"))
    assert not sandbox_ok(None)
    assert "Offline fixture" in SYNTHETIC_HTML
    assert synthetic_ok("Offline fixture\n\nExpected synthetic article.", "Daemon offline reader")
    assert not synthetic_ok("Offline fixture", "Daemon offline reader")
    assert not synthetic_ok("Offline fixture\nExpected synthetic article.", "Other")


def test_bundle_is_length_prefixed_exact_and_bounded() -> None:
    bundle = make_bundle(modules(), dict(RUN))
    (length,) = struct.unpack(">I", bundle[:4])
    assert length == len(bundle) - 4 <= BUNDLE_CAP
    value = json.loads(bundle[4:])
    assert set(value) == {"modules", "run"} and value["run"] == RUN
    assert {name: text.encode() for name, text in value["modules"].items()} == modules()
    bad_sets = [
        {k: v for k, v in modules().items() if k != "web_fetch_pilot_relay.py"},
        {**modules(), "extra.py": b"x"},
        {**modules(), "web_fetch_pilot_io.py": b"\xff"},
        {**modules(), "web_fetch_pilot_io.py": b""},
        {**modules(), "web_fetch_pilot_io.py": b"x" * (128 * 1024 + 1)},
    ]
    for bad in bad_sets:
        with pytest.raises((ValueError, UnicodeDecodeError)):
            make_bundle(bad, dict(RUN))
    with pytest.raises(ValueError):
        make_bundle(modules(), {**RUN, "mode": "patchright"})


def test_browser_command_is_isolated_with_site_and_fits_browser_policy() -> None:
    command = browser_command()
    assert command[: 1 + len(ENVIRONMENT)] == ("-i", *ENVIRONMENT)
    assert command[1 + len(ENVIRONMENT) :] == (PYTHON, "-I", "-u", "-c", BOOTSTRAP_SOURCE)
    assert "-S" not in command  # Playwright lives in site-packages.
    name = "daemon-browser-offline-" + "d" * 24
    path = str(ROOT / "scripts/web_fetch_pilot_browser_seccomp.json")
    assert browser_create_arguments(name, command, path)[-len(command) :] == command
    with pytest.raises(ValueError):
        browser_command("python")


def bootstrap_functions(stdin: bytes) -> dict[str, object]:
    """Definitions only, with a fake ``os.read`` over an in-memory fd 0."""
    tree = ast.parse(BOOTSTRAP_SOURCE)
    keep: list[ast.stmt] = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"read_exact", "unique", "reject", "load"}
        or isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id in {"NAMES", "BUNDLE_CAP", "FILE_CAP"}
            for t in node.targets
        )
    ]
    buffer = bytearray(stdin)

    def fake_read(fd: int, size: int) -> bytes:
        assert fd == 0 and 0 < size <= 65536
        chunk = bytes(buffer[: min(size, 7)])  # Short reads exercise read_exact.
        del buffer[: len(chunk)]
        return chunk

    namespace: dict[str, object] = {
        "os": types.SimpleNamespace(read=fake_read),
        "json": json,
        "struct": struct,
    }
    exec(compile(ast.Module(body=keep, type_ignores=[]), "<bootstrap>", "exec"), namespace)
    namespace["remaining"] = buffer
    return namespace


def test_bootstrap_reads_exactly_one_bundle_and_leaves_frames_unread() -> None:
    frames = b"\x01\x00\x00\x00 frame bytes"
    namespace = bootstrap_functions(make_bundle(modules(), dict(RUN)) + frames)
    loaded_modules, run = namespace["load"]()  # type: ignore[operator]
    assert run == RUN
    assert {k: v.encode() for k, v in loaded_modules.items()} == modules()
    assert bytes(namespace["remaining"]) == frames  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "stdin",
    [
        b"",
        b"\x00\x00",
        struct.pack(">I", 0),
        struct.pack(">I", 600 * 1024),
        struct.pack(">I", 10) + b"short",
        struct.pack(">I", 2) + b"{}",
        struct.pack(">I", 23) + b'{"modules":{},"run":{}}',
        struct.pack(">I", 15) + b'{"a":1,"a":2} ',
        struct.pack(">I", 3) + b"NaN",
    ],
)
def test_bootstrap_refuses_malformed_bundles(stdin: bytes) -> None:
    namespace = bootstrap_functions(stdin)
    with pytest.raises((ValueError, struct.error, UnicodeDecodeError)):
        namespace["load"]()  # type: ignore[operator]


def test_bootstrap_never_writes_stdout_and_imports_entrypoint_statically() -> None:
    tree = ast.parse(BOOTSTRAP_SOURCE)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    names = {ast.unparse(call.func) for call in calls}
    assert "print" not in names and "sys.stdout.write" not in names
    assert all(
        ast.unparse(call.args[0]) == "2" for call in calls if ast.unparse(call.func) == "os.write"
    )
    reads = [call for call in calls if ast.unparse(call.func) == "os.read"]
    assert reads and all(ast.unparse(call.args[0]) == "0" for call in reads)
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.Import)]
    assert any(
        alias.name == "scripts.web_fetch_pilot_browser_entrypoint"
        for node in imports
        for alias in node.names
    )
    assert "sys.stdin" not in BOOTSTRAP_SOURCE and "sys.stdout = sys.stderr" in BOOTSTRAP_SOURCE


def test_browse_exit_is_zero_only_without_unexpected_failure() -> None:
    assert browse_exit(None) == EXIT_OK == 0
    assert browse_exit(EntrypointRefused(EXIT_SANDBOX)) == EXIT_SANDBOX == 4
    for failure in (RuntimeError("TargetClosedError"), TimeoutError(), OSError()):
        assert browse_exit(failure) == EXIT_BROWSE == 5
    assert STAGES[0] == "start" and "navigate" in STAGES
    source = (ROOT / "scripts/web_fetch_pilot_browser_entrypoint.py").read_text()
    for name in STAGES[1:]:
        assert f'stage[0] = "{name}"' in source  # Every stage is actually recorded.


def test_request_guard_allows_only_https_manifest_hosts_on_443() -> None:
    allowed = frozenset({"openai.com"})
    assert request_allowed("https://openai.com/index/x?y=1", allowed)
    assert request_allowed("https://openai.com:443/", allowed)
    for url in (
        "http://openai.com/",
        "https://cdn.openai.com/a.js",  # Subdomains are not implied.
        "https://evil.example/",
        "https://openai.com:8443/",
        "https://user@openai.com/",
        "wss://openai.com/socket",
        "data:text/html,x",
        "chrome-error://chromewebdata/",
        None,
    ):
        assert not request_allowed(url, allowed)


def test_browser_status_requires_seccomp_nnp_and_no_capabilities() -> None:
    good = "NoNewPrivs:\t1\nSeccomp:\t2\nCapEff:\t0000000000000000\n"
    assert status_ok(good)
    assert not status_ok(good.replace("Seccomp:\t2", "Seccomp:\t0"))
    assert not status_ok(good.replace("NoNewPrivs:\t1", "NoNewPrivs:\t0"))
    assert not status_ok(good.replace("0000000000000000", "0000000000000400"))
    source = (ROOT / "scripts/web_fetch_pilot_browser_entrypoint.py").read_text()
    assert "not status_ok(status)" in source and 'context.route("**/*", guard)' in source
