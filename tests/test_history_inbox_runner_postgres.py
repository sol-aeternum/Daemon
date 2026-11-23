"""Inbox binding over the actual isolated text runner, not an acceptance stand-in.

The disposable link table is written only by the fixture trigger inside the
native A transaction. The inbox adapter translates POST /inbox/starts into the
real isolated /chat and adds an exact-link guard to the native by-key TaskOut.
"""

import asyncio
from uuid import UUID, uuid4

from asyncpg import CheckViolationError
from asyncpg.transaction import Transaction
from httpx import ASGITransport, AsyncClient, ReadError
import pytest

from orchestrator.services.restore_history.acceptance_host import create_isolated_ui_app
from orchestrator.services.restore_history.model import (
    AcceptPrepare,
    SendPossible,
    TerminalOutputPrepare,
)
from tests.history_inbox_support import ids
from tests.history_inbox_runner_support import add_root, inbox_app, inbox_key, install_inbox
from tests.test_history_composition_postgres import deadline
from tests.test_history_acceptance_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
)
from tests.test_history_observation_boundary_postgres import device
from tests.test_history_text_execution_postgres import (
    jobs,
    text_host as text_host,
)

pytestmark = pytest.mark.asyncio

ZERO = "0|0|0|0|0|0|0|0|0|0|0"


def counts(s):
    """SQL linkage/native/recovery/attempt/budget inventory, one scalar row."""
    return s.db.sql(
        """SELECT (SELECT count(*) FROM public.fixture_inbox_links),
                  (SELECT count(*) FROM public.conversations),
                  (SELECT count(*) FROM public.tasks),
                  (SELECT count(*) FROM public.messages),
                  (SELECT count(*) FROM public.task_events),
                  (SELECT count(*) FROM public.task_history_projection),
                  (SELECT count(*) FROM public.task_attempts),
                  (SELECT count(*) FROM public.task_operations),
                  (SELECT count(*) FROM public.task_inference_recovery),
                  (SELECT count(*) FROM public.entitlement_reservations
                   WHERE status='open'),
                  (SELECT count(*) FROM public.compute_reservation_metadata)"""
    )


def durable_state(s):
    """Include row contents, so an in-place change cannot hide behind equal counts."""
    rows = s.db.sql(
        """SELECT jsonb_build_array(
           (SELECT to_jsonb(l) FROM public.fixture_inbox_links l),
           (SELECT to_jsonb(t) FROM public.tasks t),
           (SELECT to_jsonb(c) FROM public.conversations c),
           (SELECT jsonb_agg(m ORDER BY id) FROM public.messages m),
           (SELECT jsonb_agg(e ORDER BY seq) FROM public.task_events e),
           (SELECT to_jsonb(a) FROM public.task_attempts a),
           (SELECT to_jsonb(r) FROM public.task_inference_recovery r),
           (SELECT to_jsonb(b) FROM public.entitlement_reservations b))"""
    )
    return counts(s), rows, (s.path / "LOG").read_bytes(), (s.path / "HEAD").read_bytes()


async def history(s):
    # A separate runner-side H coordinator; the acceptance one may be blocked.
    async with s.execution.history.operation(deadline=deadline()) as operation:
        return (await operation.verify()).entries


def assert_no_execution(s):
    assert s.db.sql("SELECT count(*) FROM public.task_attempts") == "0"
    assert s.db.sql("SELECT count(*) FROM public.task_operations") == "0"
    assert (
        s.db.sql("SELECT count(*) FROM public.tasks WHERE attempt_count<>0 OR wake_seq<>0") == "0"
    )
    assert (
        s.db.sql("SELECT count(*) FROM public.task_inference_recovery WHERE may_have_dispatched")
        == "0"
    )
    assert (
        s.db.sql("SELECT count(*) FROM public.entitlement_reservations WHERE status='open'") == "0"
    )
    assert s.db.sql("SELECT count(*) FROM public.compute_reservation_metadata") == "0"
    assert not s.execution._jobs
    assert s.calls == []


