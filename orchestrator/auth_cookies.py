"""Cookie helpers for web refresh cookie.

Architecture decisions followed:
  - Decision 15: FastAPI owns auth cookies
  - Decision 16: Web refresh cookie is __Host-daemon_refresh
  - Decision 17: Insecure cookies are development-gated

Cookie attributes:
  - Name: __Host-daemon_refresh
  - HttpOnly: always
  - SameSite=Strict: always
  - Path=/: always
  - Domain: never (rejected by spec)
  - Secure: production=true, development=false only if DAEMON_ENVIRONMENT=development
"""

from __future__ import annotations

from dataclasses import dataclass


COOKIE_NAME = "__Host-daemon_refresh"


class CookiePolicyError(Exception):
    pass


@dataclass(frozen=True)
class RefreshCookieConfig:
    name: str
    http_only: bool
    secure: bool
    same_site: str
    path: str


def make_refresh_cookie_config(
    cookie_secure: bool,
    environment: str,
) -> RefreshCookieConfig:
    if environment == "production" and not cookie_secure:
        raise CookiePolicyError(
            "daemon_cookie_secure=false is not allowed in production"
        )
    if environment == "development" and not cookie_secure:
        secure = False
    else:
        secure = True

    return RefreshCookieConfig(
        name=COOKIE_NAME,
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


def clear_refresh_cookie(config: RefreshCookieConfig) -> dict[str, str]:
    return build_refresh_cookie("", config, max_age=0)
