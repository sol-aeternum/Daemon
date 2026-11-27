"""Test-only cuts after the real operation, or rollback of its exact transaction."""

import asyncio

from asyncpg.transaction import Transaction

from orchestrator.compute_runtime import current_scope
from orchestrator.entitlements.service import EntitlementService


class FailedTransportClose:
    def __init__(self, response):
        self.response = response

    def __aiter__(self):
        return self.response

    async def aclose(self):
        await self.response.aclose()
        raise OSError("synthetic close failure")


def install(patch, host, execution, fault, *, successor):
    if fault == "launcher-eof":
        # Observe the real shutdown boundary before waiting on the intentionally
        # stalled transport. The external test owns the process reap, not Y.
        close_jobs = execution._close_jobs

        async def observed_close():
            assert host._closing and execution._closing and execution._blocked
            print('{"stage":"fenced-shutdown"}', flush=True)
            await close_jobs()

        patch.setattr(execution, "_close_jobs", observed_close)
    elif fault == "stop":
        from orchestrator.tasks import runner

        patch.setattr(runner, "HEARTBEAT_S", 0.1)
        patch.setattr(runner, "STOP_GRACE_S", 0.1)
    elif fault == "settlement":
        original = EntitlementService.settle

        async def settle(*args, **kwargs):
            await original(*args, **kwargs)
            raise ConnectionError("synthetic settlement return lost")

        patch.setattr(EntitlementService, "settle", settle)
    elif fault in ("terminal", "post-terminal-owner"):
        original_complete = execution.effects.complete

        async def complete(*args, **kwargs):
            result = await original_complete(*args, **kwargs)
            if fault == "terminal":
                raise ConnectionError("synthetic terminal return lost")

            async def failed_owner():
                raise OSError("synthetic cleanup exception after terminal acknowledgement")

            current_scope().cleanup_owners.add(asyncio.create_task(failed_owner()))
            return result

        patch.setattr(execution.effects, "complete", complete)
    elif successor and fault == "poisoned-reader":
        submit = host._submit

        async def poison_after_acceptance(*args, **kwargs):
            receipt = await submit(*args, **kwargs)
            if getattr(getattr(receipt, "task", None), "created", False):
                host._reader._history._blocked = True
            return receipt

        patch.setattr(host, "_submit", poison_after_acceptance)
    elif fault in ("handoff-rollback", "handoff-commit") or (successor and fault == "y-commit"):
        target = None
        method = "stage" if fault == "y-commit" else "stage_retired"
        original_stage = getattr(execution.projection, method)
        original_exit = Transaction.__aexit__

        async def stage(conn, *args, **kwargs):
            nonlocal target
            result = await original_stage(conn, *args, **kwargs)
            target = conn._con._top_xact
            assert target is not None
            return result

        async def finish(tx, kind, value, trace):
            if tx is not target or kind is not None:
                return await original_exit(tx, kind, value, trace)
            lost = ConnectionError("synthetic retirement ACK lost")
            if fault != "handoff-rollback":
                await original_exit(tx, kind, value, trace)
            else:
                await original_exit(tx, type(lost), lost, None)
            raise lost

        patch.setattr(execution.projection, method, stage)
        patch.setattr(Transaction, "__aexit__", finish)
