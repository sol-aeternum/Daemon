"""Cookie helpers for web refresh cookie.

Architecture decisions followed:
  - Decision 15: FastAPI owns auth cookies
  - Decision 16: Web refresh cookie is __Host-daemon_refresh
  - Decision 17: Insecure cookies are development-gated

Cookie attributes:
  - Name: __Host-daemon_refresh in secure mode; daemon_refresh only for insecure development
  - HttpOnly: always
  - SameSite=Strict: always
  - Path=/: always
  - Domain: never (rejected by spec)
  - Secure: production/secure mode only; DAEMON_COOKIE_SECURE=false raises CookiePolicyError in production
"""

from __future__ import annotations

from dataclasses import dataclass

COOKIE_NAME = "__Host-daemon_refresh"
INSECURE_DEVELOPMENT_COOKIE_NAME = "daemon_refresh"


class CookiePolicyError(Exception):
    pass


@dataclass(frozen=True)
class RefreshCookieConfig:
    name: str
    http_only: bool
    secure: bool
    same_site: str
    path: str


def get_refresh_cookie_name(
    cookie_secure: bool,
    environment: str,
) -> str:
    if environment == "development" and not cookie_secure:
        return INSECURE_DEVELOPMENT_COOKIE_NAME
    return COOKIE_NAME


def make_refresh_cookie_config(
    cookie_secure: bool,
    environment: str,
) -> RefreshCookieConfig:
    if environment == "production" and not cookie_secure:
        raise CookiePolicyError("daemon_cookie_secure=false is not allowed in production")
    if environment == "development" and not cookie_secure:
        # The __Host- prefix is only valid with Secure.  For explicit
        # insecure development over plain HTTP/LAN, use an unprefixed
        # cookie name so browsers actually persist the refresh token.
        name = get_refresh_cookie_name(cookie_secure, environment)
        secure = False
    else:
        name = get_refresh_cookie_name(cookie_secure, environment)
        secure = True

    return RefreshCookieConfig(
        name=name,
        http_only=True,
        secure=secure,
        same_site="Strict",
        path="/",
    )


def build_refresh_cookie(
    value: str,
    config: RefreshCookieConfig,
    max_age: int | None = None,
) -> dict[str, str]:
    parts = [
        f"{config.name}={value}",
        "HttpOnly",
        f"Path={config.path}",
        f"SameSite={config.same_site}",
    ]
    if config.secure:
        parts.append("Secure")
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")

    cookie_str = "; ".join(parts)
    return {"Set-Cookie": cookie_str}


def build_temporary_refresh_cookie(
    value: str,
    config: RefreshCookieConfig,
) -> dict[str, str]:
    """Build a refresh cookie for `device_persistence="temporary"`.

    Returns a Set-Cookie header with HttpOnly + Secure + SameSite=Strict
    attributes (same policy as the private cookie) but NO `Max-Age`
    attribute, which makes the browser treat it as a session cookie
    cleared on close. This is the narrow "public computer" web session
    shape from the hosted-identity decision lock
    (`_scratch_identity_decision_lock.md`, row "temporary/public
    session capabilities"). The cookie name follows the production/
    development policy from `make_refresh_cookie_config`; there is no
    new name for temporary sessions.

    The route layer is responsible for NOT exposing a refresh token in
    the response body for this path; this helper handles only the
    Set-Cookie shape.
    """
    return build_refresh_cookie(value, config, max_age=None)


def clear_refresh_cookie(config: RefreshCookieConfig) -> dict[str, str]:
    return build_refresh_cookie("", config, max_age=0)
