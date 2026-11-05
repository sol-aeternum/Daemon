# M1.5 decomposition latency bake-off: pre-registration (#601)

**Purpose:** give the owner latency, cost and reliability figures to choose the decomposition route and its timeout. The owner selects; this run decides nothing. Fixed before any call. Approval of the route, enablement of the flag and any change to the production profile are all separate.

## Arms (owner decision, 2026-11-05)

| Arm | Route | Reasoning |
|---|---|---|
| A | `luna-azure-eu` | `none` |
| B | `deepseek-flash-coreweave-fp8` | `low`, its lowest configured setting |
| C | `glm-flash-inceptron-fp8` | `low`, its lowest configured setting |

B and C at `low` were accepted by the owner, and differ from the plan's "none or minimal". The results label them as `low`. Only Luna is listed in the `memory_decomposition` profile, so choosing B or C would need its own config change and approval.

## Method

- **Inputs:** the 50 dev-50 questions, one call per question per arm: **150 calls, no retries**, one at a time. Arm order rotates by question.
- **Request:** `PROMPT_V2`, `SUBQUERY_SCHEMA` and `max_tokens` 256, unchanged from production. Only route, model and reasoning differ, and a test pins that.
- **Not streaming:** the guarded stream doesn't expose serving provider or cost, so **time to first token isn't measured**. The design doc's TTFT is dropped for this run. The figures are total latency, which is also what production's non-streaming call experiences.
- **Routes:** each pins one provider, so `sort: latency` has no effect and isn't applied.
- **Discarded:** generated sub-queries, replies and question text are never stored.

## Reported per arm

Calls, failures, completed calls, total p50 and p95, schema-valid rate and trigger rate over completed calls, truncated calls, total cost and serving providers.

## Limits and stops

- **Spend:** USD 0.50. This block has its own ledger starting at zero, so the cap is `--max-spend-usd 0.50`, checked against settled + held + the next call's ceiling before each dispatch.
- **Database:** its own new `daemon_lme_bench_m15` (owner ruling), holding ledger and account rows only, with one synthetic user. The driver refuses every other database. The fingerprints of `daemon_lme_bench` and the pilot's `daemon_lme_bench_dev2` are checked before and after.
- **Stops, sending nothing further:** spend cap, unreadable ledger or unknown ceiling, run lock busy or lost, journal mismatch or uncertain dispatch, an accounting error, or 10 consecutive failures.
- **Failures are data:** a known guard failure is recorded and the run continues, with no retry.

## Limits of the result

One sitting, one machine and 50 questions. Latency varies with time of day and provider load, which this run doesn't sample. Results show relative behaviour at that moment, not a guarantee.
