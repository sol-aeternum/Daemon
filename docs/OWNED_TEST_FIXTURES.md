# Owned search and cache fixtures (#541)

These are synthetic test prerequisites, not runtime services or production
qualification. They require no application credentials, paid inference, provider
requests, production Redis, database migrations or new dependencies.

## Search Redis

The default search pacing tests and the final-send metered-search regression use
`tests/owned_search_redis.py`, via a lazy session fixture in `tests/conftest.py`.
They require Docker and the pinned Docker Library Redis **7.4.7-alpine** image.
`tests/search_redis_image.py` owns the qualified reference and Linux/amd64 contract:

- Registry index: `sha256:02f2cc4882f8bf87c79a220ac958f58c700bdec0dfb9b9ea61b62fb0e8f1bfcf`
- Linux/amd64 manifest: `sha256:b9f6cf0cab55fdd102fd2182deeae150457943d33439d18c6e2fc5666d3228c1`
- Config: `sha256:e08bd8d5a6774025cff22e5db8361f6a5d023811e84d77d0033e47d44970cc94`

These are independently hash-verified registry responses, not a local ID assumed
to be a registry digest. Official provenance is the
[Docker Library publication](https://github.com/docker-library/official-images/blob/d12d9da8aa52024646555edc8fd567861d2d610b/library/redis)
and its matching
[Redis Dockerfile](https://github.com/redis/docker-library-redis/blob/d42d7aec93b1c54dd46f37a66a92f62478456039/7.4/alpine/Dockerfile).
The index uniquely selects that amd64 manifest, which references that config.

The hosted backend and PostgreSQL opt-in inventory jobs each provision this same
approved official index with `docker pull --platform=linux/amd64` and run the
shared verifier before pytest. Separate runners do not share an image cache.
A failed pull or verification prevents pytest from starting in that job; the
opt-in inventory remains a non-required check. There is no credential, retry,
mutable-tag fallback or skip on this path. Compressed image layers total
17,233,628 bytes. This does not change the Redis fixture runtime or approved image.

The fixture itself **never pulls**. It inspects the exact pinned reference and
rejects unknown IDs, repository digests, metadata or OS/architecture. It accepts
only the index ID (containerd image store) or the linked amd64 config ID (classic
Docker store), with `redis@INDEX` or `docker.io/library/redis@INDEX` repository
identity. It records the exact resolved daemon ID and uses that ID for container
creation with `--platform=linux/amd64 --pull=never`; container identity must still
match that same ID exactly. Local validation uses the existing cache; missing
tooling or image is an error, never a fixture download or substitution.

If that fixed image inspection exits nonzero, its operation/exit code and bounded Docker
stderr/stdout are preserved in the test error and task receipt. Exit 1 alone does
not prove a missing image: daemon or access errors are also possible. This does
not retry, provision, pull or substitute anything. Hosted runner diagnostics that
do not expose Docker's actual error leave the underlying cause unqualified.

One fresh container has network `none`, no published ports, a private Unix socket,
non-root task UID/GID, read-only root, dropped capabilities, no-new-privileges,
bounded memory/PIDs and bounded `/tmp` and `/data` tmpfs. Masking `/data` prevents
the image's declared volume from creating an anonymous volume. Only an existing
fresh synthetic socket directory is mounted. No host source or credentials mount.

The owner records launch intent before creation, then verifies exact container ID,
image, task/run nonce labels and expected configuration before start/use/cleanup.
Clients use database 0 only after socket ownership, Redis `run_id` and a nonce
marker agree. Existing per-test namespaces and timing/concurrency assertions are
unchanged; no shared DSN, `FLUSHDB` or connectivity skip exists on this path.

The host socket path must fit Linux's Unix-socket limit. Managed runs use the
existing `OPENCODE_WORK_TEST_DIR` with a short unique basename; other runners use
pytest's temporary root. A path that is too long fails explicitly. Each directory
retains `receipt.json`, including exact resource IDs and cleanup outcomes.

Teardown re-verifies ownership, gracefully stops only the exact running container
without a forced-kill timeout, inspects stopped status, then removes it without
force. Missing/dead Redis does not prohibit cleanup if Docker ownership remains
positive. Uncertain identity/stop is retained and fails teardown. Host directories
remain retained. No pruning, shared-service cleanup or automatic adoption occurs.

## Cache browser endpoints

`frontend/playwright.cache.config.ts` uses a supported global setup/returned
teardown function (`frontend/e2e/cache-fixture-setup.mjs`). Unlike Playwright's
default `webServer` teardown, this owns the actual inherited child-control pipes
instead of issuing process-group kills. It has no existing-server reuse path.
The existing loopback ports 3141/3142/3143 and all browser assertions stay fixed;
3100/3101 are unrelated and unchanged.

The launch context creates one task/run nonce and passes it as fixture-only argv,
not an application environment setting. The nonce is a public identity label, not
a secret. The shared fixture environment builder scrubs both coordinator and child
launches and checks bounded Node options before spawning. Without `TMPDIR`, ordinary
CI uses the standard OS temporary root; managed runs retain their task-private root.
The coordinator binds exclusively before
starting children. All normal app/seed routes return 503 until readiness. The
fixture-only Next custom server and programmatic Uvicorn wrapper each bind
exclusively and send their role, address and run identity through a private pipe.
No automatic port increment or foreign-server probe/adoption is permitted.

Only after every bind receipt and read-only HTTP identity agrees (no redirects)
does the coordinator arm handlers through private command pipes, perform the
existing production proxy warm-up POST, then expose the app and nonce-qualified
readiness URL. Pre-ready and wrong-nonce readiness always return 503; Playwright
can treat some 4xx responses as available, so those statuses are not used as gates.
The fixture still executes actual production Next proxy and FastAPI cache-clear
handlers; only the already-synthetic upstream identity responses remain fictional.

Child death/control-channel failure removes readiness and stops forwarding.
Shutdown uses nonce-checked commands over private pipes; EOF makes children close
their own servers. No PID/port lookup or signal fallback exists in fixture code.
The coordinator records exact child exits and cleanup outcomes under the managed
temporary root (`cache-<nonce>.jsonl`). Cleanup failure is a gate error, not success.
Task-owned receipts supplement managed scope tracking; they do not change lifecycle
manifests or make unmerged/uncertain tasks eligible for disposal.

## Commands and boundaries

Run through the registered task's `opencode-work run TASK --` wrapper:

```sh
uv run pytest -q tests/test_search_redis_image.py tests/test_owned_search_redis.py tests/test_search_pacing.py tests/test_metered_web_search.py
uv run pytest -q frontend/e2e/cache_fixture_ownership.py
npm --prefix frontend run test:run -- __tests__/cache-fixture-control.test.ts
npm --prefix frontend run test:browser -- --config playwright.cache.config.ts
```

The negative tests own their synthetic collision listeners and leave them alive
until explicit local teardown. A foreign port collision is refusal, never a reason
to stop a process, borrow a server or weaken the gate. Nonces prevent accidental
cross-task reuse under the trusted same-UID/Docker-administrator model; they are
not protection against a malicious administrator or same-UID peer.

The three real collision checks are an explicit **blocking frontend gate** after
both locked environments are installed, before the browser suite. They are not
part of default backend pytest discovery: the backend CI job intentionally does
not install Next/frontend packages. No assertion is skipped or removed; the local
runner and CI both invoke the same module explicitly, with ordering and refusal
semantics covered by `tests/test_local_ci_frontend_setup.py`.

Targeted passes do not replace the full repository gates, final independent review
or `scripts/pr_create.sh`. Resources with incomplete cleanup remain pinned with
evidence. No merge, deployment, runtime enablement or #477 lifecycle work is implied.

## PostgreSQL inventory CI shards

The non-blocking opt-in inventory runs four whole-module shards on separate
GitHub-hosted runners. Each runner provisions its own pinned PostgreSQL service,
owned fixture containers and Redis image, with shard-specific temporary roots and
artifact names. No mutable database or Docker daemon is shared between shards;
fixture ownership checks and per-test database isolation are unchanged.

`scripts/postgres_inventory_shards.py` preserves the original top-level
`tests/test_*.py` content discovery. It assigns every discovered file exactly once,
longest estimated module first, to the least-loaded shard (stable filename/index
tie-breaks). The matrix is the shard-count source of truth: selection and reporting
use GitHub's `strategy.job-total`, with contiguous one-based matrix shard IDs. The
selector runs through `uv run python` and requires an explicit `--count`.
`scripts/postgres_inventory_timings.json` records summed JUnit case
seconds from run 44232075074, not a coverage allowlist. New modules use the median
recorded module duration; removed modules are ignored. Refresh the estimates by
summing testcase `time` values by module across all four JUnit artifacts from a
completed run, preserving the source-run provenance. Estimates are not timeouts.

Each shard retains its selected-file manifest, JUnit, scope/exit/wall-time report
and pytest output with `--durations=50`. The same four browser selectors (including
parameter variants) remain excluded, not their mixed backend/browser modules.
Missing JUnit is not success. Fail-fast is disabled so one shard cannot cancel
coverage on another; pytest remains non-blocking and provisioning remains blocking
within its job. Existing 80-minute test/90-minute job safety limits are unchanged.
The historical module-duration balance predicts about 16.4 minutes per shard,
not a measured wall-clock guarantee. Every shard repeats dependency setup, image
pulls, service startup and any session-scoped fixtures. That fixed overhead adds
to both per-shard wall time and aggregate runner minutes; the snapshot does not
model it. Parallelism reduces elapsed time, not total provisioning work.

## Owned PostgreSQL image identity (#728 follow-up)

`tests/postgres_image.py` qualifies the existing pgvector pg16 pin before the
metadata fixture starts, without pulling or falling back to a mutable tag:

- Registry index: `sha256:33198da2828a14c30348d2ccb4750833d5ed9a44c88d840a0e523d7417120337`
- Linux/amd64 manifest: `sha256:16e62164a405447dca191079a924ee5b8a9dbf04fe53128701ffbea857b37782`
- Config: `sha256:4c0c0efbd40e8ff42363074292654a7831cac506fc01a1c89627f1b1040ba341`

The registry index, uniquely selected Linux/amd64 manifest and config responses
were SHA-256 verified on 2026-11-18 from Docker Hub's pgvector/pgvector repository.
The classic Docker store exposes the config ID; the containerd store exposes
the index ID. Neither is an arbitrary accepted digest: the verifier requires the
pinned repository digest, Linux/amd64 metadata and one of these two linked IDs.
The fixture records and launches that exact resolved ID with an explicit platform
and `--pull=never`. Restore operations compare the container image to that exact
qualified launch ID, not to the registry index unconditionally. Container ID,
nonce/task labels, network, mounts and running-state checks remain unchanged.
Unknown, missing, foreign or wrong-platform identities fail before creation.

This repairs the image-identity portion of the PostgreSQL inventory debt tracked
in #717; it does not close the broader inventory or claim restore qualification.
The inventory job remains nonblocking and reports actual pytest failures.
