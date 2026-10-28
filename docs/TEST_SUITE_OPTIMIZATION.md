# Test suite optimisation — 2026-10-28

Status: implementation, three full after-suite runs and all blocking local gates
verified; owner-authorized draft PR / hosted CI measurement in progress.

## Scope and baseline

Owner-selected baseline: `df8085b4` on main, in the registered
`daemon-test-speed-20261028` worktree. The original feature checkout and its
uncommitted changes are separate. No production behaviour or deployment changes.

Recent successful CI sample: run `40623086848` (`2f9c7247`). Job execution times:
backend 921 s, frontend 509 s, pre-commit 70 s, feature matrix 12 s; summed runner
time 1512 s. Blocking critical path 921 s; initial queue about 3 s. Pytest step
812 s (reported pytest time 807.43 s, 7955 passed / 431 existing skips).
The sample precedes the extra BM25 and #577 barrier regressions in the chosen
baseline, so it is context, not an equivalent-workload before/after comparison.

Backend non-pytest costs: container initialisation 17 s, type checking 32 s,
Bandit 19 + 20 s, dependency sync/audit 2 s each. Frontend: Vitest 49 s,
HTTP-cache browser regression 68 s, browser inventory 280 s, build 28 s,
type checking 9 s, lint 15 s. Repeated Ruff checks are sub-second; removing
them would not materially improve the critical path. Next build and explicit
type checking are distinct gates, not duplicate type checking.

## Ranked implementation plan

1. Measure and replace repeated migration replay with a session-local migrated
   template and fresh per-test databases (owner approved). Preserve real commit,
   rollback, multi-connection and process-death behaviour; retain clean replay.
2. Remove mock-stream pacing from tests of endpoint contracts rather than clocks;
   consolidate success-only assertions already exercised by boundary tests.
3. Resolve #575's timing race without increasing timeouts. Main already contains
   #577's barrier repair (`b7b4de79`), including both observation orderings.
4. Evaluate bounded parallelism only after measuring setup and execution costs.
   Do not add workers without resource/isolation evidence.

## Evidence location

Raw measurements and diagnostics are retained outside the source tree at
`~/.local/share/opencode-work/evidence/daemon-test-speed-20261028/`.
The local PostgreSQL fixture is a newly created task-labelled pg16 container,
limited to 2 CPUs / 1 GiB, with a Docker-assigned loopback port and synthetic
credentials. No existing database is used. The host has 8 CPUs and about 15 GiB
RAM. Locked Python dependencies are installed in this worktree's `.uv-venv`.

## Review and routing

Paid DeepSeek investigation failed before any tool effects or proposal. GLM and
Haiku pre-change reviews also failed before tool effects with provider
`Insufficient account funds`; no retries, billing changes or exhaustion-only free
fallback. Fresh read-only Luna pre-change review completed: extension placement,
DSN lifetime for child processes, closed template connections, exact-owned cleanup
and fresh seed state are mandatory acceptance checks. OpenAI same-family fallback
is disclosed; it is not family-independent review.

Final substantive read-only Luna review completed with no findings after the
primary supplied the source-only diff in the accessible worktree. An initial
attempt could not read the external evidence directory and was incomplete;
it was not counted as acceptance. This is one completed review, with the same
OpenAI-family limitation. Full-suite and final gate evidence remain separate.

## Changes and retained evidence

- The session template is created from PostgreSQL `template0`, with extensions
  in `public`, and every migration is applied in its own real transaction. Each
  test receives a UUID-named clone, fresh pool/users/encryption, and a clone DSN
  for independent connections and spawned processes. Pool size remains 2–8.
  Templates are rebuilt each session; no migration or test-result disk cache.
  Drops target only generated names and never use FORCE. Original schema replay
  and namespace/ACL regressions remain; a new clean-database regression checks
  independent visibility, commit, rollback, DDL isolation and cleanup.
- Four endpoint modules opt into unpaced mock streaming. Only the daemon module's
  50 ms mock-character sleep becomes a real scheduler yield; token production,
  SSE processing and assertions remain. Global asyncio and timing-test modules
  are untouched; any newly introduced different sleep fails the fixture.
