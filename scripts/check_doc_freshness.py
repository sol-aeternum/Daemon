#!/usr/bin/env python3
"""
Documentation freshness linter for Daemon.

Checks gated documentation (T1) against T0 source-of-truth values extracted
at runtime. Supports high-confidence structured-fact checks only:
  - migration_count / migration_latest
  - embedding_document_model
  - dedup_thresholds (merge, supersede_generic, supersede_same_slot)
  - video_providers (source-derived from VALID_VIDEO_PROVIDERS)
  - route / env_var / docker / subagent facts
  - workload model declarations (auto_fast_model, auto_reasoning_model)
  - commercial policy consistency, derived from ``config/commercial.json`` and
    ``config/inference_policy.json``:
      * commercial_plan        (documented plan set vs. declared plans)
      * commercial_capability  (plan capability claims vs. granted capabilities)
      * commercial_price       (documented display price vs. declared price)
      * legacy_plan_map        (documented legacy-name mapping vs. declared map)
      * inference_policy       (declared route/service ids, approval claims, and
                                the transport flags a prescribed request must pin)

The retired five-tier architecture (``TierConfig``, ``tier_*`` model slots,
``list_available_tiers``) no longer has a T0 source, so the old baked tier-slot
checks were removed rather than re-pointed. They are replaced by the commercial
policy checks above, which read the same declared facts the runtime resolves.

Precision limits (deliberate, to avoid false positives on narrative prose):
  - Every check is a no-op when the document makes no matching structured claim.
  - Unknown-plan detection fires only on bold/backticked plan names in an
    anchored plan list or a plan-keyed table. Unbolded names are accepted.
  - Capability detection fires only on snake_case identifiers (a vocabulary no
    English prose shares) inside a plan-keyed table or after a
    ``capabilities:``/``capability =`` label.
  - This linter compares documentation against declared policy. It never
    re-validates the policy files themselves: schema shape, capability
    vocabulary, and budget sanity stay in ``orchestrator/entitlements/``.

Exception syntax:
  <!-- DOC_FRESHNESS_EXCEPTION: <check_id> expires=YYYY-MM-DD reason="..." -->

Usage:
  python scripts/check_doc_freshness.py [--mode report|fail] [--files <paths...>] [--format text|json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_migration_facts(root: Path) -> dict[str, Any]:
    migrations_dir = root / "migrations"
    sql_files = sorted(migrations_dir.glob("*.sql"), key=lambda p: p.name)
    count = len(sql_files)
    latest = sql_files[-1].name if sql_files else None
    return {"count": count, "latest": latest}


_EMBEDDING_DOC_RE = re.compile(r'embedding_document_model:\s*str\s*=\s*"([^"]+)"')
_EMBEDDING_QUERY_RE = re.compile(r'embedding_query_model:\s*str\s*=\s*"([^"]+)"')
_EMBEDDING_DIM_RE = re.compile(r"embedding_dimensions:\s*int\s*=\s*(\d+)")
_DEDUP_MERGE_RE = re.compile(
    r"dedup_merge_threshold:\s*float\s*=\s*Field\s*\(\s*default\s*=\s*([\d.]+)"
)
_DEDUP_SUPERSEDE_GENERIC_RE = re.compile(
    r"dedup_supersede_threshold:\s*float\s*=\s*Field\s*\(\s*default\s*=\s*([\d.]+)"
)
_DEDUP_SUPERSEDE_SAME_SLOT_RE = re.compile(
    r"dedup_supersede_same_slot_threshold:\s*float\s*=\s*Field\s*\(\s*default\s*=\s*([\d.]+)"
)

# Accept bold-label (GFM), uppercase plain, and lowercase plain forms:
#   **EMBEDDING_DOCUMENT_MODEL**: voyage-4-large
#   EMBEDDING_DOCUMENT_MODEL: voyage-4-large
#   embedding_document_model: "voyage-4-large"
_EMBEDDING_DOC_MODEL_CLAIM_RE = re.compile(
    r'(?:\*\*EMBEDDING_DOCUMENT_MODEL\*\*|EMBEDDING_DOCUMENT_MODEL|embedding_document_model)[:=\s]+(?:["\'])?([a-z0-9-]+)(?=["\']?\s|$)',
    re.IGNORECASE,
)
_EMBEDDING_QUERY_MODEL_CLAIM_RE = re.compile(
    r'(?:\*\*EMBEDDING_QUERY_MODEL\*\*|EMBEDDING_QUERY_MODEL|embedding_query_model)[:=\s]+(?:["\'])?([a-z0-9-]+)(?=["\']?\s|$)',
    re.IGNORECASE,
)
_EMBEDDING_DIMENSIONS_CLAIM_RE = re.compile(
    r"(?:\*\*EMBEDDING_DIMENSIONS\*\*|EMBEDDING_DIMENSIONS|embedding_dimensions)[:=\s]+(\d+)",
    re.IGNORECASE,
)


def get_embedding_facts(root: Path) -> dict[str, Any]:
    config_path = root / "orchestrator" / "config.py"
    text = config_path.read_text(encoding="utf-8")

    doc_model = _EMBEDDING_DOC_RE.search(text)
    query_model = _EMBEDDING_QUERY_RE.search(text)
    dim = _EMBEDDING_DIM_RE.search(text)
    merge = _DEDUP_MERGE_RE.search(text)
    supersede_generic = _DEDUP_SUPERSEDE_GENERIC_RE.search(text)
    supersede_same_slot = _DEDUP_SUPERSEDE_SAME_SLOT_RE.search(text)

    return {
        "document_model": doc_model.group(1) if doc_model else None,
        "query_model": query_model.group(1) if query_model else None,
        "dimensions": int(dim.group(1)) if dim else None,
        "dedup_merge": float(merge.group(1)) if merge else None,
        "dedup_supersede_generic": float(supersede_generic.group(1)) if supersede_generic else None,
        "dedup_supersede_same_slot": float(supersede_same_slot.group(1))
        if supersede_same_slot
        else None,
    }


_VIDEO_PROVIDERS_RE = re.compile(r"VALID_VIDEO_PROVIDERS\s*=\s*\{([^}]+)\}")
_PROVIDER_CLIENT_RE = re.compile(r"class\s+(\w+(?:Client|Provider))\s*(?:\(|:)")


def get_provider_facts(root: Path) -> dict[str, Any]:
    video_credits_path = root / "orchestrator" / "routes" / "video_credits.py"
    providers_dir = root / "providers"

    video_providers: set[str] = set()
    if video_credits_path.exists():
        text = video_credits_path.read_text(encoding="utf-8")
        m = _VIDEO_PROVIDERS_RE.search(text)
        if m:
            video_providers = {p.strip().strip("'\"") for p in m.group(1).split(",")}

    provider_names: list[str] = []
    if providers_dir.exists():
        for py_file in providers_dir.glob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for m in _PROVIDER_CLIENT_RE.finditer(text):
                provider_names.append(m.group(1))

    return {
        "video_providers": sorted(video_providers),
        "provider_clients": sorted(provider_names),
    }


# Workload model declarations that remain in orchestrator/config.py. Commercial
# plan-to-model assignment is gone; these are deployment/workload slots that the
# documentation still describes.
_AUTO_FAST_MODEL_RE = re.compile(r'auto_fast_model\s*:\s*str\s*=\s*"([^"]+)"')
_AUTO_REASONING_MODEL_RE = re.compile(r'auto_reasoning_model\s*:\s*str\s*=\s*"([^"]+)"')


def get_auto_routing_facts(root: Path) -> dict[str, str]:
    config_path = root / "orchestrator" / "config.py"
    if not config_path.exists():
        return {}
    text = config_path.read_text(encoding="utf-8")
    fast = _AUTO_FAST_MODEL_RE.search(text)
    reasoning = _AUTO_REASONING_MODEL_RE.search(text)
    return {
        "auto_fast_model": fast.group(1) if fast else "",
        "auto_reasoning_model": reasoning.group(1) if reasoning else "",
    }


COMMERCIAL_CONFIG_RELPATH = Path("config") / "commercial.json"
INFERENCE_POLICY_RELPATH = Path("config") / "inference_policy.json"


def _read_policy_json(path: Path) -> dict[str, Any] | None:
    """Read a policy JSON file, or None when it is absent or unreadable.

    The linter is documentation-only tooling: it reports a doc that contradicts
    policy, never a policy that is itself malformed. Runtime loading and
    validation live in ``orchestrator/entitlements/policy.py``.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _normalized_label(value: object) -> str:
    return str(value).strip().strip("*`_").lower()


def get_commercial_facts(root: Path) -> dict[str, Any]:
    """Extract the commercial facts documentation is allowed to restate.

    Only values that ``config/commercial.json`` itself declares are read:
    plan ids, display labels, per-plan capabilities, display prices, billable
    operations, and the legacy plan map used for explicit migration. Internal
    consistency of that file is a runtime concern and is not re-checked here.
    """
    data = _read_policy_json(root / COMMERCIAL_CONFIG_RELPATH)
    if data is None:
        return {}

    plans_raw = data.get("plans")
    plans: dict[str, Any] = plans_raw if isinstance(plans_raw, dict) else {}

    plan_ids: list[str] = []
    labels: dict[str, str] = {}
    capabilities: dict[str, list[str]] = {}
    display: dict[str, dict[str, str]] = {}
    capability_vocabulary: set[str] = set()
    for raw_id, body in plans.items():
        plan_id = str(raw_id).strip().lower()
        if not plan_id:
            continue
        plan_ids.append(plan_id)
        body_map: dict[str, Any] = body if isinstance(body, dict) else {}

        display_raw = body_map.get("display")
        display_map: dict[str, Any] = display_raw if isinstance(display_raw, dict) else {}
        label = display_map.get("label")
        if isinstance(label, str) and _normalized_label(label):
            labels[_normalized_label(label)] = plan_id
        currency = display_map.get("currency")
        amount_minor = display_map.get("amount_minor")
        if isinstance(currency, str) and isinstance(amount_minor, (int, float)):
            display[plan_id] = {
                "currency": currency.strip().upper(),
                "amount_minor": str(amount_minor),
            }

        caps_raw = body_map.get("capabilities")
        caps = sorted(
            {
                str(c).strip().lower()
                for c in (caps_raw if isinstance(caps_raw, list) else [])
                if isinstance(c, str) and str(c).strip()
            }
        )
        capabilities[plan_id] = caps
        capability_vocabulary.update(caps)

    legacy_raw = data.get("legacy_plan_map")
    legacy_plan_map: dict[str, str] = {}
    if isinstance(legacy_raw, dict):
        for key, value in legacy_raw.items():
            # Only mapping entries are facts here. Keys such as "notes" whose
            # value is prose are policy commentary, not a name mapping; a
            # mapping to an undeclared plan is a policy problem, not doc drift.
            if not isinstance(value, str) or not str(key).strip():
                continue
            target = _normalized_label(value)
            if target in plan_ids:
                legacy_plan_map[_normalized_label(key)] = target

    operations_raw = data.get("operations")
    operations = sorted(
        {
            str(o).strip().lower()
            for o in (operations_raw if isinstance(operations_raw, list) else [])
            if isinstance(o, str) and str(o).strip()
        }
    )

    return {
        "plan_ids": sorted(plan_ids),
        "plan_labels": labels,
        "capabilities": capabilities,
        "capability_vocabulary": sorted(capability_vocabulary),
        "operation_vocabulary": operations,
        "display": display,
        "legacy_plan_map": legacy_plan_map,
    }


