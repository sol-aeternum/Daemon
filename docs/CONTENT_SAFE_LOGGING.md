# Content-safe runtime logging (P0.3)

## Supported launch boundary

Run the backend with `python -m orchestrator.runtime backend` and the worker
with `python -m orchestrator.runtime worker`. Repository Docker/Compose source
commands use these launchers. The backend still binds port 8000; application
routes, responses, permissions, worker retries and persistence are unchanged.
This source change does not authorize deployment.

The stdlib-only launcher installs safe failure handling before importing the
application. During dependency initialization, Python stdout/stderr are
discarded, not buffered. Early free-form startup diagnostics are deliberately
unavailable. All discovered logging handlers are detached and replaced with
managed sinks before work starts, including import-installed LiteLLM handlers
and Uvicorn access/error logging. No active sink keeps the closed discard
stream. Repeated routing-logger configuration also uses a managed sink.

Managed sinks emit fresh JSON lines, never legacy message interpolation,
arguments, exception text, traceback/source text, stack info, request URLs,
client IPs, headers or arbitrary record extras. Unconverted records become
`legacy_log` plus a bounded level, not their original diagnostic text.
Malformed structured diagnostics become `invalid_diagnostic`; sink failures
never use Python's content-bearing default `handleError` output. Fixed safe
startup/shutdown/failure events are available. DEBUG does not bypass this rule.

The existing `LOG_LEVEL` input now controls managed backend and worker sinks:
`DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`; absent, empty or unsupported
values use `INFO`. Fixed control diagnostics bypass verbosity suppression, but
not schema validation. Both Compose services receive it; see
`ENV_SURFACE_MIGRATION.md` for precedence and the same-PR migration note.

Account identifiers are **omitted entirely**: no logging hash key is added,
and authentication/encryption secrets are not reused. Source logging sites in
chat/main entrypoints, fetch, retained image adapters, worker jobs and memory processing are also
cleaned as defense in depth. Retired adapters remain retired.

## Routing diagnostics

Routing records retain supported enums, finite bounded counts/amounts, known
classifier signals and trusted configuration model/route/group identities.
The launcher snapshots these vocabularies during initialization; sink validation
does no imports or configuration loading. Dynamic labels fail closed before
initialization or if snapshot loading fails. Diagnostics require a restart to
reflect configuration changes; routing/admission policies are not changed.
Unknown strings become `unrecognized` or are omitted from lists/maps; a model
ID regex alone is not evidence that caller text is safe. Scope/reservation
UUIDs are emitted only in their dedicated fields whose inspected compute and
ledger callsites supply server-generated operation references, never account
IDs. Generic request-id arguments are omitted because their provenance is not
guaranteed at every internal caller. No client API/SSE event changes.

The structured schema is revalidated at the final sink: logger name or a
purported safe flag is not an exemption. Unknown fields/events, non-finite
numbers and unsupported types fail closed. Operators lose legacy diagnostic
text by design; existing database audit/notification payloads and stored job
arguments/results are separate surfaces and are not rewritten by this PR.

## Qualification limits and retention

This is a **managed Python logging** contract for the supported launchers,
not whole-process output isolation. Direct/native file-descriptor writes,
arbitrary application `print` calls after initialization, newly installed raw
handlers or runtime logging reconfiguration can bypass it. Such configuration
is unsupported and needs separate qualification. Direct Uvicorn/ARQ CLI runs,
evaluation scripts, frontend/other container services and external collectors
are not qualified by these tests. Existing persisted logs are not retroactively
sanitized. Production filesystem/collector inventory remains an operator gate.

Approved R3 policy: application/container logs have a maximum **seven-day age**
with **size caps as well**. This PR does not implement or qualify that retention.
Docker size-only rotation does not establish age expiry, especially during
outages or inactivity. Scheduler, expiry failures, all collectors/copies and
production verification require a separate operator plan and approval; no
live log configuration, deletion or deployment is performed here.

## Acceptance mapping

This is a prerequisite for `ACCOUNT_DELETION_DESIGN.md` §7, not account deletion
or reset implementation. The §8 deletion-report/content and failed-job scenarios
remain later state-machine/persistence tests. These tests cover only preventing
new managed application-log content: `test_safe_logging.py` (sink/schema/hooks,
import failure/discard, poison formatting, last-resort and write failures),
`test_safe_logging_runtime.py` (supported preparation with installed Uvicorn,
ARQ and LiteLLM, access/job/error paths and actual event loops),
`test_content_safe_log_sites.py` (mocked source-site sentinels), and the existing
`test_routing_log.py` behavior tests with stricter diagnostic vocabulary.
Tests never invoke a paid provider, live database or Redis service. Log-retention,
whole-container output, deployment and deletion completion are not claimed.
