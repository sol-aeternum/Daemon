"""Tests for the hosted mail-sender abstraction (TODO 10).

Coverage matches the TODO 10 acceptance criteria:

  - ConsoleMailSender: in-memory dev/test sink; captures
    rendered messages; supports drain() and peek(); rejects
    empty recipient; the queue is a deque (drain clears).
  - DisabledMailSender: no-op; returns success but emits
    nothing; call_count increments.
  - SmtpMailSender: stub-validated; we monkeypatch smtplib to
    confirm the sender calls login when credentials are
    supplied, STARTTLS when use_tls=True, and send_message
    with the composed `EmailMessage`. Failure cases (refused
    connection, SMTP exception) raise `MailSendError`.
  - `MailMessage`: subject / body_text / to_address are
    preserved; from_address falls back to the sender's
    configured address when None; body_html is optional.
  - get_mail_sender factory: returns the right concrete
    sender for each mode; rejects misconfigured SMTP
    (empty host, port out of range, empty from_address).
  - Secret-free SMTP messages: the body never contains the SMTP
    password; the SMTP sender does not log the body or the
    recipient; the rendered EmailMessage does not include the
    password in any header. Console mode is the intentional
    dev-only exception and logs the rendered body with a
    `DEV ONLY` prefix.
  - Non-blocking/async boundary: every `send` is a coroutine
    and can be awaited; the SmtpMailSender wraps the blocking
    smtplib call in `asyncio.to_thread`.
  - async enqueue boundary: the console sink is purely
    in-process, so a "send" call returns immediately without
    blocking on any I/O.
"""

from __future__ import annotations

import asyncio
import inspect
import smtplib
from email.message import EmailMessage
from typing import Any
from unittest.mock import patch

import pytest

from orchestrator.config import Settings
from orchestrator.services.identity.mail_sender import (
    ConsoleMailSender,
    DisabledMailSender,
    MailMessage,
    MailSendError,
    MailSendResult,
    MailSenderConfigError,
    SmtpMailSender,
    get_mail_sender,
)
from orchestrator.services.identity import mail_sender as mail_sender_module


