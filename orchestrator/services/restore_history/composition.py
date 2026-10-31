"""Uncalled acceptance-only H1/native join; no startup, dispatch or recovery grant.

The isolated trusted host supplies the already opened history owner, independent
identity/key, qualified SQL projection and actual native TaskStore. Only a fresh
native COMMIT followed by successful ownership cleanup can return AcceptedTask.
Historical evidence is a refusal, never an acknowledgement inferred by readback.
"""

from __future__ import annotations

import asyncio
import hmac
import time
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from orchestrator.tasks._invocation import block
from orchestrator.tasks.store import AcceptedTask, IdempotencyConflict, TaskStore

from .acceptance import AcceptanceProjection
from .coordination import HistoryCoordinator
from .model import MAX_CANONICAL, AcceptPrepare, Command, HistoryError, commitments, parse
from .projection import compare_projections
from .store import _check_time


class HistoryAcceptance:
    """Explicit isolated collaborator, inert constructor and no bind/install API.

    Matching historical keys also refuse: ACCEPT_PREPARE cannot distinguish an
    acknowledged COMMIT from a lost acknowledgement. A later observation API and
    production client policy are deliberately not supplied by this slice.
    """

    def __init__(
        self,
        tasks: TaskStore,
        history: HistoryCoordinator,
        projection: AcceptanceProjection,
        key: bytes,
    ) -> None:
        if (
            type(tasks) is not TaskStore
            or type(history) is not HistoryCoordinator
            or type(projection) is not AcceptanceProjection
            or history._expected != projection.expected
            or tasks._memory._pool is not tasks._pool
            or type(tasks._pool) is not asyncpg.Pool
            or tasks._cover_new_inference is not True
            or tasks._inference is not None
        ):
            raise HistoryError("acceptance_composition_refused")
        projection.expected.verify_key(key)
        self._tasks, self._history, self._projection, self._key = tasks, history, projection, key
        self._blocked = False

    def _request(self, options: dict[str, Any]) -> tuple[str, str]:
        account, key, canonical = (
            options.get("user_id"),
            options.get("idempotency_key"),
            options.get("request_canonical"),
        )
        if not isinstance(account, UUID) or type(canonical) is not str:
            raise HistoryError("acceptance_request_refused")
        if type(key) is not str:
            raise HistoryError("acceptance_stable_key_required")
        # The native trusted caller supplies the existing server canonical
        # fingerprint. Reject noncanonical/duplicate/nonfinite JSON and bind its
        # message/conversation to the actual native arguments before mutation.
        try:
            raw = canonical.encode("utf-8")
            if len(raw) > MAX_CANONICAL:
                raise HistoryError("acceptance_request_refused")
            value = parse(raw)
        except UnicodeError:
            raise HistoryError("acceptance_request_refused") from None
        required = {
            "conversation_id",
            "message",
            "attachments",
            "model",
            "provider",
            "metadata",
            "disable_memory_write",
            "content_parts",
        }
        if (
            not required <= set(value) <= required | {"answer_detail", "research_effort"}
            or type(value["message"]) is not str
            or type(value["model"]) is not str
            or (value["provider"] is not None and type(value["provider"]) is not str)
            or type(value["metadata"]) is not dict
            or type(value["attachments"]) is not list
            or type(value["content_parts"]) is not list
            or type(value["disable_memory_write"]) is not bool
            or value.get("message") != options.get("user_message")
            or value.get("conversation_id")
            != (str(options["conversation_id"]) if options.get("conversation_id") else None)
        ):
            raise HistoryError("acceptance_request_refused")
        if (
            value["attachments"] or value["content_parts"]
        ) and self._tasks._resource_inputs is None:
            raise HistoryError("acceptance_resources_uncomposed")
        self._projection.expected.verify_key(self._key)
        return commitments(self._key, account, key, raw)

    async def accept(self, *, deadline: float, **options: Any) -> AcceptedTask:
        """Same native acceptance arguments plus one absolute deadline.

        Only an isolated trusted host may call this. It must acquire any external
        authentication authority after H ownership, not wrap this call in an
        already-held native transaction. No HTTP/client caller is wired here.
        """
        self._history._bind()  # PID/loop refusal before touching inherited owners.
        if (
            self._history._expected != self._projection.expected
            or self._tasks._pool is not self._tasks._memory._pool
            or type(self._tasks._pool) is not asyncpg.Pool
            or self._tasks._cover_new_inference is not True
            or self._tasks._inference is not None
        ):
            raise HistoryError("acceptance_composition_refused")
        if self._blocked:
            raise HistoryError("acceptance_composition_blocked")
        _check_time(deadline)
        lookup, request = self._request(options)
        child = asyncio.create_task(self._accept(deadline, options, lookup, request))
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                result = await asyncio.shield(child)
            _check_time(deadline)
            if self._blocked or self._history._blocked:
                raise HistoryError("acceptance_composition_blocked")
            return result
        except BaseException as original:
            self._blocked = self._history._blocked = True
            block("history_acceptance_unavailable")
            # Forward cancellation once. A queued native gate waiter must settle;
            # a child already owning H drains physical work and its SQL cleanup.
            # Repeated caller cancellation cannot detach that ownership task.
            if not child.done():
                child.cancel()
            while not child.done():
                try:
                    await asyncio.shield(child)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            try:
                child.result()
            except BaseException as secondary:
                if secondary is not original:
                    original.add_note("Acceptance cleanup completion: " + type(secondary).__name__)
            original.add_note("Acceptance blocked; no retry, readback or late-result promotion")
            raise

    async def _accept(
        self, deadline: float, options: dict[str, Any], lookup: str, request: str
    ) -> AcceptedTask:
        async with self._history.operation(deadline=deadline) as operation:
            state = await operation.verify()
            for entry in state.entries:
                data = entry.command.data
                if (
                    isinstance(data, AcceptPrepare)
                    and data.account_id == options["user_id"]
                    and hmac.compare_digest(data.key_commitment, lookup)
                ):
                    if not hmac.compare_digest(data.request_commitment, request):
                        raise IdempotencyConflict("history key reused for a different request")
                    raise HistoryError("historical_acceptance", uncertain=True)
            # Full supplied-facts comparison deliberately retains non-acceptance
            # H records: this acceptance-only slice cannot qualify their absence
            # from SQL, open restore intervals or an older restored database.
            facts = []
            async with self._tasks._pool.acquire() as conn:
                if conn.is_in_transaction():
                    raise HistoryError("acceptance_transaction_owner_refused")
                async with conn.transaction(isolation="read_committed"):
                    await self._projection.qualify(conn, writing=True, deadline=deadline)
                    cursor = 0
                    while True:
                        page = await self._projection.page(
                            conn, after_seq=cursor, deadline=deadline
                        )
                        facts.extend(page)
                        if len(page) < 32:
                            break
                        cursor = page[-1].seq
            report = compare_projections(self._projection.expected.history_id, state, tuple(facts))
            if report.missing or report.extra or report.mismatched or report.restore_required:
                raise HistoryError("acceptance_reconciliation_required", uncertain=True)
            staged = False

            async def stage(conn: Any, pending: AcceptedTask) -> None:
                nonlocal staged
                if staged or type(pending) is not AcceptedTask or not pending.created:
                    raise HistoryError("acceptance_stage_refused", uncertain=True)
                capture = options["captured_generation"]
                prepared = await operation.append(
                    Command(
                        uuid4(),
                        AcceptPrepare(
                            options["user_id"],
                            capture.generation,
                            pending.task_id,
                            pending.conversation_id,
                            pending.user_message_id,
                            pending.result_message_id,
                            1,
                            1,
                            lookup,
                            request,
                            "chat-canonical-3715-v1",
                        ),
                    )
                )
                if not prepared.created:
                    raise HistoryError("historical_acceptance", uncertain=True)
                projection = await self._projection.stage(conn, prepared.entry, deadline=deadline)
                if not projection.inserted:
                    raise HistoryError("historical_acceptance", uncertain=True)
                staged = True

            pending = await self._tasks._accept_with_stage(stage=stage, **options)
            if not staged or not pending.created:
                raise HistoryError("native_acceptance_not_fresh", uncertain=True)
            _check_time(deadline)
        # Native COMMIT + connection release and H operation release are complete.
        # This is acceptance only, never authority to dispatch an external effect.
        _check_time(deadline)
        return pending
