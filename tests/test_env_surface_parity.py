"""Environment surface parity gate (audit task T13).

Scope: keep the four *tracked* configuration surfaces honest about each other
without starting the app, the worker, Compose, or a network call, and without
reading a real ``.env``/pepper file.

Surfaces compared
-----------------
1. ``orchestrator/config.py`` / ``config/video_pricing.py`` -- ``BaseSettings``
   *class metadata* only (``model_fields`` / ``model_config``). ``Settings`` is
   never instantiated here, so ``env_file = ".env"`` is never opened and no
   secret value is read. A synthetic throwaway ``BaseSettings`` subclass is
   instantiated in one self-test (``_env_file=None`` plus monkeypatched env)
   purely to prove the metadata resolver matches real resolution semantics.
2. ``.env.example`` -- the only tracked dotenv surface. Active *and* commented
   declarations count as documentation; a comment that merely explains a name
   is not a declaration (a declaration has the shape ``NAME=``).
3. ``docker-compose.yml`` -- parsed as text. Interpolations are never executed
   and the file is never handed to Compose.
4. ``frontend/`` -- every tracked JS/TS-family file (app, server, client, build
   config, tests, harness) is scanned for env access with a small regex
   scanner. No JS/TS parser dependency is added.

Why the checks are written the way they are
--------------------------------------------
* **No wildcard allowlists.** ``UNDOCUMENTED_APP_CONFIG`` enumerates the tier
  override names one by one and a test asserts set equality with the
  ``TIER_*`` fields, so a new field always fails until it is reviewed. A
  ``TIER_`` prefix exemption would silently admit future fields.
* **No self-consumer.** A documented name is only "covered" by a real reader:
  a declared settings field, a reviewed exclusion, a Compose-only entry, a
  frontend source read, or a standalone script/resolver read. Every non-field
  entry cites the ``file:line`` that reads the name (as required by the
  AGENTS.md env-surface rule), and a test forbids the example file, the
  documentation, and this gate from ever appearing as that citation.
* **Semantic, not lexical, security comparison.** ``SECURITY_CRITICAL`` names
  are compared against the *code* default resolved from metadata, so
  ``${DAEMON_COOKIE_SECURE:-1}`` passes and ``${DAEMON_COOKIE_SECURE}`` fails:
  a bare reference injects an empty value, which overrides both the code
  default and the bind-mounted dotenv file rather than deferring to code.
* **Unresolved is a failure, not a skip.** An env access the scanner cannot
  resolve to a literal name (dynamic index, rest destructuring, whole-mapping
  iteration) is reported with ``path:line`` and fails the gate unless it is
  listed in ``FRONTEND_UNRESOLVED_SITE_ALLOWLIST`` with a reason.
* **Behaviour preservation for the worker.** The nine backend-only Compose
  keys are explicit service-role exceptions with reasons. Unifying the two
  Python service environments is *not* behaviour neutral (it changes
  missing-key origins/public origin/cookies and adds a shell-override channel
  where previously only the bind-mounted dotenv file could be used), so it is
  not done implicitly and this gate does not require it.

Known conservative trade-offs (documented rather than hidden)
-------------------------------------------------------------
* The frontend scanner is lexical: it also counts occurrences inside comments
  and string literals, and it trusts a ``const env = process.env`` alias for
  the whole file. Both err toward *requiring documentation* rather than toward
  missing a real reader, and both fail loudly instead of skipping. Only
  ``process.env`` and ``import.meta.env`` are recognised as environment
  objects; ``globalThis.process.env`` is reported as an unresolved site so a
  new spelling cannot slip past unnoticed.
* The shared ``scripts/check_doc_freshness.py`` env parser only sees active
  ``NAME=`` declarations (``^([A-Z_][A-Z0-9_]*)`` with ``re.MULTILINE``), so
  it cannot see commented declarations. This file therefore owns its own
  active+commented parser; the shared script is left untouched.
"""

from __future__ import annotations

import fnmatch
import functools
import re
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import AliasChoices, AliasPath, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from config.video_pricing import VideoPricingConfig
from orchestrator.config import ModelSlotConfig, ProviderConfig, Settings, TierConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_FILENAME = ".env.example"
COMPOSE_FILENAME = "docker-compose.yml"
FRONTEND_DIRNAME = "frontend"
THIS_FILE = "tests/test_env_surface_parity.py"
MIGRATION_DOC = "docs/ENV_SURFACE_MIGRATION.md"

# The two Python services that boot ``Settings`` from the same bind-mounted
# dotenv file. ``migrate`` runs the same image once and is not a long-lived
# runtime environment, so it is intentionally outside the parity policy.
PYTHON_RUNTIME_SERVICES: tuple[str, ...] = ("backend", "worker")

# ``BaseSettings`` classes whose resolved env names are app configuration.
APP_SETTINGS_CLASSES: tuple[type[BaseSettings], ...] = (Settings, VideoPricingConfig)
# ``BaseSettings`` subclasses used as nested per-provider / per-slot / per-tier
# configuration objects. They declare no prefix and no dotenv file, and
# ``Settings`` constructs them with explicit values, but they are still
# ``BaseSettings`` so their bare field names are ambient env inputs that must
# be reasoned about rather than assumed irrelevant.
AMBIENT_SUBCONFIG_CLASSES: tuple[type[BaseSettings], ...] = (
    ProviderConfig,
    ModelSlotConfig,
    TierConfig,
)

# Inventory pins. A change here is a deliberate review trigger: the coverage
# test reports the resolved name of the new field in the same run.
EXPECTED_SETTINGS_FIELD_COUNT = 175
EXPECTED_VIDEO_PRICING_FIELD_COUNT = 9

# --------------------------------------------------------------------------
# Reviewed exclusions from .env.example documentation
# --------------------------------------------------------------------------

_TIER_OVERRIDE_REASON = (
    "tier model/provider/temperature override; stays env-configurable, but documenting "
    "and restructuring every tier slot is explicitly out of scope for this audit, so the "
    "name is enumerated here instead of being declared"
)

# Enumerated, never a ``TIER_*`` prefix wildcard: a new tier field must fail the
# coverage test until someone decides whether to declare or exclude it.
UNDOCUMENTED_APP_CONFIG: dict[str, str] = {
    "TIER_FREE_ORCHESTRATOR_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_FREE_ORCHESTRATOR_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_FREE_RESEARCH_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_FREE_CODE_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_FREE_IMAGE_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_FREE_IMAGE_PROVIDER": _TIER_OVERRIDE_REASON,
    "TIER_FREE_VIDEO_PROVIDER": _TIER_OVERRIDE_REASON,
    "TIER_FREE_READER_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_FREE_EMBEDDINGS_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_STARTER_ORCHESTRATOR_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_STARTER_ORCHESTRATOR_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_STARTER_RESEARCH_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_STARTER_RESEARCH_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_STARTER_CODE_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_STARTER_CODE_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_STARTER_IMAGE_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_STARTER_IMAGE_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_STARTER_IMAGE_PROVIDER": _TIER_OVERRIDE_REASON,
    "TIER_STARTER_VIDEO_PROVIDER": _TIER_OVERRIDE_REASON,
    "TIER_STARTER_READER_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_STARTER_READER_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_STARTER_EMBEDDINGS_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_PRO_ORCHESTRATOR_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_PRO_ORCHESTRATOR_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_PRO_RESEARCH_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_PRO_RESEARCH_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_PRO_CODE_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_PRO_CODE_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_PRO_IMAGE_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_PRO_IMAGE_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_PRO_IMAGE_PROVIDER": _TIER_OVERRIDE_REASON,
    "TIER_PRO_VIDEO_PROVIDER": _TIER_OVERRIDE_REASON,
    "TIER_PRO_READER_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_PRO_READER_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_PRO_EMBEDDINGS_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_MAX_ORCHESTRATOR_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_MAX_ORCHESTRATOR_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_MAX_ORCHESTRATOR_MODEL_GROK": _TIER_OVERRIDE_REASON,
    "TIER_MAX_ORCHESTRATOR_MODEL_GROK_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_MAX_RESEARCH_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_MAX_RESEARCH_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_MAX_CODE_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_MAX_CODE_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_MAX_IMAGE_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_MAX_IMAGE_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_MAX_IMAGE_PROVIDER": _TIER_OVERRIDE_REASON,
    "TIER_MAX_VIDEO_PROVIDER": _TIER_OVERRIDE_REASON,
    "TIER_MAX_READER_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_MAX_READER_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_MAX_EMBEDDINGS_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_BYOK_ORCHESTRATOR_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_BYOK_ORCHESTRATOR_TEMP": _TIER_OVERRIDE_REASON,
    "TIER_BYOK_RESEARCH_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_BYOK_CODE_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_BYOK_IMAGE_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_BYOK_IMAGE_PROVIDER": _TIER_OVERRIDE_REASON,
    "TIER_BYOK_VIDEO_PROVIDER": _TIER_OVERRIDE_REASON,
    "TIER_BYOK_READER_MODEL": _TIER_OVERRIDE_REASON,
    "TIER_BYOK_EMBEDDINGS_MODEL": _TIER_OVERRIDE_REASON,
}

_AMBIENT_SUBCONFIG_REASON = (
    "ambient BaseSettings input of a nested per-provider/per-slot/per-tier config object: "
    "Settings constructs these with explicit values and the classes use extra=forbid, so the "
    "bare name is not a supported deployment knob and is not advertised in .env.example"
)

# ``TIER_VIDEO_*`` below belong to TierConfig, not to the ``TIER_*`` Settings
# overrides, which is exactly why the tier list above is enumerated instead of
# prefix-matched.
UNDOCUMENTED_AMBIENT_CONFIG: dict[str, str] = {
    "API_KEY": _AMBIENT_SUBCONFIG_REASON,
    "BASE_URL": _AMBIENT_SUBCONFIG_REASON,
    "EXTRA_HEADERS": _AMBIENT_SUBCONFIG_REASON,
    "MODEL": _AMBIENT_SUBCONFIG_REASON,
    "NAME": _AMBIENT_SUBCONFIG_REASON,
    "REQUIRES_AUTH": _AMBIENT_SUBCONFIG_REASON,
    "TIMEOUT_S": _AMBIENT_SUBCONFIG_REASON,
    "EXTRA_PARAMS": _AMBIENT_SUBCONFIG_REASON,
    "MAX_TOKENS": _AMBIENT_SUBCONFIG_REASON,
    "TEMPERATURE": _AMBIENT_SUBCONFIG_REASON,
    "CODE_AGENT": _AMBIENT_SUBCONFIG_REASON,
    "EMBEDDINGS": _AMBIENT_SUBCONFIG_REASON,
    "IMAGE_AGENT": _AMBIENT_SUBCONFIG_REASON,
    "ORCHESTRATOR": _AMBIENT_SUBCONFIG_REASON,
    "READER_AGENT": _AMBIENT_SUBCONFIG_REASON,
    "RESEARCH_AGENT": _AMBIENT_SUBCONFIG_REASON,
    "TIER_VIDEO_CREDIT_DISCOUNT": _AMBIENT_SUBCONFIG_REASON,
    "TIER_VIDEO_ENABLED": _AMBIENT_SUBCONFIG_REASON,
    "TIER_VIDEO_MAX_DURATION": _AMBIENT_SUBCONFIG_REASON,
}