async def start(s, http, headers, key, text):
    response = await http.post(
        "/inbox/starts",
        headers={**headers, "Idempotency-Key": key},
        json={"text": text},
    )
    assert response.headers["cache-control"] == "no-store"
    return response


async def link_rows(s):
    async with s.db.connection() as conn:
        return await conn.fetch("SELECT * FROM public.fixture_inbox_links ORDER BY task_id")


async def test_link_check_fault_rolls_back_before_history(text_host, monkeypatch):
    s = text_host
    root = await install_inbox(s, s.account)
    key = inbox_key(root, uuid4())
    _, headers = await device(s, s.account)
    # Disposable only: the otherwise valid trigger insert must fail inside A.
    s.db.sql(
        "ALTER TABLE public.fixture_inbox_links "
        "ADD CONSTRAINT fixture_reject_link CHECK (false) NOT VALID"
    )
    async with s.make():
        async with AsyncClient(
            transport=ASGITransport(inbox_app(s.host)), base_url="http://fixture.test"
        ) as http:
            # Observe the *actual* driver's rollback reason, without altering it.
            failures = []
            exit_transaction = Transaction.__aexit__

            async def record_exit(tx, kind, exc, traceback):
                if isinstance(exc, CheckViolationError):
                    fields = exc.as_dict()
                    failures.append((fields["table_name"], fields["constraint_name"]))
                return await exit_transaction(tx, kind, exc, traceback)

            with monkeypatch.context() as spy:
                spy.setattr(Transaction, "__aexit__", record_exit)
                response = await start(s, http, headers, key, "Link check fault")
            assert ("fixture_inbox_links", "fixture_reject_link") in failures
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "history_unavailable"
            assert response.json()["detail"]["retry_safe"] is False
            assert counts(s) == ZERO
            assert await history(s) == ()
            assert s.host._acceptance._blocked
            assert_no_execution(s)
            # Acceptance refused before H; the native read still answers unknown.
            unknown = await http.get(f"/inbox/starts/by-key/{key}", headers=headers)
            assert unknown.status_code == 404
            assert unknown.json() == {"detail": "Task not found"}
            assert counts(s) == ZERO