# Requirement key in config/inference_policy.json -> transport keys that a
# documented outbound provider request must pin when that requirement is on.
# Values are the hardened contract the policy file states in prose
# ("zdr true, data_collection deny, allow_fallbacks false, require_parameters
# true"); they are recorded here so the linter can check documentation against
# the documented contract instead of trusting prose.
_REQUIREMENT_TRANSPORT_FLAGS: dict[str, tuple[str, ...]] = {
    "require_pinned_transport": ("allow_fallbacks", "require_parameters"),
    "require_zdr": ("zdr",),
    "require_no_training": ("data_collection",),
    "require_pinned_provider_selection": ("only", "order"),
    "require_price_ceiling": ("max_price",),
}
_REQUIRED_TRANSPORT_VALUES: dict[str, Any] = {
    "zdr": True,
    "data_collection": "deny",
    "allow_fallbacks": False,
    "require_parameters": True,
}
_REQUIRED_MAX_PRICE_KEYS: tuple[str, ...] = ("prompt", "completion")


def get_inference_policy_facts(root: Path) -> dict[str, Any]:
    """Extract the inference-policy facts documentation is allowed to restate.

    Route ids, tool-service ids, the default route, approval state, and the
    transport flags required by ``requirements``. Qualification rules
    themselves are enforced at runtime; this only supplies the T0 values that
    documentation claims are compared against.
    """
    data = _read_policy_json(root / INFERENCE_POLICY_RELPATH)
    if data is None:
        return {}

    def _entries(key: str, id_key: str) -> list[dict[str, Any]]:
        raw = data.get(key)
        if not isinstance(raw, list):
            return []
        return [entry for entry in raw if isinstance(entry, dict) and entry.get(id_key)]

    route_entries = _entries("routes", "route_id")
    service_entries = _entries("tool_services", "service_id")

    def _collect(entries: list[dict[str, Any]], id_key: str) -> tuple[dict[str, bool], set[str]]:
        ids: dict[str, bool] = {}
        providers: set[str] = set()
        for entry in entries:
            entry_id = _normalized_label(entry.get(id_key))
            if not entry_id:
                continue
            ids[entry_id] = entry.get("approved") is True
            provider = entry.get("provider")
            if isinstance(provider, str) and provider.strip():
                providers.add(provider.strip().lower())
        return ids, providers

    route_ids, route_providers = _collect(route_entries, "route_id")
    service_ids, service_providers = _collect(service_entries, "service_id")

    requirements_raw = data.get("requirements")
    requirements: dict[str, bool] = {}
    if isinstance(requirements_raw, dict):
        requirements = {str(k): v is True for k, v in requirements_raw.items()}

    required_flags: list[tuple[str, Any]] = []
    for requirement, flags in _REQUIREMENT_TRANSPORT_FLAGS.items():
        if not requirements.get(requirement):
            continue
        for flag in flags:
            required_flags.append((flag, _REQUIRED_TRANSPORT_VALUES.get(flag)))

    default_route = data.get("default_route_id")
    return {
        "route_ids": route_ids,
        "service_ids": service_ids,
        "providers": sorted(route_providers | service_providers),
        "requirements": requirements,
        "required_flags": required_flags,
        "default_route_id": _normalized_label(default_route) if default_route else None,
    }


def get_docker_facts(root: Path) -> dict[str, Any]:
    compose_path = root / "docker-compose.yml"
    if not compose_path.exists():
        return {"service_count": 0}
    text = compose_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    service_count = 0
    services_indent: int | None = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("services:"):
            services_indent = len(line) - len(line.lstrip())
            continue
        if services_indent is None:
            continue
        current_indent = len(line) - len(line.lstrip())
        if current_indent <= services_indent and stripped:
            break
        if current_indent == services_indent + 2 and stripped and not stripped.startswith("#"):
            if not any(
                stripped.startswith(k)
                for k in (
                    "depends_on:",
                    "environment:",
                    "ports:",
                    "volumes:",
                    "networks:",
                    "build:",
                    "command:",
                    "restart:",
                    "image:",
                    "container_name:",
                    "healthcheck:",
                    "env_file:",
                    "extends:",
                    "profiles:",
                    "deploy:",
                    "cgroup_parent:",
                    "cpu_shares:",
                    "mem_limit:",
                    "mem_reservation:",
                    "ulimits:",
                    "log_driver:",
                    "log_opt:",
                    "dns:",
                    "dns_search:",
                    "external_links:",
                    "extra_hosts:",
                    "isolation:",
                    "mac_address:",
                    "network_mode:",
                    "shm_size:",
                    "stdin_open:",
                    "tty:",
                    "user:",
                    "working_dir:",
                )
            ):
                service_count += 1
    return {"service_count": service_count}


_ROUTE_DEF_RE = re.compile(
    r'(@app\.|router\.)(get|post|put|patch|delete|options)\s*\(\s*["\']([^"\']*)["\']'
)
_ROUTER_PREFIX_RE = re.compile(r'router\s*=\s*APIRouter\s*\(\s*prefix\s*=\s*["\']([^"\']+)["\']')
_INCLUDE_ROUTER_MODULE_RE = re.compile(r"app\.include_router\(\s*(\w+)\.router\s*\)")
_INCLUDE_ROUTER_ALIAS_RE = re.compile(r"app\.include_router\(\s*(\w+)\s*\)")
_ROUTER_ALIAS_IMPORT_RE = re.compile(
    r"from\s+orchestrator\.routes\.(\w+)\s+import\s+router\s+as\s+(\w+)"
)


def _strip_trailing_slash(path: str) -> str:
    return path.rstrip("/")


def get_route_facts(root: Path) -> dict[str, Any]:
    main_path = root / "orchestrator" / "main.py"
    routes_dir = root / "orchestrator" / "routes"
    routes: dict[str, list[str]] = {}

    sources: list[Path] = [main_path]
    sources.extend(sorted(routes_dir.glob("*.py")) if routes_dir.exists() else [])
    if main_path.exists():
        # Any router module main.py mounts is gated, including modules outside
        # orchestrator/routes/. Resolving include_router() calls keeps this
        # honest when routers move, instead of pinning one historical path.
        main_text = main_path.read_text(encoding="utf-8")
        aliases = dict(_ROUTER_ALIAS_IMPORT_RE.findall(main_text))
        mounted: set[str] = set(_INCLUDE_ROUTER_MODULE_RE.findall(main_text))
        for alias in _INCLUDE_ROUTER_ALIAS_RE.findall(main_text):
            module = aliases.get(alias)
            if module:
                mounted.add(module)
        for module in sorted(mounted):
            candidate = routes_dir / f"{module}.py"
            if candidate.exists() and candidate not in sources:
                sources.append(candidate)

    for path in sources:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")

        prefix_match = _ROUTER_PREFIX_RE.search(text)
        prefix = _strip_trailing_slash(prefix_match.group(1)) if prefix_match else None

        for m in _ROUTE_DEF_RE.finditer(text):
            decorator = m.group(1)
            method = m.group(2).upper()
            path_val = m.group(3)
            if decorator == "router." and prefix is not None:
                path_val = prefix + path_val
            routes.setdefault(method, []).append(path_val)

    return {"routes": routes}


_ENV_VAR_RE = re.compile(r"`([A-Z_][A-Z0-9_]*)`")


def get_env_var_facts(root: Path) -> dict[str, list[str]]:
    env_vars: set[str] = set()
    env_var_pattern = re.compile(r"^([A-Z_][A-Z0-9_]*)=", re.MULTILINE)
    docker_list_pattern = re.compile(r"^\s*-\s+([A-Z_][A-Z0-9_]*)", re.MULTILINE)
    for file_path in [root / ".env.example", root / "docker-compose.yml"]:
        if file_path.exists():
            text = file_path.read_text(encoding="utf-8")
            for m in env_var_pattern.finditer(text):
                env_vars.add(m.group(1))
            for m in docker_list_pattern.finditer(text):
                env_vars.add(m.group(1))
    return {"env_vars": sorted(env_vars)}


def extract_all_facts(root: Path) -> dict[str, Any]:
    return {
        "migrations": get_migration_facts(root),
        "embeddings": get_embedding_facts(root),
        "providers": get_provider_facts(root),
        "routes": get_route_facts(root),
        "env_vars": get_env_var_facts(root),
        "commercial": get_commercial_facts(root),
        "inference_policy": get_inference_policy_facts(root),
        "auto_routing": get_auto_routing_facts(root),
        "docker": get_docker_facts(root),
        "subagents": get_subagent_facts(root),
    }


_SUBAGENT_IMPL_NOTES = {
    "research": "Brave Search + synthesis",
    "image": "OpenRouter/Gemini (images), xAI/fal (video)",
    "audio": "ElevenLabs SFX",
    "document": "Python code generation + execution",
}


def get_subagent_facts(root: Path) -> dict[str, dict[str, str]]:
    subagents_dir = root / "orchestrator" / "subagents"
    implemented: set[str] = set()
    for py_file in subagents_dir.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        text = py_file.read_text(encoding="utf-8")
        if re.search(r"^\s*agent_type\s*=\s*SubagentType\.", text, re.MULTILINE):
            name = py_file.stem
            if name != "base":
                implemented.add(name)

    result: dict[str, dict[str, str]] = {}
    for name in ("research", "image", "audio", "document", "code", "reader"):
        if name in implemented:
            result[name] = {"status": "implemented", "note": _SUBAGENT_IMPL_NOTES.get(name, "")}
        elif name in ("code", "reader"):
            result[name] = {"status": "reserved", "note": ""}
        else:
            result[name] = {"status": "not_implemented", "note": ""}
    return result


class CheckId(str, Enum):
    MIGRATION_COUNT = "migration_count"
    MIGRATION_LATEST = "migration_latest"
    EMBEDDING_DOC_MODEL = "embedding_document_model"
    EMBEDDING_QUERY_MODEL = "embedding_query_model"
    EMBEDDING_DIMENSIONS = "embedding_dimensions"
    DEDUP_MERGE = "dedup_merge_threshold"
    DEDUP_SUPERSEDE_GENERIC = "dedup_supersede_generic_threshold"
    DEDUP_SUPERSEDE_SAME_SLOT = "dedup_supersede_same_slot_threshold"
    VIDEO_PROVIDERS = "video_providers"
    COMMERCIAL_PLAN = "commercial_plan"
    COMMERCIAL_CAPABILITY = "commercial_capability"
    COMMERCIAL_PRICE = "commercial_price"
    LEGACY_PLAN_MAP = "legacy_plan_map"
    INFERENCE_POLICY = "inference_policy"
    ROUTE = "route"
    ENV_VAR = "env_var"
    AUTO_FAST_MODEL = "auto_fast_model"
    AUTO_REASONING_MODEL = "auto_reasoning_model"
    DOCKER_SERVICE_COUNT = "docker_service_count"
    SUBAGENT_STATUS = "subagent_status"


@dataclass
class Finding:
    doc_path: str
    line: int
    check_id: str
    kind: str
    expected: str | None
    observed: str | None
    message: str


@dataclass
class ExceptionEntry:
    check_id: str
    expires: date
    reason: str
    doc_path: str
    line: int
    suppressed_finding: bool = False


