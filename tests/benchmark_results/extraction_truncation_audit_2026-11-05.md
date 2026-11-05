# Extraction truncation audit, 2026-11-05 (#670)

**Question:** how often did extraction hit the old 2,000-token output cap, and does that explain known retention gaps?

**Answer on the benchmark snapshot:** rarely, and no. Only 3 sessions hit the cap in the whole Phase D ingest. None of them is an answer session for its question, none belongs to a failing dev-50 question, and every chunk involved was recovered. **Production was not audited** (see B2).

Read-only, $0, no memory content decrypted. `daemon_lme_bench` fingerprint `5cb6ab14…` was identical before and after.

## B1. Benchmark snapshot `daemon_lme_bench`

### Method
Per-call output tokens are in `entitlement_reservations.usage`. A call counts as at the cap when `output_tokens >= 2000`; none exceeded 2,000, because that was the limit. Calls were tied to questions through the deterministic benchmark user ID, and to sessions through the `ingest_failures` records in `longmemeval_phase_d_2026-10-27/ingest/run_manifest.redacted.json`.

### Counts

| Measure | Result |
|---|---|
| Background calls with usage, Phase D ingest (2026-10-27) | 36,976 |
| Calls at the cap | **11** (0.03% of background calls) |
| Next-largest reply | 1,977 tokens |
| Replies of 1,800 tokens or more, including the 11 | 15 |
| Replies above 1,000 tokens | 64 |
| Extraction attempts reaching the cap | 6 of about 8,638 (0.07%) |
| Sessions affected | **3** |

The denominator for attempts is the 8,633 completed chunks plus the 5 chunk failures with cap evidence. The 0.03% figure understates the extraction share, because the background profile also covers entities, equivalence, summaries and titles.

### Sessions at the cap

| dev-50 question | Session | Calls at cap | Outcome | Answer session? | Attribution |
|---|---|---|---|---|---|
| `982b5123_abs` | `sharegpt_xxk9JDz_21` | 8 (4 failed attempts) | Fifth attempt completed | No | Manifest failure records and times |
| `gpt4_93159ced` | `sharegpt_9srz3L6_0` | 2 (1 failed attempt) | Retry completed | No | Manifest failure record and time |
| `80ec1f4f_abs` | `f8501ef5_2` | 1 | No session failure recorded | No | **Inferred** from conversation times |

The other 47 question users had no call at the cap. The sixth Phase D session failure (`3ba21379` / `0f78155a_1`) recorded an embedding error and has no call at the cap, so it isn't truncation.

### Against the known gaps

| Question set | Result |
|---|---|
| Source-retention gaps `4388e9dd`, `71a3fd6b`, `7a8d0b71` | **None hit the cap.** Their users have no call at the cap at all. |
| The 16 failing dev-50 questions | **None hit the cap**, in any session, answer or otherwise. |

### Limits
- **Truncation is inferred.** `finish_reason` wasn't stored at this pin, so a call at exactly 2,000 tokens is treated as truncated without direct confirmation.
- **No per-call purpose or session link.** The ledger has neither, so the extraction denominator comes from chunk counts. The time-based session attribution was wrong for rolled-back attempts, whose conversations no longer exist, so it is used only for `f8501ef5_2`, where no rollback occurred.
- **Single hit on `f8501ef5_2`.** Either that reply parsed anyway or its retry succeeded. No chunk failed either way.
- **8 background rows have no usage**, so they can't be classified.
- **Not deterministic.** `sharegpt_xxk9JDz_21` truncated on 4 of 5 attempts in Phase D. In the dev2 pilot it failed all 4 attempts at the 2,000 cap and succeeded at 4,000, with the largest reply at 2,240 tokens.

## B2. Production: not run

**Blocked by this plan's own stop rule.** This environment has no documented read-only production route and no SSH host configured. Secret files were not searched for credentials. Nothing was queried, so **the production rate is unknown**.

The owner can run this on a read-only connection. It returns aggregates only:

```sql
BEGIN READ ONLY;
SELECT count(*)                                                     AS background_calls,
       count(*) FILTER (WHERE (usage->>'output_tokens')::int >= 2000) AS calls_at_cap,
       count(DISTINCT user_id)
         FILTER (WHERE (usage->>'output_tokens')::int >= 2000)      AS users_affected,
       min(created_at)::date                                        AS earliest
FROM entitlement_reservations
WHERE workload_profile = 'background' AND usage ? 'output_tokens';
ROLLBACK;
```

- **Conversations affected can't come from this table,** because the ledger has no conversation link. The plan's conversation count isn't obtainable this way.
- **The denominator overstates extraction calls,** for the reason given in B1.
- **Rows at the new cap:** this counts any reply of 2,000 tokens or more, so after #671 deploys it also includes replies that fit under 4,000. Limit it to dates before the deploy.

## Consequences (stated, not acted on)

1. **The pinned dev-50 snapshot is not materially affected.** Every affected chunk was recovered, and no affected session is an answer session or belongs to a failing question. Truncation alone gives no reason to re-ingest dev-50.
2. **The three source-retention gaps don't look like truncation.** They need another explanation.
3. **Production backfill: no recommendation yet.** Phase D's rate is low, but production text isn't benchmark text. A failed chunk isn't checkpointed and the worker retries it; what happens at retry exhaustion is still not established. With B2's counts, a proposal could follow. Nothing is proposed for execution.

The fix in #671 addresses the observed replies. A reply over 4,000 tokens would still fail, so #670 stays open.