# Names the audit approved for deletion from ``.env.example``. They are listed
# so the gate can *forbid* them: a re-added declaration fails instead of being
# grandfathered as a consumer-backed name. Zero-consumer proof lives in the
# audit evidence, not here.
APPROVED_REMOVED_EXAMPLE_NAMES: dict[str, str] = {
    "TIER1_MODELS": "dead declaration; a truth_set recommendation label, not configuration",
    "LITELLM_MODEL": "dead declaration; the live test input is LITELLM_MODE, a different name",
    "OPENCODE_API_KEY": "obsolete provider block; no field, dynamic family, or reader",
    "OPENCODE_BASE_URL": "obsolete provider block; no field, dynamic family, or reader",
    "OPENCODE_MODEL": "obsolete provider block; the opencode_zen option is unsupported",
    "OPENAI_SORA_API_KEY": "dead optional-key comment; Sora uses OPENAI_API_KEY",
    "TIER_PRO_VIDEO_COST_PER_SEC": (
        "misleading comment; per-second cost is not a field, VIDEO_* pricing is a different "
        "quantity and there is no value-preserving rename"
    ),
    "TIER_MAX_VIDEO_COST_PER_SEC": (
        "misleading comment; per-second cost is not a field, VIDEO_* pricing is a different "
        "quantity and there is no value-preserving rename"
    ),
    "PROVIDER_CUSTOM_BASE_URL": (
        "non-functional declaration. The dynamic PROVIDER_{name}_* resolver in "
        "orchestrator/config.py is deliberately retained and is NOT claimed to have zero "
        "source references: extra=ignore drops undeclared keys, so the example must not "
        "advertise a capability the resolver cannot deliver"
    ),
    "PROVIDER_CUSTOM_API_KEY": "non-functional declaration; see PROVIDER_CUSTOM_BASE_URL",
    "PROVIDER_CUSTOM_MODEL": "non-functional declaration; see PROVIDER_CUSTOM_BASE_URL",
    "PROVIDER_CUSTOM_REQUIRES_AUTH": "non-functional declaration; see PROVIDER_CUSTOM_BASE_URL",
}


@dataclass(frozen=True)
class NonFieldConsumer:
    """A consumer of a documented name that is not a Settings/video field.

    ``kind`` selects how ``sources`` are verified:

    * ``script`` -- standalone operational CLIs or the standalone resolver
      module. Each source is ``file:line`` (the attribution the AGENTS.md
      env-surface rule requires) and the cited line must read the name.
    * ``frontend-source`` -- tracked frontend JS/TS files; each source is
      ``file:line`` of a read.
    * ``compose-only`` -- Compose's own interpolation/injection is the only
      consumer, so ``sources`` is empty and the name must appear in
      ``docker-compose.yml``.
    """

    kind: Literal["script", "frontend-source", "compose-only"]
    sources: tuple[str, ...]
    reason: str


_RESOLVER_SOURCE = "orchestrator/database_url.py"

NON_FIELD_CONSUMERS: dict[str, NonFieldConsumer] = {
    "POSTGRES_USER": NonFieldConsumer(
        kind="script",
        sources=(f"{_RESOLVER_SOURCE}:50",),
        reason="standalone DATABASE_URL derivation component; no Settings field exists for it",
    ),
    "POSTGRES_DB": NonFieldConsumer(
        kind="script",
        sources=(f"{_RESOLVER_SOURCE}:53",),
        reason="standalone DATABASE_URL derivation component; no Settings field exists for it",
    ),
    "POSTGRES_HOST": NonFieldConsumer(
        kind="script",
        sources=(f"{_RESOLVER_SOURCE}:52",),
        reason=(
            "standalone DATABASE_URL derivation component; no Settings field exists for it. "
            "Compose pins the literal 'postgres' for the container deployment"
        ),
    ),
    "POSTGRES_PORT": NonFieldConsumer(
        kind="script",
        sources=(f"{_RESOLVER_SOURCE}:57",),
        reason=(
            "standalone DATABASE_URL derivation component (resolver default 5432); no Settings "
            "field exists for it and Compose pins the postgres service"
        ),
    ),
    "BACKUP_DIR": NonFieldConsumer(
        kind="script",
        sources=("scripts/backup_db.py:14",),
        reason="output directory of the standalone backup CLI (resolver default 'backups')",
    ),
    "ENCRYPTION_KEY": NonFieldConsumer(
        kind="script",
        sources=("scripts/test_retrieval_quality.py:29",),
        reason=(
            "diagnostic retrieval-quality script reads this unprefixed name and passes it to "
            "ContentEncryption, so it overrides that script's fallback and the canonical "
            "DAEMON_ENCRYPTION_KEY. Script repair is a separate follow-up"
        ),
    ),
    "NEXT_PUBLIC_API_URL": NonFieldConsumer(
        kind="frontend-source",
        sources=("frontend/proxy.ts:86",),
        reason="public build-time backend URL; also a Docker build arg and a runtime entry",
    ),
    "NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE": NonFieldConsumer(
        kind="frontend-source",
        sources=("frontend/lib/deployment.ts:14",),
        reason=(
            "legacy compatibility build arg; GET /v1/auth/config is authoritative but the helper, "
            "its tests, the Dockerfile ARG/ENV and the Compose entries still read it"
        ),
    ),
    "NEXT_PUBLIC_GOOGLE_CLIENT_ID": NonFieldConsumer(
        kind="frontend-source",
        sources=("frontend/lib/deployment.ts:22",),
        reason="legacy public client-id build arg; the OAuth client secret is never exposed",
    ),
    "NEXT_PUBLIC_EMAIL_ENABLED": NonFieldConsumer(
        kind="frontend-source",
        sources=("frontend/lib/deployment.ts:30",),
        reason="legacy hosted email availability build arg; the runtime value comes from the backend",
    ),
    "DAEMON_INTERNAL_API_URL": NonFieldConsumer(
        kind="frontend-source",
        sources=("frontend/app/api/chat/route.ts:6",),
        reason=(
            "optional server-only upstream override for the Next route handlers; it falls back "
            "through the public API URL. Compose does not inject it (separate deployment "
            "follow-up), so the declaration documents the standalone/server-only case"
        ),
    ),
    "DAEMON_TRUSTED_PROXY_IPS": NonFieldConsumer(
        kind="frontend-source",
        sources=("frontend/app/api/_lib/clientIp.ts:33",),
        reason="frontend server-side immediate-proxy allowlist; not a credential",
    ),
}

# Frontend env names that are read by source but are intentionally *not*
# documented in ``.env.example``, with the source that still reads them.
FRONTEND_SOURCE_EXCEPTIONS: dict[str, NonFieldConsumer] = {
    "NEXT_PUBLIC_API_BASE_URL": NonFieldConsumer(
        kind="frontend-source",
        sources=("frontend/components/SettingsPanel.tsx:38",),
        reason=(
            "stale/disconnected component: SettingsPanel has no identified import, but it is "
            "still a source read. Keep the source (no rename or delete without separately "
            "approved component cleanup) and keep this exception explicit instead of "
            "documenting a variable the deployment does not consume"
        ),
    ),
}

# Framework/toolchain variables that are set by the runtime or harness rather
# than by an operator writing ``.env``. Deliberately small and reviewed.
FRONTEND_FRAMEWORK_ENV: dict[str, str] = {
    "NODE_ENV": "set by the Next.js runtime/build; not operator configuration",
    "CI": "set by the CI provider; read by the Playwright harness",
    "TURBOPACK": "set by the Next.js dev/build tooling; not operator configuration",
}

# ``path:line`` -> reason for env accesses the scanner cannot resolve to a
# literal name. Empty today: every tracked access resolves. A new dynamic site
# fails the gate instead of being skipped, and adding one here is an explicit
# decision.
FRONTEND_UNRESOLVED_SITE_ALLOWLIST: dict[str, str] = {}

# --------------------------------------------------------------------------
# Compose policy
# --------------------------------------------------------------------------

# Semantic security policy. The expected value is the *code* default resolved
# from ``Settings.model_fields``; this dict only records why the name is
# critical so the failure message can explain the consequence.
SECURITY_CRITICAL: dict[str, str] = {
    "DAEMON_ENVIRONMENT": (
        "code default is production; a Compose default of development downgrades validation "
        "(ephemeral auth pepper, permissive empty Host allowlist, relaxed cookie checks)"
    ),
    "DAEMON_COOKIE_SECURE": (
        "code default is true; a Compose default of false drops the Secure flag on auth cookies"
    ),
}

# Backend-only keys, kept as explicit service-role exceptions. Copying the
# backend list into the worker is not behaviour neutral: it changes the
# missing-key worker origins to localhost, the worker public origin from None to
# localhost, and it adds a shell-override channel where only the bind-mounted
# dotenv file could previously be used. Unifying the two lists is therefore a
# separate, explicit decision and is not implied by this gate.
SERVICE_ROLE_EXCEPTIONS: dict[str, str] = {
    "DAEMON_ALLOWED_ORIGINS": "CORS origin allowlist; only the ASGI app evaluates Origin",
    "DAEMON_ALLOWED_HOSTS": "trusted-Host validation; only the ASGI app receives Host headers",
    "DAEMON_DEFAULT_TIMEZONE": "chat prompt timezone fallback consumed by chat rendering",
    "DAEMON_PUBLIC_ORIGIN": "CSRF origin comparison; only the ASGI app receives Origin",
    "DAEMON_COOKIE_SECURE": "auth cookie flags are set on responses the ASGI app writes",
    "DAEMON_RATE_LIMIT_CHAT_PER_TOKEN_PER_MINUTE": (
        "chat admission limit; the worker never accepts chat requests"
    ),
    "DAEMON_RATE_LIMIT_CHAT_PER_USER_PER_MINUTE": (
        "chat admission limit; the worker never accepts chat requests"
    ),
    "DAEMON_RATE_LIMIT_CHAT_PER_IP_PER_MINUTE": (
        "chat admission limit; the worker never accepts chat requests"
    ),
    "DAEMON_INTERNAL_PROXY_HMAC_SECRET": (
        "verifies the Next.js proxy's signed client-IP assertion on inbound requests; the "
        "worker does not serve that proxy"
    ),
}