_EXCEPTION_RE = re.compile(
    r"<!--\s*DOC_FRESHNESS_EXCEPTION:\s*"
    + r"([a-z_]+)\s+"
    + r"expires=(\d{4}-\d{2}-\d{2})\s+"
    + r'reason="([^"]*)"',
    re.IGNORECASE,
)
_EXCEPTION_LINE_RE = re.compile(r"<!--\s*DOC_FRESHNESS_EXCEPTION:", re.IGNORECASE)


def parse_exceptions(
    lines: list[str], doc_path: str
) -> tuple[list[ExceptionEntry], list[tuple[int, str]]]:
    exceptions: list[ExceptionEntry] = []
    malformed: list[tuple[int, str]] = []

    for lineno, line in enumerate(lines, start=1):
        if _EXCEPTION_LINE_RE.search(line):
            matched = False
            for m in _EXCEPTION_RE.finditer(line):
                matched = True
                check_id = m.group(1).lower()
                expires_str = m.group(2)
                reason = m.group(3)
                try:
                    expires = datetime.strptime(expires_str, "%Y-%m-%d").date()
                except ValueError:
                    malformed.append((lineno, f"invalid expiry date '{expires_str}'"))
                    continue
                if not reason:
                    malformed.append((lineno, "empty reason"))
                    continue
                exceptions.append(
                    ExceptionEntry(
                        check_id=check_id,
                        expires=expires,
                        reason=reason,
                        doc_path=doc_path,
                        line=lineno,
                    )
                )
            if not matched:
                malformed.append((lineno, "malformed exception syntax"))

    return exceptions, malformed


@dataclass
class CheckResult:
    check_id: str
    passed: bool
    expected: str | None = None
    observed: str | None = None
    message: str | None = None


_ROUTE_TABLE_RE = re.compile(r"`(/[^`]+)`")
_KNOWN_SINGLE_SEGMENT_ROUTES = frozenset(["/chat", "/health", "/status", "/providers"])
_METHOD_LINE_RE = re.compile(r"\|\s*[A-Z][A-Z/]+\s*\|")
_METHOD_CELL_RE = re.compile(r"\|\s*([A-Z/]+)\s*\|", re.IGNORECASE)


def _is_route_table_row(line_text: str, route_start: int) -> bool:
    return bool(_METHOD_LINE_RE.search(line_text))


def _normalize_route(route: str) -> str:
    normalized = route.strip()
    normalized = re.sub(r"\{[^}]+\}", "{id}", normalized)
    return normalized


def _check_routes(doc_content: str, source_routes: dict[str, list[str]]) -> CheckResult:
    if not source_routes:
        return CheckResult(CheckId.ROUTE, True)

    # Build normalized path sets and method lookups
    all_source: set[str] = set()
    source_methods_by_path: dict[str, set[str]] = {}
    for method, paths in source_routes.items():
        for p in paths:
            norm = _normalize_route(p)
            all_source.add(norm)
            source_methods_by_path.setdefault(norm, set()).add(method.upper())

    if not all_source:
        return CheckResult(CheckId.ROUTE, True)

    # Collect (route, methods_from_doc) from table rows
    doc_route_methods: list[tuple[str, str]] = []
    processed_lines: set[int] = set()
    for m in _ROUTE_TABLE_RE.finditer(doc_content):
        route = m.group(1).strip()
        if not route.startswith("/"):
            continue
        line_start = doc_content.rfind("\n", 0, m.start()) + 1
        line_end = doc_content.find("\n", m.start())
        if line_end == -1:
            line_end = len(doc_content)
        line_text = doc_content[line_start:line_end]
        line_key = hash(line_text)
        if line_key in processed_lines:
            continue
        processed_lines.add(line_key)
        if route.count("/") >= 2:
            methods = _extract_methods_from_row(line_text)
            all_routes_in_row = _ROUTE_TABLE_RE.findall(line_text)
            for r in all_routes_in_row:
                r_stripped = r.strip()
                if r_stripped.startswith("/"):
                    doc_route_methods.append((r_stripped, methods))
            continue
        if route in _KNOWN_SINGLE_SEGMENT_ROUTES:
            doc_route_methods.append((route, ""))
            continue
        line_start = doc_content.rfind("\n", 0, m.start()) + 1
        line_end = doc_content.find("\n", m.start())
        if line_end == -1:
            line_end = len(doc_content)
        line_text = doc_content[line_start:line_end]
        if _is_route_table_row(line_text, m.start() - line_start):
            methods = _extract_methods_from_row(line_text)
            doc_route_methods.append((route, methods))

    stale_paths: list[str] = []
    for route, _ in doc_route_methods:
        if _normalize_route(route) not in all_source:
            stale_paths.append(route)
    if stale_paths:
        return CheckResult(
            CheckId.ROUTE,
            False,
            f"all source routes: {len(all_source)} paths",
            f"stale: {', '.join(sorted(stale_paths))}",
            f"documented route(s) not found in source: {', '.join(sorted(stale_paths))}",
        )

    method_failures: list[str] = []
    for route, doc_methods in doc_route_methods:
        if "{" in route and re.search(r"\{[a-z_]+\}", route):
            continue
        if not doc_methods:
            continue
        norm = _normalize_route(route)
        src_methods = source_methods_by_path.get(norm, set())
        if not src_methods:
            continue
        for dm in doc_methods.split("/"):
            dm = dm.strip().upper()
            if dm not in src_methods:
                method_failures.append(
                    f"{route}: method {dm} not in source ({', '.join(sorted(src_methods))})"
                )

    if method_failures:
        return CheckResult(
            CheckId.ROUTE,
            False,
            "source methods",
            "; ".join(method_failures[:3]),
            f"route method mismatch: {method_failures[0]}",
        )

    return CheckResult(CheckId.ROUTE, True)


def _extract_methods_from_row(line_text: str) -> str:
    methods_match = _METHOD_CELL_RE.search(line_text)
    return methods_match.group(1).strip() if methods_match else ""


_ENV_DOC_RE = re.compile(r"`([A-Z_][A-Z0-9_]*)`")
_ENV_VAR_SUFFIXES = frozenset(
    [
        "KEY",
        "URL",
        "TOKEN",
        "SECRET",
        "PATH",
        "DB",
        "PORT",
        "HOST",
        "PASSWORD",
        "NAME",
        "ID",
        "MODEL",
        "SETTING",
        "VAR",
        "TYPE",
    ]
)


def _is_env_var_name(name: str) -> bool:
    if not name:
        return False
    if name in ("EMBEDDING_DOCUMENT_MODEL", "EMBEDDING_QUERY_MODEL", "EMBEDDING_DIMENSIONS"):
        return False
    suffix = name.split("_")[-1] if "_" in name else ""
    return suffix in _ENV_VAR_SUFFIXES


def _check_env_vars(doc_content: str, source_vars: list[str]) -> CheckResult:
    if not source_vars:
        return CheckResult(CheckId.ENV_VAR, True)
    source_set = set(source_vars)
    doc_vars = [
        m.group(1) for m in _ENV_DOC_RE.finditer(doc_content) if _is_env_var_name(m.group(1))
    ]
    if not doc_vars:
        return CheckResult(CheckId.ENV_VAR, True)
    stale: list[str] = [v for v in doc_vars if v not in source_set]
    if stale:
        return CheckResult(
            CheckId.ENV_VAR,
            False,
            f"source env vars: {', '.join(sorted(source_set))}",
            f"documented stale: {', '.join(sorted(stale))}",
            f"env var(s) not found in source: {', '.join(sorted(stale))}",
        )
    return CheckResult(CheckId.ENV_VAR, True)


_MEMORY_LAYER_REQUIRED_VARS = frozenset(
    [
        "VOYAGE_API_KEY",
        "EMBEDDING_DOCUMENT_MODEL",
        "EMBEDDING_QUERY_MODEL",
        "EMBEDDING_DIMENSIONS",
        "DEDUP_MERGE_THRESHOLD",
        "DEDUP_SUPERSEDE_THRESHOLD",
        "DEDUP_SUPERSEDE_SAME_SLOT_THRESHOLD",
    ]
)


def _check_memory_layer_env_block(doc_content: str) -> CheckResult:
    in_block = False
    found_vars: set[str] = set()
    env_assign = re.compile(r"^([A-Z_][A-Z0-9_]*)=")
    for line in doc_content.splitlines():
        stripped = line.strip()
        unescaped = stripped[1:] if stripped.startswith("\\") else stripped
        if (
            unescaped.startswith("```bash")
            and "## Environment" in doc_content[: doc_content.find(line)]
        ):
            in_block = True
            continue
        if unescaped.startswith("```") and in_block:
            in_block = False
            continue
        if in_block:
            m = env_assign.match(line.lstrip())
            if m:
                found_vars.add(m.group(1))
    missing = _MEMORY_LAYER_REQUIRED_VARS - found_vars
    if missing:
        return CheckResult(
            CheckId.ENV_VAR,
            False,
            f"required memory vars: {', '.join(sorted(_MEMORY_LAYER_REQUIRED_VARS))}",
            f"missing: {', '.join(sorted(missing))}",
            f"memory layer env block missing: {', '.join(sorted(missing))}",
        )
    return CheckResult(CheckId.ENV_VAR, True)


def _check_migration_count(doc_content: str, expected: int) -> CheckResult:
    matches = re.findall(r"\b(\d{2,3})\s+migration", doc_content, re.IGNORECASE)
    if not matches:
        return CheckResult(CheckId.MIGRATION_COUNT, True)
    for m in matches:
        observed = int(m)
        if observed != expected:
            return CheckResult(
                CheckId.MIGRATION_COUNT,
                False,
                str(expected),
                str(observed),
                f"migration count mismatch: expected {expected}, found {observed}",
            )
    return CheckResult(CheckId.MIGRATION_COUNT, True)


def _check_migration_latest(doc_content: str, expected: str) -> CheckResult:
    latest_claim_pattern = re.compile(
        r"(?:latest(?:\s+(?:migration|db))?|most\s+recent|newest)[:\s]+`?(\d{2,3}_\w+(?:\.sql)?)`?",
        re.IGNORECASE,
    )
    matches = latest_claim_pattern.findall(doc_content)
    if not matches:
        return CheckResult(CheckId.MIGRATION_LATEST, True)
    for observed in matches:
        normalized = observed if observed.endswith(".sql") else observed + ".sql"
        if normalized != expected and observed != expected:
            return CheckResult(
                CheckId.MIGRATION_LATEST,
                False,
                expected,
                observed,
                f"latest migration mismatch: expected {expected}, found {observed}",
            )
    return CheckResult(CheckId.MIGRATION_LATEST, True)


def _check_embedding_doc_model(doc_content: str, expected: str) -> CheckResult:
    m = _EMBEDDING_DOC_MODEL_CLAIM_RE.search(doc_content)
    if not m:
        return CheckResult(CheckId.EMBEDDING_DOC_MODEL, True)
    observed = m.group(1).strip()
    if _normalize_model_name(observed) != _normalize_model_name(expected):
        return CheckResult(
            CheckId.EMBEDDING_DOC_MODEL,
            False,
            expected,
            observed,
            f"embedding doc model mismatch: expected {expected}, found {observed}",
        )
    return CheckResult(CheckId.EMBEDDING_DOC_MODEL, True)


