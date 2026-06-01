#!/usr/bin/env python3
"""
Documentation freshness linter for Daemon.

Checks gated documentation (T1) against T0 source-of-truth values extracted
at runtime. Supports high-confidence structured-fact checks only:
  - migration_count / migration_latest
  - embedding_document_model
  - dedup_thresholds (merge, supersede_generic, supersede_same_slot)
  - video_providers (source-derived from VALID_VIDEO_PROVIDERS)

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
_EMBEDDING_DIM_RE = re.compile(r'embedding_dimensions:\s*int\s*=\s*(\d+)')
_DEDUP_MERGE_RE = re.compile(r"dedup_merge_threshold:\s*float\s*=\s*Field\s*\(\s*default\s*=\s*([\d.]+)")
_DEDUP_SUPERSEDE_GENERIC_RE = re.compile(r"dedup_supersede_threshold:\s*float\s*=\s*Field\s*\(\s*default\s*=\s*([\d.]+)")
_DEDUP_SUPERSEDE_SAME_SLOT_RE = re.compile(r"dedup_supersede_same_slot_threshold:\s*float\s*=\s*Field\s*\(\s*default\s*=\s*([\d.]+)")

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
    r'(?:\*\*EMBEDDING_DIMENSIONS\*\*|EMBEDDING_DIMENSIONS|embedding_dimensions)[:=\s]+(\d+)',
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
        "dedup_supersede_same_slot": float(supersede_same_slot.group(1)) if supersede_same_slot else None,
    }


_VIDEO_PROVIDERS_RE = re.compile(r'VALID_VIDEO_PROVIDERS\s*=\s*\{([^}]+)\}')
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


# Tier model defaults extraction from config.py
_TIER_MODEL_RE = re.compile(
    r'tier_([a-z]+)_([a-z_]+)_model\s*:\s*str\s*=\s*"([^"]+)"'
)
_TIER_VIDEO_PROVIDER_RE = re.compile(
    r'tier_([a-z]+)_video_provider\s*:\s*str\s*=\s*"([^"]+)"'
)
_TIER_IMAGE_PROVIDER_RE = re.compile(
    r'tier_([a-z]+)_image_provider\s*:\s*str\s*=\s*"([^"]+)"'
)
_AUTO_FAST_MODEL_RE = re.compile(r'auto_fast_model\s*:\s*str\s*=\s*"([^"]+)"')
_AUTO_REASONING_MODEL_RE = re.compile(r'auto_reasoning_model\s*:\s*str\s*=\s*"([^"]+)"')


def get_tier_facts(root: Path) -> dict[str, Any]:
    config_path = root / "orchestrator" / "config.py"
    if not config_path.exists():
        return {}
    text = config_path.read_text(encoding="utf-8")
    tiers: dict[str, dict[str, str]] = {}
    for m in _TIER_MODEL_RE.finditer(text):
        tier_name = m.group(1)
        slot = m.group(2)
        model = m.group(3)
        if tier_name not in tiers:
            tiers[tier_name] = {}
        tiers[tier_name][slot] = model
    video_providers: dict[str, str] = {}
    for m in _TIER_VIDEO_PROVIDER_RE.finditer(text):
        tier_name = m.group(1)
        provider = m.group(2)
        video_providers[tier_name] = provider
    image_providers: dict[str, str] = {}
    for m in _TIER_IMAGE_PROVIDER_RE.finditer(text):
        tier_name = m.group(1)
        provider = m.group(2)
        image_providers[tier_name] = provider
    video_enabled: dict[str, bool] = {}
    current_tier: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("if tier_name ==") or stripped.startswith("elif tier_name =="):
            m = re.search(r'==\s*"(\w+)"', stripped)
            if m:
                current_tier = m.group(1)
        elif current_tier and "tier_video_enabled" in stripped and "=" in stripped:
            if "False" in stripped:
                video_enabled[current_tier] = False
            elif "True" in stripped:
                video_enabled[current_tier] = True
            current_tier = None
    return {"tiers": tiers, "video_providers": video_providers, "image_providers": image_providers, "video_enabled": video_enabled}


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


def get_docker_facts(root: Path) -> dict[str, Any]:
    compose_path = root / "docker-compose.yml"
    if not compose_path.exists():
        return {"service_count": 0}
    import yaml
    with compose_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    services = data.get("services", {}) if isinstance(data, dict) else {}
    return {"service_count": len(services)}


_ROUTE_DEF_RE = re.compile(r'(@app\.|router\.)(get|post|put|patch|delete|options)\s*\(\s*["\']([^"\']*)["\']')
_ROUTER_PREFIX_RE = re.compile(r'router\s*=\s*APIRouter\s*\(\s*prefix\s*=\s*["\']([^"\']+)["\']')


def _strip_trailing_slash(path: str) -> str:
    return path.rstrip('/')


def get_route_facts(root: Path) -> dict[str, Any]:
    main_path = root / "orchestrator" / "main.py"
    routes_dir = root / "orchestrator" / "routes"
    image_gen_router = root / "backend" / "image_gen" / "router.py"
    routes: dict[str, list[str]] = {}

    for path in [main_path] + sorted(routes_dir.glob("*.py")) + [image_gen_router]:
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


_ENV_VAR_RE = re.compile(r'`([A-Z_][A-Z0-9_]*)`')


def get_env_var_facts(root: Path) -> dict[str, list[str]]:
    env_vars: set[str] = set()
    env_var_pattern = re.compile(r'^([A-Z_][A-Z0-9_]*)=', re.MULTILINE)
    docker_list_pattern = re.compile(r'^\s*-\s+([A-Z_][A-Z0-9_]*)', re.MULTILINE)
    for file_path in [root / ".env.example", root / "docker-compose.yml"]:
        if file_path.exists():
            text = file_path.read_text(encoding="utf-8")
            for m in env_var_pattern.finditer(text):
                env_vars.add(m.group(1))
            for m in docker_list_pattern.finditer(text):
                env_vars.add(m.group(1))
    return {"env_vars": sorted(env_vars)}


_TIER_PRICE_RE = re.compile(r'#\s*Tier:\s*(FREE|STARTER|PRO|MAX|BYOK)\s*\(([^)]+)\)', re.IGNORECASE)


def get_tier_prices(root: Path) -> dict[str, Any]:
    sys.path.insert(0, str(root))
    from orchestrator.config import get_settings
    settings = get_settings()
    tiers = settings.list_available_tiers()
    prices: dict[str, str] = {}
    for tier in tiers:
        tier_id = str(tier["id"])
        price_val = int(tier["price"])
        prices[tier_id] = f"${price_val}/mo"
    return {"tier_prices": prices}


def extract_all_facts(root: Path) -> dict[str, Any]:
    return {
        "migrations": get_migration_facts(root),
        "embeddings": get_embedding_facts(root),
        "providers": get_provider_facts(root),
        "routes": get_route_facts(root),
        "env_vars": get_env_var_facts(root),
        "tier_defaults": get_tier_facts(root),
        "tier_prices": get_tier_prices(root),
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
    TIER_MODEL = "tier_model"
    TIER_VIDEO_PROVIDER = "tier_video_provider"
    TIER_IMAGE_PROVIDER = "tier_image_provider"
    TIER_PRICE = "tier_price"
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
    r"<!--\s*DOC_FRESHNESS_EXCEPTION:\s*" +
    r"([a-z_]+)\s+" +
    r"expires=(\d{4}-\d{2}-\d{2})\s+" +
    r'reason="([^"]*)"',
    re.IGNORECASE,
)
_EXCEPTION_LINE_RE = re.compile(r"<!--\s*DOC_FRESHNESS_EXCEPTION:", re.IGNORECASE)


def parse_exceptions(lines: list[str], doc_path: str) -> tuple[list[ExceptionEntry], list[tuple[int, str]]]:
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
                exceptions.append(ExceptionEntry(
                    check_id=check_id,
                    expires=expires,
                    reason=reason,
                    doc_path=doc_path,
                    line=lineno,
                ))
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


_TIER_PRICE_TABLE_RE = re.compile(
    r'^\|\s*\*\*([A-Za-z]+)\*\*\s*\|\s*(\$[\d]+/mo)\s*\|',
    re.IGNORECASE,
)


def _check_tier_prices(doc_content: str, source_prices: dict[str, str]) -> CheckResult:
    if not source_prices:
        return CheckResult(CheckId.TIER_PRICE, True)
    lines = doc_content.splitlines()
    doc_prices: dict[str, str] = {}
    for line in lines:
        m = _TIER_PRICE_TABLE_RE.match(line.strip())
        if m:
            tier = m.group(1).lower()
            price = m.group(2)
            doc_prices[tier] = price
    if not doc_prices:
        return CheckResult(CheckId.TIER_PRICE, True)
    mismatches = []
    for tier, src_price in source_prices.items():
        doc_price = doc_prices.get(tier)
        if doc_price is None:
            mismatches.append(f"{tier}: missing (source has {src_price})")
        elif doc_price != src_price:
            mismatches.append(f"{tier}: expected {src_price}, got {doc_price}")
    if mismatches:
        return CheckResult(
            CheckId.TIER_PRICE, False,
            str(source_prices),
            str(doc_prices),
            "; ".join(mismatches),
        )
    return CheckResult(CheckId.TIER_PRICE, True)


_ROUTE_TABLE_RE = re.compile(r'`(/[^`]+)`')
_KNOWN_SINGLE_SEGMENT_ROUTES = frozenset(["/chat", "/health", "/status", "/providers"])
_METHOD_LINE_RE = re.compile(r'\|\s*[A-Z][A-Z/]+\s*\|')
_METHOD_CELL_RE = re.compile(r'\|\s*([A-Z/]+)\s*\|', re.IGNORECASE)


def _is_route_table_row(line_text: str, route_start: int) -> bool:
    return bool(_METHOD_LINE_RE.search(line_text))


def _normalize_route(route: str) -> str:
    normalized = route.strip()
    normalized = re.sub(r'\{[^}]+\}', '{id}', normalized)
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
        if not route.startswith('/'):
            continue
        line_start = doc_content.rfind('\n', 0, m.start()) + 1
        line_end = doc_content.find('\n', m.start())
        if line_end == -1:
            line_end = len(doc_content)
        line_text = doc_content[line_start:line_end]
        line_key = hash(line_text)
        if line_key in processed_lines:
            continue
        processed_lines.add(line_key)
        if route.count('/') >= 2:
            methods = _extract_methods_from_row(line_text)
            all_routes_in_row = _ROUTE_TABLE_RE.findall(line_text)
            for r in all_routes_in_row:
                r_stripped = r.strip()
                if r_stripped.startswith('/'):
                    doc_route_methods.append((r_stripped, methods))
            continue
        if route in _KNOWN_SINGLE_SEGMENT_ROUTES:
            doc_route_methods.append((route, ""))
            continue
        line_start = doc_content.rfind('\n', 0, m.start()) + 1
        line_end = doc_content.find('\n', m.start())
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
            CheckId.ROUTE, False,
            f"all source routes: {len(all_source)} paths",
            f"stale: {', '.join(sorted(stale_paths))}",
            f"documented route(s) not found in source: {', '.join(sorted(stale_paths))}",
        )

    method_failures: list[str] = []
    for route, doc_methods in doc_route_methods:
        if '{' in route and re.search(r'\{[a-z_]+\}', route):
            continue
        if not doc_methods:
            continue
        norm = _normalize_route(route)
        src_methods = source_methods_by_path.get(norm, set())
        if not src_methods:
            continue
        for dm in doc_methods.split('/'):
            dm = dm.strip().upper()
            if dm not in src_methods:
                method_failures.append(f"{route}: method {dm} not in source ({', '.join(sorted(src_methods))})")

    if method_failures:
        return CheckResult(
            CheckId.ROUTE, False,
            "source methods",
            "; ".join(method_failures[:3]),
            f"route method mismatch: {method_failures[0]}",
        )

    return CheckResult(CheckId.ROUTE, True)


def _extract_methods_from_row(line_text: str) -> str:
    methods_match = _METHOD_CELL_RE.search(line_text)
    return methods_match.group(1).strip() if methods_match else ""


_ENV_DOC_RE = re.compile(r'`([A-Z_][A-Z0-9_]*)`')


def _check_env_vars(doc_content: str, source_vars: list[str]) -> CheckResult:
    if not source_vars:
        return CheckResult(CheckId.ENV_VAR, True)
    source_set = set(source_vars)
    doc_vars = [m.group(1) for m in _ENV_DOC_RE.finditer(doc_content) if m.group(1) not in (
        "EMBEDDING_DOCUMENT_MODEL", "EMBEDDING_QUERY_MODEL", "EMBEDDING_DIMENSIONS"
    )]
    if not doc_vars:
        return CheckResult(CheckId.ENV_VAR, True)
    stale: list[str] = [v for v in doc_vars if v not in source_set]
    if stale:
        return CheckResult(
            CheckId.ENV_VAR, False,
            f"source env vars: {', '.join(sorted(source_set))}",
            f"documented stale: {', '.join(sorted(stale))}",
            f"env var(s) not found in source: {', '.join(sorted(stale))}",
        )
    return CheckResult(CheckId.ENV_VAR, True)


_MEMORY_LAYER_REQUIRED_VARS = frozenset([
    "VOYAGE_API_KEY",
    "EMBEDDING_DOCUMENT_MODEL",
    "EMBEDDING_QUERY_MODEL",
    "EMBEDDING_DIMENSIONS",
    "DEDUP_MERGE_THRESHOLD",
    "DEDUP_SUPERSEDE_THRESHOLD",
    "DEDUP_SUPERSEDE_SAME_SLOT_THRESHOLD",
])


def _check_memory_layer_env_block(doc_content: str) -> CheckResult:
    in_block = False
    found_vars: set[str] = set()
    env_assign = re.compile(r'^([A-Z_][A-Z0-9_]*)=')
    for line in doc_content.splitlines():
        stripped = line.strip()
        unescaped = stripped[1:] if stripped.startswith('\\') else stripped
        if unescaped.startswith("```bash") and "## Environment" in doc_content[:doc_content.find(line)]:
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
            CheckId.ENV_VAR, False,
            f"required memory vars: {', '.join(sorted(_MEMORY_LAYER_REQUIRED_VARS))}",
            f"missing: {', '.join(sorted(missing))}",
            f"memory layer env block missing: {', '.join(sorted(missing))}",
        )
    return CheckResult(CheckId.ENV_VAR, True)


def _check_migration_count(doc_content: str, expected: int) -> CheckResult:
    matches = re.findall(r'\b(\d{2,3})\s+migration', doc_content, re.IGNORECASE)
    if not matches:
        return CheckResult(CheckId.MIGRATION_COUNT, True)
    for m in matches:
        observed = int(m)
        if observed != expected:
            return CheckResult(CheckId.MIGRATION_COUNT, False, str(expected), str(observed),
                            f"migration count mismatch: expected {expected}, found {observed}")
    return CheckResult(CheckId.MIGRATION_COUNT, True)


def _check_migration_latest(doc_content: str, expected: str) -> CheckResult:
    latest_claim_pattern = re.compile(
        r'(?:latest(?:\s+(?:migration|db))?|most\s+recent|newest)[:\s]+`?(\d{2,3}_\w+(?:\.sql)?)`?',
        re.IGNORECASE,
    )
    matches = latest_claim_pattern.findall(doc_content)
    if not matches:
        return CheckResult(CheckId.MIGRATION_LATEST, True)
    for observed in matches:
        normalized = observed if observed.endswith(".sql") else observed + ".sql"
        if normalized != expected and observed != expected:
            return CheckResult(CheckId.MIGRATION_LATEST, False, expected, observed,
                              f"latest migration mismatch: expected {expected}, found {observed}")
    return CheckResult(CheckId.MIGRATION_LATEST, True)


def _check_embedding_doc_model(doc_content: str, expected: str) -> CheckResult:
    m = _EMBEDDING_DOC_MODEL_CLAIM_RE.search(doc_content)
    if not m:
        return CheckResult(CheckId.EMBEDDING_DOC_MODEL, True)
    observed = m.group(1).strip()
    if _normalize_model_name(observed) != _normalize_model_name(expected):
        return CheckResult(CheckId.EMBEDDING_DOC_MODEL, False, expected, observed,
                          f"embedding doc model mismatch: expected {expected}, found {observed}")
    return CheckResult(CheckId.EMBEDDING_DOC_MODEL, True)


def _check_embedding_query_model(doc_content: str, expected: str) -> CheckResult:
    m = _EMBEDDING_QUERY_MODEL_CLAIM_RE.search(doc_content)
    if not m:
        return CheckResult(CheckId.EMBEDDING_QUERY_MODEL, True)
    observed = m.group(1).strip()
    if _normalize_model_name(observed) != _normalize_model_name(expected):
        return CheckResult(CheckId.EMBEDDING_QUERY_MODEL, False, expected, observed,
                          f"embedding query model mismatch: expected {expected}, found {observed}")
    return CheckResult(CheckId.EMBEDDING_QUERY_MODEL, True)


def _check_embedding_dimensions(doc_content: str, expected: int) -> CheckResult:
    m = _EMBEDDING_DIMENSIONS_CLAIM_RE.search(doc_content)
    if not m:
        return CheckResult(CheckId.EMBEDDING_DIMENSIONS, True)
    observed = int(m.group(1))
    if observed != expected:
        return CheckResult(CheckId.EMBEDDING_DIMENSIONS, False, str(expected), str(observed),
                          f"embedding dimensions mismatch: expected {expected}, found {observed}")
    return CheckResult(CheckId.EMBEDDING_DIMENSIONS, True)


_EMBEDDING_PROSE_DOC_RE = re.compile(
    r'`(voyage-[^`]+)`\s*\((\d+)d\)[^,]*\bfor documents',
    re.IGNORECASE,
)
_EMBEDDING_PROSE_QUERY_RE = re.compile(
    r'`(voyage-[^`]+)`\s*\((\d+)d\)[^,]*\bfor queries',
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
            results.append(CheckResult(
                CheckId.EMBEDDING_DOC_MODEL, False,
                doc_model, doc_model_obs,
                f"embedding doc model mismatch in structured prose: expected {doc_model}",
            ))
        if doc_dims_obs != dims:
            results.append(CheckResult(
                CheckId.EMBEDDING_DIMENSIONS, False,
                str(dims), str(doc_dims_obs),
                f"embedding doc dims mismatch in structured prose: expected {dims}, found {doc_dims_obs}",
            ))

    query_m = _EMBEDDING_PROSE_QUERY_RE.search(doc_content)
    if query_m and query_model:
        query_model_obs = query_m.group(1)
        query_dims_obs = int(query_m.group(2))
        query_model_obs_norm = _normalize_model_name(query_model_obs)
        if query_model_obs_norm != query_norm:
            results.append(CheckResult(
                CheckId.EMBEDDING_QUERY_MODEL, False,
                query_model, query_model_obs,
                f"embedding query model mismatch in structured prose: expected {query_model}",
            ))
        if query_dims_obs != dims:
            results.append(CheckResult(
                CheckId.EMBEDDING_DIMENSIONS, False,
                str(dims), str(query_dims_obs),
                f"embedding query dims mismatch in structured prose: expected {dims}, found {query_dims_obs}",
            ))

    return results


_MEMORY_EMBEDDING_TABLE_RE = re.compile(
    r'^\|\s*([^|]+?)\s*\|\s*`([^`]+)`\s*\|\s*[^|]+\s*\|\s*(\d+)\s*\|',
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
            results.append(CheckResult(
                CheckId.EMBEDDING_DOC_MODEL, False,
                doc_model, model_raw,
                f"memory layer doc model mismatch: expected {doc_model}",
            ))
        if "document" in purpose.lower() and dims and dim_obs and dim_obs != dims:
            results.append(CheckResult(
                CheckId.EMBEDDING_DIMENSIONS, False,
                str(dims), str(dim_obs),
                f"memory layer doc dims mismatch: expected {dims}, found {dim_obs}",
            ))
        if "query" in purpose.lower() and query_model and model_norm != query_norm:
            results.append(CheckResult(
                CheckId.EMBEDDING_QUERY_MODEL, False,
                query_model, model_raw,
                f"memory layer query model mismatch: expected {query_model}",
            ))
        if "query" in purpose.lower() and dims and dim_obs and dim_obs != dims:
            results.append(CheckResult(
                CheckId.EMBEDDING_DIMENSIONS, False,
                str(dims), str(dim_obs),
                f"memory layer query dims mismatch: expected {dims}, found {dim_obs}",
            ))
    return results


def _float_normalize(val: float) -> str:
    return f"{val:.2f}"


def _check_dedup_threshold(doc_content: str, threshold_name: str, expected: float, label_pattern: str) -> CheckResult:
    label_re = re.compile(label_pattern, re.IGNORECASE)
    expected_norm = _float_normalize(expected)
    lines = doc_content.splitlines()

    for i, line in enumerate(lines):
        if not label_re.search(line):
            continue
        line_vals = re.findall(r'\b0\.\d{1,}\b', line)
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
                return CheckResult(threshold_name, False, expected_norm, wrong_val,
                                  f"dedup threshold mismatch for {threshold_name}: expected {expected_norm} near '{label_pattern}', found {wrong_val}")
            continue
        window_start = max(0, i - 2)
        window_end = min(len(lines), i + 3)
        window_text = "\n".join(lines[window_start:window_end])
        all_vals = re.findall(r'\b0\.\d{1,}\b', window_text)
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
            return CheckResult(threshold_name, False, expected_norm, wrong_val,
                              f"dedup threshold mismatch for {threshold_name}: expected {expected_norm} near '{label_pattern}', found {wrong_val}")
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
        (r'\bprovider:\s*([a-z]+)',),
        (r'\bvideo\s+provider:\s*([a-z]+)',),
    ]
    # Plural/list forms: capture raw comma-separated names, brace-enclosed set, or markdown backtick lists
    plural_patterns = [
        (r'\bproviders:\s*([a-z, ]+)',),
        (r'\bvideo\s+providers:\s*([a-z, ]+)',),
        (r'\bVALID_VIDEO_PROVIDERS\s*=\s*\{([^}]+)\}',),
    ]
    plural_backtick_label_patterns = [
        r'\*\*Providers\*\*:',
        r'\*\*Video\s+Providers\*\*:',
    ]

    singular_claims: set[str] = set()
    plural_claims: set[str] = set()

    for pat, in singular_patterns:
        for m in re.finditer(pat, doc_content, re.IGNORECASE):
            if m.lastindex and m.lastindex >= 1:
                singular_claims.add(m.group(1).lower())

    for pat, in plural_patterns:
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
            line_end = doc_content.find('\n', m.end())
            if line_end == -1:
                line_end = len(doc_content)
            remainder = doc_content[m.end():line_end]
            names = re.findall(r'`([a-z]+)`', remainder, re.IGNORECASE)
            for n in names:
                if n.lower() not in singular_claims:
                    plural_claims.add(n.lower())

    # Singular claims: validate each is in the valid set
    for claim in singular_claims:
        if claim not in valid_providers:
            valid_list = ", ".join(sorted(valid_providers))
            return CheckResult(
                CheckId.VIDEO_PROVIDERS, False,
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
                CheckId.VIDEO_PROVIDERS, False,
                f"providers in {valid_list}",
                f"claimed {claimed_list} ({detail})",
                f"video provider set mismatch",
            )

    return CheckResult(CheckId.VIDEO_PROVIDERS, True)


# Tier table row regex: | **TIER** | model | model | model | model | model | model |
# Matches actual tier names (FREE, STARTER, PRO, MAX, BYOK) in any case variant
_TIER_TABLE_ROW_RE = re.compile(
    r'^\|\s*\*\*(FREE|STARTER|PRO|MAX|BYOK)\*\*\s*\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|',
    re.IGNORECASE,
)

# Map from tier name in docs to tier name in config
_TIER_NAME_MAP = {
    "free": "free",
    "starter": "starter",
    "pro": "pro",
    "max": "max",
    "byok": "byok",
}

# Slot names in the tier table (order must match the regex above)
_TIER_SLOTS = ["orchestrator", "research", "code", "image", "reader", "embeddings", "video"]


# PROJECT_CONTEXT tier table: mixed-case tier names and 4 data columns (price, orchestrator, subagents, video)
_TIER_TABLE_ROW_RE_PLAIN = re.compile(
    r'^\|\s*\*\*(Free|Starter|Pro|Max|BYOK)\*\*\s*\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|',
    re.IGNORECASE,
)

# Map for PROJECT_CONTEXT mixed-case tier names
_TIER_NAME_MAP_PLAIN = {
    "free": "free",
    "starter": "starter",
    "pro": "pro",
    "max": "max",
    "byok": "byok",
}


def _normalize_model_name(model: str) -> str:
    normalized = model.lower()
    while True:
        stripped = False
        for prefix in ["openrouter/", "openai/", "anthropic/", "google/", "x-ai/", "deepseek/", "moonshotai/"]:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
                stripped = True
                break
        if not stripped:
            break
    for suffix in ["-image", "-video", "-instruct", "-chat", "-preview"]:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)]
            break
    normalized = normalized.replace("/", " ").replace("-", " ").replace("_", " ")
    return normalized.strip()


def _check_tier_defaults(
    doc_content: str,
    tier_defaults: dict[str, dict[str, str]],
    tier_video_providers: dict[str, str] | None = None,
    tier_image_providers: dict[str, str] | None = None,
    tier_video_enabled: dict[str, bool] | None = None,
) -> list[CheckResult]:
    """
    Validate tier table model claims against config.py defaults.

    Two table formats are supported:
    - TECHNICAL_SPECS: 6-slot columns (orchestrator, research, image, reader, embeddings, video)
    - PROJECT_CONTEXT: 4-slot columns (orchestrator, subagents, video + embedded reader/embeddings)

    For each tier-slot-model claim found, the doc model name must exactly match
    the normalized config alias. Multi-option cells (e.g. "Claude 3.5 Sonnet / Opus 4.6")
    are split on "/" and each option is validated against its corresponding slot.

    Provider drift detection:
    - Video: docs say "Disabled"/"n/a"/"—" but config has tier_X_video_provider → FAIL
    - Image (only if doc has image column): docs say "_none_" or wrong provider but config has tier_X_image_provider → FAIL
    """
    results = []
    lines = doc_content.splitlines()

    tier_claims: dict[str, dict[str, str | None]] = {}

    for line in lines:
        line_stripped = line.strip()
        # Try TECHNICAL_SPECS 6-slot format first
        m = _TIER_TABLE_ROW_RE.match(line_stripped)
        if m:
            tier_doc_name = m.group(1)
            tier_config_name = _TIER_NAME_MAP.get(tier_doc_name.lower())
            if tier_config_name:
                raw = [g.strip() for g in m.groups()[1:]]
                if " / " in raw[1]:
                    parts = [x.strip() for x in raw[1].split(" / ")]
                    p1, p2 = parts[0], parts[1]
                    cells = [raw[0], p1, p2] + raw[2:]
                else:
                    cells = [raw[0], raw[1], None] + raw[2:]
                tier_claims[tier_config_name] = dict(zip(_TIER_SLOTS, cells))
            continue

        m2 = _TIER_TABLE_ROW_RE_PLAIN.match(line_stripped)
        if m2:
            tier_doc_name = m2.group(1)
            tier_config_name = _TIER_NAME_MAP_PLAIN.get(tier_doc_name.lower())
            if not tier_config_name:
                continue
            _, orchestrator, _subagents, video = [g.strip() for g in m2.groups()[1:]]
            tier_claims[tier_config_name] = {
                "orchestrator": orchestrator.strip("`"),
                "research": None,
                "code": None,
                "image": None,
                "reader": None,
                "embeddings": None,
                "video": video,
            }

    _PLACEHOLDER = {"", "—", "disabled", "n/a", "none"}
    has_meaningful_tier_table = any(
        any(
            v is not None and v.strip('*_`-').lower() not in _PLACEHOLDER
            for v in slots.values()
        )
        for slots in tier_claims.values()
    )
    if has_meaningful_tier_table:
        for tier_name in tier_defaults:
            if tier_name not in tier_claims:
                results.append(CheckResult(
                    CheckId.TIER_MODEL, False,
                    tier_name,
                    "(missing)",
                    f"tier {tier_name} row missing from document tier table",
                ))

    for tier_name, slots in tier_defaults.items():
        doc_slots = tier_claims.get(tier_name, {})
        for slot, config_model in slots.items():
            doc_model_raw = doc_slots.get(slot)
            research_alias = _normalize_model_name(slots.get("research") or "")
            code_alias = _normalize_model_name(slots.get("code") or "")

            if (doc_model_raw is None and slot == "code" and research_alias != code_alias
                    and config_model and doc_slots.get("research") is not None):
                results.append(CheckResult(
                    CheckId.TIER_MODEL, False,
                    config_model,
                    "",
                    f"tier {tier_name} {slot} mismatch: expected {config_model}",
                ))
                continue

            if doc_model_raw is None:
                continue

            doc_model = doc_model_raw.strip('*_`')
            if not doc_model or doc_model.lower() in ("_none_", "disabled", "n/a", "—"):
                doc_model = ""

            if not config_model:
                continue

            config_alias = _normalize_model_name(config_model)

            if doc_model:
                doc_options = [opt.strip() for opt in doc_model.split("/")]
                matched = (
                    any(_normalize_model_name(opt) == config_alias for opt in doc_options)
                    or (
                        len(doc_options) == 1
                        and research_alias != code_alias
                        and (
                            _normalize_model_name(doc_options[0]) in (research_alias, code_alias)
                            or (slot == "code" and (
                                _normalize_model_name(doc_options[0]) == code_alias
                                or code_alias.endswith(_normalize_model_name(doc_options[0]))
                            ))
                        )
                    )
                )
                if not matched:
                    results.append(CheckResult(
                        CheckId.TIER_MODEL, False,
                        config_model,
                        doc_model,
                        f"tier {tier_name} {slot} mismatch: expected {config_model}",
                    ))

    if tier_video_providers and tier_claims:
        for tier_name, config_provider in tier_video_providers.items():
            if tier_name not in tier_claims:
                continue
            doc_slots = tier_claims.get(tier_name, {})
            doc_raw = (doc_slots.get("video") or "").strip("` \t")
            doc_lower = doc_raw.lower()
            is_enabled = tier_video_enabled.get(tier_name) if tier_video_enabled else None
            if doc_lower in ("disabled", "n/a", "—") or not doc_lower:
                if is_enabled:
                    observed = doc_raw if doc_raw else "(empty)"
                    results.append(CheckResult(
                        CheckId.TIER_VIDEO_PROVIDER, False,
                        config_provider,
                        observed,
                        f"tier {tier_name} video: config has provider but docs say '{observed}'",
                    ))
                continue
            if is_enabled is False:
                results.append(CheckResult(
                    CheckId.TIER_VIDEO_PROVIDER, False,
                    config_provider,
                    doc_raw,
                    f"tier {tier_name} video: config disables video but docs say '{doc_raw}'",
                ))
                continue
            embedded_m = re.search(r'\(([^)]+)\)', doc_raw)
            provider_claimed = embedded_m.group(1).strip() if embedded_m else doc_raw
            if provider_claimed.lower() != config_provider.lower():
                results.append(CheckResult(
                    CheckId.TIER_VIDEO_PROVIDER, False,
                    config_provider,
                    provider_claimed,
                    f"tier {tier_name} video provider mismatch: expected {config_provider}",
                ))
                continue
            embedded_m = re.search(r'\(([^)]+)\)', doc_raw)
            provider_claimed = embedded_m.group(1).strip() if embedded_m else doc_raw
            if is_enabled is False:
                if "enabled" in doc_lower:
                    results.append(CheckResult(
                        CheckId.TIER_VIDEO_PROVIDER, False,
                        config_provider,
                        doc_raw,
                        f"tier {tier_name} video: config disables video but docs say '{doc_raw}'",
                    ))
                continue
            if provider_claimed.lower() != config_provider.lower():
                results.append(CheckResult(
                    CheckId.TIER_VIDEO_PROVIDER, False,
                    config_provider,
                    provider_claimed,
                    f"tier {tier_name} video provider mismatch: expected {config_provider}",
                ))

    if tier_image_providers and tier_claims:
        has_image_col = any(
            (doc_slots.get("image") or "").strip()
            for doc_slots in tier_claims.values()
        )
        if not has_image_col:
            return results
        for tier_name, config_provider in tier_image_providers.items():
            if tier_name not in tier_claims:
                continue
            doc_slots = tier_claims.get(tier_name, {})
            doc_raw = (doc_slots.get("image") or "").strip("` \t")
            doc_lower = doc_raw.lower()
            if doc_lower in ("_none_", "none", ""):
                observed = doc_raw if doc_raw else "(empty)"
                results.append(CheckResult(
                    CheckId.TIER_IMAGE_PROVIDER, False,
                    config_provider,
                    observed,
                    f"tier {tier_name} image: config has provider but docs say '{observed}'",
                ))
            elif " " in doc_raw or "/" in doc_raw:
                pass
            elif doc_lower != config_provider.lower():
                results.append(CheckResult(
                    CheckId.TIER_IMAGE_PROVIDER, False,
                    config_provider,
                    doc_raw,
                    f"tier {tier_name} image provider mismatch: expected {config_provider}",
                ))

    return results


def _check_auto_routing(doc_content: str, auto_facts: dict[str, str]) -> list[CheckResult]:
    results = []
    for label, key in [("auto_fast_model", "auto_fast_model"), ("auto_reasoning_model", "auto_reasoning_model")]:
        expected = auto_facts.get(key, "")
        if not expected:
            continue
        m = re.search(
            rf'(?:\*\*{label}\*\*|{label})`?[:=\s]+`?([a-z0-9/._-]+)`?(?:\s|$)',
            doc_content, re.IGNORECASE,
        )
        if not m:
            continue
        observed = m.group(1).strip()
        if _normalize_model_name(observed) != _normalize_model_name(expected):
            check_id = CheckId.AUTO_FAST_MODEL if key == "auto_fast_model" else CheckId.AUTO_REASONING_MODEL
            results.append(CheckResult(check_id, False, expected, observed,
                                      f"{label} mismatch: expected {expected}, found {observed}"))
    return results


_DOCKER_SERVICE_COUNT_RE = re.compile(r'^#{0,3}\s*Docker Compose(?: Services)? \((\d+) services?\)', re.IGNORECASE)


def _check_docker_service_count(doc_content: str, expected: int) -> CheckResult:
    for line in doc_content.splitlines():
        m = _DOCKER_SERVICE_COUNT_RE.search(line)
        if m:
            observed = int(m.group(1))
            if observed != expected:
                return CheckResult(CheckId.DOCKER_SERVICE_COUNT, False, str(expected), str(observed),
                                 f"docker service count mismatch: expected {expected}, found {observed}")
            return CheckResult(CheckId.DOCKER_SERVICE_COUNT, True)
    return CheckResult(CheckId.DOCKER_SERVICE_COUNT, True)


_SUBAGENT_TABLE_RE = re.compile(
    r'^\|\s*`?@(\w+)`?\s*\|\s*(?:\*\*)?(Implemented|Reserved|Not implemented)(?:\*\*)?\s*\|\s*([^|]+?)\s*\|'
)


def _check_subagent_table(doc_content: str, subagent_facts: dict[str, dict[str, str]]) -> list[CheckResult]:
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
                results.append(CheckResult(
                    CheckId.SUBAGENT_STATUS, False,
                    "Implemented",
                    doc_status,
                    f"@{name} is implemented but table says '{doc_status}'",
                ))
            else:
                known_note = facts.get("note", "")
                if known_note:
                    impl_lower = impl_desc.lower()
                    note_tokens = re.split(r'[^a-z0-9]+', known_note.lower())
                    key_terms = [w for w in note_tokens if len(w) > 3 and w not in ("none", "code", "python")]
                    if key_terms and not any(term in impl_lower for term in key_terms):
                        results.append(CheckResult(
                            CheckId.SUBAGENT_STATUS, False,
                            known_note,
                            impl_desc,
                            f"@{name} implementation mismatch: expected '{known_note}', got '{impl_desc}'",
                        ))
        elif expected_status == "reserved":
            if doc_status_lower == "implemented":
                results.append(CheckResult(
                    CheckId.SUBAGENT_STATUS, False,
                    "Reserved",
                    doc_status,
                    f"@{name} is reserved but table says '{doc_status}'",
                ))
    return results


def _find_line_with_fact(lines: list[str], pattern: str) -> int:
    cre = re.compile(pattern, re.IGNORECASE)
    for i, line in enumerate(lines, start=1):
        if cre.search(line):
            return i
    return 1


def _match_exception(exceptions: list[ExceptionEntry], check_id: str, doc_path: str) -> ExceptionEntry | None:
    for exc in exceptions:
        if exc.check_id == check_id and exc.doc_path == doc_path:
            return exc
    return None


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
            findings.append(Finding(str(doc_path), _find_line_with_fact(lines, r"\dmigration"),
                                   CheckId.MIGRATION_COUNT, "mismatch",
                                   res.expected, res.observed, res.message or "migration count mismatch"))

    res = _check_migration_latest(text, mfact["latest"])
    if not res.passed:
        exc = _match_exception(exceptions, CheckId.MIGRATION_LATEST, str(doc_path))
        if exc and exc.expires >= today:
            exc.suppressed_finding = True
        else:
            findings.append(Finding(str(doc_path), _find_line_with_fact(lines, r"0\d\d_\w+\.sql"),
                                   CheckId.MIGRATION_LATEST, "mismatch",
                                   res.expected, res.observed, res.message or "migration latest mismatch"))

    res = _check_embedding_doc_model(text, efact["document_model"])
    if not res.passed:
        exc = _match_exception(exceptions, CheckId.EMBEDDING_DOC_MODEL, str(doc_path))
        if exc and exc.expires >= today:
            exc.suppressed_finding = True
        else:
            findings.append(Finding(str(doc_path), _find_line_with_fact(lines, r"EMBEDDING_DOCUMENT_MODEL"),
                                   CheckId.EMBEDDING_DOC_MODEL, "mismatch",
                                   res.expected, res.observed, res.message or "embedding doc model mismatch"))

    res = _check_embedding_query_model(text, efact["query_model"])
    if not res.passed:
        exc = _match_exception(exceptions, CheckId.EMBEDDING_QUERY_MODEL, str(doc_path))
        if exc and exc.expires >= today:
            exc.suppressed_finding = True
        else:
            findings.append(Finding(str(doc_path), _find_line_with_fact(lines, r"EMBEDDING_QUERY_MODEL"),
                                   CheckId.EMBEDDING_QUERY_MODEL, "mismatch",
                                   res.expected, res.observed, res.message or "embedding query model mismatch"))

    res = _check_embedding_dimensions(text, efact["dimensions"])
    if not res.passed:
        exc = _match_exception(exceptions, CheckId.EMBEDDING_DIMENSIONS, str(doc_path))
        if exc and exc.expires >= today:
            exc.suppressed_finding = True
        else:
            findings.append(Finding(str(doc_path), _find_line_with_fact(lines, r"EMBEDDING_DIMENSIONS"),
                                   CheckId.EMBEDDING_DIMENSIONS, "mismatch",
                                   res.expected, res.observed, res.message or "embedding dimensions mismatch"))

    if doc_path.name in ("PROJECT_CONTEXT.md", "MEMORY_LAYER.md"):
        for res in _check_embedding_prose(text, efact):
            if not res.passed:
                exc = _match_exception(exceptions, res.check_id, str(doc_path))
                if exc and exc.expires >= today:
                    exc.suppressed_finding = True
                else:
                    findings.append(Finding(str(doc_path), _find_line_with_fact(lines, f"`{res.observed}`"),
                                           res.check_id, "mismatch",
                                           res.expected, res.observed, res.message or "embedding prose mismatch"))
        for res in _check_memory_layer_table(text, efact):
            if not res.passed:
                exc = _match_exception(exceptions, res.check_id, str(doc_path))
                if exc and exc.expires >= today:
                    exc.suppressed_finding = True
                else:
                    findings.append(Finding(str(doc_path), _find_line_with_fact(lines, f"`{res.observed}`"),
                                           res.check_id, "mismatch",
                                           res.expected, res.observed, res.message or "memory layer embedding mismatch"))

    dedup_checks = [
        (CheckId.DEDUP_MERGE, efact["dedup_merge"], r"(?<!-)\bmerge\b"),
        (CheckId.DEDUP_SUPERSEDE_GENERIC, efact["dedup_supersede_generic"], r"(?<!-)\bgeneric\b"),
        (CheckId.DEDUP_SUPERSEDE_SAME_SLOT, efact["dedup_supersede_same_slot"], r"(?<!-)\bsame.?slot\b"),
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
                findings.append(Finding(str(doc_path), _find_line_with_fact(lines, label_pat),
                                       check_id.value, "mismatch",
                                       res.expected, res.observed, res.message or f"dedup threshold mismatch for {check_id.value}"))

    res = _check_video_providers(text, frozenset(facts["providers"]["video_providers"]))
    if not res.passed:
        exc = _match_exception(exceptions, CheckId.VIDEO_PROVIDERS, str(doc_path))
        if exc and exc.expires >= today:
            exc.suppressed_finding = True
        else:
            findings.append(Finding(str(doc_path), _find_line_with_fact(lines, r"provider"),
                                   CheckId.VIDEO_PROVIDERS, "mismatch",
                                   res.expected, res.observed, res.message or "video provider mismatch"))

    tier_defaults = facts.get("tier_defaults", {}).get("tiers", {})
    tier_video_providers = facts.get("tier_defaults", {}).get("video_providers", {})
    tier_image_providers = facts.get("tier_defaults", {}).get("image_providers", {})
    tier_video_enabled = facts.get("tier_defaults", {}).get("video_enabled", {})
    if tier_defaults and doc_path.name in ("TECHNICAL_SPECS.md", "PROJECT_CONTEXT.md"):
        tier_results = _check_tier_defaults(text, tier_defaults, tier_video_providers, tier_image_providers, tier_video_enabled)
        for tres in tier_results:
            if not tres.passed:
                exc = _match_exception(exceptions, tres.check_id, str(doc_path))
                if exc and exc.expires >= today:
                    exc.suppressed_finding = True
                else:
                    findings.append(Finding(str(doc_path), _find_line_with_fact(lines, r"\*\*[A-Z]+\*\*"),
                                           tres.check_id, "mismatch",
                                           tres.expected, tres.observed, tres.message or f"{tres.check_id} mismatch"))

    tier_prices = facts.get("tier_prices", {}).get("tier_prices", {})
    if tier_prices:
        res = _check_tier_prices(text, tier_prices)
        if not res.passed:
            exc = _match_exception(exceptions, CheckId.TIER_PRICE, str(doc_path))
            if exc and exc.expires >= today:
                exc.suppressed_finding = True
            else:
                findings.append(Finding(str(doc_path), _find_line_with_fact(lines, r"\$\d+/mo"),
                                       CheckId.TIER_PRICE, "mismatch",
                                       res.expected, res.observed, res.message or "tier price mismatch"))

    route_facts = facts.get("routes", {}).get("routes", {})
    if route_facts:
        res = _check_routes(text, route_facts)
        if not res.passed:
            exc = _match_exception(exceptions, CheckId.ROUTE, str(doc_path))
            if exc and exc.expires >= today:
                exc.suppressed_finding = True
            else:
                findings.append(Finding(str(doc_path), _find_line_with_fact(lines, r"`/"),
                                       CheckId.ROUTE, "mismatch",
                                       res.expected, res.observed, res.message or "route mismatch"))

    env_facts = facts.get("env_vars", {}).get("env_vars", [])
    if env_facts and doc_path.name in ("TECHNICAL_SPECS.md", "PROJECT_CONTEXT.md", "MEMORY_LAYER.md"):
        res = _check_env_vars(text, env_facts)
        if not res.passed:
            exc = _match_exception(exceptions, CheckId.ENV_VAR, str(doc_path))
            if exc and exc.expires >= today:
                exc.suppressed_finding = True
            else:
                findings.append(Finding(str(doc_path), _find_line_with_fact(lines, r"[A-Z_][A-Z0-9_]*"),
                                       CheckId.ENV_VAR, "mismatch",
                                       res.expected, res.observed, res.message or "env var mismatch"))

    if doc_path.name == "MEMORY_LAYER.md":
        mem_res = _check_memory_layer_env_block(text)
        if not mem_res.passed:
            findings.append(Finding(str(doc_path), 1,
                                   CheckId.ENV_VAR, "missing",
                                   mem_res.expected, mem_res.observed, mem_res.message or "memory layer env block check failed"))

    auto_facts = facts.get("auto_routing", {})
    if auto_facts and doc_path.name == "TECHNICAL_SPECS.md":
        auto_results = _check_auto_routing(text, auto_facts)
        for ares in auto_results:
            if not ares.passed:
                exc = _match_exception(exceptions, ares.check_id, str(doc_path))
                if exc and exc.expires >= today:
                    exc.suppressed_finding = True
                else:
                    findings.append(Finding(str(doc_path), _find_line_with_fact(lines, r"auto_.*model"),
                                           ares.check_id, "mismatch",
                                           ares.expected, ares.observed, ares.message or f"auto routing mismatch"))

    docker_count = facts.get("docker", {}).get("service_count", 0)
    if docker_count and doc_path.name in ("TECHNICAL_SPECS.md", "PROJECT_CONTEXT.md"):
        res = _check_docker_service_count(text, docker_count)
        if not res.passed:
            exc = _match_exception(exceptions, CheckId.DOCKER_SERVICE_COUNT, str(doc_path))
            if exc and exc.expires >= today:
                exc.suppressed_finding = True
            else:
                findings.append(Finding(str(doc_path), _find_line_with_fact(lines, r"Docker Compose"),
                                       CheckId.DOCKER_SERVICE_COUNT, "mismatch",
                                       res.expected, res.observed, res.message or "docker service count mismatch"))

    subagent_facts = facts.get("subagents", {})
    if subagent_facts and doc_path.name == "PROJECT_CONTEXT.md":
        sub_results = _check_subagent_table(text, subagent_facts)
        for sres in sub_results:
            if not sres.passed:
                exc = _match_exception(exceptions, CheckId.SUBAGENT_STATUS, str(doc_path))
                if exc and exc.expires >= today:
                    exc.suppressed_finding = True
                else:
                    findings.append(Finding(str(doc_path), _find_line_with_fact(lines, r"@"),
                                           sres.check_id, "mismatch",
                                           sres.expected, sres.observed, sres.message or f"subagent status mismatch"))

    all_active_exceptions = [exc for exc in exceptions if exc.expires >= today]
    return findings, all_active_exceptions




def format_text(findings: list[Finding], exceptions: list[ExceptionEntry], malformed: list[tuple[str, int, str]]) -> str:
    lines_out: list[str] = []
    for f in findings:
        lines_out.append(f"{f.doc_path}:{f.line} [{f.check_id}] expected={f.expected!r} observed={f.observed!r}  {f.message}")
    for e in exceptions:
        status = "SUPPRESSED" if e.suppressed_finding else "ACTIVE"
        lines_out.append(f"{e.doc_path}:{e.line} [EXCEPTION {e.check_id}] expires={e.expires} reason={e.reason!r} ({status})")
    for doc, lineno, msg in malformed:
        lines_out.append(f"MALFORMED_EXCEPTION {doc}:{lineno}: {msg}")
    return "\n".join(lines_out)


def format_json(findings: list[Finding], exceptions: list[ExceptionEntry],
                malformed: list[tuple[str, int, str]], facts: dict[str, Any]) -> str:
    report = {
        "checked_sources": {
            "migrations": facts["migrations"],
            "embeddings": facts["embeddings"],
            "providers": facts["providers"],
            "routes": facts["routes"],
        },
        "findings": [
            {"doc": f.doc_path, "line": f.line, "check_id": f.check_id, "kind": f.kind,
             "expected": f.expected, "observed": f.observed, "message": f.message}
            for f in findings
        ],
        "exceptions": [
            {"doc": e.doc_path, "line": e.line, "check_id": e.check_id,
             "expires": e.expires.isoformat(), "reason": e.reason, "suppressed_finding": e.suppressed_finding}
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
                all_findings.append(Finding(
                    doc_path=str(doc_path),
                    line=exc.line,
                    check_id=exc.check_id,
                    kind="expired_exception",
                    expected=f"expires >= {today.isoformat()}",
                    observed=f"expired on {exc.expires.isoformat()}",
                    message=f"expired DOC_FRESHNESS_EXCEPTION for '{exc.check_id}'",
                ))

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