# --------------------------------------------------------------------------
# Example-file parsing
# --------------------------------------------------------------------------

DeclarationState = Literal["active", "commented"]


@dataclass(frozen=True)
class Declaration:
    name: str
    lineno: int
    state: DeclarationState
    line: str


@dataclass(frozen=True)
class ExampleFile:
    declarations: tuple[Declaration, ...]
    by_name: Mapping[str, tuple[Declaration, ...]]
    names: frozenset[str]
    active_names: frozenset[str]


# ``NAME=`` / ``# NAME=`` / ``  # NAME=`` / ``export NAME=``. A declaration must
# have the ``NAME=`` shape, so prose that merely mentions a name is not counted.
# Deliberately stricter than the shared scripts/check_doc_freshness.py parser,
# which only matches active declarations.
_DECLARATION_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<comment>\#\s*)?(?:export[ \t]+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*="
)


def parse_example(text: str) -> ExampleFile:
    declarations: list[Declaration] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        match = _DECLARATION_RE.match(line)
        if match is None:
            continue
        declarations.append(
            Declaration(
                name=match.group("name"),
                lineno=lineno,
                state="commented" if match.group("comment") else "active",
                line=line.strip(),
            )
        )
    by_name: dict[str, list[Declaration]] = {}
    for declaration in declarations:
        by_name.setdefault(declaration.name, []).append(declaration)
    return ExampleFile(
        declarations=tuple(declarations),
        by_name={name: tuple(items) for name, items in by_name.items()},
        names=frozenset(by_name),
        active_names=frozenset(
            declaration.name for declaration in declarations if declaration.state == "active"
        ),
    )


# --------------------------------------------------------------------------
# Compose parsing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ComposeInventory:
    # service -> container environment key -> raw (unevaluated) value
    service_environment: Mapping[str, Mapping[str, str]]
    # service -> build arg name -> raw (unevaluated) value
    build_args: Mapping[str, Mapping[str, str]]
    # host interpolation name -> 1-based line numbers anywhere in the file
    host_names: Mapping[str, tuple[int, ...]]
    # forms this parser refuses to interpret; each is a gate failure
    unsupported_forms: tuple[str, ...]

    @property
    def container_keys(self) -> frozenset[str]:
        return frozenset(key for env in self.service_environment.values() for key in env)


_ENV_ITEM_RE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)(?:=(?P<value>.*))?$", re.S)
_INTERPOLATION_RE = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<op>:?[-?+]?)(?P<rest>.*)$", re.S
)


def iter_compose_interpolations(text: str) -> Iterable[tuple[str, str, str, int]]:
    """Yield ``(name, operator, operand, lineno)`` for every ``${...}`` in ``text``.

    ``$$`` is a Compose escape for a literal ``$``, so ``$${NAME}`` yields
    nothing. Operators are reported verbatim because ``:-`` (default when unset
    *or* empty), ``-`` (default when unset), ``:?``/``?`` (required) and
    ``:+``/``+``/``:`` are not interchangeable, and a bare ``${NAME}`` injects an
    empty value rather than deferring to a code default.
    """
    index = 0
    length = len(text)
    while index < length:
        if text[index] != "$":
            index += 1
            continue
        following = text[index + 1 : index + 2]
        if following == "$":
            index += 2
            continue
        if following != "{":
            index += 1
            continue
        depth = 0
        cursor = index + 1
        while cursor < length:
            char = text[cursor]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        if cursor >= length:
            yield (
                "",
                "unterminated",
                text[index : index + 40],
                text.count("\n", 0, index) + 1,
            )
            return
        inner = text[index + 2 : cursor]
        parsed = _INTERPOLATION_RE.match(inner)
        lineno = text.count("\n", 0, index) + 1
        if parsed is None:
            yield ("", "unparsed", inner[:40], lineno)
        else:
            yield (parsed.group("name"), parsed.group("op"), parsed.group("rest"), lineno)
        index = cursor + 1


def parse_compose(text: str) -> ComposeInventory:
    """Parse tracked Compose text for service env keys, build args and names.

    Only the forms this repository actually uses are interpreted:
    ``environment:`` as a list of ``KEY=value`` (or bare host pass-through)
    items, and ``build:``/``args:`` as a mapping. Any other form that would
    inject configuration (``env_file:``, a mapping-style ``environment:``
    block) is recorded as an unsupported form so the gate fails instead of
    silently skipping a configuration surface.
    """
    service_environment: dict[str, dict[str, str]] = {}
    build_args: dict[str, dict[str, str]] = {}
    passthrough_names: dict[str, list[int]] = {}
    unsupported: list[str] = []
    service: str | None = None
    section: str | None = None
    in_args = False
    in_services = False

    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if not in_services:
            if stripped == "services:" and indent == 0:
                in_services = True
            continue
        if indent == 0:
            break
        if indent == 2 and stripped.endswith(":"):
            service = stripped[:-1].strip()
            section = None
            in_args = False
            service_environment.setdefault(service, {})
            build_args.setdefault(service, {})
            continue
        if service is None:
            continue
        if indent == 4:
            # A block key (``environment:``) opens a section this gate reads; an
            # inline scalar (``env_file: .env``) is either an injection surface
            # this gate refuses or an opaque value it does not need to model.
            key, _, value = stripped.partition(":")
            key = key.strip()
            if key == "env_file":
                unsupported.append(
                    f"{COMPOSE_FILENAME}:{lineno}: env_file injects a file this gate does not "
                    "read; declare the names explicitly instead"
                )
            section = key if key in {"environment", "build"} and not value.strip() else None
            in_args = False
            continue
        if section == "environment" and indent == 6:
            if not stripped.startswith("- "):
                unsupported.append(
                    f"{COMPOSE_FILENAME}:{lineno}: mapping-style environment entry {stripped!r} "
                    "is not interpreted by this gate"
                )
                continue
            item = stripped[2:].strip()
            match = _ENV_ITEM_RE.match(item)
            if match is None:
                unsupported.append(
                    f"{COMPOSE_FILENAME}:{lineno}: unrecognised environment item {item!r}"
                )
                continue
            key = match.group("key")
            raw = match.group("value")
            if raw is None:
                # Bare ``- KEY`` passes the host value through under the same
                # name, so it is a host interpolation with no Compose default.
                service_environment[service][key] = f"${{{key}}}"
                passthrough_names.setdefault(key, []).append(lineno)
            else:
                service_environment[service][key] = raw
        elif section == "build":
            if indent == 6:
                in_args = stripped == "args:"
            elif in_args and indent == 8 and ":" in stripped:
                name, _, raw = stripped.partition(":")
                build_args[service][name.strip()] = raw.strip()

    host_names: dict[str, list[int]] = {}
    for name, operator, operand, lineno in iter_compose_interpolations(text):
        if operator in {"unparsed", "unterminated"}:
            unsupported.append(
                f"{COMPOSE_FILENAME}:{lineno}: {operator} interpolation ${{{name}{operand}}}"
            )
            continue
        host_names.setdefault(name, []).append(lineno)
    for name, lines in passthrough_names.items():
        host_names.setdefault(name, []).extend(lines)

    return ComposeInventory(
        service_environment=service_environment,
        build_args=build_args,
        host_names={name: tuple(sorted(lines)) for name, lines in host_names.items()},
        unsupported_forms=tuple(unsupported),
    )


# --------------------------------------------------------------------------
# Frontend scanning
# --------------------------------------------------------------------------

_FRONTEND_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
_FRONTEND_SKIP_DIRS = frozenset(
    {
        ".git",
        ".next",
        "node_modules",
        "out",
        "dist",
        "coverage",
        "playwright-report",
        "test-results",
    }
)
_FRONTEND_SKIP_GLOBS = (
    # Generated PWA/build artifacts that the repository .gitignore excludes. The
    # git path already omits them; the filesystem fallback must too, or a
    # minified service worker would be scanned as if it were source.
    "sw.js",
    "sw.js.map",
    "swe-worker-*.js",
    "next-env.d.ts",
)
# The only two global spellings of the environment object that are scanned.
# ``globalThis.process`` is reported as an unresolved site so a new spelling
# cannot slip past unnoticed.
_ENV_OBJECT_PATTERN = r"(?:process\s*\.\s*env|import\s*\.\s*meta\s*\.\s*env)"
_ENV_OBJECT_RE = re.compile(_ENV_OBJECT_PATTERN)
# The environment object must end at this token: ``process.env.FOO`` is a
# property read, not an alias binding of the whole mapping.
_ENV_OBJECT_END = r"(?![\w.\[])"
_DESTRUCTURE_RE = re.compile(
    r"\{(?P<body>[^{}]*)\}[ \t]*=[ \t]*(?P<object>" + _ENV_OBJECT_PATTERN + r")" + _ENV_OBJECT_END
)
_ALIAS_RE = re.compile(
    r"\b(?:const|let|var)[ \t]+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:[ \t]*:[^=\n]*)?[ \t]*=[ \t]*(?P<object>" + _ENV_OBJECT_PATTERN + r")" + _ENV_OBJECT_END
)
_GLOBAL_PROCESS_RE = re.compile(r"\bglobalThis\s*\.\s*process\b")
_DOT_ACCESS_RE = re.compile(r"\s*\.\s*(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)")
# A statically indexable key: 'X', "X" or `X`.
_STATIC_BRACKET_RE = re.compile(r"""\s*\[\s*['"`](?P<name>[^'"`]+)['"`]\s*\]""")
_BRACKET_RE = re.compile(r"\s*\[")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


@dataclass(frozen=True)
class FrontendInventory:
    # env name -> "relpath:lineno" sites
    names: Mapping[str, tuple[str, ...]]
    # "relpath:lineno: snippet" for accesses that cannot be resolved
    unresolved: tuple[str, ...]
    scanned_files: tuple[str, ...]


def _split_destructured_names(body: str) -> tuple[list[str], bool]:
    """Return ``(names, has_unresolved_entry)`` for one destructuring body."""
    names: list[str] = []
    unresolved = False
    for raw_entry in body.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        if entry.startswith("..."):
            # A rest element collects every remaining key of the mapping.
            unresolved = True
            continue
        if entry[0] in "'\"":
            quoted = re.match(r"""['"](?P<name>[^'"]+)['"]""", entry)
            if quoted is None:
                unresolved = True
                continue
            names.append(quoted.group("name"))
            continue
        head = re.split(r"[:=]", entry, maxsplit=1)[0].strip()
        match = _IDENTIFIER_RE.fullmatch(head)
        if match is None:
            unresolved = True
            continue
        names.append(match.group(0))
    return names, unresolved


