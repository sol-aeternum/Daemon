# Task 8 Evidence: PROJECT_CONTEXT.md Regeneration

## Header Metadata
- **Verified-against-commit**: `3155d69fa1eb1939cf5c737018242fc119480d6c`
- **Last updated**: 2026-05-31

## Upstream Sources
- `tests/benchmark_results/doc-alignment-regeneration/truth_set.md` (Primary source for current facts)
- `docs/SOURCES_OF_TRUTH.md` (Source hierarchy and mapping)
- `docs/FEATURE_MATRIX.md` (User-visible implemented-vs-planned status)
- `MEMORY_LAYER.md` (Memory architecture authority)
- `orchestrator/config.py` (T0 config)
- `docker-compose.yml` (T0 infra)
- `migrations/` (T0 database state)

## Major Corrections
- **Migrations**: Updated count from 13 to 30; latest migration is `030_add_advisor_traces.sql`.
- **Docker Services**: Updated count from 6 to 7 (added `migrate` and `crawl4ai`).
- **Endpoints**: 
    - Confirmed `/providers` exists.
    - Confirmed both `/health` and `/status` exist with different roles.
    - Confirmed `/skills` exists.
- **Video Providers**: 
    - Removed Sora (deleted/stale).
    - Confirmed `fal` (Kling) and `xai` (Imagine) support.
    - Set `fal` as default provider.
- **Memory Layer**: 
    - Updated embedding models to Voyage 4 (large/lite).
    - Updated dedup thresholds to 0.90/0.82/0.65.
    - Linked to `MEMORY_LAYER.md` for detailed architecture.
- **Subagents**: 
    - Explicitly marked `@code` and `@reader` as **Reserved / Not Implemented**.
- **Cleanup**: 
    - Removed stale references to legacy components.

## Linter Output (check_doc_freshness.py)
```json
{
  "checked_sources": {
    "migrations": {
      "count": 30,
      "latest": "030_add_advisor_traces.sql"
    },
    "embeddings": {
      "document_model": "voyage-4-large",
      "query_model": "voyage-4-lite",
      "dimensions": 1024,
      "dedup_merge": 0.9,
      "dedup_supersede_generic": 0.82,
      "dedup_supersede_same_slot": 0.65
    },
    "providers": {
      "video_providers": [
        "fal",
        "xai"
      ],
      "provider_clients": []
    },
    "routes": {
      "routes": {
        "GET": [
          "/health",
          "/providers",
          "/api/models",
          "/models",
          "/v1/models",
          "/v1/catalog",
          "/generated-images/{filename}",
          "/generated-audio/{filename}",
          "/generated-files/{filename}",
          "/audio/token",
          "/audio/scribe-token",
          "/{conversation_id}",
          "/{memory_id}",
          "/{skill_id}",
          "/{skill_id}/download",
          "/me/settings",
          "/me/settings/presets",
          "/balance",
          "/transactions",
          "/estimate"
        ],
        "POST": [
          "/v1/tools/test",
          "/chat/completions",
          "/v1/chat/completions",
          "/tts",
          "/stt",
          "/sound-effects",
          "/chat",
          "/export",
          "/import",
          "/reembed",
          "/{memory_id}/confirm",
          "/consolidate",
          "/dream",
          "/upload",
          "/{skill_id}/pending-update",
          "/admin/sync",
          "/grant"
        ],
        "PATCH": [
          "/{conversation_id}",
          "/{memory_id}",
          "/{skill_id}/enabled",
          "/{skill_id}/autonomous-edit",
          "/me/settings"
        ],
        "DELETE": [
          "/{conversation_id}",
          "/{memory_id}",
          "/{skill_id}"
        ],
        "PUT": [
          "/{skill_id}"
        ]
      }
    }
  },
  "findings": [],
  "exceptions": [],
  "malformed_exceptions": [],
  "summary": {
    "total_findings": 0,
    "total_exceptions": 0,
    "total_malformed": 0
  }
}
```
