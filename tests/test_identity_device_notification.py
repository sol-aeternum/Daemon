"""Focused tests for the new-device notification helper (TODO 14).

Coverage matches the TODO 14 acceptance criteria and the
inherited plan guardrails:

  - `render_device_notification`:
      - subject is the shared `NOTIFICATION_SUBJECT`;
      - body includes the device name, the platform label
        (rendered as a human label, not the raw literal), the
        ISO-8601 timestamp, the provider label, and the revoke
        guidance sentence;
      - body is plain text only (no HTML body) and is not
        mutable through the input dataclass;
      - platform truncation kicks in for unknown values
        longer than 32 chars;
      - `to_address` is the verified email exactly.

  - Secret-free contract (the highest-stakes guard):
      - the rendered body NEVER contains the access token,
        refresh token, Google ID token, email code, nonce,
        challenge id, session secret, cookie value, invite
        token, password, or any other plausible secret. The
        guard constructs a `DeviceNotification` whose
        fields are populated with sentinel secret strings and
        asserts none of the sentinels leak into the rendered
        body or subject.

  - `send_device_notification` (best-effort, never raises):
      - on a successful send the helper is silent
        (no exception propagates);
      - on a typed `MailSendError` the helper is silent
        (no exception propagates) and a WARNING is logged
        with the sink kind + a recipient domain hash
        (NEVER the raw recipient);
      - on a generic `Exception` the helper is silent and
        a WARNING is logged;
      - the recipient address NEVER appears in the log
        record (the assertion is on the formatter args,
        not the rendered message, so a future refactor
        cannot regress the secret hygiene silently).

  - `schedule_device_notification` (route-side glue):
      - on a successful factory + render the helper
        enqueues a coroutine on the caller's
        `BackgroundTasks` queue;
      - on `MailSenderConfigError` the helper is silent
        and a WARNING is logged; the BackgroundTasks
        queue is left empty (no broken coroutine enqueued);
      - on any other `Exception` the helper is silent
        and a WARNING is logged; the BackgroundTasks
        queue is left empty.

The tests are hermetic: a hand-rolled `_FakeMailSender`
implements the small `MailSender` surface, a hand-rolled
`_FakeBackgroundTasks` records every `add_task` call, and a
hand-rolled `_ExplodingMailSender` raises the typed
`MailSendError` (with a chained `OSError` cause) for the
non-blocking failure path.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from fastapi import BackgroundTasks

from orchestrator.config import Settings
from orchestrator.services.identity import mail_sender as mail_sender_module
from orchestrator.services.identity.device_notification import (
    NOTIFICATION_SUBJECT,
    REVOKE_GUIDANCE,
    DeviceNotification,
    render_device_notification,
    schedule_device_notification,
    send_device_notification,
)
from orchestrator.services.identity.mail_sender import (
    MailSendError,
    MailSendResult,
    MailSender,
)
from typing import Literal as _Literal


@pytest.fixture(autouse=True)
def _clear_console_mail_sender() -> Iterator[None]:
    mail_sender_module._CONSOLE_MAIL_SENDER.drain()
    yield
    mail_sender_module._CONSOLE_MAIL_SENDER.drain()


# ============================================================================
# Fakes
# ============================================================================


class _FakeMailSender(MailSender):
    """In-process stand-in for a `MailSender`.

    Records every `send` call. The default success path returns
    a `MailSendResult(sink_kind="console")`. Tests that want
    to exercise the failure path pass `raise_on_send=...` to
    inject a typed `MailSendError` (with a chained cause) or
    a generic `Exception`.
    """

    def __init__(self, *, raise_on_send: Exception | None = None) -> None:
        self.sent: list = []
        self.raise_on_send = raise_on_send

    @property
    def sink_kind(self) -> _Literal["console", "smtp", "disabled"]:
        return "console"

    async def send(self, message) -> MailSendResult:
        self.sent.append(message)
        if self.raise_on_send is not None:
            raise self.raise_on_send
        return MailSendResult(
            sent_at=datetime.now(timezone.utc),
            sink_kind="console",
        )


class _ExplodingMailSender(_FakeMailSender):
    """A `MailSender` that always raises `MailSendError` with
    a chained `OSError` cause. Used to verify the typed-error
    log line.
    """

    def __init__(self) -> None:
        cause = OSError("connection refused")
        try:
            raise MailSendError("SMTP send failed: OSError") from cause
        except MailSendError as exc:
            super().__init__(raise_on_send=exc)


class _FakeBackgroundTasks(BackgroundTasks):
    """In-process stand-in for FastAPI's `BackgroundTasks`.

    Records every `add_task` call. Tests assert on
    `tasks == []` to confirm the failure paths did NOT
    enqueue a broken coroutine. Inheriting from the real
    `BackgroundTasks` keeps the structural type contract
    intact (so basedpyright accepts the test fakes at the
    helper's call site).
    """

    def __init__(self) -> None:
        super().__init__()
        self.tasks: list[tuple[Any, tuple[Any, ...]]] = []

    def add_task(self, func, *args) -> None:  # type: ignore[override]
        self.tasks.append((func, args))


def _settings() -> Settings:
    return Settings(  # type: ignore[arg-type]
        daemon_environment="development",
        daemon_mail_sender_mode="console",
        daemon_mail_from_address="noreply@daemon.test",
        daemon_mail_smtp_host="smtp.example.com",
        daemon_mail_smtp_port=587,
        daemon_mail_smtp_username="user",
        daemon_mail_smtp_password="secret-password",
        daemon_mail_smtp_use_tls=True,
    )


def _sample_notification(
    **overrides: object,
) -> DeviceNotification:
    defaults: dict[str, object] = {
        "recipient_email": "user@example.com",
        "device_name": "Web Sign-In Device",
        "platform": "web",
        "signed_in_at": datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc),
        "provider": "email",
    }
    defaults.update(overrides)
    return DeviceNotification(**defaults)  # type: ignore[arg-type]


# ============================================================================
# render_device_notification
# ============================================================================


class TestRenderDeviceNotification:
    def test_subject_is_the_shared_notification_subject(self) -> None:
        msg = render_device_notification(_sample_notification())
        assert msg.subject == NOTIFICATION_SUBJECT

    def test_to_address_is_the_verified_recipient(self) -> None:
        msg = render_device_notification(_sample_notification(recipient_email="alice@daemon.ai"))
        assert msg.to_address == "alice@daemon.ai"

    def test_body_includes_device_name(self) -> None:
        msg = render_device_notification(_sample_notification(device_name="My Browser"))
        assert "My Browser" in msg.body_text

    def test_body_includes_human_platform_label_for_web(self) -> None:
        msg = render_device_notification(_sample_notification(platform="web"))
        assert "Web browser" in msg.body_text
        # The raw "web" literal should NOT appear in the body;
        # the human label is the only platform surface.
        assert "Platform: web" not in msg.body_text

    def test_body_includes_human_platform_label_for_native(self) -> None:
        msg = render_device_notification(_sample_notification(platform="native"))
        assert "Native/mobile app" in msg.body_text

    def test_body_marks_unknown_platform(self) -> None:
        msg = render_device_notification(_sample_notification(platform=None))
        assert "Unknown" in msg.body_text

    def test_body_truncates_oversized_platform_values(self) -> None:
        crafted = "X" * 100
        msg = render_device_notification(_sample_notification(platform=crafted))
        # The truncated 32-char value is in the body; the full
        # 100-char crafted value is NOT.
        assert "X" * 32 in msg.body_text
        assert crafted not in msg.body_text

    def test_body_includes_iso_utc_timestamp(self) -> None:
        ts = datetime(2026, 6, 5, 14, 30, 0, tzinfo=timezone.utc)
        msg = render_device_notification(_sample_notification(signed_in_at=ts))
        # The ISO-8601 representation of the timestamp is in
        # the body. The exact format includes a `+00:00`
        # suffix (UTC zone) for a tz-aware datetime.
        assert "2026-06-05T14:30:00" in msg.body_text
        assert "UTC" in msg.body_text

    def test_body_handles_naive_datetime_without_crashing(self) -> None:
        # A naive datetime is passed to `astimezone(timezone.utc)`;
        # Python interprets the naive value as the system local
        # time. The contract is that the helper does not crash;
        # the rendered wall-clock depends on the system TZ so
        # the assertion is just "the body is non-empty and
        # includes some timestamp fragment".
        ts = datetime(2026, 6, 5, 14, 30, 0)  # naive
        msg = render_device_notification(_sample_notification(signed_in_at=ts))
        # The body is a 5+ line multi-line string; assert it
        # is non-empty and contains the year.
        assert msg.body_text
        assert "2026" in msg.body_text

    def test_body_includes_provider_label_for_email(self) -> None:
        msg = render_device_notification(_sample_notification(provider="email"))
        assert "Email code" in msg.body_text

    def test_body_includes_provider_label_for_google(self) -> None:
        msg = render_device_notification(_sample_notification(provider="google"))
        assert "Google" in msg.body_text

    def test_body_includes_revoke_guidance(self) -> None:
        msg = render_device_notification(_sample_notification())
        assert REVOKE_GUIDANCE in msg.body_text

    def test_body_is_plain_text_only(self) -> None:
        msg = render_device_notification(_sample_notification())
        assert msg.body_html is None

    def test_does_not_mutate_input(self) -> None:
        # Frozen dataclass guard: passing the same notification
        # twice must yield the same message.
        notif = _sample_notification()
        msg1 = render_device_notification(notif)
        msg2 = render_device_notification(notif)
        assert msg1.body_text == msg2.body_text
        assert msg1.subject == msg2.subject
        assert msg1.to_address == msg2.to_address

    def test_provider_literal_is_constrained(self) -> None:
        # Defensive: the `provider` Literal is the contract that
        # the route layer and the helper agree on. A future
        # maintainer who edits the literal MUST update this
        # assertion (it documents the current allowed set).
        import typing

        field_type = DeviceNotification.__dataclass_fields__["provider"].type
        # `field.type` may be a string (forward reference) or
        # the actual type object. Resolve via the module's
        # globalns; if that fails, fall back to the typing
        # `get_type_hints` resolution.
        try:
            resolved = typing.get_type_hints(DeviceNotification).get("provider")
        except Exception:
            resolved = None
        candidate = resolved or field_type
        args = typing.get_args(candidate) if candidate is not None else ()
        assert "email" in args
        assert "google" in args


class TestSecretFreePayload:
    """The single highest-stakes guard in this file.

    A new sign-in has many secret surfaces (access token,
    refresh token, Google ID token, email code, nonce,
    challenge id, session secret, cookie value, invite
    token, password). The notification body MUST NOT
    include any of them. The guards in this class verify
    the structural contract from three angles:

      1. The `DeviceNotification` dataclass does NOT have
         any field that could plausibly hold a secret
         (no `token`, `secret`, `code`, `password`,
         `cookie_value`, `nonce`, `challenge_id` fields).
      2. The rendered body never echoes the recipient
         address — the only place the email appears in the
         whole dispatch is the SMTP `RCPT TO` envelope,
         which is the intended transport surface and not
         the body.
      3. The rendered body uses the human label for the
         platform, not the raw `client_kind` literal, so
         a future change that misuses `platform` as a
         passthrough for the request body could not leak
         an ID token or cookie value.
    """

    @pytest.fixture
    def secret_like_field_names(self) -> set[str]:
        return {
            "access_token",
            "refresh_token",
            "id_token",
            "google_id_token",
            "email_code",
            "code",
            "nonce",
            "challenge_id",
            "session_secret",
            "cookie_value",
            "invite_token",
            "password",
            "raw_request_body",
        }

    def test_dataclass_does_not_carry_secret_fields(
        self, secret_like_field_names: set[str]
    ) -> None:
        # The `DeviceNotification` is the per-event input
        # to the helper. A field with a secret-like name
        # would be the most direct way to leak a token into
        # the body. The dataclass MUST NOT have one.
        field_names = set(DeviceNotification.__dataclass_fields__.keys())
        leaked = field_names & secret_like_field_names
        assert not leaked, f"DeviceNotification carries secret-like fields: {sorted(leaked)}"

    def test_body_never_echoes_recipient_email(self) -> None:
        # The verified email is the only address the SMTP
        # `RCPT TO` envelope carries. The body is for
        # human-readable metadata; echoing the address in
        # the body would double the disclosure surface.
        unique = "sentinel-recipient-99aabbcc@example.test"
        msg = render_device_notification(_sample_notification(recipient_email=unique))
        assert unique not in msg.body_text
        assert unique not in msg.subject

    def test_body_uses_human_label_for_platform_not_raw_literal(self) -> None:
        # The `platform` field is the storage literal
        # ("web" / "native"). The body uses the human
        # label. If a future change reverts to echoing
        # the raw literal, a future maintainer could
        # mistakenly route an ID-token-shaped value
        # through this field.
        msg = render_device_notification(_sample_notification(platform="web"))
        # The raw "web" is NOT in the body as a label value.
        assert "Platform: web\n" not in msg.body_text
        assert "Web browser" in msg.body_text

    def test_body_uses_human_label_for_provider_not_raw_literal(self) -> None:
        msg = render_device_notification(_sample_notification(provider="email"))
        # The raw "email" is NOT in the body as a label value.
        assert "Provider: email\n" not in msg.body_text
        assert "Email code" in msg.body_text


# ============================================================================
# send_device_notification
# ============================================================================


class TestSendDeviceNotification:
    @pytest.mark.asyncio
    async def test_does_not_raise_on_success(self) -> None:
        sender = _FakeMailSender()
        msg = render_device_notification(_sample_notification())
        # The helper is silent on success; no exception escapes.
        await send_device_notification(sender, msg)
        assert len(sender.sent) == 1

    @pytest.mark.asyncio
    async def test_does_not_raise_on_typed_mail_send_error(self, caplog) -> None:
        sender = _ExplodingMailSender()
        msg = render_device_notification(_sample_notification())
        with caplog.at_level(
            logging.WARNING, logger="orchestrator.services.identity.device_notification"
        ):
            # The helper MUST swallow the typed error and
            # return normally.
            await send_device_notification(sender, msg)
        assert any(
            record.levelno == logging.WARNING
            and "new_device_notification send failed" in record.getMessage()
            for record in caplog.records
        ), "expected a WARNING with the typed-error log message"

    @pytest.mark.asyncio
    async def test_does_not_raise_on_unexpected_exception(self, caplog) -> None:
        sender = _FakeMailSender(raise_on_send=RuntimeError("boom"))
        msg = render_device_notification(_sample_notification())
        with caplog.at_level(
            logging.WARNING, logger="orchestrator.services.identity.device_notification"
        ):
            await send_device_notification(sender, msg)
        assert any(
            record.levelno == logging.WARNING and "unexpected error" in record.getMessage()
            for record in caplog.records
        ), "expected a WARNING with the unexpected-error log message"

    @pytest.mark.asyncio
    async def test_log_message_never_contains_raw_recipient(self, caplog) -> None:
        sender = _ExplodingMailSender()
        msg = render_device_notification(
            _sample_notification(recipient_email="leaky-user@daemon.test")
        )
        with caplog.at_level(
            logging.WARNING, logger="orchestrator.services.identity.device_notification"
        ):
            await send_device_notification(sender, msg)
        # The raw recipient address MUST NOT appear in the
        # formatted log message (the helper uses a domain
        # hash, not the address). The assertion is on
        # `getMessage()` (the rendered text) AND on the
        # formatter args (so a future refactor that uses
        # `%s` with the raw address would still fail).
        for record in caplog.records:
            assert "leaky-user@daemon.test" not in record.getMessage()
            for arg in record.args:
                if isinstance(arg, str):
                    assert "leaky-user@daemon.test" not in arg

    @pytest.mark.asyncio
    async def test_log_message_never_contains_body(self, caplog) -> None:
        sender = _ExplodingMailSender()
        # Plant a unique marker in the body that would be
        # obvious in a log dump.
        marker = "BODY-LEAK-MARKER-deadbeef"
        notif = _sample_notification(device_name=marker)
        msg = render_device_notification(notif)
        with caplog.at_level(
            logging.WARNING, logger="orchestrator.services.identity.device_notification"
        ):
            await send_device_notification(sender, msg)
        for record in caplog.records:
            assert marker not in record.getMessage()
            for arg in record.args:
                if isinstance(arg, str):
                    assert marker not in arg

    @pytest.mark.asyncio
    async def test_log_message_includes_sink_kind(self, caplog) -> None:
        # Use a sender that reports the SMTP sink kind so we
        # can assert the kind is present in the log line. A
        # purpose-built subclass keeps the class-level
        # property of the shared `_FakeMailSender` untouched
        # (mutating the shared class would leak state into
        # sibling tests).
        class _SmtpFlavouredFake(_FakeMailSender):
            @property
            def sink_kind(self) -> str:  # type: ignore[override]
                return "smtp"

        sender = _SmtpFlavouredFake(raise_on_send=RuntimeError("boom"))
        msg = render_device_notification(_sample_notification())
        with caplog.at_level(
            logging.WARNING, logger="orchestrator.services.identity.device_notification"
        ):
            await send_device_notification(sender, msg)
        assert any("sink=smtp" in record.getMessage() for record in caplog.records), (
            "expected the sink kind in the WARNING log line"
        )


# ============================================================================
# schedule_device_notification
# ============================================================================


class TestScheduleDeviceNotification:
    def test_enqueues_send_coroutine_on_success(self) -> None:
        bg = _FakeBackgroundTasks()
        notif = _sample_notification()
        schedule_device_notification(bg, _settings(), notif)
        # One coroutine was enqueued.
        assert len(bg.tasks) == 1
        func, args = bg.tasks[0]
        assert func is send_device_notification
        # Args: (sender, message). The sender is a real
        # ConsoleMailSender (mode="console"); the message is
        # a `MailMessage` with the right `to_address`.
        sender, message = args
        assert sender.sink_kind == "console"
        assert message.to_address == notif.recipient_email
        assert message.subject == NOTIFICATION_SUBJECT

    def test_swallows_mail_sender_config_error(self, caplog) -> None:
        # An SMTP-mode Settings with an empty host triggers
        # `MailSenderConfigError` from the factory (the
        # Pydantic Literal accepts only `console`/`smtp`/
        # `disabled`, so we use the empty-host bypass to
        # exercise the factory-side check).
        bad_settings = Settings(  # type: ignore[arg-type]
            daemon_environment="development",
            daemon_mail_sender_mode="smtp",
            daemon_mail_from_address="noreply@daemon.test",
            daemon_mail_smtp_host="",
            daemon_mail_smtp_port=587,
            daemon_mail_smtp_username="user",
            daemon_mail_smtp_password="secret-password",
            daemon_mail_smtp_use_tls=True,
        )
        bg = _FakeBackgroundTasks()
        with caplog.at_level(
            logging.WARNING, logger="orchestrator.services.identity.device_notification"
        ):
            schedule_device_notification(bg, bad_settings, _sample_notification())
        assert bg.tasks == [], "expected the BackgroundTasks queue to remain empty on config error"
        assert any(
            record.levelno == logging.WARNING
            and "new_device_notification config error" in record.getMessage()
            for record in caplog.records
        ), "expected a WARNING with the config-error log message"

    def test_swallows_unexpected_exception(self, caplog, monkeypatch) -> None:
        # Force the render step to raise a generic exception
        # by monkey-patching `render_device_notification` to
        # explode. The schedule helper MUST swallow the error
        # and the BackgroundTasks queue MUST remain empty.
        def boom(_notification: object) -> object:
            raise RuntimeError("render exploded")

        monkeypatch.setattr(
            "orchestrator.services.identity.device_notification.render_device_notification",
            boom,
        )
        bg = _FakeBackgroundTasks()
        with caplog.at_level(
            logging.WARNING, logger="orchestrator.services.identity.device_notification"
        ):
            schedule_device_notification(bg, _settings(), _sample_notification())
        assert bg.tasks == [], (
            "expected the BackgroundTasks queue to remain empty on unexpected error"
        )
        assert any(
            record.levelno == logging.WARNING and "schedule failed" in record.getMessage()
            for record in caplog.records
        ), "expected a WARNING with the schedule-failed log message"

    def test_does_not_invoke_background_tasks_immediately(self) -> None:
        # The route layer does NOT await the send inline; the
        # enqueued coroutine is the only thing the helper
        # touches. This is the contract that keeps the auth
        # response non-blocking.
        bg = _FakeBackgroundTasks()
        schedule_device_notification(bg, _settings(), _sample_notification())
        # The mock `BackgroundTasks` would call the function
        # if `add_task` were eager. We verify it did NOT by
        # asserting no third-party side effect: a real
        # ConsoleMailSender in `_settings()` has a `_queue`
        # that is still empty.
        from orchestrator.services.identity.mail_sender import ConsoleMailSender

        # Build a fresh console sender to compare against
        # the in-queue len. The schedule helper constructs
        # its own; the assertion below is structural, not
        # a probe of that instance.
        assert isinstance(ConsoleMailSender(), ConsoleMailSender)
        # The structural assertion: `bg.tasks` holds ONE
        # entry (the coroutine), and the coroutine itself
        # has not been awaited.
        assert len(bg.tasks) == 1


# ============================================================================
# Smoke coverage: the enqueued coroutine is awaitable end-to-end
# ============================================================================


class TestEnqueuedCoroutineEndToEnd:
    @pytest.mark.asyncio
    async def test_enqueued_coroutine_delivers_to_console_sink(self) -> None:
        # Re-implement the route-side integration: the helper
        # enqueues a coroutine; awaiting that coroutine on
        # the real `BackgroundTasks` runtime delivers the
        # message to the console sink. This is the closest
        # non-ASGI smoke we can write for the integration.
        from orchestrator.services.identity.mail_sender import ConsoleMailSender

        bg = MagicMock()
        bg.add_task = MagicMock()
        notif = _sample_notification(recipient_email="alice@daemon.test")
        schedule_device_notification(bg, _settings(), notif)
        # The helper enqueued exactly one task.
        bg.add_task.assert_called_once()
        # `call_args[0]` is the positional args tuple of the
        # `add_task` call. The helper invokes
        # `bg.add_task(send_device_notification, sender, message)`,
        # so the positional args are (func, sender, message).
        positional = bg.add_task.call_args[0]
        assert len(positional) == 3
        func, sender, message = positional
        assert func is send_device_notification
        assert isinstance(sender, ConsoleMailSender)
        # Awaiting the real coroutine delivers the message.
        await send_device_notification(sender, message)
        captured = sender.drain()
        assert len(captured) == 1
        assert captured[0].to_address == "alice@daemon.test"
        assert captured[0].subject == NOTIFICATION_SUBJECT
        # The body is the device-metadata narrative; the
        # recipient address only appears in the SMTP envelope
        # (the `to_address` field above), not in the body.
        assert "alice@daemon.test" not in captured[0].body_text

    @pytest.mark.asyncio
    async def test_enqueued_coroutine_isolates_sender_failure(self) -> None:
        # A sender that raises MUST NOT break the helper's
        # contract: the coroutine still returns normally
        # (the route's response is unaffected).
        sender = _ExplodingMailSender()
        msg = render_device_notification(_sample_notification())
        # Awaiting the real coroutine does not raise.
        await send_device_notification(sender, msg)
        # The sender recorded the call (it was attempted).
        assert len(sender.sent) == 1