@pytest.mark.parametrize("point", ["rollback_after_prepare", "lost_commit_ack"])
async def test_acceptance_transaction_faults_separate_sql_from_history(
    text_host, monkeypatch, point
):
    s = text_host
    root = await install_inbox(s, s.account)
    key = inbox_key(root, uuid4())
    _, headers = await device(s, s.account)
    _, second = await device(s, s.account)
    async with s.make():
        projection = s.host._acceptance._projection
        original_stage, original_exit = projection.stage, Transaction.__aexit__
        target = None
        observed = {}

        async def stage(conn, entry, *, deadline):
            nonlocal target
            result = await original_stage(conn, entry, deadline=deadline)
            target = conn._con._top_xact
            assert target is not None and entry.command.data.account_id == s.account
            return result

        async def exit_transaction(tx, kind, value, traceback):
            nonlocal target
            if tx is not target or kind is not None:
                return await original_exit(tx, kind, value, traceback)
            target = None
            # Independent SQL between native stage and the real A exit must not
            # see any of A's rows: task, conversation, messages, link, event.
            async with s.db.connection() as observer:
                precommit = await observer.fetchrow(
                    """SELECT (SELECT count(*) FROM public.tasks),
                              (SELECT count(*) FROM public.conversations),
                              (SELECT count(*) FROM public.messages),
                              (SELECT count(*) FROM public.fixture_inbox_links),
                               (SELECT count(*) FROM public.task_events),
                               (SELECT count(*) FROM public.task_history_projection),
                               (SELECT count(*) FROM public.task_inference_recovery)"""
                )
            observed["precommit"] = tuple(precommit)
            if point == "rollback_after_prepare":
                error = ConnectionError("synthetic rollback after H prepare")
                await original_exit(tx, type(error), error, None)
                raise error
            await original_exit(tx, kind, value, traceback)
            async with s.db.connection() as observer:
                postcommit = await observer.fetchrow(
                    """SELECT (SELECT count(*) FROM public.tasks),
                              (SELECT count(*) FROM public.conversations),
                              (SELECT count(*) FROM public.messages),
                              (SELECT count(*) FROM public.fixture_inbox_links),
                              (SELECT count(*) FROM public.task_events)"""
                )
            observed["postcommit"] = tuple(postcommit)
            raise ConnectionError("synthetic lost A commit acknowledgement")

        with monkeypatch.context() as fault:
            fault.setattr(projection, "stage", stage)
            fault.setattr(Transaction, "__aexit__", exit_transaction)
            async with AsyncClient(
                transport=ASGITransport(inbox_app(s.host)), base_url="http://fixture.test"
            ) as http:
                response = await start(s, http, headers, key, "Faulted transaction")
                assert response.status_code == 503
                assert response.json()["detail"]["code"] == "history_unavailable"
                assert observed["precommit"] == (0,) * 7
                assert s.host._acceptance._blocked
                assert_no_execution(s)
                entries = await history(s)
                assert [type(e.command.data) for e in entries] == [AcceptPrepare]
                prepared = entries[0].command.data
                before_read = durable_state(s)
                if point == "lost_commit_ack":
                    # SQL and link committed; the lost acknowledgement is only the
                    # transport result, never a second task or a dispatch permit.
                    assert observed["postcommit"] == (1, 1, 2, 1, 1)
                    assert counts(s) == "1|1|1|2|1|1|0|0|1|0|0"
                    rows = await link_rows(s)
                    assert len(rows) == 1
                    assert ids(prepared) == (
                        rows[0]["task_id"],
                        rows[0]["conversation_id"],
                        rows[0]["user_message_id"],
                        rows[0]["result_message_id"],
                    )
                    assert (
                        rows[0]["account_id"],
                        rows[0]["generation"],
                    ) == (s.account, prepared.account_generation)
                else:
                    assert counts(s) == ZERO
                    assert "postcommit" not in observed
        async with AsyncClient(
            transport=ASGITransport(inbox_app(s.host)), base_url="http://fixture.test"
        ) as http:
            if point == "rollback_after_prepare":
                refusal = await http.get(f"/inbox/starts/by-key/{key}", headers=headers)
                assert refusal.status_code == 409
                assert refusal.json()["detail"] == {
                    "code": "restore_recovery_required",
                    "task_id": str(prepared.task_id),
                    "retry_safe": False,
                }
            else:
                found = await http.get(f"/inbox/starts/by-key/{key}", headers=second)
                assert found.status_code == 200, found.text
                body = found.json()
                assert (body["id"], body["conversation_id"], body["status"]) == (
                    str(prepared.task_id),
                    str(prepared.conversation_id),
                    "queued",
                )
                assert body["content"] == ""
            # Same key afterwards is refused before any new acceptance.
            retry = await start(s, http, headers, key, "Faulted transaction")
            assert retry.status_code == 503
        assert durable_state(s) == before_read
        expected = ZERO if point == "rollback_after_prepare" else "1|1|1|2|1|1|0|0|1|0|0"
        assert counts(s) == expected
        assert_no_execution(s)


