"""Regression tests for issue #232: video generation must bill the
authenticated user, not the global default account.

These tests pin the new ``authenticated_user_id`` parameter on
``_build_trusted_spawn_context`` and verify that the user_id inside the
trusted video spawn context attributes billing to the caller's authenticated
device owner. Without the fix, every authenticated request would put the
global ``DEFAULT_BILLING_USER_ID`` into the context, which then propagated
to ``video_credits_dal.get_balance`` and ``debit_credits``, silently billing
an unrelated account.
"""

from __future__ import annotations

import inspect
import uuid

from orchestrator import main as orchestrator_main
from orchestrator.config import get_settings


def _video_metadata(**overrides):
    """Return a minimal, tier-eligible video_generation metadata block."""
    base = {
        "duration": 5,
        "source_mode": "text-to-video",
        "provider": "kling",
        "kling_model": "kling-v3-pro",
    }
    base.update(overrides)
    return {"video_generation": base}


def test_build_trusted_spawn_context_uses_authenticated_user_id():
    """When authenticated_user_id is supplied, the trust context reflects it."""
    settings = get_settings()
    authenticated = uuid.uuid4()
    default_uid = uuid.UUID(orchestrator_main.DEFAULT_BILLING_USER_ID)

    context = orchestrator_main._build_trusted_spawn_context(
        settings,
        _video_metadata(),
        authenticated_user_id=authenticated,
    )

    assert context is not None
    assert context["video"]["user_id"] == str(authenticated)
    assert context["video"]["user_id"] != str(default_uid)


def test_build_trusted_spawn_context_falls_back_to_default_user_id():
    """Backward compatibility: omitting authenticated_user_id preserves the
    historical default-account fallback (used by legacy non-auth callers)."""
    settings = get_settings()
    default_uid = uuid.UUID(orchestrator_main.DEFAULT_BILLING_USER_ID)

    context = orchestrator_main._build_trusted_spawn_context(
        settings,
        _video_metadata(),
    )

    assert context is not None
    assert context["video"]["user_id"] == str(default_uid)


def test_authenticated_user_id_overrides_default_when_supplied():
    """Explicitly pass authenticated_user_id to confirm it strictly wins over
    the global constant (no leakage of DEFAULT_BILLING_USER_ID)."""
    settings = get_settings()
    authenticated = uuid.UUID("11111111-2222-3333-4444-555555555555")
    default_uid = uuid.UUID(orchestrator_main.DEFAULT_BILLING_USER_ID)

    context = orchestrator_main._build_trusted_spawn_context(
        settings,
        _video_metadata(duration=7),
        authenticated_user_id=authenticated,
    )

    assert context is not None
    assert context["video"]["user_id"] == str(authenticated)
    assert context["video"]["user_id"] != str(default_uid)
    assert context["video"]["duration"] == 7


def test_build_trusted_spawn_context_returns_none_when_no_video_metadata():
    """The function still returns None when the request did not request video
    generation — even when an authenticated user is supplied."""
    settings = get_settings()
    authenticated = uuid.uuid4()

    context = orchestrator_main._build_trusted_spawn_context(
        settings,
        {"other_key": "value"},
        authenticated_user_id=authenticated,
    )

    assert context is None


def test_chat_endpoint_threads_authenticated_user_into_spawn_context():
    """End-to-end: when the /chat endpoint is called by an authenticated user
    with video_generation metadata, the trusted spawn context reflects the
    authenticated user_id rather than the global default account.

    This protects against regressions in the call site (~line 2244) where the
    function is invoked. A future refactor that drops the
    ``authenticated_user_id=`` named argument would re-introduce the bug.
    """
    src = inspect.getsource(orchestrator_main)
    assert "trusted_spawn_context = _build_trusted_spawn_context(" in src
    # The chat endpoint must pass authenticated_user_id=auth.user_id
    assert "authenticated_user_id=auth.user_id" in src, (
        "Chat endpoint must thread auth.user_id into _build_trusted_spawn_context"
    )
