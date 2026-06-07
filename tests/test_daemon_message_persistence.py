from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DAEMON_PY = REPO_ROOT / "orchestrator" / "daemon.py"


def test_daemon_does_not_call_nonexistent_update_message_content():
    text = DAEMON_PY.read_text(encoding="utf-8", errors="ignore")
    assert "update_message_content" not in text, (
        "orchestrator/daemon.py still references the non-existent method "
        "update_message_content. Use MemoryStore.update_message(content=...) instead."
    )


def test_daemon_calls_update_message_with_content_kwarg():
    text = DAEMON_PY.read_text(encoding="utf-8", errors="ignore")
    pattern = re.compile(
        r"await\s+memory_store\.update_message\("
        r"(?!\s*update_message_metadata)"
        r"[^)]*?\bcontent\s*=",
        re.DOTALL,
    )
    match = pattern.search(text)
    assert match, (
        "orchestrator/daemon.py must call MemoryStore.update_message(content=...) "
        "for incremental content persistence (issue #18)."
    )