def _check_embedding_query_model(doc_content: str, expected: str) -> CheckResult:
    m = _EMBEDDING_QUERY_MODEL_CLAIM_RE.search(doc_content)
    if not m:
        return CheckResult(CheckId.EMBEDDING_QUERY_MODEL, True)
    observed = m.group(1).strip()
    if _normalize_model_name(observed) != _normalize_model_name(expected):
        return CheckResult(
            CheckId.EMBEDDING_QUERY_MODEL,
            False,
            expected,
            observed,
            f"embedding query model mismatch: expected {expected}, found {observed}",
        )
    return CheckResult(CheckId.EMBEDDING_QUERY_MODEL, True)


def _check_embedding_dimensions(doc_content: str, expected: int) -> CheckResult:
    m = _EMBEDDING_DIMENSIONS_CLAIM_RE.search(doc_content)
    if not m:
        return CheckResult(CheckId.EMBEDDING_DIMENSIONS, True)
    observed = int(m.group(1))
    if observed != expected:
        return CheckResult(
            CheckId.EMBEDDING_DIMENSIONS,
            False,
            str(expected),
            str(observed),
            f"embedding dimensions mismatch: expected {expected}, found {observed}",
        )
    return CheckResult(CheckId.EMBEDDING_DIMENSIONS, True)


_EMBEDDING_PROSE_DOC_RE = re.compile(
    r"`([^`]+)`\s*\((\d+)d\)[^,]*\bfor documents",
    re.IGNORECASE,
)
_EMBEDDING_PROSE_QUERY_RE = re.compile(
    r"`([^`]+)`\s*\((\d+)d\)[^,]*\bfor queries",
    re.IGNORECASE,
)


def _check_embedding_prose(doc_content: str, efact: dict[str, Any]) -> list[CheckResult]:
    results = []
    doc_model = efact.get("document_model", "")
    query_model = efact.get("query_model", "")
    dims = efact.get("dimensions")
    doc_norm = _normalize_model_name(doc_model) if doc_model else ""
    query_norm = _normalize_model_name(query_model) if query_model else ""

    doc_m = _EMBEDDING_PROSE_DOC_RE.search(doc_content)
    if doc_m and doc_model:
        doc_model_obs = doc_m.group(1)
        doc_dims_obs = int(doc_m.group(2))
        doc_model_obs_norm = _normalize_model_name(doc_model_obs)
        if doc_model_obs_norm != doc_norm:
            results.append(
                CheckResult(
                    CheckId.EMBEDDING_DOC_MODEL,
                    False,
                    doc_model,
                    doc_model_obs,
                    f"embedding doc model mismatch in structured prose: expected {doc_model}",
                )
            )
        if doc_dims_obs != dims:
            results.append(
                CheckResult(
                    CheckId.EMBEDDING_DIMENSIONS,
                    False,
                    str(dims),
                    str(doc_dims_obs),
                    f"embedding doc dims mismatch in structured prose: expected {dims}, found {doc_dims_obs}",
                )
            )

    query_m = _EMBEDDING_PROSE_QUERY_RE.search(doc_content)
    if query_m and query_model:
        query_model_obs = query_m.group(1)
        query_dims_obs = int(query_m.group(2))
        query_model_obs_norm = _normalize_model_name(query_model_obs)
        if query_model_obs_norm != query_norm:
            results.append(
                CheckResult(
                    CheckId.EMBEDDING_QUERY_MODEL,
                    False,
                    query_model,
                    query_model_obs,
                    f"embedding query model mismatch in structured prose: expected {query_model}",
                )
            )
        if query_dims_obs != dims:
            results.append(
                CheckResult(
                    CheckId.EMBEDDING_DIMENSIONS,
                    False,
                    str(dims),
                    str(query_dims_obs),
                    f"embedding query dims mismatch in structured prose: expected {dims}, found {query_dims_obs}",
                )
            )

    return results


_MEMORY_EMBEDDING_TABLE_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*`([^`]+)`\s*\|\s*[^|]+\s*\|\s*(\d+)\s*\|",
    re.IGNORECASE,
)


def _check_memory_layer_table(doc_content: str, efact: dict[str, Any]) -> list[CheckResult]:
    results = []
    doc_model = efact.get("document_model", "")
    query_model = efact.get("query_model", "")
    dims = efact.get("dimensions")
    if not doc_model and not query_model:
        return results
    doc_norm = _normalize_model_name(doc_model) if doc_model else ""
    query_norm = _normalize_model_name(query_model) if query_model else ""
    for line in doc_content.splitlines():
        m = _MEMORY_EMBEDDING_TABLE_RE.match(line.strip())
        if not m:
            continue
        purpose, model_raw, dim_obs_raw = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        model_norm = _normalize_model_name(model_raw)
        dim_obs = int(dim_obs_raw) if dim_obs_raw.isdigit() else None
        if "document" in purpose.lower() and doc_model and model_norm != doc_norm:
            results.append(
                CheckResult(
                    CheckId.EMBEDDING_DOC_MODEL,
                    False,
                    doc_model,
                    model_raw,
                    f"memory layer doc model mismatch: expected {doc_model}",
                )
            )
        if "document" in purpose.lower() and dims and dim_obs and dim_obs != dims:
            results.append(
                CheckResult(
                    CheckId.EMBEDDING_DIMENSIONS,
                    False,
                    str(dims),
                    str(dim_obs),
                    f"memory layer doc dims mismatch: expected {dims}, found {dim_obs}",
                )
            )
        if "query" in purpose.lower() and query_model and model_norm != query_norm:
            results.append(
                CheckResult(
                    CheckId.EMBEDDING_QUERY_MODEL,
                    False,
                    query_model,
                    model_raw,
                    f"memory layer query model mismatch: expected {query_model}",
                )
            )
        if "query" in purpose.lower() and dims and dim_obs and dim_obs != dims:
            results.append(
                CheckResult(
                    CheckId.EMBEDDING_DIMENSIONS,
                    False,
                    str(dims),
                    str(dim_obs),
                    f"memory layer query dims mismatch: expected {dims}, found {dim_obs}",
                )
            )
    return results


def _float_normalize(val: float) -> str:
    return f"{val:.2f}"


def _check_dedup_threshold(
    doc_content: str, threshold_name: str, expected: float, label_pattern: str
) -> CheckResult:
    label_re = re.compile(label_pattern, re.IGNORECASE)
    expected_norm = _float_normalize(expected)
    lines = doc_content.splitlines()

    for i, line in enumerate(lines):
        if not label_re.search(line):
            continue
        line_vals = re.findall(r"\b0\.\d{1,}\b", line)
        if line_vals:
            matched = False
            wrong_val = None
            for v in line_vals:
                if _float_normalize(float(v)) == expected_norm:
                    matched = True
                    break
                else:
                    wrong_val = v
            if matched:
                return CheckResult(threshold_name, True)
            elif wrong_val:
                return CheckResult(
                    threshold_name,
                    False,
                    expected_norm,
                    wrong_val,
                    f"dedup threshold mismatch for {threshold_name}: expected {expected_norm} near '{label_pattern}', found {wrong_val}",
                )
            continue
        window_start = max(0, i - 2)
        window_end = min(len(lines), i + 3)
        window_text = "\n".join(lines[window_start:window_end])
        all_vals = re.findall(r"\b0\.\d{1,}\b", window_text)
        if not all_vals:
            continue
        matched = False
        wrong_val = None
        for v in all_vals:
            if _float_normalize(float(v)) == expected_norm:
                matched = True
                break
            else:
                wrong_val = v
        if matched:
            return CheckResult(threshold_name, True)
        elif wrong_val:
            return CheckResult(
                threshold_name,
                False,
                expected_norm,
                wrong_val,
                f"dedup threshold mismatch for {threshold_name}: expected {expected_norm} near '{label_pattern}', found {wrong_val}",
            )
    return CheckResult(threshold_name, True)


def _check_video_providers(doc_content: str, valid_providers: frozenset[str]) -> CheckResult:
    """
    Check structured video provider claims in doc against source-derived valid set.

    Singular/plural distinction:
      - Singular (provider: xai): validates the claimed provider is in the valid set.
        Does NOT require the full valid set to be present.
      - Plural/list (providers: xai,fal | **Providers**: `fal` `xai` | VALID_VIDEO_PROVIDERS = {...}):
        requires exact set match: no unsupported providers AND no missing required ones.

    Minimizes false positives in narrative prose by requiring word-boundary
    anchors or structural patterns (colon, braces, backticks).
    """
    # Singular forms: capture exactly one provider name after "provider:" or "video provider:"
    singular_patterns = [
        (r"\bprovider:\s*([a-z]+)",),
        (r"\bvideo\s+provider:\s*([a-z]+)",),
    ]
    # Plural/list forms: capture raw comma-separated names, brace-enclosed set, or markdown backtick lists
    plural_patterns = [
        (r"\bproviders:\s*([a-z, ]+)",),
        (r"\bvideo\s+providers:\s*([a-z, ]+)",),
        (r"\bVALID_VIDEO_PROVIDERS\s*=\s*\{([^}]+)\}",),
    ]
    plural_backtick_label_patterns = [
        r"\*\*Providers\*\*:",
        r"\*\*Video\s+Providers\*\*:",
    ]

    singular_claims: set[str] = set()
    plural_claims: set[str] = set()

    for (pat,) in singular_patterns:
        for m in re.finditer(pat, doc_content, re.IGNORECASE):
            if m.lastindex and m.lastindex >= 1:
                singular_claims.add(m.group(1).lower())

    for (pat,) in plural_patterns:
        for m in re.finditer(pat, doc_content, re.IGNORECASE):
            if m.lastindex and m.lastindex >= 1:
                raw = m.group(1)
                names = re.findall(r'["\']?([a-z]+)["\']?', raw, re.IGNORECASE)
                for n in names:
                    if n.lower() not in singular_claims:
                        plural_claims.add(n.lower())

    # Markdown bold plural labels: find the label, extract all backtick names on the SAME LINE
    for label_pat in plural_backtick_label_patterns:
        for m in re.finditer(label_pat, doc_content, re.IGNORECASE):
            # Limit remainder to current line only (next newline or end of document)
            line_end = doc_content.find("\n", m.end())
            if line_end == -1:
                line_end = len(doc_content)
            remainder = doc_content[m.end() : line_end]
            names = re.findall(r"`([a-z]+)`", remainder, re.IGNORECASE)
            for n in names:
                if n.lower() not in singular_claims:
                    plural_claims.add(n.lower())

    # Singular claims: validate each is in the valid set
    for claim in singular_claims:
        if claim not in valid_providers:
            valid_list = ", ".join(sorted(valid_providers))
            return CheckResult(
                CheckId.VIDEO_PROVIDERS,
                False,
                f"valid providers: {valid_list}",
                f"invalid: {claim}",
                f"video provider '{claim}' is not in the valid provider set",
            )

    # Plural/list claims: exact set comparison (unsupported AND missing)
    if plural_claims:
        unsupported = plural_claims - valid_providers
        missing = valid_providers - plural_claims
        if unsupported or missing:
            valid_list = ", ".join(sorted(valid_providers))
            claimed_list = ", ".join(sorted(plural_claims))
            parts = []
            if unsupported:
                parts.append(f"unsupported: {', '.join(sorted(unsupported))}")
            if missing:
                parts.append(f"missing: {', '.join(sorted(missing))}")
            detail = "; ".join(parts)
            return CheckResult(
                CheckId.VIDEO_PROVIDERS,
                False,
                f"providers in {valid_list}",
                f"claimed {claimed_list} ({detail})",
                "video provider set mismatch",
            )

    return CheckResult(CheckId.VIDEO_PROVIDERS, True)


# Model names appear in documentation with and without provider prefixes and
# modality suffixes, so both are normalized before comparison.
def _normalize_model_name(model: str) -> str:
    normalized = model.lower()
    while True:
        stripped = False
        for prefix in [
            "openrouter/",
            "openai/",
            "anthropic/",
            "google/",
            "x-ai/",
            "deepseek/",
            "moonshotai/",
        ]:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :]
                stripped = True
                break
        if not stripped:
            break
    for suffix in ["-image", "-video", "-instruct", "-chat", "-preview"]:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    normalized = normalized.replace("/", " ").replace("-", " ").replace("_", " ")
    return normalized.strip()


_PLAN_ITEM = r"(?:\*\*[A-Za-z][A-Za-z0-9 _/-]{0,30}?\*\*|`[a-z][a-z0-9_-]*`|[A-Z][A-Za-z0-9_-]*)"
_PLAN_LIST_RE = re.compile(
    r"\s*"
    + _PLAN_ITEM
    + r"(?:\s*/\s*"
    + _PLAN_ITEM
    + r")*(?:\s*,\s*(?:and\s+|or\s+)?"
    + _PLAN_ITEM
    + r")*(?:\s+(?:and|or)\s+"
    + _PLAN_ITEM
    + r")*"
)
# Items keep track of emphasis: **Free** is a deliberate claim, a bare Free is
# only a claim when the list is otherwise anchored to a declared plan.
_PLAN_ITEM_TOKEN_RE = re.compile(
    r"(?P<bold>\*\*[A-Za-z][A-Za-z0-9 _/-]{0,30}?\*\*)"
    r"|(?P<code>`[a-z][a-z0-9_-]*`)"
    r"|(?P<plain>[A-Z][A-Za-z0-9_-]*)"
)
# Anchors that make a following capitalized list a *plan* list rather than an
# ordinary enumeration: a commercial/model noun, or a plans copula.
_PLAN_SENTENCE_ANCHOR_RE = re.compile(
    r"\b(?:commercial|durable|current|paid|subscription|account)\s+"
    r"(?:plans?|models?|offerings?|tiers?)\b"
    r"|\bplans?\s+(?:are|is)\b"
    r"|\bplans?\s*:",
    re.IGNORECASE,
)
_PLAN_ROW_LABEL_RE = re.compile(r"^\|\s*\**\s*([A-Za-z][A-Za-z0-9 _/-]{0,40}?)\s*\**\s*\|")
_PLAN_TABLE_HEADER_RE = re.compile(r"^\|\s*\**\s*plans?\s*\**\s*\|", re.IGNORECASE)
# A policy vocabulary claim must be a definition: the label at the start of a
# line (optionally bulleted/bold). A mid-sentence "for vector operations: `x`"
# is prose about something else entirely.
_POLICY_LABEL_PREFIX = r"^[ \t]*(?:[-*+][ \t]+|\d+[.)][ \t]+)?(?:\*\*|__)?[ \t]*"
_CAPABILITY_LABEL_RE = re.compile(
    _POLICY_LABEL_PREFIX + r"capabilit(?:y|ies)(?:\*\*|__)?[ \t]*[:=][ \t]*([a-z0-9_, `]+)",
    re.IGNORECASE | re.MULTILINE,
)
_OPERATION_LABEL_RE = re.compile(
    _POLICY_LABEL_PREFIX + r"operations?(?:\*\*|__)?[ \t]*[:=][ \t]*([a-z0-9_, `]+)",
    re.IGNORECASE | re.MULTILINE,
)
_PRICE_CLAIM_RE = re.compile(
    r"(?P<currency>US\$|CA\$|AU\$|A\$|NZ\$|\$)\s?(?P<amount>\d+(?:\.\d{1,2})?)\s*(?:/|\s*per\s*)\s*"
    r"(?P<period>mo\b|month|period|calendar month)",
    re.IGNORECASE,
)
_CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$",
    "CAD": "CA$",
    "AUD": "A$",
    "NZD": "NZ$",
    "EUR": "€",
    "GBP": "£",
}