async def test_confirmed_200_lost_keeps_one_link_and_native_recovery(text_host):
    s = text_host
    root = await install_inbox(s, s.account)
    key = inbox_key(root, uuid4())
    _, first = await device(s, s.account)
    _, second = await device(s, s.account)
    committed = []

    # This hook is at the synthetic transport, *after* the real worker,
    # account reservation and SEND_POSSIBLE transaction have all admitted it.
    async def verify_transport(scope):
        ident = scope.inference_identity
        async with s.db.connection() as conn:
            (evidence,) = await conn.fetch(
                """SELECT l.inbox_id,t.user_id,l.generation,l.task_id,a.epoch,
                          r.first_lifecycle_id,r.first_reservation_id
                   FROM public.fixture_inbox_links l JOIN public.tasks t ON t.id=l.task_id
                   JOIN public.task_attempts a ON a.task_id=t.id
                   JOIN public.task_inference_recovery r ON r.task_id=t.id
                   JOIN public.entitlement_reservations b ON b.id=r.first_reservation_id
                   WHERE t.idempotency_key=$1 AND r.may_have_dispatched AND b.status='open'""",
                key,
            )
        send = (await history(s))[-1].command.data
        assert type(send) is SendPossible
        assert (
            ident.account_id,
            ident.generation,
            ident.task_id,
            ident.epoch,
            ident.lifecycle_id,
        ) == (send.account_id, send.account_generation, send.task_id, send.epoch, send.lifecycle_id)
        assert tuple(evidence) == (
            root,
            s.account,
            ident.generation,
            ident.task_id,
            ident.epoch,
            ident.lifecycle_id,
            send.reservation_id,
        )

    s.provider_check = verify_transport
    async with s.make():
        app = inbox_app(s.host)

        async def lose_response(scope, receive, send):
            disconnected = asyncio.Event()
            body_complete = False

            async def incoming():
                nonlocal body_complete
                if not body_complete:
                    message = await receive()
                    body_complete = not message.get("more_body", False)
                    return message
                await disconnected.wait()
                return {"type": "http.disconnect"}

            async def drop(message):
                if message["type"] == "http.response.start":
                    assert message["status"] == 200
                    committed.append(dict(message["headers"])[b"x-daemon-task-id"].decode())
                    disconnected.set()

            await app(scope, incoming, drop)
            raise ReadError("synthetic loss of the confirmed /chat response")

        async with AsyncClient(
            transport=ASGITransport(lose_response), base_url="http://fixture.test"
        ) as client:
            with pytest.raises(ReadError):
                async with asyncio.timeout(15):
                    await client.post(
                        "/inbox/starts",
                        headers={**first, "Idempotency-Key": key},
                        json={"text": "Confirmed loss"},
                    )
        assert len(committed) == 1
        await asyncio.wait_for(s.entered.wait(), 5)
        assert counts(s).split("|")[:4] == ["1", "1", "1", "2"]
        rows = await link_rows(s)
        assert len(rows) == 1 and str(rows[0]["task_id"]) == committed[0]
        assert s.db.sql("SELECT count(*) FROM public.task_attempts") == "1"
        assert (
            s.db.sql(
                "SELECT count(*) FROM public.task_inference_recovery WHERE may_have_dispatched"
            )
            == "1"
        )
        assert (
            s.db.sql("SELECT count(*) FROM public.entitlement_reservations WHERE status='open'")
            == "1"
        )
        async with AsyncClient(
            transport=ASGITransport(inbox_app(s.host)), base_url="http://fixture.test"
        ) as reopened:
            async with asyncio.timeout(10):
                while True:
                    found = await reopened.get(f"/inbox/starts/by-key/{key}", headers=second)
                    assert found.status_code == 200, found.text
                    if found.json()["content"]:
                        break
                    await asyncio.sleep(0.02)
            body = found.json()
            assert (body["id"], body["status"], body["content"]) == (
                committed[0],
                "running",
                "original partial",
            )
            again = await reopened.get(f"/inbox/starts/by-key/{key}", headers=second)
            assert again.status_code == 200 and again.json() == body
            s.release.set()
            assert await jobs(s) == ["completed"]
            final = await reopened.get(f"/inbox/starts/by-key/{key}", headers=second)
            assert final.status_code == 200, final.text
            body = final.json()
            assert (body["id"], body["status"], body["content"]) == (
                committed[0],
                "completed",
                "original partial and final",
            )
            # A browser resend to a healthy writer is historical, not a NEW receipt.
            assert not s.host._acceptance._blocked
            before_retry = durable_state(s)
            replay = await start(s, reopened, second, key, "Confirmed loss")
            assert replay.status_code == 503 and replay.json()["detail"]["retry_safe"] is False
            assert durable_state(s) == before_retry
            assert s.execution._scheduled == {UUID(committed[0])}
        assert len(rows) == 1 and len(s.calls) == 1
        assert s.db.sql("SELECT count(*) FROM public.task_attempts") == "1"
        assert s.db.sql("SELECT count(*) FROM public.task_operations") == "0"
        assert (
            s.db.sql("SELECT count(*) FROM public.entitlement_reservations WHERE status='open'")
            == "0"
        )
        entries = await history(s)
        assert type(entries[0].command.data) is AcceptPrepare
        assert any(type(e.command.data) is SendPossible for e in entries)
        assert type(entries[-1].command.data) is TerminalOutputPrepare
        assert entries[-1].command.data.task_id == UUID(committed[0])


