# OpenRouter Voyage Embedding Compatibility Spike

> Status: option B implemented; direct/routed parity remains unverified
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
  "input_type": "document",
  "encoding_format": "float",
  "dimensions": 1024
}
```

The response is the familiar indexed `data[].embedding` shape with token usage
metadata. That response is compatible with Daemon's existing
`_parse_embedding_payload()` implementation.

OpenRouter's current embeddings request schema documents Voyage-compatible
`input_type` values. An authenticated live probe on 2026-08-09 confirmed that
`voyageai/voyage-4-large` accepted `document` and
`voyageai/voyage-4-lite` accepted `query`, both returning 1024-dimensional
vectors. The probe used fixed synthetic text and no personal memory content.

References:

- [OpenRouter embeddings API reference](https://openrouter.ai/docs/api-reference/embeddings)
- [OpenRouter API overview and headers](https://openrouter.ai/docs/api-reference/overview)
- [Voyage embeddings API reference](https://docs.voyageai.com/reference/embeddings-api)
- [Voyage contextualized chunk embeddings](https://docs.voyageai.com/docs/contextualized-chunk-embeddings)

## Compatibility finding

The request and response shapes are compatible. Direct/routed vector parity is
still **unproven** because no direct `VOYAGE_API_KEY` was available for the live
comparison. Equal model names and dimensions are not sufficient evidence that
the vectors share Daemon's calibrated storage space, so the implementation uses
the approved separate `openrouter:<model-id>` identity.

## Remaining parity verification

Run the following probe with non-sensitive fixture text before implementation:

1. Submit the same fixtures directly to Voyage and through OpenRouter, then
   compare returned dimensions, finite-value validation, and cosine similarity.
2. Repeat without `input_type`. The result must differ in the way documented by
   Voyage, proving the routed parameter is effective rather than silently
   discarded.
3. Verify batch ordering, maximum input size, `dimensions=1024`, error bodies,
   rate-limit headers, retryable status codes, and usage fields.
4. Run the existing similarity calibration corpus and retrieval/dedup regression
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

## Implemented decision

Option B is implemented as an explicit fallback provider. OpenRouter calls use
the existing `OPENROUTER_API_KEY`, native `document` / `query` input types, and a
separate storage identity. Option A remains gated on the direct/routed parity and
calibration work above; no storage identities may be collapsed before it passes.