def _after_plan_copula(line: str, pos: int) -> int:
    """Advance past an optional "are"/"is"/":" between a plan anchor and its list."""
    m = _PLAN_COPULA_RE.match(line, pos)
    return m.end() if m else pos


_PLAN_COPULA_RE = re.compile(r"\s*(?:are|is|:)\s*", re.IGNORECASE)


def _plan_claims(
    doc_content: str,
    known: frozenset[str] = frozenset(),
    legacy_names: frozenset[str] = frozenset(),
) -> dict[str, list[str]]:
    """Extract plan identifiers a document claims exist.

    Two high-confidence sources:
    - a plan-keyed table whose header first cell is literally ``Plan``/``Plans``
    - an anchored sentence ("commercial plans are ...", "the commercial model is
      ...") whose trailing item list names two or more plans

    A name only counts as a claim when it sits in one of those two structures
    *and* the list is anchored: either a plan-keyed table, or a list where at
    least one item is a declared plan name or is explicitly emphasized. So a
    bold heading elsewhere in a gated document is never read as a plan, and an
    ordinary capitalized enumeration after the word "plans" is left alone.
    """
    claims: dict[str, list[str]] = {}

    def _record(plan: str, why: str) -> None:
        claims.setdefault(plan, []).append(why)

    lines = doc_content.splitlines()
    in_table = False
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if _PLAN_TABLE_HEADER_RE.match(stripped):
            in_table = True
            continue
        if in_table and stripped.startswith("|"):
            if set(stripped) <= set("|-: "):
                continue
            row = _PLAN_ROW_LABEL_RE.match(stripped)
            if row:
                label = row.group(1).strip()
                if label and not set(label) <= set("-— "):
                    _record(label.lower(), f"plan table row at line {lineno}")
            continue
        in_table = False

    for lineno, line in enumerate(lines, start=1):
        if line.lstrip().startswith("|"):
            continue
        anchor = None
        for m in _PLAN_SENTENCE_ANCHOR_RE.finditer(line):
            anchor = m
        if anchor is None:
            continue
        list_match = _PLAN_LIST_RE.match(line, _after_plan_copula(line, anchor.end()))
        if not list_match:
            continue
        items: list[tuple[str, bool]] = []
        for token in _PLAN_ITEM_TOKEN_RE.finditer(list_match.group(0)):
            emphasized = token.group("bold") is not None or token.group("code") is not None
            name = next(g for g in token.groups() if g)
            items.append((name.strip("*`_ ").lower(), emphasized))
        if len(items) < 2:
            continue
        if not any(
            emphasized or name in known or name in legacy_names for name, emphasized in items
        ):
            continue
        for name, _emphasized in items:
            if name:
                _record(name, f"plan list at line {lineno}")

    return claims


def _check_commercial_plans(doc_content: str, commercial: dict[str, Any]) -> list[CheckResult]:
    """Documented plan set vs. plans declared in config/commercial.json.

    Fails on:
      - a documented plan that policy does not declare (typo, invented plan, or
        a retired tier name presented as a current plan)
      - a plan declared in policy that the document's plan set omits
    """
    plan_ids = [str(p).lower() for p in commercial.get("plan_ids", [])]
    if not plan_ids:
        return []
    labels = {str(k).lower() for k in commercial.get("plan_labels", {})}
    known = set(plan_ids) | labels
    # Legacy names are legal in a mapping sentence, never as a current plan.
    legacy_names = {str(k).lower() for k in commercial.get("legacy_plan_map", {})}

    results: list[CheckResult] = []
    claims = _plan_claims(doc_content, frozenset(known), frozenset(legacy_names))
    for plan, whys in sorted(claims.items()):
        if plan in known:
            continue
        hint = " (retired legacy name; see legacy_plan_map)" if plan in legacy_names else ""
        results.append(
            CheckResult(
                CheckId.COMMERCIAL_PLAN,
                False,
                f"plans: {', '.join(sorted(known))}",
                plan,
                f"documented plan '{plan}' is not declared in config/commercial.json"
                f"{hint} ({whys[0]})",
            )
        )

    if not claims or not claims.keys() <= known:
        # No plan set, or an unknown plan already reported: coverage would be
        # reported against a claim set that is itself wrong.
        return results

    documented = {plan for plan in claims if plan in known}
    missing = [p for p in plan_ids if p not in documented]
    if missing and documented:
        results.append(
            CheckResult(
                CheckId.COMMERCIAL_PLAN,
                False,
                f"plans: {', '.join(plan_ids)}",
                f"missing: {', '.join(missing)}",
                f"plan(s) declared in config/commercial.json but absent from the "
                f"documented plan set: {', '.join(missing)}",
            )
        )
    return results


def _check_commercial_capabilities(
    doc_content: str, commercial: dict[str, Any]
) -> list[CheckResult]:
    """Documented capability/operation ids vs. config/commercial.json.

    Only an explicit ``capability:``/``capabilities:`` (or ``operation(s):``)
    label creates a claim, so prose that happens to discuss capabilities is not
    gated. Per-plan capability grants are covered by the plan table check below.
    """
    vocabularies: list[tuple[str, Any, str]] = [
        (
            "capabilit",
            {str(c).lower() for c in commercial.get("capability_vocabulary", [])},
            "capability",
        ),
        (
            "operation",
            {str(c).lower() for c in commercial.get("operation_vocabulary", [])},
            "operation",
        ),
    ]
    results: list[CheckResult] = []
    for label, vocabulary, noun in vocabularies:
        if not vocabulary:
            continue
        pattern = _CAPABILITY_LABEL_RE if label == "capabilit" else _OPERATION_LABEL_RE
        for m in pattern.finditer(doc_content):
            for token in re.findall(r"[a-z][a-z0-9_]*", m.group(1)):
                if token in vocabulary:
                    continue
                results.append(
                    CheckResult(
                        CheckId.COMMERCIAL_CAPABILITY,
                        False,
                        f"{noun}s: {', '.join(sorted(vocabulary))}",
                        token,
                        f"documented {noun} '{token}' is not declared in config/commercial.json",
                    )
                )

    plan_tables = _plan_tables(doc_content)
    capabilities = commercial.get("capabilities", {})
    for table in plan_tables:
        for row in table["rows"]:
            plan_id = _resolve_plan_id(row["label"], commercial)
            if plan_id is None or table["cap_col"] is None:
                continue
            if table["cap_col"] >= len(row["cells"]):
                continue
            granted = {str(c).lower() for c in capabilities.get(plan_id, [])}
            if not granted:
                continue
            tokens = {
                t.lower()
                for t in re.findall(r"[a-z][a-z0-9_]*_[a-z0-9_]+", row["cells"][table["cap_col"]])
            }
            for token in sorted(tokens - granted):
                results.append(
                    CheckResult(
                        CheckId.COMMERCIAL_CAPABILITY,
                        False,
                        f"{plan_id}: {', '.join(sorted(granted))}",
                        token,
                        f"plan {plan_id} is documented with capability '{token}' which "
                        f"config/commercial.json does not grant it",
                    )
                )
    return results


def _resolve_plan_id(label: str, commercial: dict[str, Any]) -> str | None:
    normalized = label.strip("*`_ ").lower()
    if not normalized:
        return None
    if normalized in {str(p).lower() for p in commercial.get("plan_ids", [])}:
        return normalized
    return commercial.get("plan_labels", {}).get(normalized)