async def test_two_authenticated_devices_share_one_running_original(text_host):
    s = text_host
    root = await install_inbox(s, s.account)
    key = inbox_key(root, uuid4())
    _, first = await device(s, s.account)
    _, second = await device(s, s.account)
    async with s.make():
        app = inbox_app(s.host)
        parallel = ASGITransport(app)
        async with AsyncClient(transport=parallel, base_url="http://fixture.test") as http:
            response = await start(s, http, first, key, "Shared original")
            assert response.status_code == 200
            task_id = response.headers["x-daemon-task-id"]
            await asyncio.wait_for(s.entered.wait(), 5)
            async with asyncio.timeout(10):
                while True:
                    observed = await http.get(f"/inbox/starts/by-key/{key}", headers=second)
                    assert observed.status_code == 200, observed.text
                    if observed.json()["content"]:
                        break
                    await asyncio.sleep(0.02)
            running = observed.json()
            assert (running["id"], running["status"], running["content"]) == (
                task_id,
                "running",
                "original partial",
            )
            same = await http.get(f"/inbox/starts/by-key/{key}", headers=first)
            assert (same.json()["id"], same.json()["status"], same.json()["content"]) == (
                task_id,
                "running",
                "original partial",
            )
            s.release.set()
            assert await jobs(s) == ["completed"]
            for headers in (first, second):
                final = await http.get(f"/inbox/starts/by-key/{key}", headers=headers)
                assert final.status_code == 200, final.text
                body = final.json()
                assert (body["id"], body["status"], body["content"]) == (
                    task_id,
                    "completed",
                    "original partial and final",
                )
                assert body["attempt_count"] == 1
                assert len(s.calls) == 1
        rows = await link_rows(s)
        entries = await history(s)
        prepared = entries[0].command.data
        assert type(prepared) is AcceptPrepare
        assert ids(prepared) == (
            rows[0]["task_id"],
            rows[0]["conversation_id"],
            rows[0]["user_message_id"],
            rows[0]["result_message_id"],
        )
        assert str(prepared.task_id) == task_id
        assert type(entries[-1].command.data) is TerminalOutputPrepare
        assert s.db.sql("SELECT count(*) FROM public.task_attempts") == "1"
        assert s.db.sql("SELECT count(*) FROM public.task_operations") == "0"
        # Quiescent qualified reads append no native row, H record or link change.
        before = durable_state(s)
        async with AsyncClient(
            transport=ASGITransport(inbox_app(s.host)), base_url="http://fixture.test"
        ) as quiet:
            for headers in (first, second):
                for _ in range(2):
                    assert (
                        await quiet.get(f"/inbox/starts/by-key/{key}", headers=headers)
                    ).status_code == 200
            async with AsyncClient(
                transport=ASGITransport(create_isolated_ui_app(s.host)),
                base_url="http://fixture.test",
            ) as native:
                for headers in (first, second):
                    task = await native.get(f"/tasks/by-key/{key}", headers=headers)
                    assert task.status_code == 200, task.text
                    # The inbox route returns the same native TaskOut only because
                    # the exact link is present; no extra decorated body.
                    assert task.json() == final.json()
                    conversation = await native.get(
                        f"/conversations/{task.json()['conversation_id']}", headers=headers
                    )
                    assert conversation.status_code == 200, conversation.text
        assert durable_state(s) == before
        assert s.db.sql("SELECT count(*) FROM public.task_history_projection") == "3"
        assert s.db.sql("SELECT count(*) FROM public.task_operations") == "0"
        assert s.db.sql("SELECT count(*) FROM public.task_attempts") == "1"


