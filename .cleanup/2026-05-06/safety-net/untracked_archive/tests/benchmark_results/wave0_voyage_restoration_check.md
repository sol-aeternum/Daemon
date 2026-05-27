# Wave 0 — Voyage Restoration Check
**Generated:** 2026-04-24T11:33:22.014652+00:00

## Requests

### Document embedding
- Status code: 200
- Model: voyage-4-large
- Dimensions: 1024
- First 3 values: [-0.010465052, -0.040005643, -0.010465052]

### Query embedding
- Status code: 200
- Model: voyage-4-lite
- Dimensions: 1024
- First 3 values: [0.036560554, 0.011602767, -0.01259729]

## Balance / Quota Check
- Probe URL: https://api.voyageai.com/v1/account
- HTTP status: 404
- Response snippet: `{"detail":"Not Found"}`

## Verdict
- Voyage serving document embeddings: YES
- Voyage serving query embeddings: YES
- Both returned 1024-dim vectors: YES
