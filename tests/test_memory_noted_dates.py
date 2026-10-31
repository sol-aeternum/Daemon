"""Dormant noted-dates rendering (#601 option C) and its declared-candidate guards."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.eval import campaign_launch as launch
from orchestrator.eval import campaign_validation, fact_harness
from orchestrator.eval.campaign_guard import CampaignStopped
from orchestrator.memory import injection, provenance

NOTED = datetime(2023, 2, 12, 6, 29, tzinfo=UTC)


def _inputs(*memories: dict[str, object]) -> injection.MemoryContextInputs:
    return injection.MemoryContextInputs(retrieved=list(memories))


@pytest.fixture()
def noted_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provenance, "RENDER_NOTED_DATES", True)


def test_dormant_flag_is_off_and_renders_exactly_as_before() -> None:
    assert provenance.RENDER_NOTED_DATES is False
    rendered = injection.render_memory_context(
        _inputs(
            {
                "content": "User watched the Super Bowl today.",
                "category": "fact",
                "valid_from": NOTED,
            }
        )
    )
    assert rendered == (
        '<memory_records trust="user_data">\nAbout this user:\n'
        "- Fact: User watched the Super Bowl today.\n</memory_records>"
    )


@pytest.mark.usefixtures("noted_dates")
def test_noted_dates_add_header_and_utc_date_suffix() -> None:
    adelaide = timezone(timedelta(hours=10, minutes=30))
    rendered = injection.render_memory_context(
        _inputs(
            {
                "content": "User watched the Super Bowl today.",
                "category": "fact",
                "valid_from": NOTED,
            },
            # 2023-03-20 08:00 in Adelaide is 2023-03-19 UTC.
            {
                "content": "User ran a 5K",
                "category": "fact",
                "valid_from": datetime(2023, 3, 20, 8, 0, tzinfo=adelaide),
            },
            {"content": "User has a cat", "category": "fact"},
        )
    )
    lines = rendered.splitlines()
    assert lines[1:3] == ["About this user:", provenance.NOTED_DATES_HEADER]
    assert lines[3] == "- Fact: User watched the Super Bowl today. (noted 2023-02-12)"
    assert lines[4] == "- Fact: User ran a 5K (noted 2023-03-19)"
    assert lines[5] == "- Fact: User has a cat"


@pytest.mark.usefixtures("noted_dates")
def test_noted_date_survives_truncation_of_a_long_memory() -> None:
    rendered = injection.render_memory_context(
        _inputs({"content": "x " * 400, "category": "fact", "valid_from": NOTED})
    )
    assert rendered.splitlines()[3].endswith("... (noted 2023-02-12)")


@pytest.mark.usefixtures("noted_dates")
def test_naive_timestamps_are_read_as_utc_and_created_at_is_the_fallback() -> None:
    assert provenance.noted_suffix({"valid_from": datetime(2023, 2, 12, 23, 59)}) == (
        " (noted 2023-02-12)"
    )
    assert provenance.noted_suffix({"created_at": NOTED}) == " (noted 2023-02-12)"
    assert provenance.noted_suffix({"valid_from": "2023-02-12"}) == ""


def test_no_header_without_memories(noted_dates: None) -> None:
    assert provenance.NOTED_DATES_HEADER not in injection.render_memory_context(_inputs())


def test_formatter_identity_records_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    off = fact_harness.memory_formatter_identity()
    monkeypatch.setattr(provenance, "RENDER_NOTED_DATES", True)
    on = fact_harness.memory_formatter_identity()
    assert off["render_noted_dates"] is False and on["render_noted_dates"] is True
    assert off["source_sha256"] == on["source_sha256"]
    assert off["output_sha256"] != on["output_sha256"]


SOURCE = b'"""doc"""\nRENDER_NOTED_DATES = False\n\ndef noted_suffix(): ...\n'


def test_noted_dates_candidate_must_flip_only_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    flipped = SOURCE.replace(b"= False", b"= True")
    sources = {"control": SOURCE, "candidate": flipped}
    monkeypatch.setattr(launch, "git_bytes", lambda sha, path: sources[sha])
    identity = launch.noted_dates_candidate_formatter("control", "candidate")
    assert identity["render_noted_dates"] is True
    assert provenance.RENDER_NOTED_DATES is False  # restored

    sources["candidate"] = flipped + b"EXTRA = 1\n"
    with pytest.raises(CampaignStopped, match="more than the dormant flag"):
        launch.noted_dates_candidate_formatter("control", "candidate")
    sources["candidate"] = SOURCE
    with pytest.raises(CampaignStopped, match="more than the dormant flag"):
        launch.noted_dates_candidate_formatter("control", "candidate")


def test_arm_formatter_pins_c0_only_for_declared_candidates() -> None:
    plain = SimpleNamespace(pins={"formatter": {"id": "control"}})
    declared = SimpleNamespace(
        pins={"formatter": {"id": "control"}, "candidate_formatter": {"id": "candidate"}}
    )
    assert launch.arm_formatter(plain, "C0") == {"id": "control"}  # type: ignore[arg-type]
    assert launch.arm_formatter(declared, "A2") == {"id": "control"}  # type: ignore[arg-type]
    assert launch.arm_formatter(declared, "C0") == {"id": "candidate"}  # type: ignore[arg-type]


def _manifest(formatter: str) -> dict[str, Any]:
    return {
        "substrate": "fact",
        "config_pin": "pin",
        "memory_formatter": formatter,
        "parity_exceptions": [],
        "dataset": {"sha256": "dev"},
        "routes": {
            k: k for k in ("answer", "judge", "official_judge", "inference_policy", "model_routing")
        },
        "phases": {"evaluate": {"retrieval_modes": {"embedding": 50}}},
    }


def test_comparability_skips_formatter_only_when_asked() -> None:
    campaign_validation.comparability(_manifest("a"), _manifest("b"), formatter=False)
    with pytest.raises(CampaignStopped, match="memory_formatter"):
        campaign_validation.comparability(_manifest("a"), _manifest("b"))
    with pytest.raises(CampaignStopped, match="parity_exceptions"):
        changed = _manifest("b")
        changed["parity_exceptions"] = [{"id": "x"}]
        campaign_validation.comparability(_manifest("a"), changed, formatter=False)


def test_verify_common_accepts_only_the_declared_candidate_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launch, "git_bytes", lambda *_: b"same")

    def diff(changed: list[str]) -> Any:
        return lambda *a, **k: SimpleNamespace(stdout="\n".join(changed) + "\n")

    monkeypatch.setattr(launch.subprocess, "run", diff([launch.PROVENANCE_CANDIDATE]))
    launch.verify_common("c", "k", launch.PROVENANCE_CANDIDATE)
    with pytest.raises(CampaignStopped, match="second execution variable"):
        launch.verify_common("c", "k")
    monkeypatch.setattr(
        launch.subprocess, "run", diff([launch.PROVENANCE_CANDIDATE, launch.RETRIEVAL_CANDIDATE])
    )
    with pytest.raises(CampaignStopped, match="second execution variable"):
        launch.verify_common("c", "k", launch.PROVENANCE_CANDIDATE)
