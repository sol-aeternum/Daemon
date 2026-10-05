# Memory embedding screen — 4 October 2026

Status: **bounded fictional evaluation performed; no private-memory route enabled**.
The persistence/status repair is source-only, independently reviewed and locally
gated; it is not deployed. Local gates passed with 4,778 backend tests and 816
frontend tests. Real PostgreSQL contention and live equivalence-judge accuracy
were not tested. These checks do not qualify a private embedding route.

**Subsequent rollout, 4 October 2026:** the owner approved merging and local
deployment of PR #447, including the later strict-judge repairs and separate
qualification recorded in `MEMORY_JUDGE_QUALIFICATION.md`. Merged commit
`15c7ead1d3551d0e69bba298b4f9ed9cb2d15189` was deployed from a clean archived
release; backend/frontend readiness, worker runtime and exact code/policy hashes
were checked, preserving the original checkout, environment and data mounts.
No migration, duplicate cleanup or reembedding ran. The earlier screen results
and reservations below are unchanged. Azure small adapter design and conditional
activation approval are recorded in [MEMORY_EMBEDDING_ADAPTER.md](MEMORY_EMBEDDING_ADAPTER.md);
that approval does not retrospectively qualify this transport screen.

## Approval and reproducibility

For issue [#445](https://github.com/sol-aeternum/Daemon/issues/445), the owner
authorized a fixed fictional comparison through the existing project OpenRouter
account, capped cumulatively at 24 embedding requests, 100,000 input tokens and
US$0.25, including rejected/uncertain attempts. This is application evaluation,
not agent inference, deployment or private-memory processing approval.

Runner: `scripts/benchmark_memory_embeddings.py`; offline safety coverage:
`tests/test_embedding_screen.py`. Nine fictional tea facts about Ari and Jules
contain paraphrases, a conflicting value, negation, a sibling preference, past
tense, conditional scope and another person's preference. Two queries are
evaluated at 1,024 dimensions to match the existing storage dimensionality.
No production data, IDs or database contents are read.

Pins: Voyage large/lite to `voyageai`, OpenAI small/large to `azure`, Gemini
Embedding 2 to `google-vertex/eu`. No fallback, hidden retries or embedding
redirects. ZDR candidates require `zdr:true`; all requests retain
`data_collection:deny`, parameter support and price constraints. Voyage was
explicitly permitted for fictional evaluation despite retained-route metadata,
but its refusal did not authorize relaxing those controls.

The operator-local durable ledger is maintained outside Git at
`~/.cache/daemon-memory-445/embedding-screen-20261004.json`. Reservations are
written and fsynced before dispatch and never released after uncertain sends.
Use that **same** ledger for any approved continuation; do not create a new
ledger to reset the allowance. Published Voyage tokenizers and OpenAI's local
tokenizer bound their inputs; Gemini reserves the entire published context limit
per input rather than estimating characters/tokens. This deliberately overstates
its possible cost. This trusted-operator ledger is not tamper-proof.

## Observed results

| Pinned model/route | Result under required controls |
| --- | --- |
| Voyage 4 large / Voyage | HTTP 404; not repeated after definitive refusal |
| Voyage 4 lite / Voyage | HTTP 404; not repeated after definitive refusal |
| OpenAI embedding 3 small / Azure | Documents and queries returned valid 1,024-dimensional vectors; provider attested Azure |
| OpenAI embedding 3 large / Azure | Documents and queries returned valid 1,024-dimensional vectors; provider attested Azure |
| Gemini Embedding 2 / EU Vertex | Document request HTTP 404; query screen not performed |

Total **9 requests**, including two Azure receipts initially rejected because the
gateway returned the documented native model name rather than the qualified
request identifier. Those attempts remain in the ledger. An independently
reviewed exact, pin-keyed response-name map repaired validation; no generic alias
matching or provider fallback was introduced. Reservations total **75,711 tokens
and US$0.01804121** (including a conservative gateway-fee allowance), not actual
billed usage. Accepted Azure requests reported 216 tokens in total; rejected and
uncertain outcomes remain fully reserved.

The HTTP 404 results establish only that these exact controlled requests did not
succeed. They do not identify the cause, prove the catalog models are absent or
authorize alternative controls/hosts.

### Quality observations, not qualification

| Diagnostic | OpenAI small | OpenAI large |
| --- | --- | --- |
| Duplicate-pair cosine range | 0.8731–0.8874 | 0.8801–0.9331 |
| Highest distinct-fact cosine against anchor | 0.8653 | 0.8941 |
| Minimum duplicate minus maximum distinct cosine | +0.0078 | −0.0140 |

Both models placed the conflicting green-tea statement ahead of the oolong anchor
for Ari's favorite-tea query, and retrieved Jules's statement first for Jules's
query. Because the fixture provides **no authoritative current-state timeline**,
the anchor-hit metric is not a proven stale/current retrieval error. Embeddings
alone cannot resolve which conflicting value is currently true. The larger model
also overlapped duplicate and distinct scores on this tiny set; a universal cosine
threshold is not justified. Differences are not a statistically established ranking.

## Provider recommendation and open admission work

**Direct Voyage remains the first candidate to test**, with a new key and verified
organization retention/training opt-out. Its documented shared large/lite space
preserves the intended asymmetric retrieval design. Its private-route eligibility
depends on the opt-out, not the key or model listing. Existing-account free-token
eligibility must not be assumed. No direct request was possible without a new key.

**Azure-hosted OpenAI embeddings through OpenRouter are a demonstrated transport
alternative**, not an admitted private-memory provider or established quality
winner. If choosing between those two solely for a further pilot, small avoids
the larger model's extra price without evidence here of a quality benefit.
Gemini remains unmeasured on the pinned route. Other model families were researched
but not trialed; cross-version public benchmark scores do not decide personal-fact
equivalence accuracy.

Before live activation: verify route-specific retention/training and hosting,
implement bounded account token-price reservation/settlement, qualify exact task
formatting and provider response behavior, evaluate a broader held-out fact and
retrieval set with temporal state, and obtain explicit provider/rollout approval.
Reembedding and existing-record reconciliation are separately authorized work;
neither this screen nor the dedup repair performs them.

## Primary public sources

- [Voyage embeddings and shared spaces](https://docs.voyageai.com/docs/embeddings)
- [Voyage opt-out and retention FAQ](https://docs.voyageai.com/docs/faq)
- [Voyage pricing](https://docs.voyageai.com/docs/pricing)
- [Voyage tokenization](https://docs.voyageai.com/docs/tokenization)
- [OpenRouter embedding contract](https://openrouter.ai/docs/api/api-reference/embeddings/submit-an-embedding-request)
- [OpenRouter privacy](https://openrouter.ai/docs/guides/privacy/data-collection)
- [OpenRouter live ZDR endpoints](https://openrouter.ai/api/v1/endpoints/zdr)
- [OpenAI small](https://developers.openai.com/api/docs/models/text-embedding-3-small)
  and [large](https://developers.openai.com/api/docs/models/text-embedding-3-large)
- [Google Embedding 2 formatting](https://ai.google.dev/gemini-api/docs/embeddings)

Live catalog/provider metadata was read on 4 October 2026. Voyage was absent from
the ZDR list and its upstream provider metadata reported retention, no training.
OpenRouter itself does not generally train on embeddings; that blanket claim from
an initial research draft was rejected. All privacy admission remains route-specific.
