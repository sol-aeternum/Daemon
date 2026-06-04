"""New-device notification helper for hosted identity sign-ins (TODO 14).

This module is the single chokepoint through which hosted identity
completion routes (TODO 11 `/v1/auth/email/complete` and TODO 13
`/v1/auth/google/complete`) emit a best-effort "new sign-in" email
to the user's verified address after a successful
`issue_device_session` call. The notification is purely an
operator/UX breadcrumb; it does NOT gate the auth outcome and is
NEVER used to authorize any action.

Architecture decisions followed:

  - TODO 0 decision lock: notification delivery is best-effort.
    Sender failure (SMTP transport, misconfigured host, rate
    limit, etc.) is logged at WARNING level and swallowed; the
    route layer never sees a notification exception and the
    auth/session issuance result is unchanged. The user-facing
    response shape, the access/refresh transport, and the
    cookie emission are exactly as before.
  - TODO 10 mail sender: this module reuses the `MailSender`
    abstraction (`ConsoleMailSender` / `SmtpMailSender` /
    `DisabledMailSender`) and the `MailMessage` dataclass. No
    new mail dependency is introduced. The `get_mail_sender`
    factory is the canonical entry point; the route layer's
    call site does NOT re-validate the SMTP config.
  - TODO 11/13 routes: integration is via a thin
    `schedule_device_notification(background_tasks, settings,
    notification)` helper that the route calls AFTER a
    successful `issue_device_session`. The helper itself never
    raises; any exception (config error, etc.) is logged and
    the request continues. The `MailMessage` is enqueued on the
    FastAPI `BackgroundTasks` so the SMTP send happens after
    the response is flushed and never blocks the auth handler.
  - Decision-lock secret hygiene: the rendered body contains
    ONLY the device name, the platform/client kind, the
    sign-in timestamp (UTC, ISO-8601), the provider label
    (`email` / `google`), and a generic revoke-guidance
    sentence. It NEVER contains the access token, the refresh
    token, the Google ID token, the email code, the nonce, the
    challenge id, the session secret, the cookie value, the
    invite token, the password, or any other secret. The
    helper also does not log the body or the recipient.

This module never:

  - re-implements SMTP / SES / SendGrid / etc. directly (the
    `MailSender` abstraction is the boundary; this module
    composes `MailMessage` and calls `sender.send`);
  - blocks the auth handler on SMTP latency (the route layer
    uses `BackgroundTasks` so the actual send runs after the
    response is sent; the helper itself does no I/O);
  - raises any exception out of `send_device_notification` or
    `schedule_device_notification` — both swallow the typed
    `MailSendError` (and any other `Exception` for defensive
    logging) and emit a `logger.warning` with the
    `sink_kind` + error class name + the recipient domain hash
    (the raw recipient is NEVER logged);
  - mutates the input `notification` (it is a frozen
    dataclass; the helper is a pure function);
  - stores the device metadata in a database (the audit log
    table from migration 032 is the right place for a future
    cross-device event log; this helper is the mail-side
    counterpart and lives entirely in process).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from fastapi import BackgroundTasks

from orchestrator.config import Settings
from orchestrator.services.identity.mail_sender import (
    MailMessage,
    MailSendError,
    MailSender,
    MailSenderConfigError,
    get_mail_sender,
)

logger = logging.getLogger(__name__)


# Subject line for the new-device notification. Intentionally
# distinct from the email-code sign-in subject so a user who
# receives both on the same morning can tell them apart. The
# subject contains NO device name or platform so a stale subject
# line cached by the mail client does not leak the device label.
NOTIFICATION_SUBJECT = "New sign-in to your Daemon account"


# Revoke guidance sentence. The text points the user at the
# account-wide device list (the route at `GET /v1/auth/devices`
# and `DELETE /v1/auth/devices/{device_id}`) without hard-coding
# a URL so the path can evolve without a code change here.
REVOKE_GUIDANCE = (
    "If you don't recognize this activity, open your Daemon account "
    "settings and revoke this device from the active sessions list. "
    "All Daemon sessions for the revoked device are invalidated "
    "immediately."
)


ProviderLabel = Literal["email", "google"]


@dataclass(frozen=True)
class DeviceNotification:
    """Per-event inputs for the new-device notification.

    `recipient_email` is the verified email that proved identity
    (the email-code flow's `consumed_challenge.normalized_email`
    or the Google flow's `verified.normalized_email`). It is the
    only address the mail sender will ever see; we never log it
    and we never put it in the response body of the auth route.

    `device_name` is the operator-visible label the session
    issuance helper stored on the device row (e.g. "Web Sign-In
    Device", "Web Google Sign-In Device"). It is rendered into
    the body so the user can match the email to the device in
    their account settings.

    `platform` is the client_kind string the issuance helper
    recorded ("web" or "native" today). It is rendered as a
    human label ("Web browser", "Native/mobile app") rather
    than the raw literal so the email reads naturally.

    `signed_in_at` is the UTC time the route called the helper.
    It is rendered in ISO-8601 so any timezone math the user
    does locally matches the stored device row.

    `provider` distinguishes the auth surface ("email" / "google")
    so a user who has linked both providers can tell which one
    the new sign-in used. It is rendered as a single line in
    the body; it is NOT a secret.
    """

    recipient_email: str
    device_name: str
    platform: str | None
    signed_in_at: datetime
    provider: ProviderLabel


def _platform_label(platform: str | None) -> str:
    """Map the storage `platform` literal to a human label.

    The route layer records the device's `platform` column as
    the client_kind ("web" / "native"). The notification
    presents a friendlier label so a non-technical user can
    tell at a glance which kind of client signed in. Unknown
    values are passed through unchanged (truncated to 32 chars
    to keep the email width bounded).
    """
    if not platform:
        return "Unknown"
    lowered = platform.strip().lower()
    if lowered == "web":
        return "Web browser"
    if lowered == "native":
        return "Native/mobile app"
    # Truncate aggressively so an attacker who somehow got a
    # crafted platform value into the row cannot bloat the
    # notification body. The device list view shows the raw
    # value; the email is the narrow surface.
    return platform[:32] or "Unknown"


def _recipient_domain_hash(email: str) -> str:
    """Stable, non-reversible recipient tag for the operator log.

    The application logger MUST NOT record the raw recipient
    address (it is the same surface the notification itself
    targets; logging it on failure would double the disclosure).
    A short SHA-256 of the domain part gives an operator a
    breadcrumb ("notification for the user @example.com
    failed") without exposing the local-part.
    """
    if "@" not in email:
        return "no-domain"
    domain = email.rsplit("@", 1)[-1].strip().lower()
    if not domain:
        return "no-domain"
    return hashlib.sha256(domain.encode("utf-8")).hexdigest()[:16]


def render_device_notification(notification: DeviceNotification) -> MailMessage:
    """Compose a `MailMessage` for a new-device sign-in.

    The body is plain text only. It includes:
      - the device label (so the user can match the email to
        the device in their account settings);
      - the platform / client kind (rendered as a human label);
      - the sign-in timestamp in UTC, ISO-8601 (so any
        timezone math the user does locally matches the
        stored device row);
      - the provider surface ("Email code" / "Google") so a
        user who has linked both can tell which one signed in;
      - the revoke guidance sentence pointing at the account
        settings device list.

    The body NEVER contains any token, code, nonce, challenge
    id, cookie value, invite token, or password. The rendered
    body is also NEVER written to the application log.

    Args:
        notification: the per-event inputs (see
            `DeviceNotification`).

    Returns:
        A `MailMessage` ready for `MailSender.send`. The
        `to_address` is the verified email; the subject is the
        shared `NOTIFICATION_SUBJECT`; the body is the
        rendered text below.
    """
    timestamp_iso = notification.signed_in_at.astimezone(timezone.utc).isoformat()
    platform_human = _platform_label(notification.platform)
    provider_human = "Email code" if notification.provider == "email" else "Google"
    body_text = (
        "A new sign-in to your Daemon account was recorded.\n"
        "\n"
        f"Device: {notification.device_name}\n"
        f"Platform: {platform_human}\n"
        f"Time (UTC): {timestamp_iso}\n"
        f"Provider: {provider_human}\n"
        "\n"
        f"{REVOKE_GUIDANCE}\n"
    )
    return MailMessage(
        to_address=notification.recipient_email,
        subject=NOTIFICATION_SUBJECT,
        body_text=body_text,
    )


async def send_device_notification(
    sender: MailSender,
    message: MailMessage,
) -> None:
    """Best-effort send that NEVER raises.

    The function is `async` so it is a drop-in match for the
    FastAPI `BackgroundTasks.add_task(...)` coroutine contract
    (which awaits the callable). It catches:
      - `MailSendError` — the typed mail-sender failure
        (transport refused, SMTP exception, missing from
        address, etc.);
      - `Exception` — a defensive catch-all for any
        non-mail exception (e.g. an asyncio CancelledError
        derivative, a programming error in a downstream
        hook). The catch is broad on purpose: this helper
        must NEVER block or fail the auth handler.

    On any failure the function emits a `logger.warning` with
    the `sink_kind`, the error class name, and a domain hash
    of the recipient. The raw recipient address and the
    rendered body are NEVER logged.

    On success the function is silent (a `logger.debug` line
    records the `sink_kind` and the domain hash for operator
    triage; the auth path is not affected either way).

    Args:
        sender: the concrete `MailSender` instance. The
            `sink_kind` is captured at function entry so a
            mid-call sender swap cannot affect the log line.
        message: the rendered `MailMessage` to deliver.
    """
    sink_kind = sender.sink_kind
    domain_hash = _recipient_domain_hash(message.to_address)
    try:
        await sender.send(message)
    except MailSendError as exc:
        # The typed failure mode from the mail-sender
        # abstraction. The exception MESSAGE is safe to log
        # (it does not contain the body, the recipient, or
        # any secret). The chained `__cause__` is the
        # underlying smtplib / OS error; we log the type name
        # only.
        logger.warning(
            "new_device_notification send failed: sink=%s domain=%s err=%s",
            sink_kind,
            domain_hash,
            type(exc.__cause__).__name__ if exc.__cause__ is not None else type(exc).__name__,
        )
    except Exception as exc:
        # A non-mail exception (e.g. an asyncio cancellation
        # derivative, a programming error in a downstream
        # hook). The auth handler is already in its
        # post-response background phase; the only sensible
        # action is to log and move on. Re-raising would
        # crash the background task and pollute the
        # operator log with a stack trace.
        logger.warning(
            "new_device_notification unexpected error: sink=%s domain=%s err=%s",
            sink_kind,
            domain_hash,
            type(exc).__name__,
        )


def schedule_device_notification(
    background_tasks: BackgroundTasks,
    settings: Settings,
    notification: DeviceNotification,
) -> None:
    """Schedule a new-device notification on the FastAPI
    `BackgroundTasks` queue. NEVER raises.

    The function is a thin shim that wires the rendering +
    sending pipeline into the route layer's post-response
    background task queue. The route calls it AFTER a
    successful `issue_device_session`; the actual SMTP
    transport runs AFTER the auth response is flushed so the
    user-visible latency is unaffected.

    Failure modes that the function silently absorbs:

      - `MailSenderConfigError` from `get_mail_sender` (e.g.
        SMTP host missing, port out of range, from-address
        blank in production). The auth response is already
        decided; logging a WARNING is the right
        operator-visible breadcrumb.
      - Any other `Exception` from the render or factory
        step. The defensive catch mirrors
        `send_device_notification`'s contract.

    Success:

      - A `MailMessage` is rendered and a coroutine that
        awaits `send_device_notification` is enqueued on
        `background_tasks`. FastAPI awaits the coroutine
        after the response body is sent.

    Args:
        background_tasks: the FastAPI `BackgroundTasks`
            instance injected into the route handler. The
            function does NOT create a new one; it
            `add_task`s onto the caller's queue.
        settings: the live `Settings` (used by
            `get_mail_sender` to pick the right concrete
            sender for the deployment).
        notification: the per-event inputs (see
            `DeviceNotification`).
    """
    try:
        sender = get_mail_sender(settings)
        message = render_device_notification(notification)
        background_tasks.add_task(send_device_notification, sender, message)
    except MailSenderConfigError as exc:
        # The factory refuses to construct a sender (e.g. SMTP
        # host is blank in production). The auth response is
        # already decided; this is a deployment-misconfig
        # breadcrumb. The exception MESSAGE is safe to log
        # (the factory does not put a secret in the message).
        logger.warning(
            "new_device_notification config error: err=%s",
            type(exc).__name__,
        )
    except Exception as exc:
        # Defensive catch for any other render / factory
        # failure. The auth response is already decided; the
        # only sensible action is to log and move on.
        logger.warning(
            "new_device_notification schedule failed: err=%s",
            type(exc).__name__,
        )
