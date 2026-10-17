# Owned search and cache fixtures (#541)

These are synthetic test prerequisites, not runtime services or production
qualification. They require no application credentials, paid inference, provider
requests, production Redis, database migrations or new dependencies.

## Search Redis

The default search pacing tests and the final-send metered-search regression use
`tests/owned_search_redis.py`, via a lazy session fixture in `tests/conftest.py`.
They require Docker and the already-cached official `redis:7-alpine` image (the
same tag as Compose). The fixture resolves its local immutable image ID and uses
`--pull=never`: absent tooling/image is an error, not a download, skip or fallback.
CI must provision the approved cached image separately; this change does not
authorize image pulls.

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
uv run pytest -q tests/test_owned_search_redis.py tests/test_search_pacing.py tests/test_metered_web_search.py
uv run pytest -q tests/test_cache_fixture_ownership.py
npm --prefix frontend run test:run -- __tests__/cache-fixture-control.test.ts
npm --prefix frontend run test:browser -- --config playwright.cache.config.ts
```

The negative tests own their synthetic collision listeners and leave them alive
until explicit local teardown. A foreign port collision is refusal, never a reason
to stop a process, borrow a server or weaken the gate. Nonces prevent accidental
cross-task reuse under the trusted same-UID/Docker-administrator model; they are
not protection against a malicious administrator or same-UID peer.

Targeted passes do not replace the full repository gates, final independent review
or `scripts/pr_create.sh`. Resources with incomplete cleanup remain pinned with
evidence. No merge, deployment, runtime enablement or #477 lifecycle work is implied.
