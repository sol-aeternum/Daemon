# OpenRouter Voyage Embedding Compatibility Spike

> Status: research complete; implementation intentionally deferred
> Date: 2026-08-09

## Decision context

OpenRouter now advertises Voyage embedding models behind its OpenAI-compatible
`POST /api/v1/embeddings` endpoint. Using that route could remove the need for a
second embedding-provider credential: Daemon could use its existing
`OPENROUTER_API_KEY` when the direct Voyage route is unavailable.

This is not a drop-in transport change for Daemon. The current memory pipeline
uses Voyage's asymmetric request parameter `input_type="document"` for writes
and `input_type="query"` for retrieval. Its storage-model identity, calibrated
dedup thresholds, and query/document compatibility all depend on that behavior.

## Documented request and response shape

OpenRouter's embeddings API uses the OpenAI-compatible envelope:

```json
{
  "model": "<OpenRouter embedding model id>",
  "input": ["text to embed"],
  "encoding_format": "float",
  "dimensions": 1024
}
```

The response is the familiar indexed `data[].embedding` shape with token usage
metadata. That response is compatible with Daemon's existing
`_parse_embedding_payload()` implementation.

OpenRouter's published embeddings request schema documents `model`, `input`,
`encoding_format`, `dimensions`, and `user`. It does **not** document Voyage's
`input_type` field. OpenRouter's provider-routing prompt transforms also do not
establish that arbitrary Voyage-only request fields are forwarded unchanged.

References:

- [OpenRouter embeddings API reference](https://openrouter.ai/docs/api-reference/embeddings)
- [OpenRouter API overview and headers](https://openrouter.ai/docs/api-reference/overview)
- [Voyage embeddings API reference](https://docs.voyageai.com/reference/embeddings-api)
- [Voyage contextualized chunk embeddings](https://docs.voyageai.com/docs/contextualized-chunk-embeddings)

## Compatibility finding

The response shape is compatible, but prompt-shape compatibility is
**unproven**. Omitting or losing `input_type` could produce vectors with
different retrieval characteristics from the direct Voyage document/query
pair. Equal model names and equal vector dimensions are not sufficient evidence
that the resulting vectors share Daemon's calibrated storage space.

Accordingly, do not wire OpenRouter Voyage as a fallback yet. In particular, do
not label OpenRouter-produced vectors with the existing direct-Voyage storage
identity until parity has been demonstrated.

## Required live verification

Run the following probe with non-sensitive fixture text before implementation:

1. Resolve the exact OpenRouter model IDs from the authenticated model catalog;
   do not infer the namespace from the display name.
2. Submit document and query requests through OpenRouter with `input_type` and
   confirm whether the endpoint accepts and forwards the field rather than
   ignoring it.
3. Submit the same fixtures directly to Voyage and through OpenRouter, then
   compare returned dimensions, finite-value validation, and cosine similarity.
4. Repeat without `input_type`. The result must differ in the way documented by
   Voyage, proving the routed parameter is effective rather than silently
   discarded.
5. Verify batch ordering, maximum input size, `dimensions=1024`, error bodies,
   rate-limit headers, retryable status codes, and usage fields.
6. Run the existing similarity calibration corpus and retrieval/dedup regression
   suite. Thresholds must not be reused if score distributions materially move.

No personal memory content should be used for this probe.

## Implementation options after verification

### A. Same storage identity

Use the existing Voyage storage identity only if direct and routed vectors show
parity and OpenRouter demonstrably forwards `input_type`. This provides seamless
outage fallback without a second stored vector space.

### B. Separate OpenRouter storage identity

Use an `openrouter:<model-id>` identity if the endpoint works but parity is not
established. This is safer, but it retains the multi-space retrieval and dedup
complexity already required by cross-provider fallback.

### C. Do not use OpenRouter Voyage

Keep direct Voyage as the sole Voyage transport if `input_type` is rejected,
ignored, or cannot be verified. An OpenAI-compatible response alone does not
satisfy the memory pipeline's asymmetric embedding contract.

## Recommended gate

Proceed with option A only after the live probe and calibration suite pass.
Otherwise require an explicit architecture decision between options B and C.
This spike makes no configuration, provider, storage, or API-contract changes.
