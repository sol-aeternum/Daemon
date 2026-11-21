"""Closed native finish checks for the isolated terminal profiles."""

from orchestrator.turn_finish import TurnFinish, parse_turn_finish

from .model import NONSUCCESS_LOCAL_LIMIT_KINDS


def returned_finish(value: object) -> TurnFinish:
    # The native body serializes this exact representation. No nulls, omitted
    # lists, unrelated metadata or alternate protocol versions are admitted.
    finish = parse_turn_finish(value)
    if finish is None or finish.model_dump(exclude_none=True) != value:
        raise ValueError("unsupported finish envelope")
    provider = finish.provider
    reason, outcome = finish.reason, finish.outcome
    binds = any(limit.binding for limit in finish.limits)
    if reason == "provider_stop":
        valid = outcome == "complete" and provider.reason == "stop" and not binds
    elif reason == "provider_length":
        valid = outcome == "incomplete" and provider.reason == "length" and not binds
    elif reason == "provider_content_filter":
        valid = outcome == "failed" and provider.reason == "content_filter" and not binds
    elif reason == "finish_evidence_missing":
        valid = outcome == "unknown" and provider.state == "missing" and not binds
    elif reason == "finish_evidence_conflict":
        valid = (
            outcome == "unknown"
            and (provider.state == "conflicting" or provider.reason == "tool_calls")
            and not binds
        )
    elif reason == "stream_done_with_pending":
        valid = outcome == "failed"
    else:
        valid = outcome == "incomplete" and any(
            limit.binding and limit.kind == NONSUCCESS_LOCAL_LIMIT_KINDS.get(reason)
            for limit in finish.limits
        )
    if not valid:
        raise ValueError("inconsistent finish evidence")
    return finish


def finish_matches(finish: TurnFinish, status: str, reason: str | None) -> bool:
    if status == "completed":
        return finish.outcome == "complete"
    if status == "failed":
        return finish.outcome in ("incomplete", "failed") and finish.reason == reason
    return (
        status == "needs_attention"
        and finish.outcome in ("incomplete", "failed", "unknown")
        and (
            reason == "uncertain_effect"
            or (finish.outcome == "unknown" and finish.reason == reason)
        )
    )
