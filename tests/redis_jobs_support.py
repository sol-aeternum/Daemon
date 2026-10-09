"""Producer-test seam; native transaction tests live in test_redis_jobs.

These unit fakes already model application scheduling, not Redis transactions.
Keep their recordings while validating the new owner-qualified ID contract.
Never used by application code or the real-Redis regressions.
"""

from typing import Any

import pytest

from orchestrator.redis_jobs import account_job_id


def install_fake_enqueue(monkeypatch: pytest.MonkeyPatch, *targets: str) -> None:
    async def enqueue(
        queue: Any,
        function: str,
        *args: Any,
        user_id: Any,
        job_id: str,
        settings: Any = None,
        **kwargs: Any,
    ) -> Any:
        return await queue.enqueue_job(
            function, *args, _job_id=account_job_id(user_id, job_id, settings), **kwargs
        )

    for target in targets:
        monkeypatch.setattr(target, enqueue)
