"""Closed mixed-version negative history; no production initialization."""

from dataclasses import replace
import hashlib
import json
import os
import struct
import time
from uuid import UUID

import pytest

from orchestrator.services.restore_history.model import (
    ARTIFACT_DOMAIN,
    DOMAIN,
    ArtifactPrepare,
    Command,
    HistoryError,
    canonical,
    decode_entry,
    encode_entry,
    validate_chain,
)
from orchestrator.services.restore_history.projection import ProjectionFact, compare_projections
from orchestrator.services.restore_history.store import HistoryStore, Ownership
from tests.restore_history_support import (
    HISTORY,
    ZERO,
    accept,
    stop,
    begin,
    initialize_test_directory,
)


def artifact():
    return Command(
        UUID(int=90),
        ArtifactPrepare(
            UUID(int=10),
            0,
            UUID(int=20),
            accept().record_id,
            1,
            UUID(int=30),
            UUID(int=31),
            UUID(int=32),
            UUID(int=33),
            UUID(int=40),
            UUID(int=41),
            UUID(int=42),
            UUID(int=43),
            UUID(int=44),
            0,
            UUID(int=45),
            "first-output-csv-catalog-v1",
        ),
    )


def entry(command, seq=1, previous=ZERO):
    return decode_entry(
        encode_entry(HISTORY, seq, previous, command, profile="artifact-v2"), profile="artifact-v2"
    )


def test_v1_bytes_unchanged_and_artifact_requires_explicit_version_profile():
    old = encode_entry(HISTORY, 1, ZERO, accept())
    assert old == encode_entry(HISTORY, 1, ZERO, accept(), profile="artifact-v2")
    first = entry(accept())
    command = artifact()
    frame = encode_entry(HISTORY, 2, first.digest, command, profile="artifact-v2")
    assert json.loads(frame[4:-32])["v"] == 2
    assert frame[-32:] == hashlib.sha256(ARTIFACT_DOMAIN + frame[:-32]).digest()
    assert decode_entry(frame, profile="artifact-v2").command == command
    with pytest.raises(HistoryError):
        decode_entry(frame)
    with pytest.raises(HistoryError):
        encode_entry(HISTORY, 2, first.digest, command)


@pytest.mark.parametrize(
    "change",
    [
        "version",
        "kind",
        "actor",
        "extra",
        "boolean",
        "nil",
        "schema",
        "slot",
        "domain",
        "duplicate",
        "spaces",
    ],
)
def test_closed_artifact_format_refuses_alternate_or_noncanonical_data(change):
    frame = encode_entry(HISTORY, 1, ZERO, artifact(), profile="artifact-v2")
    obj = json.loads(frame[4:-32])
    if change == "version":
        obj["v"] = 1
    if change == "kind":
        obj["kind"] = "SEND_POSSIBLE"
    if change == "actor":
        obj["actor"] = "worker"
    if change == "extra":
        obj["data"]["content"] = "forbidden"
    if change == "boolean":
        obj["data"]["epoch"] = True
    if change == "nil":
        obj["data"]["intent_id"] = str(UUID(int=0))
    if change == "schema":
        obj["data"]["operation_schema"] = "append"
    if change == "slot":
        obj["data"]["output_slot"] = 2**31
    payload = canonical(obj)
    if change == "duplicate":
        payload = payload.replace(b'"v":2', b'"v":2,"v":2')
    if change == "spaces":
        payload = b" " + payload
    raw = struct.pack(">I", len(payload)) + payload
    rebuilt = (
        raw + hashlib.sha256((DOMAIN if change == "domain" else ARTIFACT_DOMAIN) + raw).digest()
    )
    with pytest.raises(HistoryError):
        decode_entry(rebuilt, profile="artifact-v2")


def test_mixed_chain_and_projection_never_discard_new_facts():
    first = entry(accept())
    second = entry(artifact(), 2, first.digest)
    state = validate_chain(HISTORY, [first, second], profile="artifact-v2")
    facts = tuple(
        ProjectionFact(
            HISTORY,
            e.seq,
            e.command.record_id,
            e.digest,
            encode_entry(HISTORY, e.seq, e.prev_digest, e.command, profile="artifact-v2"),
        )
        for e in state.entries
    )
    assert compare_projections(HISTORY, state, facts, profile="artifact-v2").missing == ()
    assert compare_projections(HISTORY, state, facts[:1], profile="artifact-v2").missing == (2,)
    with pytest.raises(HistoryError):
        validate_chain(HISTORY, [first, second])
    with pytest.raises(HistoryError):
        compare_projections(HISTORY, state, facts)