def _alias_access_sites(
    text: str, alias: str
) -> tuple[list[tuple[str, int]], list[tuple[int, str]]]:
    """Return ``(named_sites, unresolved_sites)`` for uses of an env alias."""
    pattern = re.compile(
        r"(?<![\w$.])"
        + re.escape(alias)
        + r"\s*(?:\.\s*(?P<dot>[A-Za-z_$][A-Za-z0-9_$]*)|\[\s*(?P<bracket>[^\]\n]*)\s*\])"
    )
    named: list[tuple[str, int]] = []
    unresolved: list[tuple[int, str]] = []
    for match in pattern.finditer(text):
        lineno = text.count("\n", 0, match.start()) + 1
        dot = match.group("dot")
        if dot is not None:
            named.append((dot, lineno))
            continue
        bracket = (match.group("bracket") or "").strip()
        if len(bracket) >= 2 and bracket[0] in "'\"" and bracket[-1] == bracket[0]:
            named.append((bracket[1:-1], lineno))
        else:
            unresolved.append((lineno, match.group(0).strip()))
    destructure = re.compile(
        r"\{(?P<body>[^{}]*)\}[ \t]*=[ \t]*(?<![\w$.])" + re.escape(alias) + r"\b"
    )
    for match in destructure.finditer(text):
        lineno = text.count("\n", 0, match.start()) + 1
        names, has_unresolved = _split_destructured_names(match.group("body"))
        named.extend((name, lineno) for name in names)
        if has_unresolved:
            unresolved.append(
                (lineno, f"destructuring {{{match.group('body').strip()}}} = {alias}")
            )
    return named, unresolved


def scan_frontend_text(relpath: str, text: str) -> tuple[dict[str, list[str]], list[str]]:
    """Return ``(name -> sites, unresolved sites)`` for one frontend source file."""
    named: dict[str, list[str]] = {}
    unresolved: list[str] = []
    consumed: list[tuple[int, int]] = []

    for match in _DESTRUCTURE_RE.finditer(text):
        consumed.append(match.span())
        lineno = text.count("\n", 0, match.start()) + 1
        names, has_unresolved = _split_destructured_names(match.group("body"))
        for name in names:
            named.setdefault(name, []).append(f"{relpath}:{lineno}")
        if has_unresolved:
            unresolved.append(
                f"{relpath}:{lineno}: destructuring {{{match.group('body').strip()}}}"
            )

    for match in _ALIAS_RE.finditer(text):
        consumed.append(match.span())
        alias = match.group("name")
        alias_named, alias_unresolved = _alias_access_sites(text, alias)
        for name, lineno in alias_named:
            named.setdefault(name, []).append(f"{relpath}:{lineno} (via alias {alias})")
        for lineno, snippet in alias_unresolved:
            unresolved.append(f"{relpath}:{lineno}: {snippet}")

    for match in _GLOBAL_PROCESS_RE.finditer(text):
        consumed.append(match.span())
        lineno = text.count("\n", 0, match.start()) + 1
        unresolved.append(f"{relpath}:{lineno}: alternate spelling {match.group(0)}")

    def _consumed(start: int) -> bool:
        return any(begin <= start < end for begin, end in consumed)

    for match in _ENV_OBJECT_RE.finditer(text):
        if _consumed(match.start()):
            continue
        lineno = text.count("\n", 0, match.start()) + 1
        rest = text[match.end() :]
        dot = _DOT_ACCESS_RE.match(rest)
        if dot is not None:
            named.setdefault(dot.group("name"), []).append(f"{relpath}:{lineno}")
            continue
        bracket = _STATIC_BRACKET_RE.match(rest)
        if bracket is not None:
            named.setdefault(bracket.group("name"), []).append(f"{relpath}:{lineno}")
            continue
        if _BRACKET_RE.match(rest) is not None:
            unresolved.append(
                f"{relpath}:{lineno}: {text[match.start() : match.end() + 24].strip()}"
            )
            continue
        unresolved.append(f"{relpath}:{lineno}: whole-mapping use {match.group(0)}")

    return named, unresolved


# --------------------------------------------------------------------------
# Settings metadata resolution
# --------------------------------------------------------------------------


def resolved_field_env_names(cls: type[BaseSettings]) -> dict[str, tuple[str, ...]]:
    """Resolve every accepted environment-variable name per declared field.

    Mirrors ``pydantic_settings.sources.EnvSettingsSource._extract_field_info``:
    ``env_prefix`` applies to the *field name* only when ``env_prefix_target``
    includes ``"variable"`` and to an alias only when it includes ``"alias"``;
    the name is upper-cased unless ``case_sensitive`` is set;
    ``validation_alias``/``AliasChoices``/``AliasPath`` contribute extra
    accepted names; and the field name is added as well when there is no
    validation alias or when ``populate_by_name``/``validate_by_name`` is set.
    A plain ``Field(alias=...)`` is deliberately *not* consulted: the
    environment source looks the field name up, not the alias.

    ``cls`` is never instantiated, so no dotenv file is opened.
    """
    config = cls.model_config
    prefix = str(config.get("env_prefix") or "")
    prefix_target = str(config.get("env_prefix_target") or "variable")
    case_sensitive = bool(config.get("case_sensitive", False))
    by_name = bool(config.get("populate_by_name") or config.get("validate_by_name"))
    alias_prefix = prefix if prefix_target in {"alias", "all"} else ""
    name_prefix = prefix if prefix_target in {"variable", "all"} else ""

    def _apply(raw: str, applied_prefix: str) -> str:
        value = applied_prefix + raw
        return value if case_sensitive else value.upper()

    resolved: dict[str, tuple[str, ...]] = {}
    for field_name, field in cls.model_fields.items():
        names: list[str] = []

        def _add(value: str) -> None:
            if value not in names:
                names.append(value)

        validation_alias = field.validation_alias
        if isinstance(validation_alias, (AliasChoices, AliasPath)):
            for group in validation_alias.convert_to_aliases():
                first = group[0] if isinstance(group, list) else group
                if isinstance(first, str):
                    _add(_apply(first, alias_prefix))
        elif isinstance(validation_alias, str):
            _add(_apply(validation_alias, alias_prefix))
        if validation_alias is None or by_name:
            _add(_apply(field_name, name_prefix))
        resolved[field_name] = tuple(names)
    return resolved


def accepted_env_names(cls: type[BaseSettings]) -> frozenset[str]:
    """Every environment name the class accepts, across all declared fields."""
    return frozenset(name for names in resolved_field_env_names(cls).values() for name in names)


# --------------------------------------------------------------------------
# Surface assembly
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Surface:
    root: Path
    example: ExampleFile
    compose: ComposeInventory
    frontend: FrontendInventory
    # (class name, resolved env name -> declaring field name)
    app_env_names: tuple[tuple[str, Mapping[str, str]], ...]

    @property
    def app_names(self) -> frozenset[str]:
        return frozenset(name for _class_name, mapping in self.app_env_names for name in mapping)

    @property
    def app_name_owners(self) -> Mapping[str, str]:
        owners: dict[str, str] = {}
        for class_name, mapping in self.app_env_names:
            for name, field_name in mapping.items():
                owners.setdefault(name, f"{class_name}.{field_name}")
        return owners


def _invert(resolved: Mapping[str, Sequence[str]]) -> dict[str, str]:
    inverted: dict[str, str] = {}
    for field_name, names in resolved.items():
        for name in names:
            inverted.setdefault(name, field_name)
    return inverted


def _git_tracked(root: Path) -> tuple[str, ...] | None:
    """Tracked paths under ``root``, or ``None`` outside a git worktree."""
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--", f"{FRONTEND_DIRNAME}/"],
            cwd=root,
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return tuple(entry for entry in completed.stdout.split("\0") if entry)


def read_frontend_sources(root: Path) -> tuple[tuple[str, str], ...]:
    """Read every tracked JS/TS-family file below ``<root>/frontend``.

    Tracked files are preferred so untracked scratch files cannot change the
    gate; a filesystem walk is the fallback when ``root`` is not a git
    worktree (for example a synthetic copy used by a mutation proof). The
    ignore set is applied in both cases.
    """
    tracked = _git_tracked(root)
    if tracked is None:
        relative_paths = [
            str(path.relative_to(root))
            for path in root.rglob("*")
            if path.is_file()
            and _FRONTEND_SKIP_DIRS.isdisjoint(path.relative_to(root).parts)
            and not any(fnmatch.fnmatch(path.name, pattern) for pattern in _FRONTEND_SKIP_GLOBS)
        ]
    else:
        relative_paths = list(tracked)
    sources: list[tuple[str, str]] = []
    for relpath in sorted(relative_paths):
        if not relpath.startswith(f"{FRONTEND_DIRNAME}/") or not relpath.endswith(
            _FRONTEND_SUFFIXES
        ):
            continue
        path = root / relpath
        if not path.is_file():
            continue
        if not _FRONTEND_SKIP_DIRS.isdisjoint(path.relative_to(root).parts):
            continue
        sources.append((relpath, path.read_text(encoding="utf-8")))
    return tuple(sources)


def build_surface(
    root: Path,
    app_classes: Sequence[type[BaseSettings]] = APP_SETTINGS_CLASSES,
) -> Surface:
    """Parse the tracked surfaces under ``root`` without executing anything."""
    example = parse_example((root / EXAMPLE_FILENAME).read_text(encoding="utf-8"))
    compose = parse_compose((root / COMPOSE_FILENAME).read_text(encoding="utf-8"))
    named: dict[str, list[str]] = {}
    unresolved: list[str] = []
    frontend_files = read_frontend_sources(root)
    for relpath, text in frontend_files:
        file_named, file_unresolved = scan_frontend_text(relpath, text)
        for name, sites in file_named.items():
            named.setdefault(name, []).extend(sites)
        unresolved.extend(file_unresolved)
    frontend = FrontendInventory(
        names={name: tuple(sites) for name, sites in named.items()},
        unresolved=tuple(unresolved),
        scanned_files=tuple(relpath for relpath, _text in frontend_files),
    )
    return Surface(
        root=root,
        example=example,
        compose=compose,
        frontend=frontend,
        app_env_names=tuple(
            (cls.__name__, _invert(resolved_field_env_names(cls))) for cls in app_classes
        ),
    )


@functools.cache
def real_surface() -> Surface:
    return build_surface(REPO_ROOT)


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


def _report(violations: Sequence[str], header: str) -> None:
    assert not violations, f"{header}\n" + "\n".join(f"  - {item}" for item in violations)


