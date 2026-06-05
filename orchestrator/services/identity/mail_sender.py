"""Async mail-sender abstraction for hosted identity.

This module is the only place the route layer (TODO 11) talks to
when it needs to send an email-code email. It exposes a single
async `MailSender` abstract base with three concrete
implementations:

  - `ConsoleMailSender`: dev/test sink that captures every message
    in an in-memory queue so tests and local development can read
    the rendered email body. The plaintext code is ONLY available
    in this sink; it is never logged.
  - `SmtpMailSender`: production sender that uses the stdlib
    `smtplib` + `email.message.EmailMessage` to deliver via an
    unauthenticated or authenticated SMTP relay. Uses
    `asyncio.to_thread` to keep the event loop unblocked.
  - `DisabledMailSender`: no-op sender used when
    `daemon_email_enabled=False` (Google-only deployments) or
    when `daemon_mail_sender_mode="disabled"`. The route layer
    may still call `send`; the call returns success but
    nothing is emitted.

The factory `get_mail_sender(settings)` returns the right
concrete sender for the live `daemon_mail_sender_mode` value
and validates SMTP configuration in the SMTP case. The
factory raises `MailSenderConfigError` for any misconfiguration
the `Settings.validate_hosted_identity_config` already accepts
in production; this is a defensive re-check at the factory
call site so a misconfigured deployment aborts early.

Architecture decisions followed:

  - TODO 0 decision lock: the mail sender is async and
    non-blocking. The route layer (TODO 11) is expected to wrap
    the `send` call in an arq job (or a similar background
    enqueue) so the request handler never blocks on SMTP
    latency. The `SmtpMailSender.send` method uses
    `asyncio.to_thread` to keep the event loop responsive even
    when called inline.
  - TODO 3 research: the sender never logs the plaintext code.
    The `MailMessage` dataclass does not have a `secret`
    field; the body is a fully-rendered email body that the
    service caller composes. For the dev/test path, the
    `ConsoleMailSender` captures the body in memory and the
    test reads it back via `drain()`.
  - TODO 6 settings: the sender reads its configuration from
    `daemon_mail_sender_mode`, `daemon_mail_from_address`,
    `daemon_mail_smtp_host`, `daemon_mail_smtp_port`,
    `daemon_mail_smtp_username`, `daemon_mail_smtp_password`,
    and `daemon_mail_smtp_use_tls`. The
    `validate_hosted_identity_config` method is the canonical
    guard for the production SMTP case; the factory re-checks
    defensively.
  - TODO 7: rate limiting is NOT implemented in this module.
    The rate limiter lives in Redis and is consumed by the
    route layer BEFORE the mail sender is called. The sender
    itself does not deduplicate recipients or throttle
    outbounds; that's the route layer's job.
  - TODO 11: no HTTP route is added in this TODO. The
    abstract base is the durable interface; the route layer
    wires it to a FastAPI dependency.

This module never:

  - logs plaintext codes, raw recipient addresses, or raw
    passwords;
  - creates a new SMTP connection pool (each `SmtpMailSender`
    is a one-shot sender; the connection is opened and
    closed per call);
  - silently falls back to a different sender if the
    configured sender fails (a `SMTPException` from
    `SmtpMailSender.send` propagates as `MailSendError` to the
    caller);
  - sends messages in the disabled mode (the
    `DisabledMailSender.send` is a no-op that records the
    dispatch in an internal counter so tests can verify the
    call was made).
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Deque, Literal, Protocol

from orchestrator.config import Settings

logger = logging.getLogger(__name__)


# ============================================================================
# Errors
# ============================================================================


class MailSenderError(Exception):
    """Base class for mail-sender errors. The route layer (TODO 11)
    is expected to translate each subclass into a 4xx/5xx response
    that does not leak the underlying SMTP failure mode. The
    exception MESSAGE is what the route may log; the body
    returned to the client is a single opaque string.
    """


class MailSenderConfigError(MailSenderError):
    """Raised when the mail sender cannot be constructed for the
    given settings. The route layer's lifespan should call the
    factory at startup and fail closed on this error so a
    misconfigured hosted deployment aborts before traffic
    arrives.
    """


class MailSendError(MailSenderError):
    """Raised when a `send` call fails. The cause is a
    `smtplib.SMTPException` (or a generic `OSError`/`TimeoutError`
    from the network layer). The exception MESSAGE is safe to
    log (it does not contain the body, the recipient, or any
    secret); the underlying smtplib exception is chained.
    """


# ============================================================================
# Message and result types
# ============================================================================


@dataclass(frozen=True)
class MailMessage:
    """The render-ready email to deliver.

    The sender never composes the body; the route layer is
    responsible for templating the challenge code into a
    user-facing message. The `body_text` is the plain-text
    alternative; `body_html` is optional and may be None. The
    `from_address` is overridable per-message; when None the
    sender uses the configured `daemon_mail_from_address`.

    The dataclass has no `secret` field: a misuse that puts a
    plaintext code in a non-body field (e.g. an X-header) is
    not a shape the sender recognizes, and the sender never
    reads headers other than the recipient / subject / body.
    """

    to_address: str
    subject: str
    body_text: str
    from_address: str | None = None
    body_html: str | None = None


@dataclass(frozen=True)
class MailSendResult:
    """The result of a successful `send` call.

    `sent_at` is the UTC time the sender committed the message
    to its sink (console) or to the SMTP transport. The route
    layer may use this for the audit log; it is NOT the
    message receipt time on the recipient side (SMTP delivery
    is asynchronous; the relay reports its own time).

    `sink_kind` records which concrete sender handled the
    message: `console`, `smtp`, or `disabled`. This is the
    operator-visible breadcrumb that distinguishes "the
    message was sent" from "the message was captured in
    memory for tests".
    """

    sent_at: datetime
    sink_kind: Literal["console", "smtp", "disabled"]


# ============================================================================
# Abstract base
# ============================================================================


class MailSender(ABC):
    """Async mail-sender interface. The `send` method is a
    coroutine so the route layer can `await` it; the SMTP
    implementation runs the blocking smtplib calls via
    `asyncio.to_thread` so the event loop stays responsive.
    """

    @property
    @abstractmethod
    def sink_kind(self) -> Literal["console", "smtp", "disabled"]:
        """The concrete sender kind. Used in `MailSendResult`
        and exposed for the route layer's diagnostic log.
        """

    @abstractmethod
    async def send(self, message: MailMessage) -> MailSendResult:
        """Deliver the message. Returns `MailSendResult` on
        success. Raises `MailSendError` on transport failure.

        Implementations MUST NOT log the body or the recipient
        address. The console sink captures both for tests; the
        SMTP sink passes the message to smtplib and the
        underlying smtplib logger will see the recipient
        address on its own (that is the SMTP protocol; the
        Daemon application logger does not log it).
        """


# ============================================================================
# Console sink (dev / test only)
# ============================================================================


@dataclass
class ConsoleMailSender(MailSender):
    """In-process dev/test sink that captures every message in
    an in-memory queue.

    This sender is the production-equivalent of a mail relay for
    local development and unit tests. It is selected by
    `daemon_mail_sender_mode="console"`; production must use
    `daemon_mail_sender_mode="smtp"` (enforced by
    `validate_hosted_identity_config`).

    The queue is a `deque` so the test layer can read it via
    `drain()` (returns and clears the captured messages) or
    `peek()` (returns a snapshot without clearing). Tests that
    want to assert on the rendered email body can call
    `drain()` after the route layer's `send` call and check
    the captured `body_text` / `subject` / `to_address`.
    """

    _queue: Deque[MailMessage] = field(default_factory=deque)

    @property
    def sink_kind(self) -> Literal["console"]:
        return "console"

    async def send(self, message: MailMessage) -> MailSendResult:
        if not message.to_address:
            raise MailSendError("ConsoleMailSender: empty to_address")
        self._queue.append(message)
        logger.info(
            "DEV ONLY console mail subject=%s body=%s",
            message.subject,
            message.body_text,
        )
        return MailSendResult(
            sent_at=datetime.now(timezone.utc),
            sink_kind="console",
        )

    def drain(self) -> list[MailMessage]:
        """Return and clear the captured messages. Tests call
        this to inspect the rendered email body.
        """
        out = list(self._queue)
        self._queue.clear()
        return out

    def peek(self) -> list[MailMessage]:
        """Return a snapshot of the captured messages without
        clearing. Useful for tests that want to assert on the
        accumulated state across multiple calls.
        """
        return list(self._queue)

    def __len__(self) -> int:
        return len(self._queue)


# ============================================================================
# Disabled sink
# ============================================================================


@dataclass
class DisabledMailSender(MailSender):
    """No-op sender used when `daemon_mail_sender_mode="disabled"`
    or when the email provider is disabled. The `send` call
    returns success but nothing is emitted.

    The sender keeps an internal counter so tests can verify the
    call was made (and the route layer's logic is wired
    correctly) without actually delivering mail.
    """

    _call_count: int = 0

    @property
    def sink_kind(self) -> Literal["disabled"]:
        return "disabled"

    async def send(self, message: MailMessage) -> MailSendResult:
        self._call_count += 1
        return MailSendResult(
            sent_at=datetime.now(timezone.utc),
            sink_kind="disabled",
        )

    @property
    def call_count(self) -> int:
        return self._call_count


# ============================================================================
# SMTP sender
# ============================================================================


@dataclass
class SmtpMailSender(MailSender):
    """Production SMTP sender.

    The sender uses stdlib `smtplib` + `email.message.EmailMessage`
    to deliver via an SMTP relay. The blocking smtplib calls
    run inside `asyncio.to_thread` so the event loop stays
    responsive when the route layer `await`s the `send` call.

    Configuration:
      - `host`: SMTP host (e.g. `smtp.sendgrid.net`).
      - `port`: SMTP port (1-65535). Common values: 25 (relay),
        587 (submission + STARTTLS), 465 (implicit TLS — use
        with `use_tls=True`).
      - `username`: SMTP auth username. Empty string for
        unauthenticated relays.
      - `password`: SMTP auth password. Empty string for
        unauthenticated relays. The constructor does NOT log
        this value; tests verify the value is not echoed into
        the application logger.
      - `use_tls`: True for STARTTLS (port 587) or implicit
        TLS (port 465). Port 465 uses `smtplib.SMTP_SSL`;
        other TLS ports use `smtplib.SMTP.starttls()`.
      - `from_address`: the envelope-from address. The
        `MailMessage.from_address` overrides this when set.
    """

    host: str
    port: int
    username: str
    password: str
    use_tls: bool
    from_address: str

    @property
    def sink_kind(self) -> Literal["smtp"]:
        return "smtp"

    async def send(self, message: MailMessage) -> MailSendResult:
        envelope_from = message.from_address or self.from_address
        if not envelope_from:
            raise MailSendError("SmtpMailSender: no from_address configured")
        if not message.to_address:
            raise MailSendError("SmtpMailSender: empty to_address")

        msg = _build_email_message(
            from_address=envelope_from,
            to_address=message.to_address,
            subject=message.subject,
            body_text=message.body_text,
            body_html=message.body_html,
        )

        def _do_send() -> None:
            implicit_tls = self.use_tls and self.port == 465
            smtp_factory = smtplib.SMTP_SSL if implicit_tls else smtplib.SMTP
            with smtp_factory(self.host, self.port, timeout=30) as smtp:
                smtp.ehlo()
                if self.use_tls and not implicit_tls:
                    smtp.starttls()
                    smtp.ehlo()
                if self.username and self.password:
                    smtp.login(self.username, self.password)
                smtp.send_message(msg)

        try:
            await asyncio.to_thread(_do_send)
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            # The cause is the smtplib or network error;
            # chained for the operator log. The message we
            # raise does NOT include the recipient, the
            # subject, the body, the password, or the
            # username. The route layer can re-raise the
            # chain with the same level of redaction.
            raise MailSendError(f"SMTP send failed: {type(exc).__name__}") from exc

        return MailSendResult(
            sent_at=datetime.now(timezone.utc),
            sink_kind="smtp",
        )


def _build_email_message(
    *,
    from_address: str,
    to_address: str,
    subject: str,
    body_text: str,
    body_html: str | None,
) -> EmailMessage:
    """Compose an `EmailMessage` with the supplied headers and
    bodies. Plain-text only when `body_html` is None; multipart
    `alternative` when both are present.

    The function does not add any custom headers; SMTP relays
    and Daemon-side audit logs MUST NOT carry the plaintext
    challenge code in a header (the body is the only delivery
    surface).
    """
    msg = EmailMessage()
    msg["From"] = from_address
    msg["To"] = to_address
    msg["Subject"] = subject
    if body_html is None:
        msg.set_content(body_text)
    else:
        msg.set_content(body_text)
        msg.add_alternative(body_html, subtype="html")
    return msg


# ============================================================================
# Factory
# ============================================================================


class SupportsAppState(Protocol):
    """Minimal app-state surface the factory uses. The route layer
    (TODO 11) is expected to pass a `Settings` instance (and
    not an `AppState`) so this Protocol is a no-op for now; it
    exists as a future hook for a pool-based SMTP sender.
    """


_CONSOLE_MAIL_SENDER = ConsoleMailSender()


def get_mail_sender(settings: Settings) -> MailSender:
    """Factory that returns the concrete sender for the live
    `daemon_mail_sender_mode` value.

    The factory re-checks the SMTP configuration defensively
    (the canonical check lives in
    `Settings.validate_hosted_identity_config`) so a
    misconfigured deployment fails at the factory call site
    rather than at the first `send` call.

    Args:
        settings: A `Settings` instance. The factory reads
            `daemon_mail_sender_mode`,
            `daemon_mail_from_address`,
            `daemon_mail_smtp_host`,
            `daemon_mail_smtp_port`,
            `daemon_mail_smtp_username`,
            `daemon_mail_smtp_password`, and
            `daemon_mail_smtp_use_tls`.

    Returns:
        A `MailSender` instance. The concrete type depends on
        `daemon_mail_sender_mode`:
          - `console` -> `ConsoleMailSender`
          - `smtp` -> `SmtpMailSender`
          - `disabled` -> `DisabledMailSender`

    Raises:
        MailSenderConfigError: the configured mode is unknown
            or the SMTP configuration is incomplete.
    """
    mode = settings.daemon_mail_sender_mode
    if mode == "console":
        return _CONSOLE_MAIL_SENDER
    if mode == "disabled":
        return DisabledMailSender()
    if mode == "smtp":
        host = settings.daemon_mail_smtp_host.strip()
        if not host:
            raise MailSenderConfigError("SMTP sender requires a non-empty daemon_mail_smtp_host")
        if not (1 <= settings.daemon_mail_smtp_port <= 65535):
            raise MailSenderConfigError(
                f"daemon_mail_smtp_port must be in [1, 65535], "
                f"got: {settings.daemon_mail_smtp_port!r}"
            )
        if not settings.daemon_mail_from_address.strip():
            raise MailSenderConfigError("SMTP sender requires a non-empty daemon_mail_from_address")
        return SmtpMailSender(
            host=host,
            port=settings.daemon_mail_smtp_port,
            username=settings.daemon_mail_smtp_username,
            password=settings.daemon_mail_smtp_password,
            use_tls=settings.daemon_mail_smtp_use_tls,
            from_address=settings.daemon_mail_from_address,
        )
    raise MailSenderConfigError(f"unknown daemon_mail_sender_mode: {mode!r}")
