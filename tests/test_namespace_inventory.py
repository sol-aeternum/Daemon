"""Closed inventory parsing and parity; synthetic observations confer no authority."""

import asyncio
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from typing import cast
from uuid import UUID, uuid4

from orchestrator.services.account_control.store import Connection

import pytest

from orchestrator.namespace_inventory import (
    AppliedMarker,
    NamespaceInventory,
    NamespaceProjection,
    decode_inventory,
    inventory_from_rows,
    namespace_states,
    read_output_inventory,
)
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.mixed_store import MixedView
from orchestrator.services.deletion_journal.namespace_candidate import (
    NamespaceCommand,
    NamespaceCreateFields,
    NamespaceEntry,
)


@pytest.fixture
def sample():
    journal, event, account = uuid4(), uuid4(), uuid4()
    entry = NamespaceEntry(
        journal,
        1,
        event,
        NamespaceCommand(
            account,
            "namespace_create_intended",
            datetime.now(timezone.utc),
            NamespaceCreateFields(0, uuid4().hex),
        ),
        None,
        bytes(32),
        b"c" * 32,
        b"d" * 32,
    )
    marker = AppliedMarker(journal, 1, event, account, None, entry.digest)
    namespace = NamespaceProjection(
        entry.command.fields.namespace,
        account,
        0,
        journal,
        1,
        event,
        entry.digest,
        entry.command_digest,
        "ready",
    )
    return MixedView((entry,), 2), NamespaceInventory(journal, (marker,), (namespace,))


def wire(inventory):
    def value(v):
        if isinstance(v, UUID):
            return str(v)
        if isinstance(v, bytes):
            return v.hex()
        raise TypeError(type(v))

    return json.dumps({"schema_version": 1, **asdict(inventory)}, default=value)


def test_closed_snapshot_matches_native_normalization(sample):
    view, inventory = sample
    decoded = decode_inventory(wire(inventory), inventory.journal_id, 1)
    assert decoded == inventory
    assert (
        inventory_from_rows(
            inventory.journal_id,
            [asdict(r) for r in inventory.markers],
            [asdict(r) for r in inventory.namespaces],
        )
        == decoded
    )
    assert namespace_states(inventory.journal_id, view, decoded) == {
        inventory.namespaces[0].namespace: "ready"
    }


@pytest.mark.parametrize(
    "damage",
    [
        "unknown",
        "duplicate_json",
        "version",
        "boolean",
        "negative",
        "overflow",
        "uuid",
        "digest",
        "namespace",
        "state",
        "missing",
        "foreign_binding",
        "over_budget",
        "oversized",
        "unicode",
    ],
)
def test_closed_packet_refusals(sample, damage):
    _, inventory = sample
    payload = wire(inventory)
    data = json.loads(payload)
    if damage == "unknown":
        data["extra"] = None
    elif damage == "duplicate_json":
        payload = payload.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
    elif damage == "version":
        data["schema_version"] = True
    elif damage == "boolean":
        data["markers"][0]["seq"] = True
    elif damage == "negative":
        data["namespaces"][0]["generation"] = -1
    elif damage == "overflow":
        data["markers"][0]["seq"] = 2**63
    elif damage == "uuid":
        data["markers"][0]["event_id"] = data["markers"][0]["event_id"].replace("-", "")
    elif damage == "digest":
        data["markers"][0]["entry_digest"] = "x" * 64
    elif damage == "namespace":
        data["namespaces"][0]["namespace"] = "z" * 32
    elif damage == "state":
        data["namespaces"][0]["state"] = "ready_again"
    elif damage == "missing":
        del data["markers"][0]["operation_id"]
    elif damage == "foreign_binding":
        data["journal_id"] = str(uuid4())
    elif damage == "over_budget":
        data["markers"] *= 2
    elif damage == "oversized":
        payload = " " * 3000
    elif damage == "unicode":
        payload = "é"
    if damage not in {"duplicate_json", "oversized", "unicode"}:
        payload = json.dumps(data)
    with pytest.raises(JournalError, match="marker_mismatch"):
        decode_inventory(payload, inventory.journal_id, 1)


@pytest.mark.parametrize(
    "damage",
    [
        "missing_marker",
        "missing_namespace",
        "marker_digest",
        "birth_digest",
        "account",
        "generation",
        "duplicate_marker",
        "duplicate_namespace",
        "journal",
        "version",
    ],
)
def test_full_parity_refuses_contradictions(sample, damage):
    view, inv = sample
    if damage == "missing_marker":
        inv = replace(inv, markers=())
    elif damage == "missing_namespace":
        inv = replace(inv, namespaces=())
    elif damage == "marker_digest":
        inv = replace(inv, markers=(replace(inv.markers[0], entry_digest=b"z" * 32),))
    elif damage == "birth_digest":
        inv = replace(inv, namespaces=(replace(inv.namespaces[0], birth_digest=b"z" * 32),))
    elif damage == "account":
        inv = replace(inv, namespaces=(replace(inv.namespaces[0], account_id=uuid4()),))
    elif damage == "generation":
        inv = replace(inv, namespaces=(replace(inv.namespaces[0], generation=1),))
    elif damage == "duplicate_marker":
        inv = replace(inv, markers=inv.markers * 2)
    elif damage == "duplicate_namespace":
        inv = replace(inv, namespaces=inv.namespaces * 2)
    elif damage == "journal":
        inv = replace(inv, journal_id=uuid4())
    elif damage == "version":
        view = replace(view, container_version=1)
    with pytest.raises(JournalError):
        namespace_states(view.entries[0].journal_id, view, inv)


def test_unapplied_births_and_nonready_states_are_observations_only(sample):
    view, inv = sample
    assert namespace_states(inv.journal_id, view, replace(inv, markers=(), namespaces=())) == {}
    for state in ("provisioning", "uncertain", "retired"):
        observed = replace(inv, namespaces=(replace(inv.namespaces[0], state=state),))
        assert namespace_states(inv.journal_id, view, observed) == {
            inv.namespaces[0].namespace: state
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [None, True, 0, -1, 10001])
async def test_invalid_budget_refuses_before_sql(sample, limit):
    class Forbidden:
        async def fetchval(self, *_):
            pytest.fail("invalid budget reached SQL")

    with pytest.raises(JournalError, match="invalid_config"):
        await read_output_inventory(
            cast(Connection, Forbidden()), sample[1].journal_id, record_limit=limit
        )


@pytest.mark.asyncio
async def test_single_supplied_read_propagates_cancellation_without_retry(sample):
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    class Read:
        async def fetchval(self, *args):
            calls.append(args)
            started.set()
            await release.wait()
            return wire(sample[1])

    pending = asyncio.create_task(
        read_output_inventory(cast(Connection, Read()), sample[1].journal_id, record_limit=1)
    )
    await started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert len(calls) == 1
    assert calls[0][0].startswith("SELECT public.resource_output_namespace_inventory(")
    assert calls[0][1:] == (sample[1].journal_id, 1)
    # This read helper owns no pool/transaction/physical operation to detach;
    # a future caller must retain its actual native cleanup through cancellation.


@pytest.mark.parametrize("value", [32, 2**40, b"x", bytes(33)])
def test_native_row_digest_refuses_before_unbounded_conversion(sample, value):
    _, inv = sample
    row = asdict(inv.markers[0])
    row["entry_digest"] = value
    with pytest.raises(JournalError, match="marker_mismatch"):
        inventory_from_rows(inv.journal_id, [row], [asdict(inv.namespaces[0])])
