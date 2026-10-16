"""Pure structured application-evidence checks, no HTTP client or secrets.

Adapted from #431's c4ai evidence checks to the pinned 0.9.5 single-seed path.
An inline positive control does not qualify network/TLS, redirects or resources.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .config import require

MAX_RESPONSE = 4 * 1024 * 1024
PRIVATE = (
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:6379/",
    "http://10.0.0.1/",
    "http://[::1]/",
)
CONTROL_MARKER = "daemonqualificationallowedfixture8a2fe9"
CONTROL_URL = "raw:<html><body><p>" + CONTROL_MARKER + "</p></body></html>"
DENIAL = {"detail": "URL blocked (SSRF protection): URL blocked"}


@dataclass(frozen=True)
class Observation:
    """Transport-owned identity, not service-supplied claims. Never contains a token."""

    run: str
    endpoint: str
    url: str
    authenticated: bool
    status: int | None
    raw: bytes
    transport_ok: bool = True


def _object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def body(observation: Observation) -> object:
    require(observation.transport_ok is True and type(observation.status) is int)
    require(type(observation.raw) is bytes and 0 < len(observation.raw) <= MAX_RESPONSE)
    try:
        return json.loads(
            observation.raw, object_pairs_hook=_object, parse_constant=lambda _: require(False)
        )
    except (ValueError, UnicodeError, RecursionError):
        require(False)


def require_application_evidence(
    run: str,
    endpoint: str,
    control: Observation,
    unauthenticated: Observation,
    private: tuple[Observation, ...],
) -> None:
    """Validate one trusted collector run; success never means runtime-qualified.

    Callers must authenticate/bind collector identity outside this pure checker.
    HTTP bodies alone cannot establish which endpoint, token or run produced them.
    """
    require(type(run) is str and len(run) == 32 and all(c in "0123456789abcdef" for c in run))
    require(type(endpoint) is str and 0 < len(endpoint) <= 256)
    require(type(private) is tuple and len(private) == len(PRIVATE))
    require(tuple(o.url for o in private) == PRIVATE)
    for observation in (control, unauthenticated, *private):
        require(observation.run == run and observation.endpoint == endpoint)
        require(type(observation.authenticated) is bool)
    require(control.url == unauthenticated.url == CONTROL_URL)
    require(control.authenticated is True and unauthenticated.authenticated is False)
    require(unauthenticated.status == 401)
    require(type(body(unauthenticated)) is dict)
    data = body(control)
    require(control.status == 200 and type(data) is dict)
    assert isinstance(data, dict)
    require(data.get("success") is True)
    results = data.get("results")
    require(type(results) is list and len(results) == 1)
    assert isinstance(results, list)
    result = results[0]
    require(type(result) is dict and result.get("url") == CONTROL_URL)
    assert isinstance(result, dict)
    require(result.get("success") is True)
    markdown = result.get("markdown")
    require(type(markdown) is dict and markdown.get("raw_markdown") == CONTROL_MARKER)
    for observation in private:
        require(observation.authenticated is True)
        require(body(observation) == DENIAL and observation.status == 400)