async def test_foreign_known_and_unknown_keys_are_indistinguishable(text_host):
    s = text_host
    root = await install_inbox(s, s.account)
    key = inbox_key(root, uuid4())
    async with s.db.connection() as conn:
        s.foreign = await s.db.account(conn)
    foreign_root = await add_root(s, s.foreign)
    assert foreign_root != root
    _, first = await device(s, s.account)
    _, foreign = await device(s, s.foreign)
    async with s.make():
        async with AsyncClient(
            transport=ASGITransport(inbox_app(s.host)), base_url="http://fixture.test"
        ) as http:
            response = await start(s, http, first, key, "Owner original")
            assert response.status_code == 200
            task_id = response.headers["x-daemon-task-id"]
            await asyncio.wait_for(s.entered.wait(), 5)
            unknown = inbox_key(foreign_root, uuid4())
            known = await http.get(f"/inbox/starts/by-key/{key}", headers=foreign)
            random = await http.get(f"/inbox/starts/by-key/{unknown}", headers=foreign)
            assert known.status_code == random.status_code == 404
            assert known.json() == random.json() == {"detail": "Task not found"}
            async with AsyncClient(
                transport=ASGITransport(create_isolated_ui_app(s.host)),
                base_url="http://fixture.test",
            ) as native:
                known = await native.get(f"/tasks/by-key/{key}", headers=foreign)
                random = await native.get(f"/tasks/by-key/{unknown}", headers=foreign)
                assert known.status_code == random.status_code == 404
                assert known.json() == random.json() == {"detail": "Task not found"}
                listed = await native.get("/conversations", headers=foreign)
                assert listed.status_code == 200
                assert listed.json()["conversations"] == []
            owning = await http.get(f"/inbox/starts/by-key/{key}", headers=first)
            assert owning.status_code == 200
            assert owning.json()["id"] == task_id
            s.release.set()
            assert await jobs(s) == ["completed"]
            # A real, provisioned foreign account cannot bind the owner's root.
            before = counts(s)
            refused = await start(s, http, foreign, key, "Foreign attempt")
            assert refused.status_code == 503
            assert refused.json()["detail"]["code"] == "history_unavailable"
            assert counts(s) == before
            assert len(await link_rows(s)) == 1
            assert len(s.calls) == 1


@pytest.mark.parametrize("damage", ["missing_link", "swapped_link"])
async def test_incomplete_link_refuses_inbox_read_but_not_native(text_host, damage):
    s = text_host
    root = await install_inbox(s, s.account)
    key = inbox_key(root, uuid4())
    _, headers = await device(s, s.account)
    async with s.make():
        async with AsyncClient(
            transport=ASGITransport(inbox_app(s.host)), base_url="http://fixture.test"
        ) as http:
            response = await start(s, http, headers, key, "Linked original")
            assert response.status_code == 200
            task_id = response.headers["x-daemon-task-id"]
            await asyncio.wait_for(s.entered.wait(), 5)
            async with s.db.connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM public.fixture_inbox_links WHERE task_id=$1",
                    UUID(task_id),
                )
                assert row is not None
                if damage == "missing_link":
                    await conn.execute(
                        "DELETE FROM public.fixture_inbox_links WHERE task_id=$1",
                        row["task_id"],
                    )
                else:
                    await conn.execute(
                        """UPDATE public.fixture_inbox_links
                           SET user_message_id=result_message_id,
                               result_message_id=user_message_id WHERE task_id=$1""",
                        row["task_id"],
                    )
            refused = await http.get(f"/inbox/starts/by-key/{key}", headers=headers)
            assert refused.status_code == 503
            assert refused.json() == {"detail": {"code": "inbox_unavailable", "retry_safe": False}}
            async with AsyncClient(
                transport=ASGITransport(create_isolated_ui_app(s.host)),
                base_url="http://fixture.test",
            ) as native:
                observed = await native.get(f"/tasks/by-key/{key}", headers=headers)
                assert observed.status_code == 200, observed.text
                assert observed.json()["id"] == task_id
            s.release.set()
            assert await jobs(s) == ["completed"]
            assert len(s.calls) == 1
            assert s.db.sql("SELECT count(*) FROM public.task_attempts") == "1"
            assert (
                await http.get(f"/inbox/starts/by-key/{key}", headers=headers)
            ).status_code == 503
            assert counts(s).split("|")[0] == ("0" if damage == "missing_link" else "1")
