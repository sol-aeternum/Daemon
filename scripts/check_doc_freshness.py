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
_EMBEDDING_DOC_MODEL_CLAIM_RE = re.compile(
    r'embedding_document_model[:=]\s*"([^"]+)"',
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
_PROVIDER_CLIENT_RE = re.compile(r"class\s+(\w+(?:Client|Provider))\s*\(")


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


_ROUTE_DEF_RE = re.compile(r'(?:@app\.|router\.)(get|post|put|patch|delete|options)\s*\(\s*["\']([^"\']+)["\']')


def get_route_facts(root: Path) -> dict[str, Any]:
    main_path = root / "orchestrator" / "main.py"
    routes_dir = root / "orchestrator" / "routes"
    routes: dict[str, list[str]] = {}

    for path in [main_path] + list(routes_dir.glob("*.py")):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for m in _ROUTE_DEF_RE.finditer(text):
            method = m.group(1).upper()
            path_val = m.group(2)
            routes.setdefault(method, []).append(path_val)

    return {"routes": routes}


_FEATURE_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|[^|]*\|\s*([^|]+?)\s*\|-")


def get_feature_states(root: Path) -> dict[str, str]:
    matrix_path = root / "docs" / "FEATURE_MATRIX.md"
    if not matrix_path.exists():
        return {}

    text = matrix_path.read_text(encoding="utf-8")
    states: dict[str, str] = {}

    in_matrix = False
    for line in text.splitlines():
        if line.startswith("## Feature Matrix"):
            in_matrix = True
            continue
        if in_matrix and line.startswith("## "):
            break
        if not in_matrix:
            continue
        if line.startswith("| Feature |") or line.startswith("|---") or line.startswith("| **"):
            continue

        m = _FEATURE_ROW_RE.match(line)
        if m:
            feature = m.group(1).strip()
            state = m.group(2).strip()
            if feature and state:
                states[feature] = state

    return states


_ENV_VAR_RE = re.compile(r'`([A-Z_][A-Z0-9_]*)`')


def get_env_var_facts(root: Path) -> dict[str, list[str]]:
    config_path = root / "orchestrator" / "config.py"
    text = config_path.read_text(encoding="utf-8")
    env_vars: set[str] = set()
    for m in _ENV_VAR_RE.finditer(text):
        env_vars.add(m.group(1))
    return {"env_vars": sorted(env_vars)}


def extract_all_facts(root: Path) -> dict[str, Any]:
    return {
        "migrations": get_migration_facts(root),
        "embeddings": get_embedding_facts(root),
        "providers": get_provider_facts(root),
        "routes": get_route_facts(root),
        "feature_states": get_feature_states(root),
        "env_vars": get_env_var_facts(root),
    }




class CheckId(str, Enum):
    MIGRATION_COUNT = "migration_count"
    MIGRATION_LATEST = "migration_latest"
    EMBEDDING_DOC_MODEL = "embedding_document_model"
    DEDUP_MERGE = "dedup_merge_threshold"
    DEDUP_SUPERSEDE_GENERIC = "dedup_supersede_generic_threshold"
    DEDUP_SUPERSEDE_SAME_SLOT = "dedup_supersede_same_slot_threshold"
    VIDEO_PROVIDERS = "video_providers"


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


def _check_migration_count(doc_content: str, expected: int) -> CheckResult:
    m = re.search(r'\b(\d{2,3})\s+migration', doc_content, re.IGNORECASE)
    if not m:
        return CheckResult(CheckId.MIGRATION_COUNT, True)
    observed = int(m.group(1))
    if observed != expected:
        return CheckResult(CheckId.MIGRATION_COUNT, False, str(expected), str(observed),
                          f"migration count mismatch: expected {expected}, found {observed}")
    return CheckResult(CheckId.MIGRATION_COUNT, True)


def _check_migration_latest(doc_content: str, expected: str) -> CheckResult:
    m = re.search(r'\b(0\d{2}_\w+\.sql|\d{2}_\w+\.sql)\b', doc_content)
    if not m:
        return CheckResult(CheckId.MIGRATION_LATEST, True)
    observed = m.group(1)
    if observed != expected and observed != expected.replace(".sql", ""):
        return CheckResult(CheckId.MIGRATION_LATEST, False, expected, observed,
                          f"latest migration mismatch: expected {expected}, found {observed}")
    return CheckResult(CheckId.MIGRATION_LATEST, True)


def _check_embedding_doc_model(doc_content: str, expected: str) -> CheckResult:
    m = _EMBEDDING_DOC_MODEL_CLAIM_RE.search(doc_content)
    if not m:
        return CheckResult(CheckId.EMBEDDING_DOC_MODEL, True)
    observed = m.group(1).strip()
    if expected not in observed and observed not in expected:
        return CheckResult(CheckId.EMBEDDING_DOC_MODEL, False, expected, observed)
    return CheckResult(CheckId.EMBEDDING_DOC_MODEL, True)


def _check_dedup_threshold(doc_content: str, threshold_name: str, expected: float) -> CheckResult:
    # Match ≥expected or exactly expected
    pattern = rf'[\d.]+\s*(?:≥|>)\s*{re.escape(str(expected))}|{re.escape(str(expected))}'
    if re.search(pattern, doc_content):
        return CheckResult(threshold_name, True)
    wrong_pattern = rf'\b0\.\d{{2}}\b(?!\s*(?:≥|>))\s*(?:≥|>)\s*{re.escape(str(expected))}'
    if re.search(wrong_pattern, doc_content):
        return CheckResult(threshold_name, False, str(expected), "mismatched threshold")
    return CheckResult(threshold_name, True)


def _check_video_providers(doc_content: str, valid_providers: frozenset[str]) -> CheckResult:
    """
    Check structured video provider claims in doc against source-derived valid set.

    Singular/plural distinction:
      - Singular (provider: xai): validates the claimed provider is in the valid set.
        Does NOT require the full valid set to be present.
      - Plural/list (providers: xai,fal | VALID_VIDEO_PROVIDERS = {...}):
        requires exact set match: no unsupported providers AND no missing required ones.

    Minimizes false positives in narrative prose by requiring word-boundary
    anchors or structural patterns (colon, braces).
    """
    # Singular forms: capture exactly one provider name after "provider:" or "video provider:"
    singular_patterns = [
        (r'\bprovider:\s*([a-z]+)',),
        (r'\bvideo\s+provider:\s*([a-z]+)',),
    ]
    # Plural/list forms: capture raw comma-separated names or brace-enclosed set
    plural_patterns = [
        (r'\bproviders:\s*([a-z, ]+)',),          # "providers: xai, kling"
        (r'\bvideo\s+providers:\s*([a-z, ]+)',),   # "video providers: xai, kling"
        (r'\bVALID_VIDEO_PROVIDERS\s*=\s*\{([^}]+)\}',),  # VALID_VIDEO_PROVIDERS = {...}
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
                # Extract individual provider names (quoted or unquoted, comma-separated)
                names = re.findall(r'["\']?([a-z]+)["\']?', raw, re.IGNORECASE)
                plural_claims.update(n.lower() for n in names)

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
    updated_exceptions: list[ExceptionEntry] = []

    mfact = facts["migrations"]
    efact = facts["embeddings"]

    res = _check_migration_count(text, mfact["count"])
    if not res.passed:
        exc = _match_exception(exceptions, CheckId.MIGRATION_COUNT, str(doc_path))
        if exc and exc.expires >= today:
            exc.suppressed_finding = True
            updated_exceptions.append(exc)
        else:
            findings.append(Finding(str(doc_path), _find_line_with_fact(lines, r"\dmigration"),
                                   CheckId.MIGRATION_COUNT, "mismatch",
                                   res.expected, res.observed, res.message or "migration count mismatch"))

    res = _check_migration_latest(text, mfact["latest"])
    if not res.passed:
        exc = _match_exception(exceptions, CheckId.MIGRATION_LATEST, str(doc_path))
        if exc and exc.expires >= today:
            exc.suppressed_finding = True
            updated_exceptions.append(exc)
        else:
            findings.append(Finding(str(doc_path), _find_line_with_fact(lines, r"0\d\d_\w+\.sql"),
                                   CheckId.MIGRATION_LATEST, "mismatch",
                                   res.expected, res.observed, res.message or "migration latest mismatch"))

    res = _check_embedding_doc_model(text, efact["document_model"])
    if not res.passed:
        exc = _match_exception(exceptions, CheckId.EMBEDDING_DOC_MODEL, str(doc_path))
        if exc and exc.expires >= today:
            exc.suppressed_finding = True
            updated_exceptions.append(exc)
        else:
            findings.append(Finding(str(doc_path), _find_line_with_fact(lines, r"embedding_document_model"),
                                   CheckId.EMBEDDING_DOC_MODEL, "mismatch",
                                   res.expected, res.observed, res.message or "embedding doc model mismatch"))

    dedup_checks = [
        (CheckId.DEDUP_MERGE, efact["dedup_merge"], r"merge.*0\.\d+"),
        (CheckId.DEDUP_SUPERSEDE_GENERIC, efact["dedup_supersede_generic"], r"supersede.*generic.*0\.\d+"),
        (CheckId.DEDUP_SUPERSEDE_SAME_SLOT, efact["dedup_supersede_same_slot"], r"supersede.*same.*slot.*0\.\d+"),
    ]
    for check_id, expected_val, pattern in dedup_checks:
        if expected_val is None:
            continue
        res = _check_dedup_threshold(text, check_id.value, expected_val)
        if not res.passed:
            exc = _match_exception(exceptions, check_id.value, str(doc_path))
            if exc and exc.expires >= today:
                exc.suppressed_finding = True
                updated_exceptions.append(exc)
            else:
                findings.append(Finding(str(doc_path), _find_line_with_fact(lines, pattern),
                                       check_id.value, "mismatch",
                                       res.expected, res.observed, res.message or f"dedup threshold mismatch for {check_id.value}"))

    res = _check_video_providers(text, frozenset(facts["providers"]["video_providers"]))
    if not res.passed:
        exc = _match_exception(exceptions, CheckId.VIDEO_PROVIDERS, str(doc_path))
        if exc and exc.expires >= today:
            exc.suppressed_finding = True
            updated_exceptions.append(exc)
        else:
            findings.append(Finding(str(doc_path), _find_line_with_fact(lines, r"provider"),
                                   CheckId.VIDEO_PROVIDERS, "mismatch",
                                   res.expected, res.observed, res.message or "video provider mismatch"))

    return findings, updated_exceptions




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
