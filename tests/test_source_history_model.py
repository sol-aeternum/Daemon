"""The source profile's closed mixed history grammar."""

from dataclasses import replace
from typing import Literal
from uuid import UUID

import pytest

from orchestrator.services.restore_history.model import (
    Command,
    HistoryError,
    SendPossible,
    SourcePinPrepare,
    SourceReadPossible,
    decode_entry,
    encode_entry,
    validate_chain,
)
from tests.restore_history_support import HISTORY, ZERO, accept, send, stop
from tests.test_terminal_history_model import chain, terminal


PROFILE = "inbox-sources-v1"


def priced(index: int, kind: Literal["completion", "web_search"] = "completion"):
    template = send().data
    assert isinstance(template, SendPossible)
    return Command(
        UUID(int=100 + index),
        replace(
            template,
            effect_kind=kind,
            reservation_id=UUID(int=200 + index),
            runtime_identity=UUID(int=300 + index),
            dispatch_nonce=UUID(int=400 + index),
        ),
    )


def possible(ordinal=1, count=2):
    return Command(
        UUID(int=500 + ordinal),
        SourceReadPossible(
            UUID(int=10),
            0,
            UUID(int=20),
            1,
            accept().record_id,
            ordinal,
            count,
            priced(2, "web_search").record_id,
            priced(3).record_id,
            str(ordinal) * 64,
        ),
    )


def pin(ordinal=1):
    return Command(
        UUID(int=600 + ordinal),
        SourcePinPrepare(
            UUID(int=10),
            0,
            UUID(int=20),
            1,
            accept().record_id,
            ordinal,
            possible(ordinal).record_id,
            UUID(int=700 + ordinal),
            10 + ordinal,
            str(ordinal + 3) * 64,
        ),
    )


def prefix():
    return [accept(), priced(1), priced(2, "web_search"), priced(3)]


def verify(commands):
    entries = chain(*commands, profile=PROFILE)
    return validate_chain(HISTORY, entries, profile=PROFILE)


@pytest.mark.parametrize("count", [1, 2])
def test_every_legal_prefix_is_observable_without_completing_it(count):
    commands = prefix()
    for ordinal in range(1, count + 1):
        commands.extend((possible(ordinal, count), pin(ordinal)))
    commands.extend((priced(4), terminal()))
    for end in range(1, len(commands) + 1):
        assert len(verify(commands[:end]).entries) == end


@pytest.mark.parametrize(
    "tail",
    [
        lambda: [pin()],
        lambda: [possible(), priced(4)],
        lambda: [possible(), pin(), priced(4)],
        lambda: [possible(), pin(), possible(2, 1)],
        lambda: [possible(), stop(), pin()],
        lambda: [possible(), pin(), possible(2), pin(1)],
        lambda: [possible(1, 1), pin(), possible(2)],
        lambda: [terminal()],
    ],
)
def test_bad_source_sequences_are_refused(tail):
    with pytest.raises(HistoryError):
        verify(prefix() + tail())


@pytest.mark.parametrize(
    "profile",
    ["h1", "artifact-v2", "terminal-v3", "terminal-v4", "inbox-retired-v1", "inbox-research-v1"],
)
def test_v6_records_are_closed_to_legacy_profiles(profile):
    for command in (possible(), pin()):
        raw = encode_entry(HISTORY, 1, ZERO, command, profile=PROFILE)
        with pytest.raises(HistoryError):
            decode_entry(raw, profile=profile)
    assert encode_entry(HISTORY, 1, ZERO, accept(), profile=profile) == encode_entry(
        HISTORY, 1, ZERO, accept(), profile=PROFILE
    )