@pytest.mark.parametrize(
    "change", ["acceptance", "account", "generation", "new_record", "new_epoch"]
)
def test_negative_intent_identity_cannot_be_refreshed(change):
    first = entry(accept())
    original = artifact()
    second = entry(original, 2, first.digest)
    data = original.data
    if change == "acceptance":
        data = replace(data, acceptance_record_id=UUID(int=900))
    if change == "account":
        data = replace(data, account_id=UUID(int=901))
    if change == "generation":
        data = replace(data, account_generation=1)
    if change == "new_epoch":
        data = replace(data, epoch=2)
    changed = replace(original, record_id=UUID(int=999), data=data)
    prefix = [first, second] if change in ("new_record", "new_epoch") else [first]
    with pytest.raises(HistoryError):
        validate_chain(
            HISTORY,
            [*prefix, entry(changed, len(prefix) + 1, prefix[-1].digest)],
            profile="artifact-v2",
        )


@pytest.mark.parametrize("control", ["stop", "restore"])
def test_negative_control_precedes_artifact(control):
    first = entry(accept())
    middle = entry(stop() if control == "stop" else begin(1, first.digest), 2, first.digest)
    with pytest.raises(HistoryError, match="effect_stopped"):
        validate_chain(
            HISTORY, [first, middle, entry(artifact(), 3, middle.digest)], profile="artifact-v2"
        )


def test_posix_mixed_history_reopens_only_under_explicit_profile(tmp_path):
    path = tmp_path / "history"
    identity = initialize_test_directory(path)
    owner = Ownership(os.getuid(), frozenset({os.getuid()}), os.getgid())
    with HistoryStore(
        path, identity, owner, deadline=time.monotonic() + 3, profile="artifact-v2"
    ) as store:
        assert store.append(accept(), deadline=time.monotonic() + 3).created
        assert store.append(artifact(), deadline=time.monotonic() + 3).created
    with pytest.raises(HistoryError):
        HistoryStore(path, identity, owner, deadline=time.monotonic() + 3)
    with HistoryStore(
        path, identity, owner, deadline=time.monotonic() + 3, profile="artifact-v2"
    ) as store:
        assert len(store.verify(deadline=time.monotonic() + 3).entries) == 2


@pytest.mark.parametrize("fault", ["partial_write", "fsync", "rename"])
def test_v2_interrupted_append_is_retained_without_promotion_or_repair(
    tmp_path, monkeypatch, fault
):
    path = tmp_path / "history"
    identity = initialize_test_directory(path)
    owner = Ownership(os.getuid(), frozenset({os.getuid()}), os.getgid())
    store = HistoryStore(
        path, identity, owner, deadline=time.monotonic() + 3, profile="artifact-v2"
    )
    store.append(accept(), deadline=time.monotonic() + 3)
    with monkeypatch.context() as patch:
        if fault == "partial_write":
            native = HistoryStore._write

            def fail(value, fd, data, deadline):
                if fd == value._fds["LOG"]:
                    os.write(fd, data[:7])
                    raise OSError("synthetic partial v2 frame")
                return native(value, fd, data, deadline)

            patch.setattr(HistoryStore, "_write", fail)
        elif fault == "fsync":
            native_sync = os.fsync

            def fail_sync(fd):
                if fd == store._fds["LOG"]:
                    raise OSError("synthetic v2 fsync")
                return native_sync(fd)

            patch.setattr(os, "fsync", fail_sync)
        else:

            def fail_rename(*args, **kwargs):
                raise OSError("synthetic v2 HEAD rename")

            patch.setattr(os, "replace", fail_rename)
        with pytest.raises(HistoryError):
            store.append(artifact(), deadline=time.monotonic() + 3)
        assert store._poisoned
    store.close()
    evidence = (path / "LOG").read_bytes()
    with pytest.raises(HistoryError):
        HistoryStore(path, identity, owner, deadline=time.monotonic() + 3, profile="artifact-v2")
    assert (path / "LOG").read_bytes() == evidence
