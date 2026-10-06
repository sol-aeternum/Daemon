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
    # The assistant row is written through ``message_writer``, which is the
    # memory store unless a durable task routes it through its lease fence.
    assert re.search(
        r"message_writer\s*=\s*message_sink\s+if\s+message_sink\s+is\s+not\s+None"
        r"\s+else\s+memory_store",
        text,
    ), "daemon.py must default the assistant-row writer to memory_store"
    pattern = re.compile(
        r"await\s+(?:memory_store|message_writer)\.update_message\("
        r"(?!\s*update_message_metadata)"
        r"[^)]*?\bcontent\s*=",
        re.DOTALL,
    )
    match = pattern.search(text)
    assert match, (
        "orchestrator/daemon.py must call MemoryStore.update_message(content=...) "
        "for incremental content persistence (issue #18)."
    )


def test_daemon_persists_accumulated_partial_content():
    text = DAEMON_PY.read_text(encoding="utf-8", errors="ignore")
    incremental_block = re.search(
        r"# Periodic persistence of incremental content(?P<block>.*?)_last_persist_s = current_time",
        text,
        re.DOTALL,
    )
    assert incremental_block, "Could not find the incremental persistence block in daemon.py."

    block = incremental_block.group("block")
    assert 'content="".join(final_text_parts)' in block, (
        "Incremental persistence must write the accumulated streamed response, "
        "not only the latest delta_text chunk."
    )
    assert "content=delta_text" not in block, (
        "MemoryStore.update_message(content=...) replaces content, so persisting only "
        "delta_text would leave interrupted streams with just the last persisted chunk."
    )