def _plan_tables(doc_content: str) -> list[dict[str, Any]]:
    """Return plan-keyed tables with their capability/price column indexes."""
    tables: list[dict[str, Any]] = []
    lines = doc_content.splitlines()
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        if not _PLAN_TABLE_HEADER_RE.match(stripped):
            idx += 1
            continue
        header = [c.strip().lower() for c in stripped.strip("|").split("|")]
        cap_col = next((i for i, c in enumerate(header) if "capabilit" in c), None)
        price_col = next(
            (i for i, c in enumerate(header) if any(k in c for k in ("price", "cost", "amount"))),
            None,
        )
        rows: list[dict[str, Any]] = []
        cursor = idx + 1
        while cursor < len(lines) and lines[cursor].strip().startswith("|"):
            row_text = lines[cursor].strip()
            cursor += 1
            if set(row_text) <= set("|-: "):
                continue
            cells = [c.strip() for c in row_text.strip("|").split("|")]
            label_match = _PLAN_ROW_LABEL_RE.match(row_text)
            if not label_match:
                continue
            label = label_match.group(1).strip()
            if not label or set(label) <= set("-— "):
                continue
            rows.append({"line": cursor, "label": label, "cells": cells})
        tables.append(
            {"header_line": idx + 1, "cap_col": cap_col, "price_col": price_col, "rows": rows}
        )
        idx = cursor
    return tables


def _check_commercial_prices(doc_content: str, commercial: dict[str, Any]) -> list[CheckResult]:
    """Documented plan prices vs. display prices in config/commercial.json.

    Only explicit "<currency><amount> per <period>" claims attached to a plan on
    the same row or line are checked, so narrative figures that merely mention
    money are not gated.
    """
    display = commercial.get("display", {})
    if not display:
        return []

    results: list[CheckResult] = []
    for table in _plan_tables(doc_content):
        for row in table["rows"]:
            plan_id = _resolve_plan_id(row["label"], commercial)
            if plan_id is None or table["price_col"] is None:
                continue
            cell = (
                row["cells"][table["price_col"]] if table["price_col"] < len(row["cells"]) else ""
            )
            results.extend(
                _price_results(cell, plan_id, display, f"plan table row at line {row['line']}")
            )

    for lineno, line in enumerate(doc_content.splitlines(), start=1):
        if line.lstrip().startswith("|"):
            continue
        for label_match in re.finditer(r"\*\*([A-Za-z][A-Za-z0-9 _/-]{0,30}?)\*\*", line):
            plan_id = _resolve_plan_id(label_match.group(1), commercial)
            if plan_id is None:
                continue
            results.extend(_price_results(line, plan_id, display, f"line {lineno}"))
    return results


def _price_results(
    text: str,
    plan_id: str,
    display: dict[str, dict[str, str]],
    where: str,
) -> list[CheckResult]:
    declared = display.get(plan_id)
    if not declared or declared.get("amount_minor") is None:
        return []
    try:
        expected_value = int(declared["amount_minor"]) / 100
    except ValueError:
        return []
    expected_amount = f"{expected_value:.2f}"
    expected_symbol = _CURRENCY_SYMBOLS.get(declared.get("currency", ""), "")
    results: list[CheckResult] = []
    for price in _PRICE_CLAIM_RE.finditer(text):
        observed_amount = price.group("amount")
        try:
            observed_value = float(observed_amount)
        except ValueError:
            continue
        problems: list[str] = []
        if abs(observed_value - expected_value) > 0.0049:
            problems.append(
                f"expected {expected_symbol}{expected_amount}, got "
                f"{price.group('currency')}{observed_amount}"
            )
        elif expected_symbol and price.group("currency") != expected_symbol:
            problems.append(
                f"declared currency is {declared.get('currency')} ({expected_symbol}), "
                f"doc uses {price.group('currency')}"
            )
        if problems:
            results.append(
                CheckResult(
                    CheckId.COMMERCIAL_PRICE,
                    False,
                    f"{plan_id}: {expected_symbol}{expected_amount} {declared.get('currency')}",
                    f"{price.group('currency')}{observed_amount}",
                    f"plan {plan_id} price mismatch in {where}: {'; '.join(problems)}",
                )
            )
    return results


_LEGACY_MAP_KEYWORD_RE = re.compile(
    r"\b(?:map|mapping|legacy|retired|renamed|migrate|migration)\b", re.IGNORECASE
)
_LEGACY_ARROW_RE = re.compile(
    r"(?:^|[,;:()]|\band\b)\s*\**([A-Za-z][A-Za-z0-9_-]{0,30}?)\**\s*"
    r"(?:\u2192|(?<!-)->(?!>))\s*\**([A-Za-z][A-Za-z0-9_-]{0,30})\**"
)