- #575 reads real SQL state before exercising a controlled local-clock boundary.
  It proves a held write gate still permits observation, then fences at expiry
  and rejects a late mutation. SQL observation timeout is deliberately not
  confused with lease expiry. Real-clock renewal/deadline and shutdown tests in
  `test_task_runner_postgres.py` remain unchanged. #577's two barrier orderings
  from main remain unchanged and pass the targeted run.
- Empty-test/skip-reason checks share parsed ASTs within that module invocation,
  rather than parsing every source file repeatedly. All discovered files and
  violation predicates remain checked. No cross-run result reuse.
- The local gate runner now prints elapsed time for every gate, preserving exit
  status and the existing blocking/inventory classification.

### Grouped deletion/consolidation ledger

| Group | Removed/consolidated | Protection retained |
|---|---|---|
| Chat rate limit | One success-only test removed | `test_chat_endpoint_returns_429_after_user_limit` already asserts both first requests return 200, then requires 429 and headers. Injecting premature rejection fails its retained 200 assertion. |
| Heartbeat constants | One constant/monkeypatch-only test removed | `test_valid_renewal_uses_request_time_not_ack_or_cadence` exercises delayed real SQL renewal with cadence shorter than latency and checks the independent I/O cap. Explicit short-I/O, expiry, completion and shutdown tests retain distinct outcomes. Literal reassignments are not runtime evidence. |
| AST walker sanity | One standalone nonempty-directory test folded into the shared parsing fixture | Both substantive checks now fail before scanning an empty test tree. The separate all-Python syntax gate remains unchanged. |
| Admin comparison scanner | Four whole-tree scans consolidated into one test | Every original forbidden spelling is checked against every originally eligible file, with all offenders reported. |
| Python `hmac` self-tests | Three library-only tests removed | They called only stdlib `compare_digest`, never Daemon. Application admin authentication tests (`test_memories.py`, `test_skill_api_contracts.py`) and the issue #22 source regression remain; testing Python's implementation here adds no Daemon fault coverage. |

Nine old test cases removed through deletion/consolidation, two new fixture
qualification tests added. No existing boundary case is skipped or reclassified.

### Initial measurements and fault injection

Local baseline: 7965 passed, 428 existing skips, 14 warnings, **971.53 s**.
Rounded pytest durations: setup **504.03 s**, call **380.42 s**, teardown
**53.12 s**. Database-heavy setup accounts for **490.96 s**. Rounding omits
sub-centisecond work; these are not exact phase totals. Collection alone:
**7.264 s** process wall, **5.51 s** reported collection (includes the two newly
added qualification tests, not yet wired into the suite fixture).

Five same-host fixture samples: old replay setup 0.616–0.938 s, teardown
0.050–0.118 s; clone setup 0.098–0.122 s, teardown 0.023–0.034 s after a
1.707 s first-drop outlier. Template build 0.528 s. Outlier retained, not discarded
from full-suite timings. Targeted regression run: **164 passed / 2 existing skips
in 56.46 s**, including both #577 orderings and the new clone qualification.

In-memory, one-shot fault injection (production files unchanged) independently
caused the expected assertion failures:

- Remove local fencing: #575 retained test fails `state.fenced`.
- Reject requests before their quota: consolidated rate test fails `429 == 200`.
- Reuse the template instead of fresh clones: isolation test fails database
  identity uniqueness (`1 == 3`).
- Synthetic source faults: all four original unsafe admin-comparison spellings,
  an empty test body, an undocumented skip and empty test discovery are detected
  by the retained/consolidated checks.

Supplementary #575 qualification passed 14 independent pytest invocations while
the broader local gate command was active. A planned 20-sample command hit its
120-second tool limit during sample 15; that sample and the remaining five are
unverified, not retries or successes. The stopped process scope was reconciled
with unknown exit status. This partial check is separate from full-suite timing.

### Equivalent local full-suite comparison

All four runs use the same pinned main baseline plus this patch, Python 3.14.8,
locked dependencies, host, single pytest process and owned 2-CPU / 1-GiB PostgreSQL
container. Before uses original tests/fixtures; after includes the nine removals
and two new qualification tests. No worker/shard fan-out or increased runner size.

