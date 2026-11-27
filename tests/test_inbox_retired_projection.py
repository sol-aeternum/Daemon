"""The narrowly selected, success-only retirement wire profile."""

from dataclasses import replace
import hashlib
import json
import struct
from uuid import UUID

import pytest

from orchestrator.services.restore_history.model import (
    Command,
    HistoryError,
    INBOX_RETIRED_DOMAIN,
    InboxOriginalRetired,
    canonical,
    decode_entry,
    encode_entry,
    validate_chain,
)
from tests.restore_history_support import HISTORY, ZERO, accept, assessed, begin, send, stop
from tests.test_artifact_history_model import artifact
from tests.test_terminal_history_model import chain, terminal, nonsuccess

PROFILE = "inbox-retired-v1"


def retirement():
    return Command(UUID(int=102), InboxOriginalRetired(terminal().record_id, send().record_id))


def test_retirement_frame_is_closed_and_legacy_frames_unchanged():
    for command in (accept(), send(), terminal(), stop()):
        assert encode_entry(HISTORY, 1, ZERO, command, profile=PROFILE) == encode_entry(
            HISTORY, 1, ZERO, command, profile="terminal-v3"
        )
    frame = encode_entry(HISTORY, 4, ZERO, retirement(), profile=PROFILE)
    assert frame[-32:] == hashlib.sha256(INBOX_RETIRED_DOMAIN + frame[:-32]).digest()
    assert decode_entry(frame, profile=PROFILE).command == retirement()
    payload = json.loads(frame[4:-32])
    assert (payload["kind"], payload["actor"], payload["v"]) == (
        "INBOX_ORIGINAL_RETIRED",
        "worker",
        5,
    )
    assert set(payload["data"]) == {"terminal_record_id", "send_record_id"}
    for profile in ("h1", "artifact-v2", "terminal-v3", "terminal-v4"):
        with pytest.raises(HistoryError):
            decode_entry(frame, profile=profile)
        with pytest.raises(HistoryError):
            encode_entry(HISTORY, 4, ZERO, retirement(), profile=profile)
    with pytest.raises(HistoryError):
        encode_entry(HISTORY, 1, ZERO, nonsuccess(), profile=PROFILE)


def test_retirement_requires_exact_untouched_ancestors():
    originals = (accept(), send(), terminal())
    valid = chain(*originals, retirement(), profile=PROFILE)
    assert validate_chain(HISTORY, valid, profile=PROFILE).entries == tuple(valid)
    additional_send = replace(
        send(),
        record_id=UUID(int=800),
        data=replace(send().data, dispatch_nonce=UUID(int=801)),
    )
    wrong_send = replace(send(), data=replace(send().data, effect_kind="embedding"))
    wrong_epoch = replace(send(), data=replace(send().data, epoch=2))
    foreign_account_id = UUID(int=811)
    foreign_task_id = UUID(int=812)
    foreign = replace(
        accept(),
        record_id=UUID(int=810),
        data=replace(
            accept().data,
            account_id=foreign_account_id,
            task_id=foreign_task_id,
            key_commitment="c" * 64,
        ),
    )
    foreign_send = replace(
        send(),
        record_id=UUID(int=813),
        data=replace(
            send().data,
            account_id=foreign_account_id,
            task_id=foreign_task_id,
            dispatch_nonce=UUID(int=814),
        ),
    )
    restore = begin()
    restored = assessed(1, chain(restore, profile=PROFILE)[0].digest)
    for commands in (
        (accept(), terminal()),
        (accept(), wrong_epoch, terminal()),
        (accept(), terminal(), retirement()),
        (accept(), send(), retirement(), terminal()),
        (accept(), send(), additional_send, terminal(), retirement()),
        (*originals, foreign, retirement()),
        (
            *originals,
            foreign,
            foreign_send,
            replace(
                retirement(),
                data=InboxOriginalRetired(terminal().record_id, foreign_send.record_id),
            ),
        ),
        (accept(), wrong_send, terminal(), retirement()),
        (accept(), wrong_epoch, terminal(), retirement()),
        (accept(), send(), artifact(), terminal(), retirement()),
        (restore, restored, *originals, retirement()),
        (*originals, stop(), retirement()),
        (*originals, retirement(), foreign),
        (*originals, retirement(), stop()),
        (*originals, retirement(), replace(retirement(), record_id=UUID(int=802))),
        (
            *originals,
            replace(
                retirement(), data=InboxOriginalRetired(send().record_id, terminal().record_id)
            ),
        ),
    ):
        with pytest.raises(HistoryError):
            validate_chain(HISTORY, chain(*commands, profile=PROFILE), profile=PROFILE)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: {**payload, "actor": "backend"},
        lambda payload: {**payload, "v": 4},
        lambda payload: {**payload, "seq": 16385},
        lambda payload: {**payload, "data": {**payload["data"], "ack": True}},
        lambda payload: {
            **payload,
            "data": {**payload["data"], "send_record_id": str(UUID(int=0))},
        },
        lambda payload: {
            **payload,
            "data": {**payload["data"], "send_record_id": payload["data"]["terminal_record_id"]},
        },
        lambda payload: {
            **payload,
            "record_id": payload["data"]["send_record_id"],
        },
    ],
)
def test_retirement_decoder_rejects_rehashed_wrong_shape(mutate):
    frame = encode_entry(HISTORY, 4, ZERO, retirement(), profile=PROFILE)
    raw = canonical(mutate(json.loads(frame[4:-32])))
    body = struct.pack(">I", len(raw)) + raw
    with pytest.raises(HistoryError):
        decode_entry(body + hashlib.sha256(INBOX_RETIRED_DOMAIN + body).digest(), profile=PROFILE)