def consumer_universe(surface: Surface) -> Mapping[str, str]:
    """Map every accepted name to the kind of consumer that covers it."""
    consumers: dict[str, str] = {}
    for name, owner in surface.app_name_owners.items():
        consumers.setdefault(name, f"declared settings field {owner}")
    for name in UNDOCUMENTED_APP_CONFIG:
        consumers.setdefault(name, "reviewed exclusion in UNDOCUMENTED_APP_CONFIG")
    for name, consumer in NON_FIELD_CONSUMERS.items():
        consumers.setdefault(name, f"{consumer.kind} consumer")
    for name in UNDOCUMENTED_AMBIENT_CONFIG:
        consumers.setdefault(name, "reviewed ambient sub-config input")
    return consumers


def example_coverage_violations(surface: Surface) -> list[str]:
    return [
        f"{EXAMPLE_FILENAME} declares no active or commented declaration for {name} "
        f"(declared field {surface.app_name_owners[name]}); declare it or add a reviewed "
        "UNDOCUMENTED_APP_CONFIG entry with a reason"
        for name in sorted(surface.app_names)
        if name not in surface.example.names and name not in UNDOCUMENTED_APP_CONFIG
    ]


def undocumented_allowlist_violations(surface: Surface) -> list[str]:
    violations: list[str] = []
    # Classified by the *field* name, not by a class name or a name prefix, so a
    # tier field added to any Settings subclass is still subject to the
    # enumeration.
    tier_names = {
        name
        for name, owner in surface.app_name_owners.items()
        if owner.rsplit(".", 1)[-1].startswith("tier_")
    }
    for name, reason in sorted(UNDOCUMENTED_APP_CONFIG.items()):
        if not reason.strip():
            violations.append(f"UNDOCUMENTED_APP_CONFIG[{name}] has no reason")
        if any(character in name for character in "*?["):
            violations.append(f"UNDOCUMENTED_APP_CONFIG[{name}] looks like a pattern, not a name")
        if name not in tier_names:
            violations.append(
                f"UNDOCUMENTED_APP_CONFIG[{name}] is not a Settings tier override field; stale "
                "entry, declare the name instead"
            )
        if name in surface.example.active_names:
            violations.append(
                f"UNDOCUMENTED_APP_CONFIG[{name}] is also an active {EXAMPLE_FILENAME} "
                "declaration; an active declaration overrides the code default and contradicts "
                "the exclusion"
            )
    for name in sorted(tier_names - set(UNDOCUMENTED_APP_CONFIG)):
        if name in surface.example.names:
            continue
        violations.append(
            f"{name} is a tier override field with no {EXAMPLE_FILENAME} declaration and no "
            "UNDOCUMENTED_APP_CONFIG entry"
        )
    ambient = {name for cls in AMBIENT_SUBCONFIG_CLASSES for name in accepted_env_names(cls)}
    for name, reason in sorted(UNDOCUMENTED_AMBIENT_CONFIG.items()):
        if not reason.strip():
            violations.append(f"UNDOCUMENTED_AMBIENT_CONFIG[{name}] has no reason")
        if name not in ambient:
            violations.append(
                f"UNDOCUMENTED_AMBIENT_CONFIG[{name}] is not a sub-config field name; stale entry"
            )
        if name in surface.example.names:
            violations.append(
                f"UNDOCUMENTED_AMBIENT_CONFIG[{name}] is declared in {EXAMPLE_FILENAME} but is an "
                "ambient sub-config input with no supported deployment path"
            )
    for name in sorted(ambient - set(UNDOCUMENTED_AMBIENT_CONFIG)):
        violations.append(f"{name} is a new ambient sub-config field with no reviewed entry")
    return violations


def duplicate_declaration_violations(surface: Surface) -> list[str]:
    violations = []
    for name, declarations in sorted(surface.example.by_name.items()):
        if len(declarations) > 1:
            sites = ", ".join(f"line {item.lineno} ({item.state})" for item in declarations)
            violations.append(
                f"{EXAMPLE_FILENAME} declares {name} {len(declarations)} times ({sites}); keep "
                "one declaration per name and describe the alternatives in prose"
            )
    return violations


def approved_removal_violations(surface: Surface) -> list[str]:
    return [
        f"{EXAMPLE_FILENAME}:{declarations[0].lineno} still declares {name}, which the audit "
        f"approved for deletion ({APPROVED_REMOVED_EXAMPLE_NAMES[name]}); remove the declaration "
        "rather than grandfathering it"
        for name, declarations in sorted(surface.example.by_name.items())
        if name in APPROVED_REMOVED_EXAMPLE_NAMES
    ]


def approved_removal_field_violations(surface: Surface) -> list[str]:
    """The approved deletions must stay dead, i.e. must not gain a settings field."""
    return [
        f"{name} is listed as an approved removal but is now a declared settings field; it needs "
        "a real consumer review, not a deletion exemption"
        for name in sorted(APPROVED_REMOVED_EXAMPLE_NAMES)
        if name in surface.app_names
    ]


def example_consumer_violations(surface: Surface) -> list[str]:
    consumers = consumer_universe(surface)
    return [
        f"{EXAMPLE_FILENAME}:{declarations[0].lineno} declares {name} with no identified consumer "
        f"({declarations[0].line!r}); add a settings field, a reviewed exclusion, or a "
        "NON_FIELD_CONSUMERS entry citing the file:line that reads it"
        for name, declarations in sorted(surface.example.by_name.items())
        if name not in consumers
    ]


def _cited_line(text: str, lineno: int) -> str:
    lines = text.splitlines()
    if lineno < 1 or lineno > len(lines):
        return ""
    return lines[lineno - 1]


def consumer_attribution_violations(surface: Surface) -> list[str]:
    violations: list[str] = []
    for name, consumer in sorted(NON_FIELD_CONSUMERS.items()):
        if not consumer.reason.strip():
            violations.append(f"NON_FIELD_CONSUMERS[{name}] has no reason")
        if name not in surface.example.names:
            violations.append(
                f"NON_FIELD_CONSUMERS[{name}] is not declared in {EXAMPLE_FILENAME}; stale entry"
            )
        if consumer.kind == "compose-only":
            if consumer.sources:
                violations.append(
                    f"NON_FIELD_CONSUMERS[{name}] is compose-only but cites sources; use a "
                    "script/frontend-source kind or drop the sources"
                )
            if (
                name not in surface.compose.container_keys
                and name not in surface.compose.host_names
            ):
                violations.append(
                    f"NON_FIELD_CONSUMERS[{name}] is compose-only but appears nowhere in "
                    f"{COMPOSE_FILENAME}; stale entry"
                )
            continue
        if not consumer.sources:
            violations.append(f"NON_FIELD_CONSUMERS[{name}] cites no file:line source")
        violations.extend(_attribution_violations(name, consumer, "NON_FIELD_CONSUMERS", surface))
    for name, consumer in sorted(FRONTEND_SOURCE_EXCEPTIONS.items()):
        if not consumer.reason.strip():
            violations.append(f"FRONTEND_SOURCE_EXCEPTIONS[{name}] has no reason")
        if name in surface.example.names:
            violations.append(
                f"FRONTEND_SOURCE_EXCEPTIONS[{name}] is now declared in {EXAMPLE_FILENAME}; drop "
                "the exception and keep the documentation"
            )
        if not consumer.sources:
            violations.append(f"FRONTEND_SOURCE_EXCEPTIONS[{name}] cites no file:line source")
        violations.extend(
            _attribution_violations(name, consumer, "FRONTEND_SOURCE_EXCEPTIONS", surface)
        )
    return violations


def _attribution_violations(
    name: str, consumer: NonFieldConsumer, table: str, surface: Surface
) -> list[str]:
    violations: list[str] = []
    for source in consumer.sources:
        path_text, _, lineno_text = source.rpartition(":")
        if not path_text or not lineno_text.isdigit():
            violations.append(f"{table}[{name}] cites {source!r} without a file:line attribution")
            continue
        path = surface.root / path_text
        if not path.is_file():
            violations.append(f"{table}[{name}] cites missing file {path_text}")
            continue
        line = _cited_line(path.read_text(encoding="utf-8"), int(lineno_text))
        if consumer.kind == "script":
            found = f'"{name}"' in line or f"'{name}'" in line
        else:
            found = re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", line) is not None
        if not found:
            violations.append(
                f"{table}[{name}] cites {source} but that line no longer reads the name; update "
                "the attribution or remove the entry"
            )
    return violations


def self_consumer_violations() -> list[str]:
    """The example file, the docs and this gate may never be a cited consumer."""
    violations: list[str] = []
    for table, entries in (
        ("NON_FIELD_CONSUMERS", NON_FIELD_CONSUMERS),
        ("FRONTEND_SOURCE_EXCEPTIONS", FRONTEND_SOURCE_EXCEPTIONS),
    ):
        for name, consumer in entries.items():
            for source in consumer.sources:
                path_text = source.rpartition(":")[0]
                if path_text in {EXAMPLE_FILENAME, THIS_FILE} or path_text.endswith(".md"):
                    violations.append(
                        f"{table}[{name}] cites {source}; a consumer must be real code, not the "
                        "example file, documentation, or this parity gate"
                    )
    return violations


def compose_documentation_violations(surface: Surface) -> list[str]:
    violations = list(surface.compose.unsupported_forms)
    for name, lines in sorted(surface.compose.host_names.items()):
        if name in surface.example.names:
            continue
        locations = ", ".join(f"line {line}" for line in lines)
        violations.append(
            f"{COMPOSE_FILENAME} interpolates host variable {name} ({locations}) with no "
            f"declaration in {EXAMPLE_FILENAME}; document it or inject a literal"
        )
    return violations


def compose_consumer_violations(surface: Surface) -> list[str]:
    consumers = consumer_universe(surface)
    violations: list[str] = []
    for service, environment in sorted(surface.compose.service_environment.items()):
        for key in sorted(environment):
            if key not in consumers:
                violations.append(
                    f"{COMPOSE_FILENAME}: service {service} injects {key} with no identified consumer"
                )
    for service, args in sorted(surface.compose.build_args.items()):
        for key in sorted(args):
            if key not in consumers:
                violations.append(
                    f"{COMPOSE_FILENAME}: service {service} passes build arg {key} with no "
                    "identified consumer"
                )
    return violations


@functools.cache
def _settings_code_defaults() -> Mapping[str, object]:
    resolved = resolved_field_env_names(Settings)
    return {
        name: Settings.model_fields[field_name].default
        for field_name, names in resolved.items()
        for name in names
    }