| Run | Pytest wall / serial runner time | Setup | Execution | Teardown | Outcome |
|---|---:|---:|---:|---:|---|
| Before, cold Python cache | 971.53 s | 504.03 s | 380.42 s | 53.12 s | 7965 passed, 428 skipped |
| After, cold Python cache | 425.29 s | 103.17 s | 260.28 s | 30.19 s | 7958 passed, 428 skipped |
| After, warm Python cache | 368.66 s | 85.79 s | 237.05 s | 23.30 s | 7958 passed, 428 skipped |
| After, warm repeat in full local gates | 483.60 s | not collected | not collected | not collected | 7958 passed, 428 skipped |

Cold-to-cold saving: **546.24 s / 56.2%**. Warm after saving against the cold
baseline: **602.87 s / 62.1%**, not a warm-to-warm claim. Setup reduction explains
400.86 s of the cold comparison; execution 120.14 s; teardown 22.93 s. Rounded
phase sums omit tiny durations and collection/reporting. All runs report the same
14 existing warnings. Cold means an initially empty task-owned Python bytecode
cache; host filesystem/image caches are not flushed. PostgreSQL starts fresh for
the baseline and remains running; template construction is repeated each session.
A brief separate collection measurement overlapped the baseline. Shared-host
load and database buffer warmth are limitations; the paired fixture microbench
and large setup delta separately support the mechanism, not just cache effects.
The warm repeat was slower (483.60 s, still 50.2% below the cold baseline);
the supplementary #575 command overlapped part of the broader gate run. These
shared-host results are not a stable warm-cache median or an isolated CPU benchmark.

Hosted CI after-timings are **not yet measured**; the local delta must not be
substituted for a hosted critical-path claim. The unchanged frontend already
took 509 s in the reference hosted run, so further backend-only savings will
eventually stop reducing the overall CI critical path.

An exact pinned-main hosted baseline was subsequently retrieved: run
`40633084741`, commit `df8085b40f2da796664b70b0e40d1f1d1823f7e0`.
Backend **942 s**, frontend **670 s**, pre-commit **55 s**, feature matrix **9 s**;
total runner time **1676 s (27m56s)**, critical job path **942 s (15m42s)**,
creation-to-completion **945 s**, initial queue about **3 s**. Pytest **827.35 s**
(831 s step), **7962 passed / 431 existing skips**. Use this exact-head sample,
not the earlier exploratory sample, for the draft PR's hosted comparison.
The three extra locally enabled tests explain the local/hosted count difference.
Frontend variation (509–670 s across these observations) is retained in the
evidence; a single hosted run cannot establish a noise-free median.

### Parallelism decision

Keep one backend pytest process. Clones prove database/data/DDL separation, and
the full process-death tests pass against clone DSNs, but this does not establish
whole-suite parallel safety. The existing search Redis fixture uses per-session
owned Unix sockets and per-test namespaces; browser fixtures exclusively own
fixed ports 3141–3143 (and browser inventory uses 3100/3101). Environment and app
globals are still process-scoped. Full sharding would require a partition/coverage
manifest plus an audit of every process/filesystem fixture. Adding workers now
would multiply imports, session templates and peak connections while the
frontend increasingly limits the critical path. No parallel speedup, extra
runner minutes or exactly-once shard guarantee is claimed: every existing
intended test still runs once in the ordinary unfiltered suite.

All blocking local gates passed in 775 s serial wall time: backend pytest,
Ruff, types, high-severity Bandit, dependency audits, frontend install/security,
types/lint/format, 1464 Vitest tests, three cache-fixture ownership tests,
30 HTTP-cache browser regressions, production build, feature matrix and pre-commit.
Full Bandit remains the existing non-blocking inventory (exit 1; tracked in #313).
Separate default browser inventory: 29 passed / 24 failed in 3.0 minutes, including
21 design-token cases with Next devtools inline-style CSP errors, two long-chat
reload cases and one navigation-width case. Tracked under #490; no blanket
baseline-causality claim. Traces were preserved before later browser gate output
reuse; only the two runner-generated Next ambient imports were restored.
Hosted results are pending. The baseline pytest process
exited 0, but lifecycle bookkeeping returned EAGAIN afterwards. Its exact scope
was reconciled by the installed lifecycle helper as stopped / exit status
unknown; this does not change the separately preserved pytest result.
