"""Database proofs run only inside the restart drill's disposable project."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from typing import Any, cast

from scripts.durable_restart_fixture import ANSWERS, CONTROL, validate_isolation


def require(condition: Any, explanation: str) -> None:
    if not condition:
        raise RuntimeError(explanation)


async def check(command: str, argument: str | None) -> None:
    validate_isolation()
    os.environ["LITELLM_MODE"] = "PRODUCTION"
    import asyncpg

    from orchestrator.config import get_settings
    from orchestrator.database_url import resolve_database_url
    from orchestrator.memory.encryption import ContentEncryption
    from orchestrator.memory.store import MemoryStore
    from orchestrator.services.identity.session_issuance import (
        IssueSessionRequest,
        issue_device_session,
    )
    from orchestrator.tasks.store import LeaseLost, TaskStore

    settings = get_settings()
    pool = await asyncpg.create_pool(resolve_database_url(), min_size=1, max_size=2)
    if pool is None:
        raise RuntimeError("could not open disposable database")
    encryption = ContentEncryption(settings.daemon_encryption_key)
    try:
        if command == "session":
            rows = await pool.fetch(
                "SELECT DISTINCT user_id, tenant_id FROM sessions WHERE revoked_at IS NULL"
            )
            require(len(rows) == 1, "expected exactly the synthetic setup owner")
            async with pool.acquire() as conn, conn.transaction():
                issued = await issue_device_session(
                    cast(Any, conn),
                    IssueSessionRequest(
                        user_id=rows[0]["user_id"],
                        tenant_id=rows[0]["tenant_id"],
                        client_kind="native",
                        device_persistence="temporary",
                        device_name="Synthetic second drill client",
                    ),
                )
            print(issued.access_token)
            return
        task_id = uuid.UUID(str(argument))
        if command == "expire":
            # SQL clock, only after the script has stopped this worker.
            await pool.execute(
                "UPDATE tasks SET lease_expires_at = clock_timestamp() - interval '1 second', last_wake_at = NULL WHERE id = $1",
                task_id,
            )
            return
        task = await pool.fetchrow("SELECT * FROM tasks WHERE id = $1", task_id)
        require(task is not None, "accepted task missing")
        require(
            await pool.fetchval(
                "SELECT count(*) FROM tasks WHERE user_id = $1 AND idempotency_key = $2",
                task["user_id"],
                task["idempotency_key"],
            )
            == 1,
            "same key accepted more than once",
        )
        attempts = await pool.fetch(
            "SELECT * FROM task_attempts WHERE task_id = $1 ORDER BY epoch", task_id
        )
        if command == "B":
            require(
                [row["outcome"] for row in attempts] == ["lost", "completed"],
                "B must be lost then completed",
            )
            require(
                encryption.decrypt(attempts[0]["partial_ciphertext"]) == ANSWERS["B"][:8],
                "lost attempt did not preserve committed partial output",
            )
            message = await pool.fetchrow(
                "SELECT content, metadata FROM messages WHERE id = $1", task["result_message_id"]
            )
            require(
                encryption.decrypt(message["content"]) == ANSWERS["B"],
                "recovered content differs from deterministic committed answer",
            )
            metadata = (
                json.loads(message["metadata"])
                if isinstance(message["metadata"], str)
                else message["metadata"]
            )
            require(
                metadata.get("regenerated_after_interruption") == 1, "interruption notice missing"
            )
            holds = await pool.fetch(
                "SELECT r.*, a.epoch FROM entitlement_reservations r JOIN task_attempts a ON a.compute_scope_id = r.scope_id WHERE a.task_id = $1 ORDER BY a.epoch",
                task_id,
            )
            require(
                len(holds) == 2 and all(row["status"] == "settled" for row in holds),
                "both dispatched attempts must have settled reservations",
            )
            require(
                holds[0]["actual_microusd"] == holds[0]["reserved_microusd"],
                "lost provider hold not settled conservatively",
            )
            require(
                0 <= holds[1]["actual_microusd"] <= holds[1]["reserved_microusd"],
                "completed settlement invalid",
            )
            store = TaskStore(pool, encryption, MemoryStore(pool, encryption))
            for operation in (
                lambda: store.write_partial(task_id, 1, content="stale overwrite", delta_seq=999),
                lambda: store.complete(task_id, 1, content="stale completion"),
                lambda: store.begin_operation(
                    task_id, 1, tool_name="notification_send", target={}, min_lease_margin_s=15
                ),
            ):
                try:
                    await operation()
                except LeaseLost:
                    pass
                else:
                    raise RuntimeError("stale worker was allowed to publish or act")
            print(
                "B: committed partial, exact recovered result, notice, two settlements and stale-worker rejection verified"
            )
        elif command == "D":
            require(
                task["status"] == "needs_attention",
                "material-effect task was automatically retried",
            )
            require(len(attempts) == 1, "material effect caused another attempt")
            operations = await pool.fetch(
                "SELECT * FROM task_operations WHERE task_id = $1", task_id
            )
            require(
                len(operations) == 1 and operations[0]["outcome"] == "succeeded",
                "material effect evidence missing or untruthful",
            )
            require(
                (CONTROL / "effects").read_text().splitlines() == ["performed"],
                "material effect repeated",
            )
            print(
                "D: one performed material effect, preserved outcome and no automatic repetition verified"
            )
        else:
            raise ValueError("unknown drill assertion")
    finally:
        await pool.close()


def main() -> None:
    if len(sys.argv) not in {2, 3}:
        raise SystemExit("usage: durable_restart_assertions.py session|expire|B|D [task UUID]")
    asyncio.run(check(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else None))


if __name__ == "__main__":
    main()