def _code_default(name: str) -> object:
    defaults = _settings_code_defaults()
    assert name in defaults, f"{name} is not a Settings field"
    return defaults[name]


def _as_bool(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on", "t", "y"}:
        return True
    if normalized in {"false", "0", "no", "off", "f", "n"}:
        return False
    return None


def effective_compose_default(raw: str, name: str) -> tuple[bool, str]:
    """Return ``(has_default, default_text)`` for a Compose environment entry.

    A bare ``${NAME}`` has no default: Compose injects an empty value when the
    host key is absent, and the empty process value overrides both the code
    default and the bind-mounted dotenv file. That is not a deferral to code.
    """
    stripped = raw.strip()
    if stripped.startswith("$") and "{" in stripped and stripped.endswith("}"):
        inner = stripped[stripped.index("{") + 1 : stripped.rindex("}")].strip()
        match = _INTERPOLATION_RE.match(inner)
        if match is not None and match.group("name") == name:
            if match.group("op") in {":-", "-"}:
                return True, match.group("rest")
            return False, ""
        return False, ""
    if stripped in {"", f"${{{name}}}"}:
        return False, ""
    return True, stripped


def security_critical_violations(surface: Surface) -> list[str]:
    violations: list[str] = []
    for name, why in sorted(SECURITY_CRITICAL.items()):
        code_default = _code_default(name)
        if isinstance(code_default, bool):
            if code_default is not True:
                violations.append(f"{name} code default weakened to {code_default!r}; {why}")
        elif str(code_default).strip().lower() != "production":
            violations.append(f"{name} code default weakened to {code_default!r}; {why}")
        for service in PYTHON_RUNTIME_SERVICES:
            raw = surface.compose.service_environment.get(service, {}).get(name)
            if raw is None:
                continue  # no entry defers to the code default
            has_default, default_text = effective_compose_default(raw, name)
            if not has_default:
                violations.append(
                    f"{COMPOSE_FILENAME}: service {service} sets {name}={raw} with no Compose "
                    "default, so an absent host key injects an empty value that overrides the "
                    f"code default; {why}"
                )
                continue
            if isinstance(code_default, bool):
                matches = default_text.strip() != "" and _as_bool(default_text) is True
            else:
                matches = default_text.strip().lower() == str(code_default).strip().lower()
            if not matches:
                violations.append(
                    f"{COMPOSE_FILENAME}: service {service} defaults {name} to {default_text!r} "
                    f"instead of the code default {code_default!r}; {why}"
                )
    return violations


def with_service_env(
    inventory: ComposeInventory, service: str, name: str, raw: str
) -> ComposeInventory:
    environment = {key: dict(value) for key, value in inventory.service_environment.items()}
    environment[service][name] = raw
    return ComposeInventory(
        service_environment=environment,
        build_args=inventory.build_args,
        host_names=inventory.host_names,
        unsupported_forms=inventory.unsupported_forms,
    )


def without_service_env(inventory: ComposeInventory, service: str, name: str) -> ComposeInventory:
    environment = {key: dict(value) for key, value in inventory.service_environment.items()}
    environment.get(service, {}).pop(name, None)
    return ComposeInventory(
        service_environment=environment,
        build_args=inventory.build_args,
        host_names=inventory.host_names,
        unsupported_forms=inventory.unsupported_forms,
    )


# Existing non-tier inputs intentionally keep their bind-mounted dotenv/code
# channel. Adding Compose injection would change precedence, so preserve this
# exact baseline rather than implicitly exempting every field absent from both
# services. Tier inputs have the same legacy channel and are already enumerated
# in UNDOCUMENTED_APP_CONFIG. New fields require injection or explicit review.
LEGACY_DOTENV_ONLY = frozenset(
    """
    AUTO_FAST_MODEL AUTO_FAST_MODEL_GROK AUTO_FAST_MODEL_GROK_TEMP AUTO_FAST_TEMP
    AUTO_REASONING_MODEL AUTO_REASONING_TEMP BACKGROUND_REASONING_MODEL
    CHAT_HISTORY_LIMIT CONSOLIDATION_ENABLED CONSOLIDATION_INTERVAL_DAYS
    CONSOLIDATION_NUDGE_CONVERSATION_INTERVAL CONSOLIDATION_NUDGE_ENABLED
    CONSOLIDATION_NUDGE_MIN_SKILLS CONSOLIDATION_NUDGE_STALE_DAYS
    CORS_ALLOWED_ORIGINS CRAWL4AI_URL DAEMON_HTTP_ALLOWED_DOMAINS
    DAEMON_MAX_CHAT_BODY_BYTES DAEMON_MAX_GRANT_AMOUNT_PER_REQUEST
    DAEMON_MAX_REQUEST_BODY_BYTES DAEMON_MAX_SKILL_UPLOAD_BODY_BYTES
    DAEMON_MAX_STT_BODY_BYTES DAEMON_MIN_GRANT_DESCRIPTION_LENGTH
    DAEMON_SESSION_CLEANUP_GRACE_DAYS DAEMON_SESSION_CLEANUP_INTERVAL_SECONDS
    DAEMON_SESSION_CLEANUP_MAX_DELETE_FRACTION DAEMON_SETUP_TOKEN_FILE
    DAEMON_SSE_KEEPALIVE_INTERVAL_S DAEMON_WORKER_FAILURE_ALERT_EMAIL
    DEDUP_MERGE_THRESHOLD DEDUP_SUPERSEDE_SAME_SLOT_THRESHOLD DEDUP_SUPERSEDE_THRESHOLD
    DEFAULT_PROVIDER DEFAULT_TIER DREAMING_ENABLED DREAM_MIN_CLUSTER_SIZE DREAM_SCHEDULE_HOUR
    EMBEDDING_DIMENSIONS EMBEDDING_DOCUMENT_MODEL EMBEDDING_FALLBACK_PROVIDERS
    EMBEDDING_OPENAI_FALLBACK_MODEL EMBEDDING_OPENROUTER_DOCUMENT_MODEL
    EMBEDDING_OPENROUTER_QUERY_MODEL EMBEDDING_QUERY_MODEL ENV
    FETCH_ALLOWED_CONTENT_TYPES FETCH_BLOCKED_DOMAINS FETCH_CACHE_TTL_SECONDS
    FETCH_ERROR_SIGNATURES FETCH_MAX_DEPTH FETCH_MIN_CONTENT_LENGTH JINA_API_KEY LOG_LEVEL
    MODEL_EXTRA_PARAMS OPENROUTER_BASE_URL OPENROUTER_IMAGE_MODEL OPENROUTER_REFERER
    OPENROUTER_TITLE PGPASSWORD PROVIDER_EXTRA_PARAMS REQUEST_TIMEOUT_S
    RETRIEVAL_LOGGING_DEBUG RETRIEVAL_LOGGING_ENABLED STREAM_PING_INTERVAL_S TITLE_MODEL
    VIDEO_COST_10S VIDEO_COST_15S VIDEO_COST_20S VIDEO_COST_30S VIDEO_COST_5S
    VIDEO_CREDITS_PER_SECOND VIDEO_TIER_BYOK_DISCOUNT VIDEO_TIER_MAX_DISCOUNT
    VIDEO_TIER_PRO_DISCOUNT
    """.split()
)


def service_parity_violations(surface: Surface) -> list[str]:
    backend = set(surface.compose.service_environment.get("backend", {}))
    worker = set(surface.compose.service_environment.get("worker", {}))
    violations: list[str] = []
    legacy = LEGACY_DOTENV_ONLY | set(UNDOCUMENTED_APP_CONFIG)
    for name in sorted(legacy - surface.app_names):
        violations.append(f"{name}: stale legacy dotenv-only exception")
    for name in sorted(legacy & (backend | worker)):
        violations.append(
            f"{name}: legacy dotenv-only input gained Compose injection; review precedence"
        )
    for name in sorted(surface.app_names - legacy - set(SERVICE_ROLE_EXCEPTIONS)):
        if name not in backend or name not in worker:
            violations.append(
                f"{name}: shared app input must be injected into both Python services"
            )
    for name in sorted(backend | worker):
        in_backend = name in backend
        in_worker = name in worker
        if in_backend and in_worker:
            if name in SERVICE_ROLE_EXCEPTIONS:
                violations.append(
                    f"{COMPOSE_FILENAME}: {name} is now injected into both Python services, so its "
                    "SERVICE_ROLE_EXCEPTIONS entry is void; removing the exception is an explicit "
                    "behaviour decision (it widens the shell-override channel and changes the "
                    "worker's missing-key result)"
                )
            continue
        reason = SERVICE_ROLE_EXCEPTIONS.get(name)
        if reason is None:
            side = "worker-only" if in_worker else "backend-only"
            violations.append(
                f"{COMPOSE_FILENAME}: {name} is {side}; inject it into both Python services or "
                "record an explicit SERVICE_ROLE_EXCEPTIONS entry with a reason"
            )
            continue
        if not reason.strip():
            violations.append(
                f"SERVICE_ROLE_EXCEPTIONS[{name}] has no reason; a role difference must state why "
                "the key is not shared"
            )
        if not in_backend:
            violations.append(
                f"SERVICE_ROLE_EXCEPTIONS[{name}] is not injected into the backend; stale entry"
            )
        if in_worker:
            violations.append(
                f"{COMPOSE_FILENAME}: {name} is enumerated as a backend-only service-role "
                "exception but is now also injected into the worker; unifying the two "
                "environments is a separate explicit behaviour decision"
            )
    return violations


def frontend_coverage_violations(surface: Surface) -> list[str]:
    violations: list[str] = []
    allowed = {name: "framework/toolchain variable" for name in FRONTEND_FRAMEWORK_ENV}
    allowed.update(
        {
            name: f"reviewed source exception ({consumer.reason})"
            for name, consumer in FRONTEND_SOURCE_EXCEPTIONS.items()
        }
    )
    for name, sites in sorted(surface.frontend.names.items()):
        if name in surface.example.names or name in allowed:
            continue
        shown = ", ".join(sites[:3])
        more = "; ..." if len(sites) > 3 else ""
        violations.append(
            f"frontend reads {name} ({shown}{more}) but {EXAMPLE_FILENAME} declares no "
            "declaration for it; declare it or add a reasoned FRONTEND_FRAMEWORK_ENV / "
            "FRONTEND_SOURCE_EXCEPTIONS entry"
        )
    for name, reason in sorted(FRONTEND_FRAMEWORK_ENV.items()):
        if not reason.strip():
            violations.append(f"FRONTEND_FRAMEWORK_ENV[{name}] has no reason")
        if name not in surface.frontend.names:
            violations.append(
                f"FRONTEND_FRAMEWORK_ENV[{name}] is no longer read by any tracked frontend "
                "source; remove the allowlist entry"
            )
    return violations


def frontend_unresolved_violations(surface: Surface) -> list[str]:
    violations: list[str] = []
    for site in surface.frontend.unresolved:
        if site in FRONTEND_UNRESOLVED_SITE_ALLOWLIST:
            if not FRONTEND_UNRESOLVED_SITE_ALLOWLIST[site].strip():
                violations.append(f"FRONTEND_UNRESOLVED_SITE_ALLOWLIST[{site}] has no reason")
            continue
        violations.append(
            f"unresolved frontend environment access at {site}; resolve it to a literal name or "
            "record an explicit FRONTEND_UNRESOLVED_SITE_ALLOWLIST entry with a reason"
        )
    for site in sorted(FRONTEND_UNRESOLVED_SITE_ALLOWLIST):
        if site not in surface.frontend.unresolved:
            violations.append(
                f"FRONTEND_UNRESOLVED_SITE_ALLOWLIST[{site}] no longer matches a scanned site; "
                "remove the stale entry"
            )
    return violations


# --------------------------------------------------------------------------
# Tests: settings metadata
# --------------------------------------------------------------------------


def test_app_settings_inventory_is_pinned() -> None:
    """The app configuration surface is 175 + 9 fields, resolved by metadata."""
    assert len(Settings.model_fields) == EXPECTED_SETTINGS_FIELD_COUNT, (
        "Settings field count changed: document the new field in .env.example (or add a "
        "reasoned UNDOCUMENTED_APP_CONFIG entry) and update EXPECTED_SETTINGS_FIELD_COUNT"
    )
    assert len(VideoPricingConfig.model_fields) == EXPECTED_VIDEO_PRICING_FIELD_COUNT, (
        "VideoPricingConfig field count changed: document the new VIDEO_* name and update "
        "EXPECTED_VIDEO_PRICING_FIELD_COUNT"
    )
    assert Settings.model_config.get("env_prefix") in (None, "")
    assert Settings.model_config.get("case_sensitive") is False
    assert VideoPricingConfig.model_config.get("env_prefix") == "VIDEO_"
    assert VideoPricingConfig.model_config.get("case_sensitive") is False


def _settings_from_env(cls: Any) -> Any:
    """Instantiate a throwaway settings class from the (monkeypatched) environment.

    ``_env_file=None`` guarantees that no dotenv file is opened. Both the
    parameter and the return type are ``Any`` because pydantic synthesises a
    per-class ``__init__`` signature that a type checker cannot match against
    ``BaseSettings(**values)`` -- and this helper is only ever used with the
    synthetic classes defined in the self-test below.
    """
    return cls(_env_file=None)


def test_resolution_handles_prefix_alias_and_alias_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Metadata resolution matches pydantic-settings, proven behaviourally.

    The synthetic classes are throwaway: ``env_file=None`` keeps any dotenv
    file out of the picture and only monkeypatched variables are asserted.
    """

    class Prefixed(BaseSettings):
        model_config = SettingsConfigDict(env_file=None, env_prefix="VIDEO_", extra="ignore")

        cost_5s: int = 1
        plain: str = "x"

    class WithAliases(BaseSettings):
        model_config = SettingsConfigDict(env_file=None, extra="ignore")

        legacy: int = Field(0, validation_alias=AliasChoices("OLD_COST", "LEGACY_COST"))
        aliased: str = Field("x", alias="REAL_NAME")

    class Strict(BaseSettings):
        model_config = SettingsConfigDict(env_file=None, case_sensitive=True, extra="ignore")

        mixed: str = "x"

    class TargetAll(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=None, env_prefix="APP_", env_prefix_target="all", extra="ignore"
        )

        chosen: str = Field("x", validation_alias="PREFERRED")

    assert resolved_field_env_names(Prefixed) == {
        "cost_5s": ("VIDEO_COST_5S",),
        "plain": ("VIDEO_PLAIN",),
    }
    assert resolved_field_env_names(WithAliases) == {
        "legacy": ("OLD_COST", "LEGACY_COST"),
        # pydantic populates validation_alias from a bare Field(alias=...):
        # the accepted environment name is the alias, not the field name.
        "aliased": ("REAL_NAME",),
    }
    assert resolved_field_env_names(Strict) == {"mixed": ("mixed",)}
    # env_prefix_target="all" also applies the prefix to the alias branch.
    assert resolved_field_env_names(TargetAll) == {"chosen": ("APP_PREFERRED",)}

    monkeypatch.setattr("os.environ", {})
    for name, value in {
        "VIDEO_COST_5S": "7",
        "OLD_COST": "3",
        "LEGACY_COST": "4",
        "REAL_NAME": "aliased-value",
        "mixed": "kept",
        "APP_PREFERRED": "yes",
    }.items():
        monkeypatch.setenv(name, value)
    assert _settings_from_env(Prefixed).cost_5s == 7
    assert _settings_from_env(WithAliases).legacy == 3  # first AliasChoices entry wins
    assert _settings_from_env(WithAliases).aliased == "aliased-value"
    assert _settings_from_env(Strict).mixed == "kept"
    assert _settings_from_env(TargetAll).chosen == "yes"


def test_ambient_subconfig_names_are_inventoried() -> None:
    ambient = {name for cls in AMBIENT_SUBCONFIG_CLASSES for name in accepted_env_names(cls)}
    assert ambient == set(UNDOCUMENTED_AMBIENT_CONFIG)
    # TierConfig's TIER_VIDEO_* fields are *not* Settings tier overrides. Keeping
    # them in the ambient table rather than the Settings allowlist is what stops a
    # TIER_-prefix wildcard from silently exempting the wrong family.
    assert "TIER_VIDEO_ENABLED" in ambient
    assert "TIER_VIDEO_ENABLED" not in UNDOCUMENTED_APP_CONFIG
    assert UNDOCUMENTED_APP_CONFIG.keys() & set(ambient) == set()
    # 59 Settings tier overrides + 3 ambient TierConfig TIER_* names = the 62
    # TIER_-prefixed env names a reviewer may count; they are two distinct
    # families with two distinct allowlists, not one 62-entry list.
    assert len(UNDOCUMENTED_APP_CONFIG) == 59
    assert len([name for name in ambient if name.startswith("TIER_")]) == 3


# --------------------------------------------------------------------------
# Tests: example file
# --------------------------------------------------------------------------


def test_example_declares_every_app_setting_name_or_has_a_reviewed_exclusion() -> None:
    _report(
        example_coverage_violations(real_surface()),
        f"{EXAMPLE_FILENAME} must cover every resolved Settings/VideoPricingConfig env name",
    )


def test_reviewed_exclusions_are_enumerated_and_never_wildcards() -> None:
    _report(
        undocumented_allowlist_violations(real_surface()),
        "UNDOCUMENTED_APP_CONFIG / UNDOCUMENTED_AMBIENT_CONFIG must stay exactly enumerated",
    )


def test_example_has_no_duplicate_declarations() -> None:
    _report(
        duplicate_declaration_violations(real_surface()),
        f"{EXAMPLE_FILENAME} must declare each name at most once",
    )


def test_approved_deletions_are_not_declared_and_stay_dead() -> None:
    surface = real_surface()
    _report(
        approved_removal_violations(surface),
        "names approved for deletion must not come back as declarations",
    )
    _report(
        approved_removal_field_violations(surface),
        "names approved for deletion must not silently become settings fields",
    )


def test_removals_and_renames_have_a_migration_note() -> None:
    """AGENTS.md requires a migration section for env renames/removals."""
    assert APPROVED_REMOVED_EXAMPLE_NAMES, "the approved-deletion list should not be empty"
    path = REPO_ROOT / MIGRATION_DOC
    assert path.is_file(), (
        f"{MIGRATION_DOC} is required in the same PR as the env removals; list the exact "
        "production keys to add, change or drop and the resulting fallback behaviour"
    )
    text = path.read_text(encoding="utf-8")
    missing = [name for name in sorted(APPROVED_REMOVED_EXAMPLE_NAMES) if name not in text]
    assert not missing, f"{MIGRATION_DOC} does not mention the removed names: {missing}"


def test_every_example_declaration_has_a_real_consumer() -> None:
    _report(
        example_consumer_violations(real_surface()),
        f"every {EXAMPLE_FILENAME} declaration needs an identified consumer",
    )


def test_consumer_attribution_points_at_real_readers() -> None:
    _report(
        consumer_attribution_violations(real_surface()),
        "consumer allowlists must cite file:line readers that really read the name",
    )


def test_allowlists_never_use_the_example_or_this_gate_as_a_consumer() -> None:
    _report(
        self_consumer_violations(),
        "a documented name is covered by code, not by the example file or this test",
    )


# --------------------------------------------------------------------------
# Tests: compose
# --------------------------------------------------------------------------


def test_compose_interpolation_names_are_documented() -> None:
    _report(
        compose_documentation_violations(real_surface()),
        f"every {COMPOSE_FILENAME} interpolation must be documented in {EXAMPLE_FILENAME}",
    )


def test_compose_injected_keys_and_build_args_have_consumers() -> None:
    _report(
        compose_consumer_violations(real_surface()),
        f"every {COMPOSE_FILENAME} environment key and build arg needs a consumer",
    )


def test_compose_parser_separates_container_keys_from_host_names() -> None:
    inventory = real_surface().compose
    # A container key that Compose hard-codes has no host name to document...
    assert "POSTGRES_HOST" in inventory.container_keys
    assert "POSTGRES_HOST" not in inventory.host_names
    # ...while an injected key names both the container key and the host name.
    assert set(inventory.host_names) <= set(inventory.container_keys)
    assert set(inventory.container_keys) - set(inventory.host_names)
    assert inventory.service_environment["frontend"]["NEXT_PUBLIC_API_URL"] == (
        "http://localhost:8000"
    )
    assert inventory.build_args["frontend"]["NEXT_PUBLIC_API_URL"] == (
        "${NEXT_PUBLIC_API_URL:-http://localhost:8000}"
    )
    assert inventory.build_args["frontend"]["NEXT_PUBLIC_EMAIL_ENABLED"] == (
        "${NEXT_PUBLIC_EMAIL_ENABLED:-true}"
    )


def test_compose_interpolation_forms_and_escapes() -> None:
    text = (
        "services:\n"
        "  demo:\n"
        "    environment:\n"
        "      - PLAIN=${PLAIN}\n"
        "      - DASHED=${DASHED-default}\n"
        "      - COLON_DASHED=${COLON_DASHED:-x}\n"
        "      - REQUIRED=${REQUIRED:?is required}\n"
        "      - REQUIRED2=${REQUIRED2?also required}\n"
        "      - ALT=${ALT:+set}\n"
        "      - NESTED=${NESTED:-${DASHED}}\n"
        "      - LITERAL=$${LITERAL}\n"
        "      - PASSTHROUGH\n"
        "    ports:\n"
        '      - "${HOST_PORT}:8000"\n'
    )
    inventory = parse_compose(text)
    assert inventory.unsupported_forms == ()
    assert inventory.service_environment["demo"] == {
        "PLAIN": "${PLAIN}",
        "DASHED": "${DASHED-default}",
        "COLON_DASHED": "${COLON_DASHED:-x}",
        "REQUIRED": "${REQUIRED:?is required}",
        "REQUIRED2": "${REQUIRED2?also required}",
        "ALT": "${ALT:+set}",
        "NESTED": "${NESTED:-${DASHED}}",
        "LITERAL": "$${LITERAL}",
        "PASSTHROUGH": "${PASSTHROUGH}",
    }
    assert set(inventory.host_names) == {
        "PLAIN",
        "DASHED",
        "COLON_DASHED",
        "REQUIRED",
        "REQUIRED2",
        "ALT",
        "NESTED",
        "HOST_PORT",
        "PASSTHROUGH",
    }
    # `$$` escapes a literal `$`; the escaped name needs no documentation.
    assert "LITERAL" not in inventory.host_names


def test_compose_parser_refuses_forms_it_does_not_understand() -> None:
    mapping_style = parse_compose("services:\n  demo:\n    environment:\n      KEY: value\n")
    assert mapping_style.unsupported_forms
    env_file = parse_compose("services:\n  demo:\n    env_file: .env\n")
    assert any("env_file" in form for form in env_file.unsupported_forms)
    unterminated = parse_compose("services:\n  demo:\n    environment:\n      - A=${A\n")
    assert any("unterminated" in form for form in unterminated.unsupported_forms)


def test_security_critical_compose_defaults_do_not_weaken_the_code_defaults() -> None:
    _report(
        security_critical_violations(real_surface()),
        "SECURITY_CRITICAL compose defaults must not weaken the code defaults",
    )


def test_security_critical_names_are_still_covered_by_the_gate() -> None:
    surface = real_surface()
    assert set(SECURITY_CRITICAL) == {"DAEMON_ENVIRONMENT", "DAEMON_COOKIE_SECURE"}
    for name in SECURITY_CRITICAL:
        assert name in surface.app_names
    assert _code_default("DAEMON_ENVIRONMENT") == "production"
    assert _code_default("DAEMON_COOKIE_SECURE") is True
    # A bare reference injects an empty value: it is not a deferral to code.
    assert effective_compose_default("${DAEMON_ENVIRONMENT}", "DAEMON_ENVIRONMENT") == (False, "")
    assert effective_compose_default("${DAEMON_ENVIRONMENT:?}", "DAEMON_ENVIRONMENT")[0] is False
    assert effective_compose_default("${DAEMON_ENVIRONMENT:-production}", "DAEMON_ENVIRONMENT") == (
        True,
        "production",
    )
    assert effective_compose_default("${DAEMON_COOKIE_SECURE:-true}", "DAEMON_COOKIE_SECURE") == (
        True,
        "true",
    )
    assert effective_compose_default("${DAEMON_COOKIE_SECURE:-1}", "DAEMON_COOKIE_SECURE") == (
        True,
        "1",
    )
    assert _as_bool("1") is True
    assert _as_bool("") is None


def test_security_critical_detects_a_weakened_default() -> None:
    surface = real_surface()
    for name, raw in (
        ("DAEMON_COOKIE_SECURE", "${DAEMON_COOKIE_SECURE:-false}"),
        ("DAEMON_ENVIRONMENT", "${DAEMON_ENVIRONMENT:-development}"),
        ("DAEMON_ENVIRONMENT", "${DAEMON_ENVIRONMENT}"),
    ):
        weakened = Surface(
            root=surface.root,
            example=surface.example,
            compose=with_service_env(surface.compose, "backend", name, raw),
            frontend=surface.frontend,
            app_env_names=surface.app_env_names,
        )
        violations = security_critical_violations(weakened)
        assert any(name in violation for violation in violations), (name, raw, violations)


def test_python_service_environments_are_parity_checked_with_reasons() -> None:
    _report(
        service_parity_violations(real_surface()),
        f"{COMPOSE_FILENAME} backend/worker environment parity with explicit role exceptions",
    )


def test_service_role_exceptions_are_exactly_the_nine_backend_only_keys() -> None:
    surface = real_surface()
    backend = set(surface.compose.service_environment.get("backend", {}))
    worker = set(surface.compose.service_environment.get("worker", {}))
    assert set(SERVICE_ROLE_EXCEPTIONS) == backend - worker
    assert len(SERVICE_ROLE_EXCEPTIONS) == 9
    for name, reason in SERVICE_ROLE_EXCEPTIONS.items():
        assert reason.strip(), name


def test_service_parity_detects_an_unexplained_difference() -> None:
    surface = real_surface()
    dropped = without_service_env(surface.compose, "worker", "DAEMON_MAIL_SMTP_USE_TLS")
    probe = Surface(
        root=surface.root,
        example=surface.example,
        compose=dropped,
        frontend=surface.frontend,
        app_env_names=surface.app_env_names,
    )
    violations = service_parity_violations(probe)
    assert any(
        "DAEMON_MAIL_SMTP_USE_TLS" in violation and "both Python services" in violation
        for violation in violations
    ), violations
    # Unifying one of the nine enumerated backend-only keys is a behaviour
    # decision, so it must be reported rather than accepted silently.
    unified = with_service_env(
        surface.compose, "worker", "DAEMON_COOKIE_SECURE", "${DAEMON_COOKIE_SECURE:-true}"
    )
    unified_probe = Surface(
        root=surface.root,
        example=surface.example,
        compose=unified,
        frontend=surface.frontend,
        app_env_names=surface.app_env_names,
    )
    assert any(
        "DAEMON_COOKIE_SECURE" in violation
        for violation in service_parity_violations(unified_probe)
    )


# --------------------------------------------------------------------------
# Tests: frontend
# --------------------------------------------------------------------------


def test_service_parity_rejects_inputs_missing_from_both_services() -> None:
    surface = real_surface()
    compose = without_service_env(surface.compose, "backend", "OPENROUTER_API_KEY")
    compose = without_service_env(compose, "worker", "OPENROUTER_API_KEY")
    assert any(
        "OPENROUTER_API_KEY" in violation
        for violation in service_parity_violations(replace(surface, compose=compose))
    )
    probe = replace(
        surface,
        app_env_names=(
            *surface.app_env_names,
            ("SyntheticSettings", {"NEW_RUNTIME_INPUT": "new_runtime_input"}),
        ),
    )
    assert any("NEW_RUNTIME_INPUT" in violation for violation in service_parity_violations(probe))


def test_frontend_env_accesses_are_documented_or_allowlisted() -> None:
    _report(
        frontend_coverage_violations(real_surface()),
        "every tracked frontend env access must be documented or explicitly allowed",
    )


def test_frontend_has_no_unresolved_env_accesses() -> None:
    _report(
        frontend_unresolved_violations(real_surface()),
        "unresolved frontend env access must be resolved, not skipped",
    )


def test_frontend_scan_covers_all_tracked_js_ts_sources() -> None:
    inventory = real_surface().frontend
    assert len(inventory.scanned_files) > 100
    for expected in (
        "frontend/lib/deployment.ts",
        "frontend/next.config.mjs",
        "frontend/playwright.config.ts",
        "frontend/proxy.ts",
        "frontend/components/SettingsPanel.tsx",
        "frontend/__tests__/deployment.test.ts",
    ):
        assert expected in inventory.scanned_files
    assert not any(
        part in {"node_modules", ".next"}
        for path in inventory.scanned_files
        for part in path.split("/")
    )
    assert set(FRONTEND_FRAMEWORK_ENV) == {"NODE_ENV", "CI", "TURBOPACK"}
    assert set(FRONTEND_SOURCE_EXCEPTIONS) == {"NEXT_PUBLIC_API_BASE_URL"}
    assert set(inventory.names) == {
        "NEXT_PUBLIC_API_URL",
        "DAEMON_INTERNAL_API_URL",
        "DAEMON_TRUSTED_PROXY_IPS",
        "DAEMON_INTERNAL_PROXY_HMAC_SECRET",
        "NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE",
        "NEXT_PUBLIC_GOOGLE_CLIENT_ID",
        "NEXT_PUBLIC_EMAIL_ENABLED",
        "NEXT_PUBLIC_API_BASE_URL",
        "NODE_ENV",
        "CI",
        "TURBOPACK",
    }


def test_frontend_scanner_resolves_access_shapes() -> None:
    named, unresolved = scan_frontend_text(
        "frontend/sample.tsx",
        "\n".join(
            [
                "const a = process.env.DOT_NAME;",
                "const b = process.env['BRACKET_NAME'];",
                "const c = process.env[`TEMPLATE_NAME`];",
                "const { DESTRUCTURED, RENAMED: local, WITH_DEFAULT = 'x' } = process.env;",
                "const { 'QUOTED_NAME': q } = process.env;",
                "const env = process.env;",
                "const d = env.ALIAS_NAME;",
                "const e = env['ALIAS_BRACKET'];",
                "const { FROM_ALIAS } = env;",
                "const f = import.meta.env.VITE_STYLE_NAME;",
                "// process.env.COMMENTED_NAME is still counted",
                "const g = other.OBJECT_NAME;",
            ]
        ),
    )
    assert set(named) == {
        "DOT_NAME",
        "BRACKET_NAME",
        "TEMPLATE_NAME",
        "DESTRUCTURED",
        "RENAMED",
        "WITH_DEFAULT",
        "QUOTED_NAME",
        "ALIAS_NAME",
        "ALIAS_BRACKET",
        "FROM_ALIAS",
        "VITE_STYLE_NAME",
        "COMMENTED_NAME",
    }
    assert unresolved == []


def test_frontend_scanner_reports_unresolved_shapes_instead_of_skipping() -> None:
    named, unresolved = scan_frontend_text(
        "frontend/sample.ts",
        "\n".join(
            [
                "const a = process.env[key];",
                "const { known, ...rest } = process.env;",
                "const env = process.env;",
                "const b = env[key];",
                "const c = Object.keys(process.env);",
                "const d = globalThis.process.env.ANY_NAME;",
                "const { [computed]: x } = process.env;",
            ]
        ),
    )
    assert set(named) == {"known"}
    assert len(unresolved) == 6
    assert all(site.startswith("frontend/sample.ts:") for site in unresolved)