def _check_legacy_plan_map(doc_content: str, commercial: dict[str, Any]) -> list[CheckResult]:
    """Documented legacy-name mapping vs. legacy_plan_map in config/commercial.json.

    Legacy names are legitimate in prose as migration sources. What must not
    drift is the mapping itself: "Max -> Power" documented as "Max -> Pro".
    """
    legacy_map = commercial.get("legacy_plan_map", {})
    if not legacy_map:
        return []
    plan_ids = {str(p).lower() for p in commercial.get("plan_ids", [])}
    if not plan_ids:
        return []

    results: list[CheckResult] = []
    in_fence = False
    for lineno, line in enumerate(doc_content.splitlines(), start=1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or line.lstrip().startswith("|"):
            continue
        if not _LEGACY_MAP_KEYWORD_RE.search(line):
            continue
        for m in _LEGACY_ARROW_RE.finditer(line):
            source = m.group(1).strip().lower()
            target = m.group(2).strip().lower()
            expected = legacy_map.get(source)
            if expected is None:
                if target in plan_ids or target in legacy_map:
                    results.append(
                        CheckResult(
                            CheckId.LEGACY_PLAN_MAP,
                            False,
                            "declared legacy names: " + ", ".join(sorted(legacy_map)),
                            f"{source} -> {target}",
                            f"line {lineno} maps undocumented legacy name '{source}'",
                        )
                    )
                continue
            if target != expected:
                results.append(
                    CheckResult(
                        CheckId.LEGACY_PLAN_MAP,
                        False,
                        f"{source} -> {expected}",
                        f"{source} -> {target}",
                        f"line {lineno} legacy plan map mismatch: expected "
                        f"'{source}' to map to '{expected}', found '{target}'",
                    )
                )
    return results


_APPROVAL_LANGUAGE_RE = re.compile(
    r"\b(?:approved|approval|qualified|enabled|available|cleared|in production)\b",
    re.IGNORECASE,
)
# A requirement or negation in front of the approval word means the document is
# describing what is *not* approved yet, which is the correct current state.
_APPROVAL_NEGATION_RE = re.compile(
    r"\b(?:not|never|no|without|pending|until|requires?|required|must|cannot|"
    r"unapproved|fails?|disabled|before)\b",
    re.IGNORECASE,
)
_ROUTE_LABEL_RE = re.compile(
    r"(?:route|service)[ _-]?id\s*[:=]\s*`?([A-Za-z0-9][A-Za-z0-9._-]{1,60})`?",
    re.IGNORECASE,
)
_DEFAULT_ROUTE_RE = re.compile(
    r"default[ _-]?route(?:[ _-]?id)?\s*[:=]\s*`?([A-Za-z0-9][A-Za-z0-9._-]{1,60})`?",
    re.IGNORECASE,
)


def _check_inference_policy(doc_content: str, policy: dict[str, Any]) -> list[CheckResult]:
    """Documented inference-policy claims vs. config/inference_policy.json.

    Deliberately narrow. It does NOT re-verify privacy qualification, operator
    review, or budget ceilings at runtime; it only rejects documentation that
    names a route/service policy does not declare, or that calls an unapproved
    route approved, or that documents a default route when policy sets none.
    """
    route_ids = {str(k).lower(): v for k, v in policy.get("route_ids", {}).items()}
    service_ids = {str(k).lower(): v for k, v in policy.get("service_ids", {}).items()}
    if not route_ids and not service_ids:
        return []

    declared = {**route_ids, **service_ids}
    results: list[CheckResult] = []

    def _check_identifier(value: str, context: str) -> None:
        ident = value.strip().lower()
        if not ident or ident in declared:
            return
        results.append(
            CheckResult(
                CheckId.INFERENCE_POLICY,
                False,
                f"declared ids: {', '.join(sorted(declared))}",
                ident,
                f"{context} names inference policy id '{value}' which "
                f"config/inference_policy.json does not declare",
            )
        )

    for lineno, line in enumerate(doc_content.splitlines(), start=1):
        if line.lstrip().startswith("|"):
            continue
        for m in _ROUTE_LABEL_RE.finditer(line):
            _check_identifier(m.group(1), f"line {lineno}")

        m_default = _DEFAULT_ROUTE_RE.search(line)
        if m_default:
            ident = m_default.group(1).strip().lower()
            default_id = policy.get("default_route_id")
            if not default_id or ident != str(default_id).lower():
                results.append(
                    CheckResult(
                        CheckId.INFERENCE_POLICY,
                        False,
                        f"default route: {default_id or '(none declared)'}",
                        ident,
                        f"line {lineno} documents default route '{ident}' but "
                        f"config/inference_policy.json declares "
                        f"{default_id or 'no default route'}",
                    )
                )

        approval = _APPROVAL_LANGUAGE_RE.search(line)
        if not approval:
            continue
        # "is not approved", "requires approval", "pending review" describe the
        # unapproved steady state and must not be read as an approval claim.
        if _APPROVAL_NEGATION_RE.search(line[: approval.start()]):
            continue
        lowered = line.lower()
        for ident, approved in declared.items():
            if ident in lowered and not approved:
                results.append(
                    CheckResult(
                        CheckId.INFERENCE_POLICY,
                        False,
                        f"{ident}: approved",
                        f"{ident} described as approved/available",
                        f"line {lineno} treats '{ident}' as approved but "
                        f"config/inference_policy.json has approved=false",
                    )
                )

    required_flags = policy.get("required_flags", [])
    for fence in _json_fences(doc_content):
        parsed = _try_json_object(fence["body"])
        if parsed is None:
            continue
        provider = parsed.get("provider")
        if not isinstance(provider, dict):
            continue
        for flag, expected_value in required_flags:
            if flag not in provider:
                results.append(
                    CheckResult(
                        CheckId.INFERENCE_POLICY,
                        False,
                        f"provider.{flag} required by policy",
                        "(absent)",
                        f"documented request at line {fence['line']} omits "
                        f"provider.{flag}, which config/inference_policy.json requires",
                    )
                )
                continue
            if expected_value is not None and provider.get(flag) != expected_value:
                results.append(
                    CheckResult(
                        CheckId.INFERENCE_POLICY,
                        False,
                        f"provider.{flag}={expected_value!r}",
                        repr(provider.get(flag)),
                        f"documented request at line {fence['line']} sets "
                        f"provider.{flag}={provider.get(flag)!r}, policy requires "
                        f"{expected_value!r}",
                    )
                )
        max_price = provider.get("max_price")
        if any(flag == "max_price" for flag, _ in required_flags) and isinstance(max_price, dict):
            missing_keys = [k for k in _REQUIRED_MAX_PRICE_KEYS if k not in max_price]
            if missing_keys:
                results.append(
                    CheckResult(
                        CheckId.INFERENCE_POLICY,
                        False,
                        "provider.max_price: " + ", ".join(_REQUIRED_MAX_PRICE_KEYS),
                        "missing: " + ", ".join(missing_keys),
                        f"documented request at line {fence['line']} omits "
                        f"provider.max_price.{', provider.max_price.'.join(missing_keys)}",
                    )
                )
    return results


def _json_fences(doc_content: str) -> list[dict[str, Any]]:
    """Return fenced ```json blocks with their starting line numbers."""
    fences: list[dict[str, Any]] = []
    lines = doc_content.splitlines()
    in_fence = False
    fence_lang = ""
    start_line = 0
    body: list[str] = []
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence = True
                fence_lang = stripped[3:].strip().lower()
                start_line = lineno
                body = []
            else:
                in_fence = False
                if fence_lang in ("json", "jsonc", ""):
                    fences.append({"line": start_line, "body": "\n".join(body)})
            continue
        if in_fence:
            body.append(line)
    return fences


def _try_json_object(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _check_auto_routing(doc_content: str, auto_facts: dict[str, str]) -> list[CheckResult]:
    results = []
    for label, key in [
        ("auto_fast_model", "auto_fast_model"),
        ("auto_reasoning_model", "auto_reasoning_model"),
    ]:
        expected = auto_facts.get(key, "")
        if not expected:
            continue
        m = re.search(
            rf"(?:\*\*{label}\*\*|{label})`?[:=\s]+`?([a-z0-9/._-]+)`?(?:\s|$)",
            doc_content,
            re.IGNORECASE,
        )
        if not m:
            continue
        observed = m.group(1).strip()
        if _normalize_model_name(observed) != _normalize_model_name(expected):
            check_id = (
                CheckId.AUTO_FAST_MODEL
                if key == "auto_fast_model"
                else CheckId.AUTO_REASONING_MODEL
            )
            results.append(
                CheckResult(
                    check_id,
                    False,
                    expected,
                    observed,
                    f"{label} mismatch: expected {expected}, found {observed}",
                )
            )
    return results


_DOCKER_SERVICE_COUNT_RE = re.compile(
    r"^#{0,3}\s*Docker Compose(?: Services)? \((\d+) services?\)", re.IGNORECASE
)


def _check_docker_service_count(doc_content: str, expected: int) -> CheckResult:
    for line in doc_content.splitlines():
        m = _DOCKER_SERVICE_COUNT_RE.search(line)
        if m:
            observed = int(m.group(1))
            if observed != expected:
                return CheckResult(
                    CheckId.DOCKER_SERVICE_COUNT,
                    False,
                    str(expected),
                    str(observed),
                    f"docker service count mismatch: expected {expected}, found {observed}",
                )
            return CheckResult(CheckId.DOCKER_SERVICE_COUNT, True)
    return CheckResult(CheckId.DOCKER_SERVICE_COUNT, True)


_SUBAGENT_TABLE_RE = re.compile(
    r"^\|\s*`?@(\w+)`?\s*\|\s*(?:\*\*)?(Implemented|Reserved|Not implemented)(?:\*\*)?\s*\|\s*([^|]+?)\s*\|"
)


def _check_subagent_table(
    doc_content: str, subagent_facts: dict[str, dict[str, str]]
) -> list[CheckResult]:
    results = []
    for line in doc_content.splitlines():
        m = _SUBAGENT_TABLE_RE.match(line.strip())
        if not m:
            continue
        name = m.group(1).lower()
        doc_status = m.group(2).strip()
        impl_desc = m.group(3).strip()
        if name not in subagent_facts:
            continue
        facts = subagent_facts[name]
        expected_status = facts["status"]
        doc_status_lower = doc_status.lower()
        if expected_status == "implemented":
            if doc_status_lower in ("reserved", "not implemented"):
                results.append(
                    CheckResult(
                        CheckId.SUBAGENT_STATUS,
                        False,
                        "Implemented",
                        doc_status,
                        f"@{name} is implemented but table says '{doc_status}'",
                    )
                )
            else:
                known_note = facts.get("note", "")
                if known_note:
                    impl_lower = impl_desc.lower()
                    note_tokens = re.split(r"[^a-z0-9]+", known_note.lower())
                    key_terms = [
                        w for w in note_tokens if len(w) > 3 and w not in ("none", "code", "python")
                    ]
                    if key_terms and not any(term in impl_lower for term in key_terms):
                        results.append(
                            CheckResult(
                                CheckId.SUBAGENT_STATUS,
                                False,
                                known_note,
                                impl_desc,
                                f"@{name} implementation mismatch: expected '{known_note}', got '{impl_desc}'",
                            )
                        )
        elif expected_status == "reserved":
            if doc_status_lower == "implemented":
                results.append(
                    CheckResult(
                        CheckId.SUBAGENT_STATUS,
                        False,
                        "Reserved",
                        doc_status,
                        f"@{name} is reserved but table says '{doc_status}'",
                    )
                )
        elif expected_status == "not_implemented":
            if doc_status_lower == "implemented":
                results.append(
                    CheckResult(
                        CheckId.SUBAGENT_STATUS,
                        False,
                        "Not implemented",
                        doc_status,
                        f"@{name} is not implemented but table says '{doc_status}'",
                    )
                )
    return results


def _find_line_with_fact(lines: list[str], pattern: str) -> int:
    cre = re.compile(pattern, re.IGNORECASE)
    for i, line in enumerate(lines, start=1):
        if cre.search(line):
            return i
    return 1


def _match_exception(
    exceptions: list[ExceptionEntry], check_id: str, doc_path: str
) -> ExceptionEntry | None:
    for exc in exceptions:
        if exc.check_id == check_id and exc.doc_path == doc_path:
            return exc
    return None


def _collect_finding_list(
    results: list[CheckResult],
    exceptions: list[ExceptionEntry],
    doc_path: Path,
    lines: list[str],
    today: date,
    line_pat: str,
) -> list[Finding]:
    """Turn failed CheckResults into Findings, honouring active exceptions."""
    collected: list[Finding] = []
    for res in results:
        if res.passed:
            continue
        exc = _match_exception(exceptions, res.check_id, str(doc_path))
        if exc and exc.expires >= today:
            exc.suppressed_finding = True
            continue
        collected.append(
            Finding(
                str(doc_path),
                _find_line_with_fact(lines, line_pat),
                res.check_id,
                "mismatch",
                res.expected,
                res.observed,
                res.message or f"{res.check_id} mismatch",
            )
        )
    return collected


def check_document(
    doc_path: Path,
    facts: dict[str, Any],
    exceptions: list[ExceptionEntry],
    today: date,
) -> tuple[list[Finding], list[ExceptionEntry]]:
    text = doc_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    findings: list[Finding] = []

    mfact = facts["migrations"]
    efact = facts["embeddings"]

    res = _check_migration_count(text, mfact["count"])
    if not res.passed:
        exc = _match_exception(exceptions, CheckId.MIGRATION_COUNT, str(doc_path))
        if exc and exc.expires >= today:
            exc.suppressed_finding = True
        else:
            findings.append(
                Finding(
                    str(doc_path),
                    _find_line_with_fact(lines, r"\dmigration"),
                    CheckId.MIGRATION_COUNT,
                    "mismatch",
                    res.expected,
                    res.observed,
                    res.message or "migration count mismatch",
                )
            )

    res = _check_migration_latest(text, mfact["latest"])
    if not res.passed:
        exc = _match_exception(exceptions, CheckId.MIGRATION_LATEST, str(doc_path))
        if exc and exc.expires >= today:
            exc.suppressed_finding = True
        else:
            findings.append(
                Finding(
                    str(doc_path),
                    _find_line_with_fact(lines, r"0\d\d_\w+\.sql"),
                    CheckId.MIGRATION_LATEST,
                    "mismatch",
                    res.expected,
                    res.observed,
                    res.message or "migration latest mismatch",
                )
            )

    res = _check_embedding_doc_model(text, efact["document_model"])
    if not res.passed:
        exc = _match_exception(exceptions, CheckId.EMBEDDING_DOC_MODEL, str(doc_path))
        if exc and exc.expires >= today:
            exc.suppressed_finding = True
        else:
            findings.append(
                Finding(
                    str(doc_path),
                    _find_line_with_fact(lines, r"EMBEDDING_DOCUMENT_MODEL"),
                    CheckId.EMBEDDING_DOC_MODEL,
                    "mismatch",
                    res.expected,
                    res.observed,
                    res.message or "embedding doc model mismatch",
                )
            )

    res = _check_embedding_query_model(text, efact["query_model"])
    if not res.passed:
        exc = _match_exception(exceptions, CheckId.EMBEDDING_QUERY_MODEL, str(doc_path))
        if exc and exc.expires >= today:
            exc.suppressed_finding = True
        else:
            findings.append(
                Finding(
                    str(doc_path),
                    _find_line_with_fact(lines, r"EMBEDDING_QUERY_MODEL"),
                    CheckId.EMBEDDING_QUERY_MODEL,
                    "mismatch",
                    res.expected,
                    res.observed,
                    res.message or "embedding query model mismatch",
                )
            )

    res = _check_embedding_dimensions(text, efact["dimensions"])
    if not res.passed:
        exc = _match_exception(exceptions, CheckId.EMBEDDING_DIMENSIONS, str(doc_path))
        if exc and exc.expires >= today:
            exc.suppressed_finding = True
        else:
            findings.append(
                Finding(
                    str(doc_path),
                    _find_line_with_fact(lines, r"EMBEDDING_DIMENSIONS"),
                    CheckId.EMBEDDING_DIMENSIONS,
                    "mismatch",
                    res.expected,
                    res.observed,
                    res.message or "embedding dimensions mismatch",
                )
            )

    if doc_path.name in ("PROJECT_CONTEXT.md", "MEMORY_LAYER.md"):
        for res in _check_embedding_prose(text, efact):
            if not res.passed:
                exc = _match_exception(exceptions, res.check_id, str(doc_path))
                if exc and exc.expires >= today:
                    exc.suppressed_finding = True
                else:
                    findings.append(
                        Finding(
                            str(doc_path),
                            _find_line_with_fact(lines, f"`{res.observed}`"),
                            res.check_id,
                            "mismatch",
                            res.expected,
                            res.observed,
                            res.message or "embedding prose mismatch",
                        )
                    )
        for res in _check_memory_layer_table(text, efact):
            if not res.passed:
                exc = _match_exception(exceptions, res.check_id, str(doc_path))
                if exc and exc.expires >= today:
                    exc.suppressed_finding = True
                else:
                    findings.append(
                        Finding(
                            str(doc_path),
                            _find_line_with_fact(lines, f"`{res.observed}`"),
                            res.check_id,
                            "mismatch",
                            res.expected,
                            res.observed,
                            res.message or "memory layer embedding mismatch",
                        )
                    )

    dedup_checks = [
        (CheckId.DEDUP_MERGE, efact["dedup_merge"], r"(?<!-)\bmerge\b"),
        (CheckId.DEDUP_SUPERSEDE_GENERIC, efact["dedup_supersede_generic"], r"(?<!-)\bgeneric\b"),
        (
            CheckId.DEDUP_SUPERSEDE_SAME_SLOT,
            efact["dedup_supersede_same_slot"],
            r"(?<!-)\bsame.?slot\b",
        ),
    ]
    for check_id, expected_val, label_pat in dedup_checks:
        if expected_val is None:
            continue
        res = _check_dedup_threshold(text, check_id.value, expected_val, label_pat)
        if not res.passed:
            exc = _match_exception(exceptions, check_id.value, str(doc_path))
            if exc and exc.expires >= today:
                exc.suppressed_finding = True
            else:
                findings.append(
                    Finding(
                        str(doc_path),
                        _find_line_with_fact(lines, label_pat),
                        check_id.value,
                        "mismatch",
                        res.expected,
                        res.observed,
                        res.message or f"dedup threshold mismatch for {check_id.value}",
                    )
                )

    res = _check_video_providers(text, frozenset(facts["providers"]["video_providers"]))
    if not res.passed:
        exc = _match_exception(exceptions, CheckId.VIDEO_PROVIDERS, str(doc_path))
        if exc and exc.expires >= today:
            exc.suppressed_finding = True
        else:
            findings.append(
                Finding(
                    str(doc_path),
                    _find_line_with_fact(lines, r"provider"),
                    CheckId.VIDEO_PROVIDERS,
                    "mismatch",
                    res.expected,
                    res.observed,
                    res.message or "video provider mismatch",
                )
            )

    commercial = facts.get("commercial", {})
    if commercial:
        findings.extend(
            _collect_finding_list(
                _check_commercial_plans(text, commercial),
                exceptions,
                doc_path,
                lines,
                today,
                r"\bplans?\b|\bcommercial model\b",
            )
        )
        findings.extend(
            _collect_finding_list(
                _check_commercial_capabilities(text, commercial),
                exceptions,
                doc_path,
                lines,
                today,
                r"[`a-z0-9_]*capabilit",
            )
        )
        findings.extend(
            _collect_finding_list(
                _check_commercial_prices(text, commercial),
                exceptions,
                doc_path,
                lines,
                today,
                r"[$A-Z]{1,3}\d",
            )
        )
        findings.extend(
            _collect_finding_list(
                _check_legacy_plan_map(text, commercial),
                exceptions,
                doc_path,
                lines,
                today,
                r"[Ll]egacy|map",
            )
        )

    inference_policy = facts.get("inference_policy", {})
    if inference_policy:
        findings.extend(
            _collect_finding_list(
                _check_inference_policy(text, inference_policy),
                exceptions,
                doc_path,
                lines,
                today,
                r"route|service|provider",
            )
        )

    route_facts = facts.get("routes", {}).get("routes", {})
    if route_facts:
        res = _check_routes(text, route_facts)
        if not res.passed:
            exc = _match_exception(exceptions, CheckId.ROUTE, str(doc_path))
            if exc and exc.expires >= today:
                exc.suppressed_finding = True
            else:
                findings.append(
                    Finding(
                        str(doc_path),
                        _find_line_with_fact(lines, r"`/"),
                        CheckId.ROUTE,
                        "mismatch",
                        res.expected,
                        res.observed,
                        res.message or "route mismatch",
                    )
                )

    env_facts = facts.get("env_vars", {}).get("env_vars", [])
    if env_facts and doc_path.name in (
        "TECHNICAL_SPECS.md",
        "PROJECT_CONTEXT.md",
        "MEMORY_LAYER.md",
    ):
        res = _check_env_vars(text, env_facts)
        if not res.passed:
            exc = _match_exception(exceptions, CheckId.ENV_VAR, str(doc_path))
            if exc and exc.expires >= today:
                exc.suppressed_finding = True
            else:
                findings.append(
                    Finding(
                        str(doc_path),
                        _find_line_with_fact(lines, r"[A-Z_][A-Z0-9_]*"),
                        CheckId.ENV_VAR,
                        "mismatch",
                        res.expected,
                        res.observed,
                        res.message or "env var mismatch",
                    )
                )

    if doc_path.name == "MEMORY_LAYER.md":
        mem_res = _check_memory_layer_env_block(text)
        if not mem_res.passed:
            exc = _match_exception(exceptions, CheckId.ENV_VAR, str(doc_path))
            if exc and exc.expires >= today:
                exc.suppressed_finding = True
            else:
                findings.append(
                    Finding(
                        str(doc_path),
                        1,
                        CheckId.ENV_VAR,
                        "missing",
                        mem_res.expected,
                        mem_res.observed,
                        mem_res.message or "memory layer env block check failed",
                    )
                )

    auto_facts = facts.get("auto_routing", {})
    if auto_facts and doc_path.name == "TECHNICAL_SPECS.md":
        auto_results = _check_auto_routing(text, auto_facts)
        for ares in auto_results:
            if not ares.passed:
                exc = _match_exception(exceptions, ares.check_id, str(doc_path))
                if exc and exc.expires >= today:
                    exc.suppressed_finding = True
                else:
                    findings.append(
                        Finding(
                            str(doc_path),
                            _find_line_with_fact(lines, r"auto_.*model"),
                            ares.check_id,
                            "mismatch",
                            ares.expected,
                            ares.observed,
                            ares.message or "auto routing mismatch",
                        )
                    )

    docker_count = facts.get("docker", {}).get("service_count", 0)
    if docker_count and doc_path.name in ("TECHNICAL_SPECS.md", "PROJECT_CONTEXT.md"):
        res = _check_docker_service_count(text, docker_count)
        if not res.passed:
            exc = _match_exception(exceptions, CheckId.DOCKER_SERVICE_COUNT, str(doc_path))
            if exc and exc.expires >= today:
                exc.suppressed_finding = True
            else:
                findings.append(
                    Finding(
                        str(doc_path),
                        _find_line_with_fact(lines, r"Docker Compose"),
                        CheckId.DOCKER_SERVICE_COUNT,
                        "mismatch",
                        res.expected,
                        res.observed,
                        res.message or "docker service count mismatch",
                    )
                )

    subagent_facts = facts.get("subagents", {})
    if subagent_facts and doc_path.name == "PROJECT_CONTEXT.md":
        sub_results = _check_subagent_table(text, subagent_facts)
        for sres in sub_results:
            if not sres.passed:
                exc = _match_exception(exceptions, CheckId.SUBAGENT_STATUS, str(doc_path))
                if exc and exc.expires >= today:
                    exc.suppressed_finding = True
                else:
                    findings.append(
                        Finding(
                            str(doc_path),
                            _find_line_with_fact(lines, r"@"),
                            sres.check_id,
                            "mismatch",
                            sres.expected,
                            sres.observed,
                            sres.message or "subagent status mismatch",
                        )
                    )

    all_active_exceptions = [exc for exc in exceptions if exc.expires >= today]
    return findings, all_active_exceptions


def format_text(
    findings: list[Finding], exceptions: list[ExceptionEntry], malformed: list[tuple[str, int, str]]
) -> str:
    lines_out: list[str] = []
    for f in findings:
        lines_out.append(
            f"{f.doc_path}:{f.line} [{f.check_id}] expected={f.expected!r} observed={f.observed!r}  {f.message}"
        )
    for e in exceptions:
        status = "SUPPRESSED" if e.suppressed_finding else "ACTIVE"
        lines_out.append(
            f"{e.doc_path}:{e.line} [EXCEPTION {e.check_id}] expires={e.expires} reason={e.reason!r} ({status})"
        )
    for doc, lineno, msg in malformed:
        lines_out.append(f"MALFORMED_EXCEPTION {doc}:{lineno}: {msg}")
    return "\n".join(lines_out)


def format_json(
    findings: list[Finding],
    exceptions: list[ExceptionEntry],
    malformed: list[tuple[str, int, str]],
    facts: dict[str, Any],
) -> str:
    report = {
        "checked_sources": {
            "migrations": facts["migrations"],
            "embeddings": facts["embeddings"],
            "providers": facts["providers"],
            "routes": facts["routes"],
            "commercial": facts.get("commercial", {}),
            "inference_policy": facts.get("inference_policy", {}),
        },
        "findings": [
            {
                "doc": f.doc_path,
                "line": f.line,
                "check_id": f.check_id,
                "kind": f.kind,
                "expected": f.expected,
                "observed": f.observed,
                "message": f.message,
            }
            for f in findings
        ],
        "exceptions": [
            {
                "doc": e.doc_path,
                "line": e.line,
                "check_id": e.check_id,
                "expires": e.expires.isoformat(),
                "reason": e.reason,
                "suppressed_finding": e.suppressed_finding,
            }
            for e in exceptions
        ],
        "malformed_exceptions": [{"doc": m[0], "line": m[1], "message": m[2]} for m in malformed],
        "summary": {
            "total_findings": len(findings),
            "total_exceptions": len(exceptions),
            "total_malformed": len(malformed),
        },
    }
    return json.dumps(report, indent=2)


SOURCES_OF_TRUTH = Path("docs/SOURCES_OF_TRUTH.md")


def get_gated_docs(root: Path) -> list[Path]:
    sot_path = root / SOURCES_OF_TRUTH
    if not sot_path.exists():
        return []

    text = sot_path.read_text(encoding="utf-8")
    gated: list[Path] = []

    in_table = False
    for line in text.splitlines():
        if "File" in line and "Tier" in line and "Classification" in line:
            in_table = True
            continue
        if in_table:
            if line.startswith("|") and not line.startswith("|--"):
                parts = [p.strip() for p in line.split("|")[1:-1]]
                if len(parts) >= 4:
                    file_col = parts[0].strip()
                    classification = parts[3].strip().lower()
                    if classification == "gated" and file_col:
                        file_path = root / file_col.strip().strip("`")
                        gated.append(file_path)
            elif line.startswith("##"):
                break

    return gated


def main() -> int:
    parser = argparse.ArgumentParser(description="Documentation freshness linter")
    parser.add_argument("--mode", choices=["report", "fail"], default="fail")
    parser.add_argument("--files", nargs="+", type=Path, default=None)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    root = repo_root()
    today = date.today()

    facts = extract_all_facts(root)

    if args.files:
        docs_to_check = [f.resolve() for f in args.files]
    else:
        docs_to_check = get_gated_docs(root)

    if not docs_to_check:
        print("No documents to check.", file=sys.stderr)
        return 0 if args.mode == "report" else 1

    all_findings: list[Finding] = []
    all_exceptions: list[ExceptionEntry] = []
    all_malformed: list[tuple[str, int, str]] = []

    for doc_path in docs_to_check:
        if not doc_path.exists():
            all_malformed.append((str(doc_path), 0, f"source file not found: {doc_path}"))
            continue

        text = doc_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        exceptions, malformed = parse_exceptions(lines, str(doc_path))
        all_malformed.extend((doc_path.name, lineno, msg) for lineno, msg in malformed)

        for exc in exceptions:
            if exc.expires < today:
                all_findings.append(
                    Finding(
                        doc_path=str(doc_path),
                        line=exc.line,
                        check_id=exc.check_id,
                        kind="expired_exception",
                        expected=f"expires >= {today.isoformat()}",
                        observed=f"expired on {exc.expires.isoformat()}",
                        message=f"expired DOC_FRESHNESS_EXCEPTION for '{exc.check_id}'",
                    )
                )

        findings, updated_exceptions = check_document(doc_path, facts, exceptions, today)
        all_findings.extend(findings)
        all_exceptions.extend(updated_exceptions)

    if args.format == "json":
        print(format_json(all_findings, all_exceptions, all_malformed, facts))
    else:
        if all_findings or all_malformed or all_exceptions:
            print(format_text(all_findings, all_exceptions, all_malformed))
        else:
            print("No drift detected.")

    if args.mode == "report":
        return 0
    if all_malformed:
        return 1
    unsuppressed = [f for f in all_findings if f.kind != "suppressed"]
    return 1 if unsuppressed else 0


if __name__ == "__main__":
    raise SystemExit(main())