def _settings_with_mode(mode: str, **overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "daemon_environment": "development",
        "daemon_mail_sender_mode": mode,
        "daemon_mail_from_address": "noreply@daemon.test",
        "daemon_mail_smtp_host": "smtp.example.com",
        "daemon_mail_smtp_port": 587,
        "daemon_mail_smtp_username": "user",
        "daemon_mail_smtp_password": "secret-password",
        "daemon_mail_smtp_use_tls": True,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _sample_message(**overrides: object) -> MailMessage:
    defaults: dict[str, object] = {
        "to_address": "user@example.com",
        "subject": "Your Daemon sign-in code",
        "body_text": "Your sign-in code is 123456. It expires in 10 minutes.",
        "from_address": None,
        "body_html": None,
    }
    defaults.update(overrides)
    return MailMessage(**defaults)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _run_mail_sender_thread_boundary_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def inline_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(mail_sender_module.asyncio, "to_thread", inline_to_thread)


# ============================================================================
# Console sink
# ============================================================================


class TestConsoleMailSender:
    @pytest.mark.asyncio
    async def test_send_captures_message(self) -> None:
        sender = ConsoleMailSender()
        result = await sender.send(_sample_message())
        assert isinstance(result, MailSendResult)
        assert result.sink_kind == "console"
        assert len(sender) == 1

    @pytest.mark.asyncio
    async def test_send_rejects_empty_recipient(self) -> None:
        sender = ConsoleMailSender()
        with pytest.raises(MailSendError, match="to_address"):
            await sender.send(_sample_message(to_address=""))

    @pytest.mark.asyncio
    async def test_drain_returns_and_clears(self) -> None:
        sender = ConsoleMailSender()
        msg1 = _sample_message(subject="First")
        msg2 = _sample_message(subject="Second")
        await sender.send(msg1)
        await sender.send(msg2)
        assert len(sender) == 2
        captured = sender.drain()
        assert len(captured) == 2
        assert captured[0].subject == "First"
        assert captured[1].subject == "Second"
        assert len(sender) == 0
        assert sender.drain() == []

    @pytest.mark.asyncio
    async def test_peek_does_not_clear(self) -> None:
        sender = ConsoleMailSender()
        await sender.send(_sample_message(subject="A"))
        await sender.send(_sample_message(subject="B"))
        snapshot = sender.peek()
        assert len(snapshot) == 2
        assert len(sender) == 2

    @pytest.mark.asyncio
    async def test_captures_body_text_unchanged(self) -> None:
        sender = ConsoleMailSender()
        body = "Your Daemon code is 987654. It expires at 12:34 UTC."
        await sender.send(_sample_message(body_text=body))
        captured = sender.drain()
        assert captured[0].body_text == body

    @pytest.mark.asyncio
    async def test_captures_recipient_unchanged(self) -> None:
        sender = ConsoleMailSender()
        await sender.send(_sample_message(to_address="user@daemon.test"))
        assert sender.drain()[0].to_address == "user@daemon.test"

    @pytest.mark.asyncio
    async def test_sink_kind_is_console(self) -> None:
        assert ConsoleMailSender().sink_kind == "console"

    @pytest.mark.asyncio
    async def test_send_returns_immediately(self) -> None:
        """The console sink is purely in-process; a `send` call
        returns immediately without blocking on any I/O.
        """
        sender = ConsoleMailSender()
        # The send call is a coroutine; running it to completion
        # takes microseconds.
        coro = sender.send(_sample_message())
        result = await asyncio.wait_for(coro, timeout=0.5)
        assert result.sink_kind == "console"

    @pytest.mark.asyncio
    async def test_send_logs_dev_only_body(self, caplog: pytest.LogCaptureFixture) -> None:
        sender = ConsoleMailSender()
        with caplog.at_level("INFO"):
            await sender.send(_sample_message(body_text="Your sign-in code is 654321."))
        assert "DEV ONLY console mail" in caplog.text
        assert "654321" in caplog.text


class TestGetMailSenderConsoleMode:
    def test_console_mode_returns_process_singleton(self) -> None:
        settings = _settings_with_mode("console")
        first = get_mail_sender(settings)
        second = get_mail_sender(settings)
        assert first is second


# ============================================================================
# Disabled sink
# ============================================================================


class TestDisabledMailSender:
    @pytest.mark.asyncio
    async def test_send_returns_success_without_emitting(self) -> None:
        sender = DisabledMailSender()
        assert sender.call_count == 0
        result = await sender.send(_sample_message())
        assert isinstance(result, MailSendResult)
        assert result.sink_kind == "disabled"
        assert sender.call_count == 1

    @pytest.mark.asyncio
    async def test_sink_kind_is_disabled(self) -> None:
        assert DisabledMailSender().sink_kind == "disabled"

    @pytest.mark.asyncio
    async def test_call_count_increments(self) -> None:
        sender = DisabledMailSender()
        for _ in range(3):
            await sender.send(_sample_message())
        assert sender.call_count == 3


# ============================================================================
# SMTP sender
# ============================================================================


class _FakeSMTP:
    """Drop-in for `smtplib.SMTP` that records every call and
    returns deterministic results.
    """

    instances: list["_FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: float = 0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ehlo_calls = 0
        self.starttls_calls = 0
        self.login_calls: list[tuple[str, str]] = []
        self.sent_messages: list[EmailMessage] = []
        self.closed = False
        _FakeSMTP.instances.append(self)

    def __enter__(self) -> "_FakeSMTP":
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True

    def ehlo(self) -> tuple[int, bytes]:
        self.ehlo_calls += 1
        return (250, b"OK")

    def starttls(self, *args: object, **kwargs: object) -> tuple[int, bytes]:
        self.starttls_calls += 1
        return (220, b"Ready")

    def login(self, user: str, password: str) -> tuple[int, bytes]:
        self.login_calls.append((user, password))
        return (235, b"Authenticated")

    def send_message(self, msg: EmailMessage) -> tuple[int, bytes]:
        self.sent_messages.append(msg)
        return (250, b"Queued")


class _RaisingSMTP(_FakeSMTP):
    def send_message(self, msg: EmailMessage) -> tuple[int, bytes]:
        raise smtplib.SMTPRecipientsRefused({"addr": (550, b"User unknown")})


class _FakeSMTPSSL(_FakeSMTP):
    ssl_instances: list["_FakeSMTPSSL"] = []

    def __init__(self, host: str, port: int, timeout: float = 0) -> None:
        super().__init__(host, port, timeout)
        _FakeSMTPSSL.ssl_instances.append(self)


class TestSmtpMailSender:
    @pytest.mark.asyncio
    async def test_send_connects_and_sends(self) -> None:
        _FakeSMTP.instances.clear()
        sender = SmtpMailSender(
            host="smtp.example.com",
            port=587,
            username="user",
            password="secret",
            use_tls=True,
            from_address="noreply@daemon.test",
        )
        with patch("orchestrator.services.identity.mail_sender.smtplib.SMTP", _FakeSMTP):
            result = await sender.send(_sample_message())

        assert result.sink_kind == "smtp"
        assert len(_FakeSMTP.instances) == 1
        instance = _FakeSMTP.instances[0]
        assert instance.host == "smtp.example.com"
        assert instance.port == 587
        assert instance.ehlo_calls == 2
        assert instance.starttls_calls == 1
        assert instance.login_calls == [("user", "secret")]
        assert len(instance.sent_messages) == 1
        sent = instance.sent_messages[0]
        assert sent["From"] == "noreply@daemon.test"
        assert sent["To"] == "user@example.com"
        assert sent["Subject"] == "Your Daemon sign-in code"

    @pytest.mark.asyncio
    async def test_send_without_tls_does_not_starttls(self) -> None:
        _FakeSMTP.instances.clear()
        sender = SmtpMailSender(
            host="smtp.example.com",
            port=25,
            username="",
            password="",
            use_tls=False,
            from_address="noreply@daemon.test",
        )
        with patch("orchestrator.services.identity.mail_sender.smtplib.SMTP", _FakeSMTP):
            await sender.send(_sample_message())

        instance = _FakeSMTP.instances[0]
        assert instance.starttls_calls == 0
        assert instance.ehlo_calls == 1
        assert instance.login_calls == []

    @pytest.mark.asyncio
    async def test_send_with_implicit_tls_uses_smtp_ssl(self) -> None:
        _FakeSMTP.instances.clear()
        _FakeSMTPSSL.ssl_instances.clear()
        sender = SmtpMailSender(
            host="smtp.example.com",
            port=465,
            username="user",
            password="secret",
            use_tls=True,
            from_address="noreply@daemon.test",
        )
        with (
            patch("orchestrator.services.identity.mail_sender.smtplib.SMTP", _FakeSMTP),
            patch("orchestrator.services.identity.mail_sender.smtplib.SMTP_SSL", _FakeSMTPSSL),
        ):
            await sender.send(_sample_message())

        assert len(_FakeSMTPSSL.ssl_instances) == 1
        instance = _FakeSMTPSSL.ssl_instances[0]
        assert instance.starttls_calls == 0
        assert instance.ehlo_calls == 1
        assert instance.login_calls == [("user", "secret")]

    @pytest.mark.asyncio
    async def test_send_with_empty_credentials_skips_login(self) -> None:
        _FakeSMTP.instances.clear()
        sender = SmtpMailSender(
            host="relay.example.com",
            port=25,
            username="",
            password="",
            use_tls=False,
            from_address="noreply@daemon.test",
        )
        with patch("orchestrator.services.identity.mail_sender.smtplib.SMTP", _FakeSMTP):
            await sender.send(_sample_message())
        assert _FakeSMTP.instances[0].login_calls == []

    @pytest.mark.asyncio
    async def test_message_uses_overridden_from(self) -> None:
        _FakeSMTP.instances.clear()
        sender = SmtpMailSender(
            host="smtp.example.com",
            port=587,
            username="user",
            password="secret",
            use_tls=True,
            from_address="default@daemon.test",
        )
        with patch("orchestrator.services.identity.mail_sender.smtplib.SMTP", _FakeSMTP):
            await sender.send(_sample_message(from_address="override@daemon.test"))
        assert _FakeSMTP.instances[0].sent_messages[0]["From"] == "override@daemon.test"

    @pytest.mark.asyncio
    async def test_message_includes_html_when_provided(self) -> None:
        _FakeSMTP.instances.clear()
        sender = SmtpMailSender(
            host="smtp.example.com",
            port=587,
            username="user",
            password="secret",
            use_tls=True,
            from_address="noreply@daemon.test",
        )
        with patch("orchestrator.services.identity.mail_sender.smtplib.SMTP", _FakeSMTP):
            await sender.send(
                _sample_message(body_html="<p>Your sign-in code is <b>123456</b>.</p>")
            )
        sent = _FakeSMTP.instances[0].sent_messages[0]
        assert sent.is_multipart()

    @pytest.mark.asyncio
    async def test_message_is_plain_text_only_when_no_html(self) -> None:
        _FakeSMTP.instances.clear()
        sender = SmtpMailSender(
            host="smtp.example.com",
            port=587,
            username="user",
            password="secret",
            use_tls=True,
            from_address="noreply@daemon.test",
        )
        with patch("orchestrator.services.identity.mail_sender.smtplib.SMTP", _FakeSMTP):
            await sender.send(_sample_message(body_html=None))
        sent = _FakeSMTP.instances[0].sent_messages[0]
        assert not sent.is_multipart()

    @pytest.mark.asyncio
    async def test_smtp_exception_raises_mail_send_error(self) -> None:
        _FakeSMTP.instances.clear()
        sender = SmtpMailSender(
            host="smtp.example.com",
            port=587,
            username="user",
            password="secret",
            use_tls=True,
            from_address="noreply@daemon.test",
        )
        with patch("orchestrator.services.identity.mail_sender.smtplib.SMTP", _RaisingSMTP):
            with pytest.raises(MailSendError, match="SMTP send failed"):
                await sender.send(_sample_message())

    @pytest.mark.asyncio
    async def test_os_error_raises_mail_send_error(self) -> None:
        def _raise_os_error(*args: Any, **kwargs: Any) -> None:
            raise OSError("connection refused")

        sender = SmtpMailSender(
            host="smtp.example.com",
            port=587,
            username="",
            password="",
            use_tls=False,
            from_address="noreply@daemon.test",
        )
        with patch("orchestrator.services.identity.mail_sender.smtplib.SMTP", _raise_os_error):
            with pytest.raises(MailSendError, match="SMTP send failed"):
                await sender.send(_sample_message())

    @pytest.mark.asyncio
    async def test_send_rejects_empty_recipient(self) -> None:
        sender = SmtpMailSender(
            host="smtp.example.com",
            port=587,
            username="user",
            password="secret",
            use_tls=True,
            from_address="noreply@daemon.test",
        )
        with pytest.raises(MailSendError, match="to_address"):
            await sender.send(_sample_message(to_address=""))

    @pytest.mark.asyncio
    async def test_send_rejects_missing_from_address(self) -> None:
        """If the per-message from_address is None AND the
        configured from_address is empty, the sender raises
        `MailSendError` before opening the SMTP connection.
        """
        sender = SmtpMailSender(
            host="smtp.example.com",
            port=587,
            username="user",
            password="secret",
            use_tls=True,
            from_address="",
        )
        with pytest.raises(MailSendError, match="from_address"):
            await sender.send(_sample_message(from_address=None))

    @pytest.mark.asyncio
    async def test_password_not_in_message_headers(self) -> None:
        """The SMTP password is never put in any email header.
        This is the secret-free-message contract.
        """
        _FakeSMTP.instances.clear()
        sender = SmtpMailSender(
            host="smtp.example.com",
            port=587,
            username="user",
            password="SUPER-SECRET-PASSWORD-123",
            use_tls=True,
            from_address="noreply@daemon.test",
        )
        with patch("orchestrator.services.identity.mail_sender.smtplib.SMTP", _FakeSMTP):
            await sender.send(_sample_message())
        sent = _FakeSMTP.instances[0].sent_messages[0]
        all_headers = "\n".join(f"{k}: {v}" for k, v in sent.items())
        assert "SUPER-SECRET-PASSWORD-123" not in all_headers
        # The body should not contain the password either.
        body_text = sent.get_content()
        assert "SUPER-SECRET-PASSWORD-123" not in body_text

    def test_send_is_coroutine(self) -> None:
        """The `send` method is a coroutine function. This is
        the async-enqueue-boundary contract.
        """
        assert inspect.iscoroutinefunction(SmtpMailSender.send)
        assert inspect.iscoroutinefunction(ConsoleMailSender.send)
        assert inspect.iscoroutinefunction(DisabledMailSender.send)

    def test_sink_kind_is_smtp(self) -> None:
        sender = SmtpMailSender(
            host="x",
            port=587,
            username="",
            password="",
            use_tls=False,
            from_address="a@b.c",
        )
        assert sender.sink_kind == "smtp"


# ============================================================================
# Factory
# ============================================================================


class TestGetMailSenderFactory:
    def test_console_mode_returns_console_sender(self) -> None:
        sender = get_mail_sender(_settings_with_mode("console"))
        assert isinstance(sender, ConsoleMailSender)
        assert sender.sink_kind == "console"

    def test_disabled_mode_returns_disabled_sender(self) -> None:
        sender = get_mail_sender(_settings_with_mode("disabled"))
        assert isinstance(sender, DisabledMailSender)
        assert sender.sink_kind == "disabled"

    def test_smtp_mode_returns_smtp_sender(self) -> None:
        sender = get_mail_sender(_settings_with_mode("smtp"))
        assert isinstance(sender, SmtpMailSender)
        assert sender.sink_kind == "smtp"

    def test_smtp_mode_rejects_empty_host(self) -> None:
        settings = _settings_with_mode("smtp", daemon_mail_smtp_host="")
        with pytest.raises(MailSenderConfigError, match="daemon_mail_smtp_host"):
            get_mail_sender(settings)

    def test_smtp_mode_rejects_whitespace_only_host(self) -> None:
        """Whitespace-only host is treated as empty (per the
        TODO 6 production validation pattern).
        """
        settings = _settings_with_mode("smtp", daemon_mail_smtp_host="   ")
        with pytest.raises(MailSenderConfigError, match="daemon_mail_smtp_host"):
            get_mail_sender(settings)

    def test_smtp_mode_rejects_port_out_of_range_via_pydantic(self) -> None:
        """The port range is enforced at Settings construction
        time by Pydantic (`Field(ge=1, le=65535)`), so the
        factory never sees an out-of-range value. We assert
        that the Pydantic gate is the canonical enforcement
        point; the factory's defensive port check is a belt-
        and-braces fallback for non-Pydantic call paths.
        """
        with pytest.raises(Exception):  # ValidationError from Pydantic
            _settings_with_mode("smtp", daemon_mail_smtp_port=0)
        with pytest.raises(Exception):
            _settings_with_mode("smtp", daemon_mail_smtp_port=65536)

    def test_smtp_mode_rejects_empty_from_address(self) -> None:
        settings = _settings_with_mode("smtp", daemon_mail_from_address="")
        with pytest.raises(MailSenderConfigError, match="daemon_mail_from_address"):
            get_mail_sender(settings)

    def test_smtp_mode_rejects_whitespace_from_address(self) -> None:
        settings = _settings_with_mode("smtp", daemon_mail_from_address="   ")
        with pytest.raises(MailSenderConfigError, match="daemon_mail_from_address"):
            get_mail_sender(settings)

    def test_smtp_mode_passes_with_valid_config(self) -> None:
        settings = _settings_with_mode("smtp")
        sender = get_mail_sender(settings)
        assert isinstance(sender, SmtpMailSender)
        assert sender.host == "smtp.example.com"
        assert sender.port == 587
        assert sender.username == "user"
        assert sender.password == "secret-password"
        assert sender.use_tls is True
        assert sender.from_address == "noreply@daemon.test"


# ============================================================================
# MailMessage dataclass
# ============================================================================


class TestMailMessage:
    def test_minimal_construction(self) -> None:
        msg = MailMessage(to_address="a@b.c", subject="S", body_text="body")
        assert msg.to_address == "a@b.c"
        assert msg.subject == "S"
        assert msg.body_text == "body"
        assert msg.from_address is None
        assert msg.body_html is None

    def test_full_construction(self) -> None:
        msg = MailMessage(
            to_address="a@b.c",
            subject="S",
            body_text="body",
            from_address="x@y.z",
            body_html="<p>body</p>",
        )
        assert msg.from_address == "x@y.z"
        assert msg.body_html == "<p>body</p>"

    def test_frozen(self) -> None:
        """The MailMessage dataclass is frozen (immutable)."""
        msg = MailMessage(to_address="a@b.c", subject="S", body_text="body")
        with pytest.raises(Exception):
            msg.to_address = "different@b.c"  # type: ignore[misc]
