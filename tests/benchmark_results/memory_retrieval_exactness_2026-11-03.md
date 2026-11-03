# Memory retrieval exactness and candidate-pool audit — 2026-11-03

Diagnostic only; not a candidate or campaign authorization. Zero provider calls and cost.
No restore, admission, DDL, ANALYZE or session SET. Settings use SET LOCAL within read-only repeatable-read transactions.
The three connection recipes are sampled serially. The campaign recipe is reproduced without invoking campaign restore/admission.
Each connection records current_setting('ivfflat.probes', true) before loading pgvector: None means the setting was not yet registered or set. SELECT NULL::vector then loads the backend-local library without a write or DDL; the subsequent SHOW records effective probes. Decisions and SET LOCAL restores use that effective integer, not the pre-load placeholder.
Fingerprint equality inside one snapshot does not detect concurrent commits outside that snapshot.
Git: `495314bea94033cf0f6c1b61ab058a764f7c33ef`. Raw SHA256: `da94df3b9584d8982667daa399df3b5e59bcaf70f14af022611f221627b64d85`.
Fingerprint before: `5cb6ab14f2eb6bf11bb5e4c5ed164a08b70d0fb92155918d006be4f630b874f3`; after: `5cb6ab14f2eb6bf11bb5e4c5ed164a08b70d0fb92155918d006be4f630b874f3`.
Historical question_as_of and RECENCY_REFERENCE_TIME; logged vectors selected by exact question/model match, exactly one in the pinned pilot A1 window.
Window: `2026-11-02T09:55:58+00:00` through `2026-11-02T10:34:15+00:00` (inclusive).
Pool override: INITIAL_VECTOR_CANDIDATES, shared by vector and BM25. There is no separate BM25 constant. Source constants remain pool=10 and MAX_MEMORY_ITEMS=5.
Baseline pilot rendered-block hash matches: 50/50; failure aborts before the rest of the grid.
An empty baseline retrieval is fatal even if frozen memories or summaries leave a nonempty rendered block; legitimate empty non-baseline retrievals are recorded and counted per configuration.

## Input pins

```json
{
  "cases": "c54798d04a3136921b86841043eb22c4d3fb27c897e8eff68325ec173bbb11f2",
  "dataset": "6612e3a03c79b92bab2f1750d4661908e3dc37359715b7fb6b62c6f758bddb34",
  "evidence_index": "3fe6e32efc329719728ade67fc677dfe7195688f8afdb3b147b3cc31ebac3043",
  "ingest_checkpoint": "fbf6910d16f7a533c78b06c49f723ab579ce1b87f57eb01730975e0957470478",
  "ingest_manifest": "536436c12bfb2fcb40c9c128389060f3a4f65ad4d3eb3dfa013a9478a97c94b6",
  "phase3b_manifest": "616f767ff1432442e425e49c2542a56dc8c499744399836e12cb149ccb03bda5",
  "pilot_manifest": "348f1970eea4c49ed8f0c61b8dbba95563d6d0949347c695aadf507c46284704",
  "pilot_results": "1136c9af6011b767b9a1b9eda8e92e3c06e5e93ff35906e5c77ced0e7cf68105"
}
```

## T0 — execution facts

### connection_probes

```json
{
  "campaign": 1,
  "harness": 1,
  "sweep": 1
}
```

### connection_probe_details

```json
{
  "campaign": {
    "probes": 1,
    "probes_before_load": null
  },
  "harness": {
    "probes": 1,
    "probes_before_load": null
  },
  "sweep": {
    "probes": 1,
    "probes_before_load": null
  }
}
```

### connection_recipes

```json
{
  "campaign": {
    "command_timeout": 30,
    "init": null,
    "max_inactive_connection_lifetime": 0,
    "max_queries": 1000000,
    "max_size": 10,
    "min_size": 10,
    "recipe_only": true,
    "timeout": 10
  },
  "harness": {
    "concurrency": 1,
    "init": null,
    "max_size": 10,
    "min_size": 2
  },
  "sweep": {
    "default_transaction_read_only": "on",
    "statement_timeout": 30000,
    "timeout": 10
  }
}
```

### execution_facts

```json
{
  "db_role_settings": [],
  "dev_user_counts": {
    "count": 50,
    "max": 617,
    "median": 510.0,
    "min": 393,
    "per_user": {
      "0784bdf4-224b-51db-bb12-4fb12ed84a3b": 485,
      "0ff1313a-b3c1-5eda-869a-7330875ce603": 486,
      "10dd955e-d9dc-50a5-8bac-b9026f3370fc": 495,
      "151f67c4-a121-5698-bd1e-3a1c4797dc29": 617,
      "1f781c32-c7e9-52e3-8997-0f2a599bd681": 477,
      "26c7b8b9-50f8-5e11-b248-8c76a58f7c8c": 503,
      "2867b2b4-a8de-5d81-894c-ba6e8c58400b": 538,
      "2a80b29c-943e-5669-b950-fff1c11ad444": 535,
      "31e59ff2-257f-5dec-a8b7-cf772175e726": 472,
      "347dceae-52c7-5e6f-8513-b5731cc8e9e9": 493,
      "3ad0f840-853a-50ec-9d50-1884e8170559": 528,
      "3d2baada-b22b-5313-b6a1-029666037062": 481,
      "3d3c1826-8ad4-5400-9d7e-23a6f673a644": 462,
      "3e564893-cd64-51d0-bfae-684157b20c3b": 499,
      "3fa0d7c1-4a03-5e5e-ae23-c8b3968e587b": 486,
      "4007bac8-f334-5f3d-9220-aa192da1ecde": 583,
      "4ac82d9a-2ec5-5176-ade2-ae1400c4f75a": 511,
      "57a641ad-d87d-5f9e-9fe6-3e0dac4287e4": 557,
      "5b63b8b9-2ad7-5ba9-8522-d17f310ebbea": 470,
      "68b48a16-3188-52b1-b646-3f6b4b4fed99": 498,
      "697ca198-9ae7-525b-a3c2-da8b74391252": 509,
      "69b783fe-c712-5215-a9eb-690c824d5506": 473,
      "745398f6-2189-5f89-ab47-16f16c9456e3": 552,
      "79621d51-7c07-541c-9c32-fc4671ba5926": 504,
      "7b55cf6b-94fd-5de7-84e1-f0df9a558a2b": 527,
      "82d0b8c7-c2bb-57f4-b24f-d1223584a17d": 538,
      "86d4217c-bb9e-585f-b342-753d3f4c778b": 541,
      "87253dc0-abfb-5830-8e97-294c59cdd163": 528,
      "880ad6b6-3a22-58ee-a3e4-59b22b3ff4c0": 409,
      "8b17c4ca-dbfd-5e36-9d88-b2cb64798829": 403,
      "8ba80cb2-2350-5ff4-8f7a-dfc80aeb5c15": 465,
      "9672b8bb-5295-56a1-8ad9-70d98bb5bccb": 511,
      "9ac96328-424c-552e-a9f4-4b09ed31010f": 492,
      "a4f02a12-da5c-5081-a5d4-3d5e67368853": 568,
      "b23bec83-8f6a-5a7c-87d2-ea7199584c69": 393,
      "b3d4a250-a690-5b09-8cae-d26fe80bb56e": 456,
      "b61da641-10c7-5adf-85e3-42ce2d8df935": 515,
      "c2cf4db3-86dc-511d-91fc-39b0e4e3eb5c": 546,
      "c8dbbbef-ea1f-5067-a34a-aaf8d07fa142": 502,
      "c9de19f6-f779-5f85-82b4-9506576bca53": 570,
      "cc0c333b-a299-5416-84c5-4217fc527053": 518,
      "cd88f8a7-35dc-515f-b171-1c57f188cd40": 566,
      "cdb3442d-a979-5382-8064-8cfd5ac1ad23": 485,
      "dea5ac12-1ae7-5ed0-890a-61477e82b09b": 527,
      "e0b614f6-a209-5add-ac86-c5886b368536": 553,
      "e2191f52-7509-5fb3-9f99-122d25270a51": 529,
      "e40efc3e-00b8-545e-84f8-3098e87c4efd": 550,
      "e4d76eae-d5b8-535c-b557-f3f6cb532554": 460,
      "f0a8bc09-04e9-501d-b299-c12cac8b9fcf": 527,
      "fe49a610-649d-5a2c-bb1e-a5452c82a994": 542
    }
  },
  "indexes": [
    {
      "definition_sha256": "111c5b671c140fe0df4a85359831b70e40e3cff111bf6d85c3f8b9f6835de490",
      "name": "idx_memories_active"
    },
    {
      "definition_sha256": "6191be63b1ce9c10b9663122a309f31964347871fa8d3e0973acdeeae4d4dea7",
      "name": "idx_memories_active_content_hash_unique"
    },
    {
      "definition_sha256": "c17bec623ee108c12ecb17be93450a56a969d2858dccc003cea738ba4b4cebbf",
      "name": "idx_memories_embedding_ivfflat"
    },
    {
      "definition_sha256": "6d8cc83523106eaa59f3cb6fbab9a8a3bff6c595efdb2d3d5e14e1f70e7b3792",
      "name": "idx_memories_slot_history"
    },
    {
      "definition_sha256": "ebd090e0fa539b22d3764e9749dfa6e3e4b2dc34d046ed513089576bde46896e",
      "name": "idx_memories_user_id"
    },
    {
      "definition_sha256": "3d0ac2d426cc0437c43b18e1d7e4e623671381c5b22473d1a7bb1f64c52bd8f2",
      "name": "idx_memories_user_status"
    }
  ],
  "pg_settings": [
    {
      "reset": 1,
      "setting": 1,
      "source": "default"
    }
  ],
  "pgvector_version": "0.8.1",
  "rows_by_embedding_model": [
    {
      "count": 25435,
      "model": "openrouter:azure:openai/text-embedding-3-small:1024"
    }
  ]
}
```

### plans

```json
[
  {
    "exact_non_ivfflat_plan": true,
    "indexes": [
      "idx_memories_user_id"
    ],
    "ivfflat_used": false,
    "kind": "vector",
    "mode": "harness",
    "question_id": "0bb5a684",
    "shape": [
      {
        "index": null,
        "node": "Limit",
        "path": "0"
      },
      {
        "index": null,
        "node": "Sort",
        "path": "0.0"
      },
      {
        "index": null,
        "node": "Bitmap_Heap_Scan",
        "path": "0.0.0"
      },
      {
        "index": "idx_memories_user_id",
        "node": "Bitmap_Index_Scan",
        "path": "0.0.0.0"
      }
    ],
    "shape_sha256": "41d7719af45cbc45556b37561a161c444876da7088bfa2c8dc99be57fdfe7a59"
  },
  {
    "exact_non_ivfflat_plan": true,
    "indexes": [
      "idx_memories_user_id"
    ],
    "ivfflat_used": false,
    "kind": "vector",
    "mode": "probes10",
    "question_id": "0bb5a684",
    "shape": [
      {
        "index": null,
        "node": "Limit",
        "path": "0"
      },
      {
        "index": null,
        "node": "Sort",
        "path": "0.0"
      },
      {
        "index": null,
        "node": "Bitmap_Heap_Scan",
        "path": "0.0.0"
      },
      {
        "index": "idx_memories_user_id",
        "node": "Bitmap_Index_Scan",
        "path": "0.0.0.0"
      }
    ],
    "shape_sha256": "41d7719af45cbc45556b37561a161c444876da7088bfa2c8dc99be57fdfe7a59"
  },
  {
    "exact_non_ivfflat_plan": true,
    "indexes": [
      "idx_memories_content_tsv"
    ],
    "ivfflat_used": false,
    "kind": "bm25",
    "mode": "harness",
    "question_id": "0bb5a684",
    "shape": [
      {
        "index": null,
        "node": "Limit",
        "path": "0"
      },
      {
        "index": null,
        "node": "Sort",
        "path": "0.0"
      },
      {
        "index": null,
        "node": "Bitmap_Heap_Scan",
        "path": "0.0.0"
      },
      {
        "index": "idx_memories_content_tsv",
        "node": "Bitmap_Index_Scan",
        "path": "0.0.0.0"
      }
    ],
    "shape_sha256": "06455d6e78233d712eb3953856d0e0876c4eef0da58ddc70217904f9938937e4"
  },
  {
    "exact_non_ivfflat_plan": true,
    "indexes": [
      "idx_memories_content_tsv"
    ],
    "ivfflat_used": false,
    "kind": "bm25",
    "mode": "probes10",
    "question_id": "0bb5a684",
    "shape": [
      {
        "index": null,
        "node": "Limit",
        "path": "0"
      },
      {
        "index": null,
        "node": "Sort",
        "path": "0.0"
      },
      {
        "index": null,
        "node": "Bitmap_Heap_Scan",
        "path": "0.0.0"
      },
      {
        "index": "idx_memories_content_tsv",
        "node": "Bitmap_Index_Scan",
        "path": "0.0.0.0"
      }
    ],
    "shape_sha256": "06455d6e78233d712eb3953856d0e0876c4eef0da58ddc70217904f9938937e4"
  },
  {
    "exact_non_ivfflat_plan": true,
    "indexes": [
      "idx_memories_user_id"
    ],
    "ivfflat_used": false,
    "kind": "vector",
    "mode": "harness",
    "question_id": "0a34ad58",
    "shape": [
      {
        "index": null,
        "node": "Limit",
        "path": "0"
      },
      {
        "index": null,
        "node": "Sort",
        "path": "0.0"
      },
      {
        "index": null,
        "node": "Bitmap_Heap_Scan",
        "path": "0.0.0"
      },
      {
        "index": "idx_memories_user_id",
        "node": "Bitmap_Index_Scan",
        "path": "0.0.0.0"
      }
    ],
    "shape_sha256": "41d7719af45cbc45556b37561a161c444876da7088bfa2c8dc99be57fdfe7a59"
  },
  {
    "exact_non_ivfflat_plan": true,
    "indexes": [
      "idx_memories_user_id"
    ],
    "ivfflat_used": false,
    "kind": "vector",
    "mode": "probes10",
    "question_id": "0a34ad58",
    "shape": [
      {
        "index": null,
        "node": "Limit",
        "path": "0"
      },
      {
        "index": null,
        "node": "Sort",
        "path": "0.0"
      },
      {
        "index": null,
        "node": "Bitmap_Heap_Scan",
        "path": "0.0.0"
      },
      {
        "index": "idx_memories_user_id",
        "node": "Bitmap_Index_Scan",
        "path": "0.0.0.0"
      }
    ],
    "shape_sha256": "41d7719af45cbc45556b37561a161c444876da7088bfa2c8dc99be57fdfe7a59"
  },
  {
    "exact_non_ivfflat_plan": true,
    "indexes": [
      "idx_memories_content_tsv"
    ],
    "ivfflat_used": false,
    "kind": "bm25",
    "mode": "harness",
    "question_id": "0a34ad58",
    "shape": [
      {
        "index": null,
        "node": "Limit",
        "path": "0"
      },
      {
        "index": null,
        "node": "Sort",
        "path": "0.0"
      },
      {
        "index": null,
        "node": "Bitmap_Heap_Scan",
        "path": "0.0.0"
      },
      {
        "index": "idx_memories_content_tsv",
        "node": "Bitmap_Index_Scan",
        "path": "0.0.0.0"
      }
    ],
    "shape_sha256": "06455d6e78233d712eb3953856d0e0876c4eef0da58ddc70217904f9938937e4"
  },
  {
    "exact_non_ivfflat_plan": true,
    "indexes": [
      "idx_memories_content_tsv"
    ],
    "ivfflat_used": false,
    "kind": "bm25",
    "mode": "probes10",
    "question_id": "0a34ad58",
    "shape": [
      {
        "index": null,
        "node": "Limit",
        "path": "0"
      },
      {
        "index": null,
        "node": "Sort",
        "path": "0.0"
      },
      {
        "index": null,
        "node": "Bitmap_Heap_Scan",
        "path": "0.0.0"
      },
      {
        "index": "idx_memories_content_tsv",
        "node": "Bitmap_Index_Scan",
        "path": "0.0.0.0"
      }
    ],
    "shape_sha256": "06455d6e78233d712eb3953856d0e0876c4eef0da58ddc70217904f9938937e4"
  },
  {
    "exact_non_ivfflat_plan": true,
    "indexes": [
      "idx_memories_user_id"
    ],
    "ivfflat_used": false,
    "kind": "vector",
    "mode": "harness",
    "question_id": "b86304ba",
    "shape": [
      {
        "index": null,
        "node": "Limit",
        "path": "0"
      },
      {
        "index": null,
        "node": "Sort",
        "path": "0.0"
      },
      {
        "index": null,
        "node": "Bitmap_Heap_Scan",
        "path": "0.0.0"
      },
      {
        "index": "idx_memories_user_id",
        "node": "Bitmap_Index_Scan",
        "path": "0.0.0.0"
      }
    ],
    "shape_sha256": "41d7719af45cbc45556b37561a161c444876da7088bfa2c8dc99be57fdfe7a59"
  },
  {
    "exact_non_ivfflat_plan": true,
    "indexes": [
      "idx_memories_user_id"
    ],
    "ivfflat_used": false,
    "kind": "vector",
    "mode": "probes10",
    "question_id": "b86304ba",
    "shape": [
      {
        "index": null,
        "node": "Limit",
        "path": "0"
      },
      {
        "index": null,
        "node": "Sort",
        "path": "0.0"
      },
      {
        "index": null,
        "node": "Bitmap_Heap_Scan",
        "path": "0.0.0"
      },
      {
        "index": "idx_memories_user_id",
        "node": "Bitmap_Index_Scan",
        "path": "0.0.0.0"
      }
    ],
    "shape_sha256": "41d7719af45cbc45556b37561a161c444876da7088bfa2c8dc99be57fdfe7a59"
  },
  {
    "exact_non_ivfflat_plan": true,
    "indexes": [
      "idx_memories_content_tsv"
    ],
    "ivfflat_used": false,
    "kind": "bm25",
    "mode": "harness",
    "question_id": "b86304ba",
    "shape": [
      {
        "index": null,
        "node": "Limit",
        "path": "0"
      },
      {
        "index": null,
        "node": "Sort",
        "path": "0.0"
      },
      {
        "index": null,
        "node": "Bitmap_Heap_Scan",
        "path": "0.0.0"
      },
      {
        "index": "idx_memories_content_tsv",
        "node": "Bitmap_Index_Scan",
        "path": "0.0.0.0"
      }
    ],
    "shape_sha256": "06455d6e78233d712eb3953856d0e0876c4eef0da58ddc70217904f9938937e4"
  },
  {
    "exact_non_ivfflat_plan": true,
    "indexes": [
      "idx_memories_content_tsv"
    ],
    "ivfflat_used": false,
    "kind": "bm25",
    "mode": "probes10",
    "question_id": "b86304ba",
    "shape": [
      {
        "index": null,
        "node": "Limit",
        "path": "0"
      },
      {
        "index": null,
        "node": "Sort",
        "path": "0.0"
      },
      {
        "index": null,
        "node": "Bitmap_Heap_Scan",
        "path": "0.0.0"
      },
      {
        "index": "idx_memories_content_tsv",
        "node": "Bitmap_Index_Scan",
        "path": "0.0.0.0"
      }
    ],
    "shape_sha256": "06455d6e78233d712eb3953856d0e0876c4eef0da58ddc70217904f9938937e4"
  }
]
```

### plan_shape_differences

```json
[]
```

## T1 — exact versus approximate ranking

Exactness retains the captured production eligibility SQL and disables index, index-only and bitmap scans locally. Plans are checked for absence of IVFFlat.
LIMIT NULL exhausts eligible rows without rewriting filters. Exact ranks retain the SQL's unspecified ordering among cosine ties. Probes100 checks compare ID/cosine mappings at LIMIT100; boundary ties may conservatively stop the audit.
Exact/probes100 verifications: 5. Recall denominator is min(k, exact eligible rows); an empty reference has recall 1. LIMIT10 recall@50 cannot exceed 10/50 when at least 50 rows qualify.

### Aggregate recall

```json
{
  "harness_10": {
    "recall10": 1.0,
    "recall50": 0.2
  },
  "harness_100": {
    "recall10": 1.0,
    "recall50": 1.0
  },
  "probes100_10": {
    "recall10": 1.0,
    "recall50": 0.2
  },
  "probes100_100": {
    "recall10": 1.0,
    "recall50": 1.0
  },
  "probes10_10": {
    "recall10": 1.0,
    "recall50": 0.2
  },
  "probes10_100": {
    "recall10": 1.0,
    "recall50": 1.0
  }
}
```

| Question | Mode | LIMIT | Recall@10 | Recall@50 |
|---|---|---|---|---|
| b86304ba | harness | 10 | 1.0 | 0.2 |
| b86304ba | harness | 100 | 1.0 | 1.0 |
| b86304ba | probes10 | 10 | 1.0 | 0.2 |
| b86304ba | probes10 | 100 | 1.0 | 1.0 |
| b86304ba | probes100 | 10 | 1.0 | 0.2 |
| b86304ba | probes100 | 100 | 1.0 | 1.0 |
| 8550ddae | harness | 10 | 1.0 | 0.2 |
| 8550ddae | harness | 100 | 1.0 | 1.0 |
| 8550ddae | probes10 | 10 | 1.0 | 0.2 |
| 8550ddae | probes10 | 100 | 1.0 | 1.0 |
| 8550ddae | probes100 | 10 | 1.0 | 0.2 |
| 8550ddae | probes100 | 100 | 1.0 | 1.0 |
| 86f00804 | harness | 10 | 1.0 | 0.2 |
| 86f00804 | harness | 100 | 1.0 | 1.0 |
| 86f00804 | probes10 | 10 | 1.0 | 0.2 |
| 86f00804 | probes10 | 100 | 1.0 | 1.0 |
| 86f00804 | probes100 | 10 | 1.0 | 0.2 |
| 86f00804 | probes100 | 100 | 1.0 | 1.0 |
| 19b5f2b3 | harness | 10 | 1.0 | 0.2 |
| 19b5f2b3 | harness | 100 | 1.0 | 1.0 |
| 19b5f2b3 | probes10 | 10 | 1.0 | 0.2 |
| 19b5f2b3 | probes10 | 100 | 1.0 | 1.0 |
| 19b5f2b3 | probes100 | 10 | 1.0 | 0.2 |
| 19b5f2b3 | probes100 | 100 | 1.0 | 1.0 |
| caf9ead2 | harness | 10 | 1.0 | 0.2 |
| caf9ead2 | harness | 100 | 1.0 | 1.0 |
| caf9ead2 | probes10 | 10 | 1.0 | 0.2 |
| caf9ead2 | probes10 | 100 | 1.0 | 1.0 |
| caf9ead2 | probes100 | 10 | 1.0 | 0.2 |
| caf9ead2 | probes100 | 100 | 1.0 | 1.0 |
| fca762bc | harness | 10 | 1.0 | 0.2 |
| fca762bc | harness | 100 | 1.0 | 1.0 |
| fca762bc | probes10 | 10 | 1.0 | 0.2 |
| fca762bc | probes10 | 100 | 1.0 | 1.0 |
| fca762bc | probes100 | 10 | 1.0 | 0.2 |
| fca762bc | probes100 | 100 | 1.0 | 1.0 |
| 4388e9dd | harness | 10 | 1.0 | 0.2 |
| 4388e9dd | harness | 100 | 1.0 | 1.0 |
| 4388e9dd | probes10 | 10 | 1.0 | 0.2 |
| 4388e9dd | probes10 | 100 | 1.0 | 1.0 |
| 4388e9dd | probes100 | 10 | 1.0 | 0.2 |
| 4388e9dd | probes100 | 100 | 1.0 | 1.0 |
| e8a79c70 | harness | 10 | 1.0 | 0.2 |
| e8a79c70 | harness | 100 | 1.0 | 1.0 |
| e8a79c70 | probes10 | 10 | 1.0 | 0.2 |
| e8a79c70 | probes10 | 100 | 1.0 | 1.0 |
| e8a79c70 | probes100 | 10 | 1.0 | 0.2 |
| e8a79c70 | probes100 | 100 | 1.0 | 1.0 |
| d596882b | harness | 10 | 1.0 | 0.2 |
| d596882b | harness | 100 | 1.0 | 1.0 |
| d596882b | probes10 | 10 | 1.0 | 0.2 |
| d596882b | probes10 | 100 | 1.0 | 1.0 |
| d596882b | probes100 | 10 | 1.0 | 0.2 |
| d596882b | probes100 | 100 | 1.0 | 1.0 |
| 71a3fd6b | harness | 10 | 1.0 | 0.2 |
| 71a3fd6b | harness | 100 | 1.0 | 1.0 |
| 71a3fd6b | probes10 | 10 | 1.0 | 0.2 |
| 71a3fd6b | probes10 | 100 | 1.0 | 1.0 |
| 71a3fd6b | probes100 | 10 | 1.0 | 0.2 |
| 71a3fd6b | probes100 | 100 | 1.0 | 1.0 |
| 28dc39ac | harness | 10 | 1.0 | 0.2 |
| 28dc39ac | harness | 100 | 1.0 | 1.0 |
| 28dc39ac | probes10 | 10 | 1.0 | 0.2 |
| 28dc39ac | probes10 | 100 | 1.0 | 1.0 |
| 28dc39ac | probes100 | 10 | 1.0 | 0.2 |
| 28dc39ac | probes100 | 100 | 1.0 | 1.0 |
| ba358f49 | harness | 10 | 1.0 | 0.2 |
| ba358f49 | harness | 100 | 1.0 | 1.0 |
| ba358f49 | probes10 | 10 | 1.0 | 0.2 |
| ba358f49 | probes10 | 100 | 1.0 | 1.0 |
| ba358f49 | probes100 | 10 | 1.0 | 0.2 |
| ba358f49 | probes100 | 100 | 1.0 | 1.0 |
| 6cb6f249 | harness | 10 | 1.0 | 0.2 |
| 6cb6f249 | harness | 100 | 1.0 | 1.0 |
| 6cb6f249 | probes10 | 10 | 1.0 | 0.2 |
| 6cb6f249 | probes10 | 100 | 1.0 | 1.0 |
| 6cb6f249 | probes100 | 10 | 1.0 | 0.2 |
| 6cb6f249 | probes100 | 100 | 1.0 | 1.0 |
| 2318644b | harness | 10 | 1.0 | 0.2 |
| 2318644b | harness | 100 | 1.0 | 1.0 |
| 2318644b | probes10 | 10 | 1.0 | 0.2 |
| 2318644b | probes10 | 100 | 1.0 | 1.0 |
| 2318644b | probes100 | 10 | 1.0 | 0.2 |
| 2318644b | probes100 | 100 | 1.0 | 1.0 |
| 1192316e | harness | 10 | 1.0 | 0.2 |
| 1192316e | harness | 100 | 1.0 | 1.0 |
| 1192316e | probes10 | 10 | 1.0 | 0.2 |
| 1192316e | probes10 | 100 | 1.0 | 1.0 |
| 1192316e | probes100 | 10 | 1.0 | 0.2 |
| 1192316e | probes100 | 100 | 1.0 | 1.0 |
| 8c18457d | harness | 10 | 1.0 | 0.2 |
| 8c18457d | harness | 100 | 1.0 | 1.0 |
| 8c18457d | probes10 | 10 | 1.0 | 0.2 |
| 8c18457d | probes10 | 100 | 1.0 | 1.0 |
| 8c18457d | probes100 | 10 | 1.0 | 0.2 |
| 8c18457d | probes100 | 100 | 1.0 | 1.0 |
| gpt4_4edbafa2 | harness | 10 | 1.0 | 0.2 |
| gpt4_4edbafa2 | harness | 100 | 1.0 | 1.0 |
| gpt4_4edbafa2 | probes10 | 10 | 1.0 | 0.2 |
| gpt4_4edbafa2 | probes10 | 100 | 1.0 | 1.0 |
| gpt4_4edbafa2 | probes100 | 10 | 1.0 | 0.2 |
| gpt4_4edbafa2 | probes100 | 100 | 1.0 | 1.0 |
| 6613b389 | harness | 10 | 1.0 | 0.2 |
| 6613b389 | harness | 100 | 1.0 | 1.0 |
| 6613b389 | probes10 | 10 | 1.0 | 0.2 |
| 6613b389 | probes10 | 100 | 1.0 | 1.0 |
| 6613b389 | probes100 | 10 | 1.0 | 0.2 |
| 6613b389 | probes100 | 100 | 1.0 | 1.0 |
| gpt4_af6db32f | harness | 10 | 1.0 | 0.2 |
| gpt4_af6db32f | harness | 100 | 1.0 | 1.0 |
| gpt4_af6db32f | probes10 | 10 | 1.0 | 0.2 |
| gpt4_af6db32f | probes10 | 100 | 1.0 | 1.0 |
| gpt4_af6db32f | probes100 | 10 | 1.0 | 0.2 |
| gpt4_af6db32f | probes100 | 100 | 1.0 | 1.0 |
| gpt4_b0863698 | harness | 10 | 1.0 | 0.2 |
| gpt4_b0863698 | harness | 100 | 1.0 | 1.0 |
| gpt4_b0863698 | probes10 | 10 | 1.0 | 0.2 |
| gpt4_b0863698 | probes10 | 100 | 1.0 | 1.0 |
| gpt4_b0863698 | probes100 | 10 | 1.0 | 0.2 |
| gpt4_b0863698 | probes100 | 100 | 1.0 | 1.0 |
| 852ce960 | harness | 10 | 1.0 | 0.2 |
| 852ce960 | harness | 100 | 1.0 | 1.0 |
| 852ce960 | probes10 | 10 | 1.0 | 0.2 |
| 852ce960 | probes10 | 100 | 1.0 | 1.0 |
| 852ce960 | probes100 | 10 | 1.0 | 0.2 |
| 852ce960 | probes100 | 100 | 1.0 | 1.0 |
| 6a1eabeb | harness | 10 | 1.0 | 0.2 |
| 6a1eabeb | harness | 100 | 1.0 | 1.0 |
| 6a1eabeb | probes10 | 10 | 1.0 | 0.2 |
| 6a1eabeb | probes10 | 100 | 1.0 | 1.0 |
| 6a1eabeb | probes100 | 10 | 1.0 | 0.2 |
| 6a1eabeb | probes100 | 100 | 1.0 | 1.0 |
| 5831f84d | harness | 10 | 1.0 | 0.2 |
| 5831f84d | harness | 100 | 1.0 | 1.0 |
| 5831f84d | probes10 | 10 | 1.0 | 0.2 |
| 5831f84d | probes10 | 100 | 1.0 | 1.0 |
| 5831f84d | probes100 | 10 | 1.0 | 0.2 |
| 5831f84d | probes100 | 100 | 1.0 | 1.0 |
| f685340e | harness | 10 | 1.0 | 0.2 |
| f685340e | harness | 100 | 1.0 | 1.0 |
| f685340e | probes10 | 10 | 1.0 | 0.2 |
| f685340e | probes10 | 100 | 1.0 | 1.0 |
| f685340e | probes100 | 10 | 1.0 | 0.2 |
| f685340e | probes100 | 100 | 1.0 | 1.0 |
| 184da446 | harness | 10 | 1.0 | 0.2 |
| 184da446 | harness | 100 | 1.0 | 1.0 |
| 184da446 | probes10 | 10 | 1.0 | 0.2 |
| 184da446 | probes10 | 100 | 1.0 | 1.0 |
| 184da446 | probes100 | 10 | 1.0 | 0.2 |
| 184da446 | probes100 | 100 | 1.0 | 1.0 |
| f685340e_abs | harness | 10 | 1.0 | 0.2 |
| f685340e_abs | harness | 100 | 1.0 | 1.0 |
| f685340e_abs | probes10 | 10 | 1.0 | 0.2 |
| f685340e_abs | probes10 | 100 | 1.0 | 1.0 |
| f685340e_abs | probes100 | 10 | 1.0 | 0.2 |
| f685340e_abs | probes100 | 100 | 1.0 | 1.0 |
| 982b5123_abs | harness | 10 | 1.0 | 0.2 |
| 982b5123_abs | harness | 100 | 1.0 | 1.0 |
| 982b5123_abs | probes10 | 10 | 1.0 | 0.2 |
| 982b5123_abs | probes10 | 100 | 1.0 | 1.0 |
| 982b5123_abs | probes100 | 10 | 1.0 | 0.2 |
| 982b5123_abs | probes100 | 100 | 1.0 | 1.0 |
| gpt4_93159ced_abs | harness | 10 | 1.0 | 0.2 |
| gpt4_93159ced_abs | harness | 100 | 1.0 | 1.0 |
| gpt4_93159ced_abs | probes10 | 10 | 1.0 | 0.2 |
| gpt4_93159ced_abs | probes10 | 100 | 1.0 | 1.0 |
| gpt4_93159ced_abs | probes100 | 10 | 1.0 | 0.2 |
| gpt4_93159ced_abs | probes100 | 100 | 1.0 | 1.0 |
| 80ec1f4f_abs | harness | 10 | 1.0 | 0.2 |
| 80ec1f4f_abs | harness | 100 | 1.0 | 1.0 |
| 80ec1f4f_abs | probes10 | 10 | 1.0 | 0.2 |
| 80ec1f4f_abs | probes10 | 100 | 1.0 | 1.0 |
| 80ec1f4f_abs | probes100 | 10 | 1.0 | 0.2 |
| 80ec1f4f_abs | probes100 | 100 | 1.0 | 1.0 |
| gpt4_372c3eed_abs | harness | 10 | 1.0 | 0.2 |
| gpt4_372c3eed_abs | harness | 100 | 1.0 | 1.0 |
| gpt4_372c3eed_abs | probes10 | 10 | 1.0 | 0.2 |
| gpt4_372c3eed_abs | probes10 | 100 | 1.0 | 1.0 |
| gpt4_372c3eed_abs | probes100 | 10 | 1.0 | 0.2 |
| gpt4_372c3eed_abs | probes100 | 100 | 1.0 | 1.0 |
| ad7109d1 | harness | 10 | 1.0 | 0.2 |
| ad7109d1 | harness | 100 | 1.0 | 1.0 |
| ad7109d1 | probes10 | 10 | 1.0 | 0.2 |
| ad7109d1 | probes10 | 100 | 1.0 | 1.0 |
| ad7109d1 | probes100 | 10 | 1.0 | 0.2 |
| ad7109d1 | probes100 | 100 | 1.0 | 1.0 |
| 2bf43736 | harness | 10 | 1.0 | 0.2 |
| 2bf43736 | harness | 100 | 1.0 | 1.0 |
| 2bf43736 | probes10 | 10 | 1.0 | 0.2 |
| 2bf43736 | probes10 | 100 | 1.0 | 1.0 |
| 2bf43736 | probes100 | 10 | 1.0 | 0.2 |
| 2bf43736 | probes100 | 100 | 1.0 | 1.0 |
| 75f70248 | harness | 10 | 1.0 | 0.2 |
| 75f70248 | harness | 100 | 1.0 | 1.0 |
| 75f70248 | probes10 | 10 | 1.0 | 0.2 |
| 75f70248 | probes10 | 100 | 1.0 | 1.0 |
| 75f70248 | probes100 | 10 | 1.0 | 0.2 |
| 75f70248 | probes100 | 100 | 1.0 | 1.0 |
| a3332713 | harness | 10 | 1.0 | 0.2 |
| a3332713 | harness | 100 | 1.0 | 1.0 |
| a3332713 | probes10 | 10 | 1.0 | 0.2 |
| a3332713 | probes10 | 100 | 1.0 | 1.0 |
| a3332713 | probes100 | 10 | 1.0 | 0.2 |
| a3332713 | probes100 | 100 | 1.0 | 1.0 |
| 0bb5a684 | harness | 10 | 1.0 | 0.2 |
| 0bb5a684 | harness | 100 | 1.0 | 1.0 |
| 0bb5a684 | probes10 | 10 | 1.0 | 0.2 |
| 0bb5a684 | probes10 | 100 | 1.0 | 1.0 |
| 0bb5a684 | probes100 | 10 | 1.0 | 0.2 |
| 0bb5a684 | probes100 | 100 | 1.0 | 1.0 |
| 0977f2af | harness | 10 | 1.0 | 0.2 |
| 0977f2af | harness | 100 | 1.0 | 1.0 |
| 0977f2af | probes10 | 10 | 1.0 | 0.2 |
| 0977f2af | probes10 | 100 | 1.0 | 1.0 |
| 0977f2af | probes100 | 10 | 1.0 | 0.2 |
| 0977f2af | probes100 | 100 | 1.0 | 1.0 |
| c5e8278d | harness | 10 | 1.0 | 0.2 |
| c5e8278d | harness | 100 | 1.0 | 1.0 |
| c5e8278d | probes10 | 10 | 1.0 | 0.2 |
| c5e8278d | probes10 | 100 | 1.0 | 1.0 |
| c5e8278d | probes100 | 10 | 1.0 | 0.2 |
| c5e8278d | probes100 | 100 | 1.0 | 1.0 |
| c4f10528 | harness | 10 | 1.0 | 0.2 |
| c4f10528 | harness | 100 | 1.0 | 1.0 |
| c4f10528 | probes10 | 10 | 1.0 | 0.2 |
| c4f10528 | probes10 | 100 | 1.0 | 1.0 |
| c4f10528 | probes100 | 10 | 1.0 | 0.2 |
| c4f10528 | probes100 | 100 | 1.0 | 1.0 |
| 0a34ad58 | harness | 10 | 1.0 | 0.2 |
| 0a34ad58 | harness | 100 | 1.0 | 1.0 |
| 0a34ad58 | probes10 | 10 | 1.0 | 0.2 |
| 0a34ad58 | probes10 | 100 | 1.0 | 1.0 |
| 0a34ad58 | probes100 | 10 | 1.0 | 0.2 |
| 0a34ad58 | probes100 | 100 | 1.0 | 1.0 |
| 92a0aa75 | harness | 10 | 1.0 | 0.2 |
| 92a0aa75 | harness | 100 | 1.0 | 1.0 |
| 92a0aa75 | probes10 | 10 | 1.0 | 0.2 |
| 92a0aa75 | probes10 | 100 | 1.0 | 1.0 |
| 92a0aa75 | probes100 | 10 | 1.0 | 0.2 |
| 92a0aa75 | probes100 | 100 | 1.0 | 1.0 |
| gpt4_65aabe59 | harness | 10 | 1.0 | 0.2 |
| gpt4_65aabe59 | harness | 100 | 1.0 | 1.0 |
| gpt4_65aabe59 | probes10 | 10 | 1.0 | 0.2 |
| gpt4_65aabe59 | probes10 | 100 | 1.0 | 1.0 |
| gpt4_65aabe59 | probes100 | 10 | 1.0 | 0.2 |
| gpt4_65aabe59 | probes100 | 100 | 1.0 | 1.0 |
| 3ba21379 | harness | 10 | 1.0 | 0.2 |
| 3ba21379 | harness | 100 | 1.0 | 1.0 |
| 3ba21379 | probes10 | 10 | 1.0 | 0.2 |
| 3ba21379 | probes10 | 100 | 1.0 | 1.0 |
| 3ba21379 | probes100 | 10 | 1.0 | 0.2 |
| 3ba21379 | probes100 | 100 | 1.0 | 1.0 |
| 545bd2b5 | harness | 10 | 1.0 | 0.2 |
| 545bd2b5 | harness | 100 | 1.0 | 1.0 |
| 545bd2b5 | probes10 | 10 | 1.0 | 0.2 |
| 545bd2b5 | probes10 | 100 | 1.0 | 1.0 |
| 545bd2b5 | probes100 | 10 | 1.0 | 0.2 |
| 545bd2b5 | probes100 | 100 | 1.0 | 1.0 |
| 1de5cff2 | harness | 10 | 1.0 | 0.2 |
| 1de5cff2 | harness | 100 | 1.0 | 1.0 |
| 1de5cff2 | probes10 | 10 | 1.0 | 0.2 |
| 1de5cff2 | probes10 | 100 | 1.0 | 1.0 |
| 1de5cff2 | probes100 | 10 | 1.0 | 0.2 |
| 1de5cff2 | probes100 | 100 | 1.0 | 1.0 |
| 09d032c9 | harness | 10 | 1.0 | 0.2 |
| 09d032c9 | harness | 100 | 1.0 | 1.0 |
| 09d032c9 | probes10 | 10 | 1.0 | 0.2 |
| 09d032c9 | probes10 | 100 | 1.0 | 1.0 |
| 09d032c9 | probes100 | 10 | 1.0 | 0.2 |
| 09d032c9 | probes100 | 100 | 1.0 | 1.0 |
| cc06de0d | harness | 10 | 1.0 | 0.2 |
| cc06de0d | harness | 100 | 1.0 | 1.0 |
| cc06de0d | probes10 | 10 | 1.0 | 0.2 |
| cc06de0d | probes10 | 100 | 1.0 | 1.0 |
| cc06de0d | probes100 | 10 | 1.0 | 0.2 |
| cc06de0d | probes100 | 100 | 1.0 | 1.0 |
| gpt4_93159ced | harness | 10 | 1.0 | 0.2 |
| gpt4_93159ced | harness | 100 | 1.0 | 1.0 |
| gpt4_93159ced | probes10 | 10 | 1.0 | 0.2 |
| gpt4_93159ced | probes10 | 100 | 1.0 | 1.0 |
| gpt4_93159ced | probes100 | 10 | 1.0 | 0.2 |
| gpt4_93159ced | probes100 | 100 | 1.0 | 1.0 |
| 59524333 | harness | 10 | 1.0 | 0.2 |
| 59524333 | harness | 100 | 1.0 | 1.0 |
| 59524333 | probes10 | 10 | 1.0 | 0.2 |
| 59524333 | probes10 | 100 | 1.0 | 1.0 |
| 59524333 | probes100 | 10 | 1.0 | 0.2 |
| 59524333 | probes100 | 100 | 1.0 | 1.0 |
| 25e5aa4f | harness | 10 | 1.0 | 0.2 |
| 25e5aa4f | harness | 100 | 1.0 | 1.0 |
| 25e5aa4f | probes10 | 10 | 1.0 | 0.2 |
| 25e5aa4f | probes10 | 100 | 1.0 | 1.0 |
| 25e5aa4f | probes100 | 10 | 1.0 | 0.2 |
| 25e5aa4f | probes100 | 100 | 1.0 | 1.0 |
| 7a8d0b71 | harness | 10 | 1.0 | 0.2 |
| 7a8d0b71 | harness | 100 | 1.0 | 1.0 |
| 7a8d0b71 | probes10 | 10 | 1.0 | 0.2 |
| 7a8d0b71 | probes10 | 100 | 1.0 | 1.0 |
| 7a8d0b71 | probes100 | 10 | 1.0 | 0.2 |
| 7a8d0b71 | probes100 | 100 | 1.0 | 1.0 |

### Named target ranks

| Question | Memory | Exact cosine rank | BM25 rank |
|---|---|---|---|
| 28dc39ac | 06729686-3261-40ed-8ff6-df507bf435c3 | 1 | None |
| 28dc39ac | 0807f4f5-3e15-43bf-b776-857b726ad7e2 | 18 | None |
| 28dc39ac | 11672c8b-34a5-4b99-9fc5-b92c5fc17bc5 | 14 | None |
| 28dc39ac | 8925ddbd-0184-4573-b2c5-a7f91bf5e677 | 2 | None |
| 28dc39ac | 99db9c59-462a-4f38-bc8b-7c4261a8437a | 9 | None |
| 28dc39ac | f98e98cb-d6bb-4a7d-a0f4-a7c415b4570f | 6 | None |
| 1192316e | 2b34b3b2-589a-43eb-908e-3e2bedf033e5 | 1 | None |
| 1192316e | b2239692-8040-4005-abaa-79588eedf2b1 | 6 | None |
| 6613b389 | 21ac11f9-9689-4571-af6f-3c5c7c858260 | 1 | None |
| 6613b389 | 55eab6cb-17c0-4201-be69-9b3b70700029 | 15 | None |
| 6613b389 | 94c365d4-d4cb-47c1-8661-9b9ff73a09f6 | 17 | None |
| 0bb5a684 | 0770505d-0b96-4908-af07-2e692c034b9b | 16 | None |
| 0bb5a684 | 3c418fde-f8c6-4383-8513-9df463894cd9 | 24 | None |
| 0bb5a684 | e65f7073-1d85-4bc9-bfc5-2fe9e54dacc8 | 2 | None |
| 0a34ad58 | 07c6d26d-8bd3-438b-aecc-6d8451f0ec5d | 19 | None |
| 0a34ad58 | 3bb5fed7-48de-4aa3-9cbc-5ec9d1fb9d42 | 14 | None |
| 0a34ad58 | ddabe83c-c72c-4e28-b6b2-287102a9c442 | 33 | None |

## T2 — production gather/render grid

Configurations: 24; duplicate probes10 collapsed: False.
Coverage and COVERAGE_REGRESSION causes reuse exposure.py unchanged. Gold-session coverage is a proxy, not answer-fact exposure. Abstention distractors reuse the sweep rule and are warnings, not decision vetoes.
Distractors are measured only for _abs and abstention-overreach questions; unmeasured payloads stay None, not zero. Every aggregate and candidate delta uses only that fixed measured cohort, with its question count reported. Baseline added rows and added-row plausible support are zero by definition (relative to itself); own plausible support independently measures all of the baseline's visible memory rows using the same lexical proxy.

| Configuration | Strict /5 | Pre-budget /5 | Coverage losses | Target drops | Total drops | Tokens | Empty retrievals | Measured questions | Added distractors | Added plausible support | Own plausible support |
|---|---|---|---|---|---|---|---|---|---|---|---|
| pool100_exact_k10 | 1 | 1 | 0 | 0 | 0 | 13459 | 0 | 5 | 25 | 2 | 5 |
| pool100_exact_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |
| pool100_harness_k10 | 1 | 1 | 0 | 0 | 0 | 13459 | 0 | 5 | 25 | 2 | 5 |
| pool100_harness_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |
| pool100_probes10_k10 | 1 | 1 | 0 | 0 | 0 | 13459 | 0 | 5 | 25 | 2 | 5 |
| pool100_probes10_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |
| pool10_exact_k10 | 1 | 1 | 0 | 0 | 0 | 13422 | 0 | 5 | 25 | 2 | 5 |
| pool10_exact_k5 | 0 | 0 | 0 | 0 | 0 | 7430 | 0 | 5 | 0 | 0 | 3 |
| pool10_harness_k10 | 1 | 1 | 0 | 0 | 0 | 13422 | 0 | 5 | 25 | 2 | 5 |
| pool10_harness_k5 | 0 | 0 | 0 | 0 | 0 | 7430 | 0 | 5 | 0 | 0 | 3 |
| pool10_probes10_k10 | 1 | 1 | 0 | 0 | 0 | 13422 | 0 | 5 | 25 | 2 | 5 |
| pool10_probes10_k5 | 0 | 0 | 0 | 0 | 0 | 7430 | 0 | 5 | 0 | 0 | 3 |
| pool25_exact_k10 | 1 | 1 | 0 | 0 | 0 | 13474 | 0 | 5 | 25 | 2 | 5 |
| pool25_exact_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |
| pool25_harness_k10 | 1 | 1 | 0 | 0 | 0 | 13474 | 0 | 5 | 25 | 2 | 5 |
| pool25_harness_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |
| pool25_probes10_k10 | 1 | 1 | 0 | 0 | 0 | 13474 | 0 | 5 | 25 | 2 | 5 |
| pool25_probes10_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |
| pool50_exact_k10 | 1 | 1 | 0 | 0 | 0 | 13459 | 0 | 5 | 25 | 2 | 5 |
| pool50_exact_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |
| pool50_harness_k10 | 1 | 1 | 0 | 0 | 0 | 13459 | 0 | 5 | 25 | 2 | 5 |
| pool50_harness_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |
| pool50_probes10_k10 | 1 | 1 | 0 | 0 | 0 | 13459 | 0 | 5 | 25 | 2 | 5 |
| pool50_probes10_k5 | 0 | 0 | 0 | 0 | 0 | 7431 | 0 | 5 | 0 | 0 | 3 |

### Coverage-loss attribution

```json
{
  "pool100_exact_k10": {},
  "pool100_exact_k5": {},
  "pool100_harness_k10": {},
  "pool100_harness_k5": {},
  "pool100_probes10_k10": {},
  "pool100_probes10_k5": {},
  "pool10_exact_k10": {},
  "pool10_exact_k5": {},
  "pool10_harness_k10": {},
  "pool10_harness_k5": {},
  "pool10_probes10_k10": {},
  "pool10_probes10_k5": {},
  "pool25_exact_k10": {},
  "pool25_exact_k5": {},
  "pool25_harness_k10": {},
  "pool25_harness_k5": {},
  "pool25_probes10_k10": {},
  "pool25_probes10_k5": {},
  "pool50_exact_k10": {},
  "pool50_exact_k5": {},
  "pool50_harness_k10": {},
  "pool50_harness_k5": {},
  "pool50_probes10_k10": {},
  "pool50_probes10_k5": {}
}
```

## T3 — exact-search pool stability

The fixed union is the memories returned across exact-search pools/widths. Components come directly from scored_candidates_out, never a reimplementation. A union member absent from a pool has no observed score there: full-union order is explicitly unknown, not imputed. Pairwise common-set order changes and changed components are reported independently; correlated component changes are not causal proof.

```json
[
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "104763a7-e07e-4b70-82e5-8841cb48f0c2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.19933556712421563,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2726711363941985
        },
        "2847168e-ee42-49c2-b741-af85ef0f69aa": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.23210767524204645,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2742153528682787
        },
        "2ab2368b-6313-4039-a54d-e8e71c15423a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24738016548346647,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.31656033568762076
        },
        "33c18c16-d263-4b7c-85bb-e6e356844c0d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23021067769140546,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2904213506144393
        },
        "35dd1270-a797-482f-ac48-2369d5e04625": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.23346841422279568,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31693682844559135
        },
        "4a22c733-f049-4c22-8fdf-a167a348cd0d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.30277868496186533,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43555736515535903
        },
        "7190d334-14d6-4e00-bcb7-a3f993d94bc2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.23043801848526085,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3108760369705217
        },
        "c4756641-44a1-4180-be27-11b0b1a44ec4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2600901826353968,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3441803662244679
        },
        "c8ff18fd-2460-45fc-95a4-df7c27ccbf8d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2302925656626919,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.29858513561691824
        },
        "db61b417-38db-4ea3-9c07-458f80e252a4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.22806530051677676,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.27213059769569337
        }
      },
      "100": {
        "104763a7-e07e-4b70-82e5-8841cb48f0c2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.19933556712421563,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2726711363941985
        },
        "2847168e-ee42-49c2-b741-af85ef0f69aa": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.23210767524204645,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2742153528682787
        },
        "2ab2368b-6313-4039-a54d-e8e71c15423a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24738016548346647,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.31656033568762076
        },
        "33c18c16-d263-4b7c-85bb-e6e356844c0d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23021067769140546,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2904213506144393
        },
        "35dd1270-a797-482f-ac48-2369d5e04625": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.23346841422279568,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31693682844559135
        },
        "4a22c733-f049-4c22-8fdf-a167a348cd0d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.30277868496186533,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43555736515535903
        },
        "7190d334-14d6-4e00-bcb7-a3f993d94bc2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.23043801848526085,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3108760369705217
        },
        "93588248-5883-4317-873a-f9a750139f70": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.21999529017261615,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2518905827055762
        },
        "c4756641-44a1-4180-be27-11b0b1a44ec4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2600901826353968,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3441803662244679
        },
        "c8ff18fd-2460-45fc-95a4-df7c27ccbf8d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2302925656626919,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.29858513561691824
        },
        "db61b417-38db-4ea3-9c07-458f80e252a4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.22806530051677676,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.27213059769569337
        }
      },
      "25": {
        "104763a7-e07e-4b70-82e5-8841cb48f0c2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.19933556712421563,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2726711363941985
        },
        "2847168e-ee42-49c2-b741-af85ef0f69aa": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.23210767524204645,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2742153528682787
        },
        "2ab2368b-6313-4039-a54d-e8e71c15423a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24738016548346647,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.31656033568762076
        },
        "33c18c16-d263-4b7c-85bb-e6e356844c0d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23021067769140546,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2904213506144393
        },
        "35dd1270-a797-482f-ac48-2369d5e04625": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.23346841422279568,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31693682844559135
        },
        "4a22c733-f049-4c22-8fdf-a167a348cd0d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.30277868496186533,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43555736515535903
        },
        "7190d334-14d6-4e00-bcb7-a3f993d94bc2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.23043801848526085,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3108760369705217
        },
        "93588248-5883-4317-873a-f9a750139f70": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.21999529017261615,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2518905827055762
        },
        "c4756641-44a1-4180-be27-11b0b1a44ec4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2600901826353968,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3441803662244679
        },
        "c8ff18fd-2460-45fc-95a4-df7c27ccbf8d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2302925656626919,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.29858513561691824
        },
        "db61b417-38db-4ea3-9c07-458f80e252a4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.22806530051677676,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.27213059769569337
        }
      },
      "50": {
        "104763a7-e07e-4b70-82e5-8841cb48f0c2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.19933556712421563,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2726711363941985
        },
        "2847168e-ee42-49c2-b741-af85ef0f69aa": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.23210767524204645,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2742153528682787
        },
        "2ab2368b-6313-4039-a54d-e8e71c15423a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24738016548346647,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.31656033568762076
        },
        "33c18c16-d263-4b7c-85bb-e6e356844c0d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23021067769140546,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2904213506144393
        },
        "35dd1270-a797-482f-ac48-2369d5e04625": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.23346841422279568,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31693682844559135
        },
        "4a22c733-f049-4c22-8fdf-a167a348cd0d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.30277868496186533,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43555736515535903
        },
        "7190d334-14d6-4e00-bcb7-a3f993d94bc2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.23043801848526085,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3108760369705217
        },
        "93588248-5883-4317-873a-f9a750139f70": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.21999529017261615,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2518905827055762
        },
        "c4756641-44a1-4180-be27-11b0b1a44ec4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2600901826353968,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3441803662244679
        },
        "c8ff18fd-2460-45fc-95a4-df7c27ccbf8d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2302925656626919,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.29858513561691824
        },
        "db61b417-38db-4ea3-9c07-458f80e252a4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.22806530051677676,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.27213059769569337
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "93588248-5883-4317-873a-f9a750139f70"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "b86304ba",
    "union_ids": [
      "104763a7-e07e-4b70-82e5-8841cb48f0c2",
      "2847168e-ee42-49c2-b741-af85ef0f69aa",
      "2ab2368b-6313-4039-a54d-e8e71c15423a",
      "33c18c16-d263-4b7c-85bb-e6e356844c0d",
      "35dd1270-a797-482f-ac48-2369d5e04625",
      "4a22c733-f049-4c22-8fdf-a167a348cd0d",
      "7190d334-14d6-4e00-bcb7-a3f993d94bc2",
      "93588248-5883-4317-873a-f9a750139f70",
      "c4756641-44a1-4180-be27-11b0b1a44ec4",
      "c8ff18fd-2460-45fc-95a4-df7c27ccbf8d",
      "db61b417-38db-4ea3-9c07-458f80e252a4"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "2f5e8d06-4a56-416b-ae1a-827a30a2b124": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.3443230641744741,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5286461259647625
        },
        "3cbc59b5-a18e-4369-a8c5-d3fe5c721414": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37006162546427185,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5619232556492315
        },
        "3fa89f7b-7a1a-4008-8143-cfc0fff9df71": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.33024907067802184,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49049813658767205
        },
        "44abbc7c-b791-48b1-a60f-55694382e7c4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3318503096833916,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.48946062031092064
        },
        "5120eb80-00fb-4bd3-94b2-99936b4138c2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.34725692491466764,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5202738507734728
        },
        "5c4bed7c-2e01-4e9f-a62e-3088f361622a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3249209273570701,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49684185042260587
        },
        "698f683b-e66f-459b-999d-8e7a249ff504": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36134147682066453,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5444829583620169
        },
        "87fda430-600f-41cd-b2b3-c5ad49ae60b4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.39408054955077054,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6121610964789367
        },
        "9695cc10-2197-43d5-8228-301d9a7a03d5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3446241992734319,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5272484028383982
        },
        "b20a9bf9-6da7-48b4-a04b-d5409e56bf76": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3638779934010243,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.565755991093583
        }
      },
      "100": {
        "2f5e8d06-4a56-416b-ae1a-827a30a2b124": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.3443230641744741,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5286461259647625
        },
        "35a767d3-ea77-4800-b2b3-dbee5a71666e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.32935825824260534,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48871651171683905
        },
        "3cbc59b5-a18e-4369-a8c5-d3fe5c721414": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37006162546427185,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5619232556492315
        },
        "3fa89f7b-7a1a-4008-8143-cfc0fff9df71": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.33024907067802184,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49049813658767205
        },
        "44abbc7c-b791-48b1-a60f-55694382e7c4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3318503096833916,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.48946062031092064
        },
        "5120eb80-00fb-4bd3-94b2-99936b4138c2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.34725692491466764,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5202738507734728
        },
        "5c4bed7c-2e01-4e9f-a62e-3088f361622a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3249209273570701,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49684185042260587
        },
        "698f683b-e66f-459b-999d-8e7a249ff504": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36134147682066453,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5444829583620169
        },
        "87fda430-600f-41cd-b2b3-c5ad49ae60b4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.39408054955077054,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6121610964789367
        },
        "9695cc10-2197-43d5-8228-301d9a7a03d5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3446241992734319,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5272484028383982
        },
        "b20a9bf9-6da7-48b4-a04b-d5409e56bf76": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3638779934010243,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.565755991093583
        }
      },
      "25": {
        "2f5e8d06-4a56-416b-ae1a-827a30a2b124": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.3443230641744741,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5286461259647625
        },
        "35a767d3-ea77-4800-b2b3-dbee5a71666e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.32935825824260534,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48871651171683905
        },
        "3cbc59b5-a18e-4369-a8c5-d3fe5c721414": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37006162546427185,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5619232556492315
        },
        "3fa89f7b-7a1a-4008-8143-cfc0fff9df71": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.33024907067802184,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49049813658767205
        },
        "44abbc7c-b791-48b1-a60f-55694382e7c4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3318503096833916,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.48946062031092064
        },
        "5120eb80-00fb-4bd3-94b2-99936b4138c2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.34725692491466764,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5202738507734728
        },
        "5c4bed7c-2e01-4e9f-a62e-3088f361622a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3249209273570701,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49684185042260587
        },
        "698f683b-e66f-459b-999d-8e7a249ff504": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36134147682066453,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5444829583620169
        },
        "87fda430-600f-41cd-b2b3-c5ad49ae60b4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.39408054955077054,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6121610964789367
        },
        "9695cc10-2197-43d5-8228-301d9a7a03d5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3446241992734319,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5272484028383982
        },
        "b20a9bf9-6da7-48b4-a04b-d5409e56bf76": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3638779934010243,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.565755991093583
        }
      },
      "50": {
        "2f5e8d06-4a56-416b-ae1a-827a30a2b124": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.3443230641744741,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5286461259647625
        },
        "35a767d3-ea77-4800-b2b3-dbee5a71666e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.32935825824260534,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48871651171683905
        },
        "3cbc59b5-a18e-4369-a8c5-d3fe5c721414": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37006162546427185,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5619232556492315
        },
        "3fa89f7b-7a1a-4008-8143-cfc0fff9df71": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.33024907067802184,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49049813658767205
        },
        "44abbc7c-b791-48b1-a60f-55694382e7c4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3318503096833916,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.48946062031092064
        },
        "5120eb80-00fb-4bd3-94b2-99936b4138c2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.34725692491466764,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5202738507734728
        },
        "5c4bed7c-2e01-4e9f-a62e-3088f361622a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3249209273570701,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49684185042260587
        },
        "698f683b-e66f-459b-999d-8e7a249ff504": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36134147682066453,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5444829583620169
        },
        "87fda430-600f-41cd-b2b3-c5ad49ae60b4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.39408054955077054,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6121610964789367
        },
        "9695cc10-2197-43d5-8228-301d9a7a03d5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3446241992734319,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5272484028383982
        },
        "b20a9bf9-6da7-48b4-a04b-d5409e56bf76": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3638779934010243,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.565755991093583
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "35a767d3-ea77-4800-b2b3-dbee5a71666e"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "8550ddae",
    "union_ids": [
      "2f5e8d06-4a56-416b-ae1a-827a30a2b124",
      "35a767d3-ea77-4800-b2b3-dbee5a71666e",
      "3cbc59b5-a18e-4369-a8c5-d3fe5c721414",
      "3fa89f7b-7a1a-4008-8143-cfc0fff9df71",
      "44abbc7c-b791-48b1-a60f-55694382e7c4",
      "5120eb80-00fb-4bd3-94b2-99936b4138c2",
      "5c4bed7c-2e01-4e9f-a62e-3088f361622a",
      "698f683b-e66f-459b-999d-8e7a249ff504",
      "87fda430-600f-41cd-b2b3-c5ad49ae60b4",
      "9695cc10-2197-43d5-8228-301d9a7a03d5",
      "b20a9bf9-6da7-48b4-a04b-d5409e56bf76"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "08be6645-1fc9-495d-bea0-09ceb70662f5": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2840642190098044,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3942484348653309
        },
        "1c1e5e36-4f54-4004-8464-4bf5a0171d7b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3143154911587638,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4630309793134535
        },
        "20506f3e-71e4-4e6e-8802-778763dc459f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.281801826755737,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.38144365020699245
        },
        "3c13d6dc-0321-4df9-bb76-1ad830f318d0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.26171329496203377,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3578265869199935
        },
        "496cef7c-3514-4f55-91e3-44b82e2a55e9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2605775134237205,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34691502779157846
        },
        "685d91ea-f3b4-4b23-b5aa-9ea3814299c2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2769641112394392,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37392822724724994
        },
        "a4dd3bc0-6459-4248-bf94-de94f8cdb0bb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.25077890548064596,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3589978052964664
        },
        "be3c11bc-1acc-4b93-901d-bba6c731c12b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.26466384585627667,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3453276883746932
        },
        "c65bb8a7-ce6c-4e85-9cbd-7720cd36d9f2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2575288854039277,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35305777509938985
        },
        "e934e639-8a54-4382-967f-126a2f844149": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.27795547148117855,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.387910940458962
        }
      },
      "100": {
        "08be6645-1fc9-495d-bea0-09ceb70662f5": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2840642190098044,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3942484348653309
        },
        "1c1e5e36-4f54-4004-8464-4bf5a0171d7b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3143154911587638,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4630309793134535
        },
        "20506f3e-71e4-4e6e-8802-778763dc459f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.281801826755737,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.38144365020699245
        },
        "3c13d6dc-0321-4df9-bb76-1ad830f318d0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.26171329496203377,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3578265869199935
        },
        "496cef7c-3514-4f55-91e3-44b82e2a55e9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2605775134237205,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34691502779157846
        },
        "685d91ea-f3b4-4b23-b5aa-9ea3814299c2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2769641112394392,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37392822724724994
        },
        "a4dd3bc0-6459-4248-bf94-de94f8cdb0bb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.25077890548064596,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3589978052964664
        },
        "be3c11bc-1acc-4b93-901d-bba6c731c12b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.26466384585627667,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3453276883746932
        },
        "c65bb8a7-ce6c-4e85-9cbd-7720cd36d9f2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2575288854039277,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35305777509938985
        },
        "e934e639-8a54-4382-967f-126a2f844149": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.27795547148117855,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.387910940458962
        }
      },
      "25": {
        "08be6645-1fc9-495d-bea0-09ceb70662f5": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2840642190098044,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3942484348653309
        },
        "1c1e5e36-4f54-4004-8464-4bf5a0171d7b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3143154911587638,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4630309793134535
        },
        "20506f3e-71e4-4e6e-8802-778763dc459f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.281801826755737,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.38144365020699245
        },
        "3c13d6dc-0321-4df9-bb76-1ad830f318d0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.26171329496203377,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3578265869199935
        },
        "496cef7c-3514-4f55-91e3-44b82e2a55e9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2605775134237205,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34691502779157846
        },
        "685d91ea-f3b4-4b23-b5aa-9ea3814299c2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2769641112394392,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37392822724724994
        },
        "a4dd3bc0-6459-4248-bf94-de94f8cdb0bb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.25077890548064596,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3589978052964664
        },
        "be3c11bc-1acc-4b93-901d-bba6c731c12b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.26466384585627667,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3453276883746932
        },
        "c65bb8a7-ce6c-4e85-9cbd-7720cd36d9f2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2575288854039277,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35305777509938985
        },
        "e934e639-8a54-4382-967f-126a2f844149": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.27795547148117855,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.387910940458962
        }
      },
      "50": {
        "08be6645-1fc9-495d-bea0-09ceb70662f5": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2840642190098044,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3942484348653309
        },
        "1c1e5e36-4f54-4004-8464-4bf5a0171d7b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3143154911587638,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4630309793134535
        },
        "20506f3e-71e4-4e6e-8802-778763dc459f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.281801826755737,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.38144365020699245
        },
        "3c13d6dc-0321-4df9-bb76-1ad830f318d0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.26171329496203377,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3578265869199935
        },
        "496cef7c-3514-4f55-91e3-44b82e2a55e9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2605775134237205,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34691502779157846
        },
        "685d91ea-f3b4-4b23-b5aa-9ea3814299c2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2769641112394392,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37392822724724994
        },
        "a4dd3bc0-6459-4248-bf94-de94f8cdb0bb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.25077890548064596,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3589978052964664
        },
        "be3c11bc-1acc-4b93-901d-bba6c731c12b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.26466384585627667,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3453276883746932
        },
        "c65bb8a7-ce6c-4e85-9cbd-7720cd36d9f2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2575288854039277,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35305777509938985
        },
        "e934e639-8a54-4382-967f-126a2f844149": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.27795547148117855,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.387910940458962
        }
      }
    },
    "fixed_union_order_changed": false,
    "fixed_union_order_observable": true,
    "missing_from_pool": {
      "10": [],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "86f00804",
    "union_ids": [
      "08be6645-1fc9-495d-bea0-09ceb70662f5",
      "1c1e5e36-4f54-4004-8464-4bf5a0171d7b",
      "20506f3e-71e4-4e6e-8802-778763dc459f",
      "3c13d6dc-0321-4df9-bb76-1ad830f318d0",
      "496cef7c-3514-4f55-91e3-44b82e2a55e9",
      "685d91ea-f3b4-4b23-b5aa-9ea3814299c2",
      "a4dd3bc0-6459-4248-bf94-de94f8cdb0bb",
      "be3c11bc-1acc-4b93-901d-bba6c731c12b",
      "c65bb8a7-ce6c-4e85-9cbd-7720cd36d9f2",
      "e934e639-8a54-4382-967f-126a2f844149"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "0fab6be1-b2ab-40d7-88e4-14fb37860f5e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2718080320820877,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35961606082631525
        },
        "412e2b47-415e-4867-967b-8112b9317f9b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.20469699084190113,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24439398168380222
        },
        "a275a794-e27a-45f3-be5a-6a2d125ce257": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.34538311403461325,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5067662247313663
        },
        "b4f54119-eec3-43e1-994b-a8a721bac4ba": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.19000101260920688,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23700203046362245
        },
        "ba1560a8-ad18-47cb-bd1c-1be87a5bb711": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3087436890986519,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43748738296567535
        },
        "d0741fa9-374d-45d2-9222-466b0b73a6e2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.338027170473846,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49605434571606355
        },
        "d61e0e5f-de96-4a31-9099-c054f51c1e55": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.29459570085412035,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.41919139693986907
        },
        "f078a7a6-73a5-4aa0-bfe7-3a4a43a972fd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2863086647058054,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39261733417998235
        },
        "f2577691-0d26-46e6-90a8-72be06ecb04c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.27456475726334,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4061295197718887
        },
        "fba0702d-1d09-4ac9-b231-37c2abc340ae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8399999737739563,
          "final_score": 0.2232967751743312,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2785935555938711
        }
      },
      "100": {
        "0fab6be1-b2ab-40d7-88e4-14fb37860f5e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2718080320820877,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35961606082631525
        },
        "412e2b47-415e-4867-967b-8112b9317f9b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.20469699084190113,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24439398168380222
        },
        "a275a794-e27a-45f3-be5a-6a2d125ce257": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.34538311403461325,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5067662247313663
        },
        "b4f54119-eec3-43e1-994b-a8a721bac4ba": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.19000101260920688,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23700203046362245
        },
        "ba1560a8-ad18-47cb-bd1c-1be87a5bb711": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3087436890986519,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43748738296567535
        },
        "d0741fa9-374d-45d2-9222-466b0b73a6e2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.338027170473846,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49605434571606355
        },
        "d61e0e5f-de96-4a31-9099-c054f51c1e55": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.29459570085412035,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.41919139693986907
        },
        "f06cfe64-a0f0-4152-a52b-3eedba29f745": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2020526078423181,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2237052172581988
        },
        "f078a7a6-73a5-4aa0-bfe7-3a4a43a972fd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2863086647058054,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39261733417998235
        },
        "f2577691-0d26-46e6-90a8-72be06ecb04c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.27456475726334,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4061295197718887
        },
        "fba0702d-1d09-4ac9-b231-37c2abc340ae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8399999737739563,
          "final_score": 0.2232967751743312,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2785935555938711
        }
      },
      "25": {
        "0fab6be1-b2ab-40d7-88e4-14fb37860f5e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2718080320820877,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35961606082631525
        },
        "412e2b47-415e-4867-967b-8112b9317f9b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.20469699084190113,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24439398168380222
        },
        "a275a794-e27a-45f3-be5a-6a2d125ce257": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.34538311403461325,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5067662247313663
        },
        "b4f54119-eec3-43e1-994b-a8a721bac4ba": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.19000101260920688,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23700203046362245
        },
        "ba1560a8-ad18-47cb-bd1c-1be87a5bb711": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3087436890986519,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43748738296567535
        },
        "d0741fa9-374d-45d2-9222-466b0b73a6e2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.338027170473846,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49605434571606355
        },
        "d61e0e5f-de96-4a31-9099-c054f51c1e55": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.29459570085412035,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.41919139693986907
        },
        "f06cfe64-a0f0-4152-a52b-3eedba29f745": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2020526078423181,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2237052172581988
        },
        "f078a7a6-73a5-4aa0-bfe7-3a4a43a972fd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2863086647058054,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39261733417998235
        },
        "f2577691-0d26-46e6-90a8-72be06ecb04c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.27456475726334,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4061295197718887
        },
        "fba0702d-1d09-4ac9-b231-37c2abc340ae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8399999737739563,
          "final_score": 0.2232967751743312,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2785935555938711
        }
      },
      "50": {
        "0fab6be1-b2ab-40d7-88e4-14fb37860f5e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2718080320820877,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35961606082631525
        },
        "412e2b47-415e-4867-967b-8112b9317f9b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.20469699084190113,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24439398168380222
        },
        "a275a794-e27a-45f3-be5a-6a2d125ce257": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.34538311403461325,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5067662247313663
        },
        "b4f54119-eec3-43e1-994b-a8a721bac4ba": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.19000101260920688,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23700203046362245
        },
        "ba1560a8-ad18-47cb-bd1c-1be87a5bb711": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3087436890986519,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43748738296567535
        },
        "d0741fa9-374d-45d2-9222-466b0b73a6e2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.338027170473846,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49605434571606355
        },
        "d61e0e5f-de96-4a31-9099-c054f51c1e55": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.29459570085412035,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.41919139693986907
        },
        "f06cfe64-a0f0-4152-a52b-3eedba29f745": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2020526078423181,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2237052172581988
        },
        "f078a7a6-73a5-4aa0-bfe7-3a4a43a972fd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2863086647058054,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39261733417998235
        },
        "f2577691-0d26-46e6-90a8-72be06ecb04c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.27456475726334,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4061295197718887
        },
        "fba0702d-1d09-4ac9-b231-37c2abc340ae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8399999737739563,
          "final_score": 0.2232967751743312,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2785935555938711
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "f06cfe64-a0f0-4152-a52b-3eedba29f745"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "19b5f2b3",
    "union_ids": [
      "0fab6be1-b2ab-40d7-88e4-14fb37860f5e",
      "412e2b47-415e-4867-967b-8112b9317f9b",
      "a275a794-e27a-45f3-be5a-6a2d125ce257",
      "b4f54119-eec3-43e1-994b-a8a721bac4ba",
      "ba1560a8-ad18-47cb-bd1c-1be87a5bb711",
      "d0741fa9-374d-45d2-9222-466b0b73a6e2",
      "d61e0e5f-de96-4a31-9099-c054f51c1e55",
      "f06cfe64-a0f0-4152-a52b-3eedba29f745",
      "f078a7a6-73a5-4aa0-bfe7-3a4a43a972fd",
      "f2577691-0d26-46e6-90a8-72be06ecb04c",
      "fba0702d-1d09-4ac9-b231-37c2abc340ae"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "08a2414a-2871-4f82-8ebb-840101ad9492": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.20138327949736468,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24436655985303624
        },
        "379d8770-2516-46d9-a64e-231d1f030138": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.1932077126499157,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22441542959136584
        },
        "40f13ff9-74ae-49f0-8ed3-afcd5a834c9e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.34438538679372405,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.523170770583374
        },
        "44f90524-ea0f-4289-a400-c055b85bdbe4": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2788738652923095,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.38764773509073014
        },
        "719dc2fe-4021-477f-baac-f067f65aad4f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.19136817410067258,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.21273634343297354
        },
        "828acef0-d1e3-4ef7-bfa8-ac7b5c41c212": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21239126018187537,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2447825251321223
        },
        "9f0a33e8-d0bc-49ed-9b4e-08e78dd9598e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.1970977688202631,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2141955424088977
        },
        "bbe15d1c-755f-4467-8e6a-901da760f575": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.28840574292716553,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4148114901458655
        },
        "d305edb3-9a17-4f0d-9760-117a98a0634d": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.35357962157190315,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5370592476499174
        },
        "de024395-0d0d-4efd-96e7-7fc94aaec6b5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.4044583717894482,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6469167478704309
        }
      },
      "100": {
        "04b0682a-d101-4dc0-b81b-a27b84119e8a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.19432939098828408,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.19065878722177687
        },
        "08a2414a-2871-4f82-8ebb-840101ad9492": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.20138327949736468,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24436655985303624
        },
        "379d8770-2516-46d9-a64e-231d1f030138": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.1932077126499157,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22441542959136584
        },
        "395a4d85-18f6-4ee3-a0b9-5bd305fe6cc0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2014051764066691,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.20921035386237996
        },
        "40f13ff9-74ae-49f0-8ed3-afcd5a834c9e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.34438538679372405,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.523170770583374
        },
        "44f90524-ea0f-4289-a400-c055b85bdbe4": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2788738652923095,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.38764773509073014
        },
        "719dc2fe-4021-477f-baac-f067f65aad4f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.19136817410067258,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.21273634343297354
        },
        "828acef0-d1e3-4ef7-bfa8-ac7b5c41c212": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21239126018187537,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2447825251321223
        },
        "9f0a33e8-d0bc-49ed-9b4e-08e78dd9598e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.1970977688202631,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2141955424088977
        },
        "bbe15d1c-755f-4467-8e6a-901da760f575": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.28840574292716553,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4148114901458655
        },
        "d305edb3-9a17-4f0d-9760-117a98a0634d": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.35357962157190315,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5370592476499174
        },
        "de024395-0d0d-4efd-96e7-7fc94aaec6b5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.4044583717894482,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6469167478704309
        }
      },
      "25": {
        "04b0682a-d101-4dc0-b81b-a27b84119e8a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.19432939098828408,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.19065878722177687
        },
        "08a2414a-2871-4f82-8ebb-840101ad9492": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.20138327949736468,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24436655985303624
        },
        "379d8770-2516-46d9-a64e-231d1f030138": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.1932077126499157,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22441542959136584
        },
        "395a4d85-18f6-4ee3-a0b9-5bd305fe6cc0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2014051764066691,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.20921035386237996
        },
        "40f13ff9-74ae-49f0-8ed3-afcd5a834c9e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.34438538679372405,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.523170770583374
        },
        "44f90524-ea0f-4289-a400-c055b85bdbe4": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2788738652923095,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.38764773509073014
        },
        "719dc2fe-4021-477f-baac-f067f65aad4f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.19136817410067258,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.21273634343297354
        },
        "828acef0-d1e3-4ef7-bfa8-ac7b5c41c212": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21239126018187537,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2447825251321223
        },
        "9f0a33e8-d0bc-49ed-9b4e-08e78dd9598e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.1970977688202631,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2141955424088977
        },
        "bbe15d1c-755f-4467-8e6a-901da760f575": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.28840574292716553,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4148114901458655
        },
        "d305edb3-9a17-4f0d-9760-117a98a0634d": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.35357962157190315,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5370592476499174
        },
        "de024395-0d0d-4efd-96e7-7fc94aaec6b5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.4044583717894482,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6469167478704309
        }
      },
      "50": {
        "04b0682a-d101-4dc0-b81b-a27b84119e8a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.19432939098828408,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.19065878722177687
        },
        "08a2414a-2871-4f82-8ebb-840101ad9492": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.20138327949736468,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24436655985303624
        },
        "379d8770-2516-46d9-a64e-231d1f030138": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.1932077126499157,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22441542959136584
        },
        "395a4d85-18f6-4ee3-a0b9-5bd305fe6cc0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2014051764066691,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.20921035386237996
        },
        "40f13ff9-74ae-49f0-8ed3-afcd5a834c9e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.34438538679372405,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.523170770583374
        },
        "44f90524-ea0f-4289-a400-c055b85bdbe4": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2788738652923095,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.38764773509073014
        },
        "719dc2fe-4021-477f-baac-f067f65aad4f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.19136817410067258,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.21273634343297354
        },
        "828acef0-d1e3-4ef7-bfa8-ac7b5c41c212": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21239126018187537,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2447825251321223
        },
        "9f0a33e8-d0bc-49ed-9b4e-08e78dd9598e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.1970977688202631,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2141955424088977
        },
        "bbe15d1c-755f-4467-8e6a-901da760f575": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.28840574292716553,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4148114901458655
        },
        "d305edb3-9a17-4f0d-9760-117a98a0634d": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.35357962157190315,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5370592476499174
        },
        "de024395-0d0d-4efd-96e7-7fc94aaec6b5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.4044583717894482,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6469167478704309
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "04b0682a-d101-4dc0-b81b-a27b84119e8a",
        "395a4d85-18f6-4ee3-a0b9-5bd305fe6cc0"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "caf9ead2",
    "union_ids": [
      "04b0682a-d101-4dc0-b81b-a27b84119e8a",
      "08a2414a-2871-4f82-8ebb-840101ad9492",
      "379d8770-2516-46d9-a64e-231d1f030138",
      "395a4d85-18f6-4ee3-a0b9-5bd305fe6cc0",
      "40f13ff9-74ae-49f0-8ed3-afcd5a834c9e",
      "44f90524-ea0f-4289-a400-c055b85bdbe4",
      "719dc2fe-4021-477f-baac-f067f65aad4f",
      "828acef0-d1e3-4ef7-bfa8-ac7b5c41c212",
      "9f0a33e8-d0bc-49ed-9b4e-08e78dd9598e",
      "bbe15d1c-755f-4467-8e6a-901da760f575",
      "d305edb3-9a17-4f0d-9760-117a98a0634d",
      "de024395-0d0d-4efd-96e7-7fc94aaec6b5"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 13,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 13,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 13,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "260fd4e2-dd0d-4709-9995-933675147216": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24715095898723627,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33230192226600697
        },
        "39d14815-01b9-4c46-aa22-e30d5829fead": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.27531216324638524,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3724243312134583
        },
        "41c1aa09-549d-4f1c-8281-dac345bf1c23": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2507868697221518,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3392137408605099
        },
        "44c0bbbe-41a8-40f3-8c6c-639b659fb3e1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.26007851268529164,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3601570229863975
        },
        "52421553-7b1d-4391-9080-147caafbd135": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2628298910593445,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3514197830628265
        },
        "53e0c578-a4cf-4468-93d2-3b102381bcca": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2492399928120254,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33647998991558525
        },
        "57ef4bb8-19b4-4938-b3ec-1c6c558c16c4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.23151216276765874,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3343243302560053
        },
        "6fc2846a-d240-4af4-9b87-c9abf6000fd2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.24384064625991717,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34512128685500887
        },
        "ac237a31-2ff8-4684-a57d-c8ac4e41a637": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.35568909884955185,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5473781991296152
        },
        "f38bd84c-9690-433e-852c-78cf4885ed4b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.232679322065173,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3366586488510338
        }
      },
      "100": {
        "260fd4e2-dd0d-4709-9995-933675147216": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24715095898723627,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33230192226600697
        },
        "288c961b-0ac0-4a8e-85a9-5ba8c42bb1b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.24820692505891856,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30281385116687887
        },
        "39d14815-01b9-4c46-aa22-e30d5829fead": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.27531216324638524,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3724243312134583
        },
        "41c1aa09-549d-4f1c-8281-dac345bf1c23": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2507868697221518,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3392137408605099
        },
        "4247332a-7cfc-4862-94ae-7e31fdbf3151": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.24421395489093428,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.330027903487618
        },
        "44c0bbbe-41a8-40f3-8c6c-639b659fb3e1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.26007851268529164,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3601570229863975
        },
        "52421553-7b1d-4391-9080-147caafbd135": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2628298910593445,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3514197830628265
        },
        "53e0c578-a4cf-4468-93d2-3b102381bcca": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2492399928120254,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33647998991558525
        },
        "57ef4bb8-19b4-4938-b3ec-1c6c558c16c4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.23151216276765874,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3343243302560053
        },
        "6fc2846a-d240-4af4-9b87-c9abf6000fd2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.24384064625991717,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34512128685500887
        },
        "93606f1f-1dc6-4633-8450-43dc57040655": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.24479928405125323,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2959985691515482
        },
        "ac237a31-2ff8-4684-a57d-c8ac4e41a637": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.35568909884955185,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5473781991296152
        },
        "f38bd84c-9690-433e-852c-78cf4885ed4b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.232679322065173,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3366586488510338
        }
      },
      "25": {
        "260fd4e2-dd0d-4709-9995-933675147216": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24715095898723627,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33230192226600697
        },
        "288c961b-0ac0-4a8e-85a9-5ba8c42bb1b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.24820692505891856,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30281385116687887
        },
        "39d14815-01b9-4c46-aa22-e30d5829fead": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.27531216324638524,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3724243312134583
        },
        "41c1aa09-549d-4f1c-8281-dac345bf1c23": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2507868697221518,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3392137408605099
        },
        "4247332a-7cfc-4862-94ae-7e31fdbf3151": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.24421395489093428,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.330027903487618
        },
        "44c0bbbe-41a8-40f3-8c6c-639b659fb3e1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.26007851268529164,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3601570229863975
        },
        "52421553-7b1d-4391-9080-147caafbd135": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2628298910593445,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3514197830628265
        },
        "53e0c578-a4cf-4468-93d2-3b102381bcca": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2492399928120254,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33647998991558525
        },
        "57ef4bb8-19b4-4938-b3ec-1c6c558c16c4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.23151216276765874,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3343243302560053
        },
        "6fc2846a-d240-4af4-9b87-c9abf6000fd2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.24384064625991717,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34512128685500887
        },
        "93606f1f-1dc6-4633-8450-43dc57040655": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.24479928405125323,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2959985691515482
        },
        "ac237a31-2ff8-4684-a57d-c8ac4e41a637": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.35568909884955185,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5473781991296152
        },
        "f38bd84c-9690-433e-852c-78cf4885ed4b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.232679322065173,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3366586488510338
        }
      },
      "50": {
        "260fd4e2-dd0d-4709-9995-933675147216": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24715095898723627,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33230192226600697
        },
        "288c961b-0ac0-4a8e-85a9-5ba8c42bb1b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.24820692505891856,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30281385116687887
        },
        "39d14815-01b9-4c46-aa22-e30d5829fead": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.27531216324638524,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3724243312134583
        },
        "41c1aa09-549d-4f1c-8281-dac345bf1c23": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2507868697221518,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3392137408605099
        },
        "4247332a-7cfc-4862-94ae-7e31fdbf3151": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.24421395489093428,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.330027903487618
        },
        "44c0bbbe-41a8-40f3-8c6c-639b659fb3e1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.26007851268529164,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3601570229863975
        },
        "52421553-7b1d-4391-9080-147caafbd135": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2628298910593445,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3514197830628265
        },
        "53e0c578-a4cf-4468-93d2-3b102381bcca": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2492399928120254,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33647998991558525
        },
        "57ef4bb8-19b4-4938-b3ec-1c6c558c16c4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.23151216276765874,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3343243302560053
        },
        "6fc2846a-d240-4af4-9b87-c9abf6000fd2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.24384064625991717,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34512128685500887
        },
        "93606f1f-1dc6-4633-8450-43dc57040655": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.24479928405125323,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2959985691515482
        },
        "ac237a31-2ff8-4684-a57d-c8ac4e41a637": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.35568909884955185,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5473781991296152
        },
        "f38bd84c-9690-433e-852c-78cf4885ed4b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.232679322065173,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3366586488510338
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "288c961b-0ac0-4a8e-85a9-5ba8c42bb1b1",
        "4247332a-7cfc-4862-94ae-7e31fdbf3151",
        "93606f1f-1dc6-4633-8450-43dc57040655"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "fca762bc",
    "union_ids": [
      "260fd4e2-dd0d-4709-9995-933675147216",
      "288c961b-0ac0-4a8e-85a9-5ba8c42bb1b1",
      "39d14815-01b9-4c46-aa22-e30d5829fead",
      "41c1aa09-549d-4f1c-8281-dac345bf1c23",
      "4247332a-7cfc-4862-94ae-7e31fdbf3151",
      "44c0bbbe-41a8-40f3-8c6c-639b659fb3e1",
      "52421553-7b1d-4391-9080-147caafbd135",
      "53e0c578-a4cf-4468-93d2-3b102381bcca",
      "57ef4bb8-19b4-4938-b3ec-1c6c558c16c4",
      "6fc2846a-d240-4af4-9b87-c9abf6000fd2",
      "93606f1f-1dc6-4633-8450-43dc57040655",
      "ac237a31-2ff8-4684-a57d-c8ac4e41a637",
      "f38bd84c-9690-433e-852c-78cf4885ed4b"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 13,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 13,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 13,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "0e927b95-84e0-4fc0-a8ab-22baadaae976": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6000000238418579,
          "final_score": 0.1981264408084064,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2642528763716041
        },
        "2cd66c0d-a4c4-4716-9c4b-f93edaa35b8c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2258412804662974,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2536825661778035
        },
        "30832101-1d80-4d8b-b802-6748fef2252b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.20990347566070083,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24820695761565215
        },
        "48369288-7a14-499f-aa63-67d68755a962": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21490059753830976,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23620119612566126
        },
        "b1298f15-9760-45d4-a963-d9bfd3dc1499": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.18754973328009505,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23996447151691236
        },
        "b2f82308-4397-4970-b24a-2d972677d141": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21694776069644656,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24029552244193486
        },
        "b8e3112b-02f0-4274-98df-92cbaf2075a0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.218781601999491,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23956320924419072
        },
        "daaf94ca-771b-4c06-9462-96819fd2083d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21097413270495435,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24194827017828024
        },
        "e825ae62-f79d-4731-92b5-e8ea2cba6ec2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24070394077190196,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.28340788678901263
        },
        "ecf102d7-129a-41fa-a53e-d5bd67a7f18a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21884379891330671,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23968760307182213
        }
      },
      "100": {
        "0e927b95-84e0-4fc0-a8ab-22baadaae976": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6000000238418579,
          "final_score": 0.1981264408084064,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2642528763716041
        },
        "2cd66c0d-a4c4-4716-9c4b-f93edaa35b8c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2258412804662974,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2536825661778035
        },
        "2f47c4ec-f6ab-4f2e-98b3-1620f6f9420f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21218502862363997,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2210900583487737
        },
        "30832101-1d80-4d8b-b802-6748fef2252b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.20990347566070083,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24820695761565215
        },
        "48369288-7a14-499f-aa63-67d68755a962": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21490059753830976,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23620119612566126
        },
        "b1298f15-9760-45d4-a963-d9bfd3dc1499": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.18754973328009505,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23996447151691236
        },
        "b2f82308-4397-4970-b24a-2d972677d141": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21694776069644656,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24029552244193486
        },
        "b8e3112b-02f0-4274-98df-92cbaf2075a0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.218781601999491,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23956320924419072
        },
        "ccdd9392-4509-45f2-9444-8dfd8f465fd3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21276633198276315,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.227532669210735
        },
        "d37649b1-d4e4-4ca7-a4d7-e4743b9d88a7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21020356520898245,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2224071356631736
        },
        "daaf94ca-771b-4c06-9462-96819fd2083d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21097413270495435,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24194827017828024
        },
        "e825ae62-f79d-4731-92b5-e8ea2cba6ec2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24070394077190196,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.28340788678901263
        },
        "ecf102d7-129a-41fa-a53e-d5bd67a7f18a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21884379891330671,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23968760307182213
        }
      },
      "25": {
        "0e927b95-84e0-4fc0-a8ab-22baadaae976": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6000000238418579,
          "final_score": 0.1981264408084064,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2642528763716041
        },
        "2cd66c0d-a4c4-4716-9c4b-f93edaa35b8c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2258412804662974,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2536825661778035
        },
        "2f47c4ec-f6ab-4f2e-98b3-1620f6f9420f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21218502862363997,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2210900583487737
        },
        "30832101-1d80-4d8b-b802-6748fef2252b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.20990347566070083,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24820695761565215
        },
        "48369288-7a14-499f-aa63-67d68755a962": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21490059753830976,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23620119612566126
        },
        "b1298f15-9760-45d4-a963-d9bfd3dc1499": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.18754973328009505,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23996447151691236
        },
        "b2f82308-4397-4970-b24a-2d972677d141": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21694776069644656,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24029552244193486
        },
        "b8e3112b-02f0-4274-98df-92cbaf2075a0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.218781601999491,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23956320924419072
        },
        "ccdd9392-4509-45f2-9444-8dfd8f465fd3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21276633198276315,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.227532669210735
        },
        "d37649b1-d4e4-4ca7-a4d7-e4743b9d88a7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21020356520898245,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2224071356631736
        },
        "daaf94ca-771b-4c06-9462-96819fd2083d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21097413270495435,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24194827017828024
        },
        "e825ae62-f79d-4731-92b5-e8ea2cba6ec2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24070394077190196,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.28340788678901263
        },
        "ecf102d7-129a-41fa-a53e-d5bd67a7f18a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21884379891330671,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23968760307182213
        }
      },
      "50": {
        "0e927b95-84e0-4fc0-a8ab-22baadaae976": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6000000238418579,
          "final_score": 0.1981264408084064,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2642528763716041
        },
        "2cd66c0d-a4c4-4716-9c4b-f93edaa35b8c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2258412804662974,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2536825661778035
        },
        "2f47c4ec-f6ab-4f2e-98b3-1620f6f9420f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21218502862363997,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2210900583487737
        },
        "30832101-1d80-4d8b-b802-6748fef2252b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.20990347566070083,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24820695761565215
        },
        "48369288-7a14-499f-aa63-67d68755a962": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21490059753830976,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23620119612566126
        },
        "b1298f15-9760-45d4-a963-d9bfd3dc1499": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.18754973328009505,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23996447151691236
        },
        "b2f82308-4397-4970-b24a-2d972677d141": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21694776069644656,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24029552244193486
        },
        "b8e3112b-02f0-4274-98df-92cbaf2075a0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.218781601999491,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23956320924419072
        },
        "ccdd9392-4509-45f2-9444-8dfd8f465fd3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21276633198276315,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.227532669210735
        },
        "d37649b1-d4e4-4ca7-a4d7-e4743b9d88a7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21020356520898245,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2224071356631736
        },
        "daaf94ca-771b-4c06-9462-96819fd2083d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21097413270495435,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24194827017828024
        },
        "e825ae62-f79d-4731-92b5-e8ea2cba6ec2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24070394077190196,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.28340788678901263
        },
        "ecf102d7-129a-41fa-a53e-d5bd67a7f18a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21884379891330671,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23968760307182213
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "2f47c4ec-f6ab-4f2e-98b3-1620f6f9420f",
        "ccdd9392-4509-45f2-9444-8dfd8f465fd3",
        "d37649b1-d4e4-4ca7-a4d7-e4743b9d88a7"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "4388e9dd",
    "union_ids": [
      "0e927b95-84e0-4fc0-a8ab-22baadaae976",
      "2cd66c0d-a4c4-4716-9c4b-f93edaa35b8c",
      "2f47c4ec-f6ab-4f2e-98b3-1620f6f9420f",
      "30832101-1d80-4d8b-b802-6748fef2252b",
      "48369288-7a14-499f-aa63-67d68755a962",
      "b1298f15-9760-45d4-a963-d9bfd3dc1499",
      "b2f82308-4397-4970-b24a-2d972677d141",
      "b8e3112b-02f0-4274-98df-92cbaf2075a0",
      "ccdd9392-4509-45f2-9444-8dfd8f465fd3",
      "d37649b1-d4e4-4ca7-a4d7-e4743b9d88a7",
      "daaf94ca-771b-4c06-9462-96819fd2083d",
      "e825ae62-f79d-4731-92b5-e8ea2cba6ec2",
      "ecf102d7-129a-41fa-a53e-d5bd67a7f18a"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 14,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 14,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 14,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "51dbe0a1-a379-4fdf-ba41-5466ab10471b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21243460494529276,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2961692146112733
        },
        "5922d401-2ab6-499e-b6f3-1bf3201d9f99": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.38919418517686755,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6041483712978726
        },
        "716e55ff-0806-445a-a909-6a516b3dcc27": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.22173248272980003,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29496496545960005
        },
        "81b4ea0e-b1a5-497c-8c58-569ff8dda85b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.22594364016654342,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29744728599791226
        },
        "cf46295d-132b-432a-9a60-049cce4598b7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2551409152077777,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.31668183146459716
        },
        "d20311d6-9fe1-47c9-b92f-119091068eae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.22255602447027623,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30211205418576115
        },
        "e5a9f8f4-1bd7-4adb-bc58-cbf4945c63c8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.22829254227245704,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.29258508597542554
        },
        "ef7cbabf-83c9-4679-92ab-6db0969f3952": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.24259139320055753,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3109427873452526
        },
        "f404eed3-832d-4030-b2ef-221262d41b47": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2565700669406358,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37814013388127155
        },
        "f4f98a52-5151-49b1-99a0-35e5bc77c1d1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21874682406290108,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29449365337101086
        }
      },
      "100": {
        "51dbe0a1-a379-4fdf-ba41-5466ab10471b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21243460494529276,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2961692146112733
        },
        "5922d401-2ab6-499e-b6f3-1bf3201d9f99": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.38919418517686755,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6041483712978726
        },
        "716e55ff-0806-445a-a909-6a516b3dcc27": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.22173248272980003,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29496496545960005
        },
        "81b4ea0e-b1a5-497c-8c58-569ff8dda85b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.22594364016654342,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29744728599791226
        },
        "ad3a07f1-38b8-4088-b532-0504dd68973b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23864111369054375,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29028222213587873
        },
        "ad3c85e8-4b2c-4dd6-a611-072c156da1b2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.22422184407202467,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.26628368483956777
        },
        "cf46295d-132b-432a-9a60-049cce4598b7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2551409152077777,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.31668183146459716
        },
        "d20311d6-9fe1-47c9-b92f-119091068eae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.22255602447027623,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30211205418576115
        },
        "e0a004dc-5a0c-40a9-9606-d3aa73ac80b9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2267449724562447,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.27348994968086093
        },
        "e5a9f8f4-1bd7-4adb-bc58-cbf4945c63c8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.22829254227245704,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.29258508597542554
        },
        "ef7cbabf-83c9-4679-92ab-6db0969f3952": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.24259139320055753,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3109427873452526
        },
        "f404eed3-832d-4030-b2ef-221262d41b47": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2565700669406358,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37814013388127155
        },
        "f4dc93e7-1214-4655-827b-f62f1680c15f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.22976085924457235,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2725217132439359
        },
        "f4f98a52-5151-49b1-99a0-35e5bc77c1d1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21874682406290108,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29449365337101086
        }
      },
      "25": {
        "51dbe0a1-a379-4fdf-ba41-5466ab10471b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21243460494529276,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2961692146112733
        },
        "5922d401-2ab6-499e-b6f3-1bf3201d9f99": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.38919418517686755,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6041483712978726
        },
        "716e55ff-0806-445a-a909-6a516b3dcc27": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.22173248272980003,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29496496545960005
        },
        "81b4ea0e-b1a5-497c-8c58-569ff8dda85b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.22594364016654342,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29744728599791226
        },
        "ad3a07f1-38b8-4088-b532-0504dd68973b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23864111369054375,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29028222213587873
        },
        "ad3c85e8-4b2c-4dd6-a611-072c156da1b2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.22422184407202467,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.26628368483956777
        },
        "cf46295d-132b-432a-9a60-049cce4598b7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2551409152077777,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.31668183146459716
        },
        "d20311d6-9fe1-47c9-b92f-119091068eae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.22255602447027623,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30211205418576115
        },
        "e0a004dc-5a0c-40a9-9606-d3aa73ac80b9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2267449724562447,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.27348994968086093
        },
        "e5a9f8f4-1bd7-4adb-bc58-cbf4945c63c8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.22829254227245704,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.29258508597542554
        },
        "ef7cbabf-83c9-4679-92ab-6db0969f3952": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.24259139320055753,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3109427873452526
        },
        "f404eed3-832d-4030-b2ef-221262d41b47": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2565700669406358,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37814013388127155
        },
        "f4dc93e7-1214-4655-827b-f62f1680c15f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.22976085924457235,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2725217132439359
        },
        "f4f98a52-5151-49b1-99a0-35e5bc77c1d1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21874682406290108,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29449365337101086
        }
      },
      "50": {
        "51dbe0a1-a379-4fdf-ba41-5466ab10471b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21243460494529276,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2961692146112733
        },
        "5922d401-2ab6-499e-b6f3-1bf3201d9f99": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.38919418517686755,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6041483712978726
        },
        "716e55ff-0806-445a-a909-6a516b3dcc27": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.22173248272980003,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29496496545960005
        },
        "81b4ea0e-b1a5-497c-8c58-569ff8dda85b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.22594364016654342,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29744728599791226
        },
        "ad3a07f1-38b8-4088-b532-0504dd68973b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23864111369054375,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29028222213587873
        },
        "ad3c85e8-4b2c-4dd6-a611-072c156da1b2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.22422184407202467,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.26628368483956777
        },
        "cf46295d-132b-432a-9a60-049cce4598b7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2551409152077777,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.31668183146459716
        },
        "d20311d6-9fe1-47c9-b92f-119091068eae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.22255602447027623,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30211205418576115
        },
        "e0a004dc-5a0c-40a9-9606-d3aa73ac80b9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2267449724562447,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.27348994968086093
        },
        "e5a9f8f4-1bd7-4adb-bc58-cbf4945c63c8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.22829254227245704,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.29258508597542554
        },
        "ef7cbabf-83c9-4679-92ab-6db0969f3952": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.24259139320055753,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3109427873452526
        },
        "f404eed3-832d-4030-b2ef-221262d41b47": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2565700669406358,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37814013388127155
        },
        "f4dc93e7-1214-4655-827b-f62f1680c15f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.22976085924457235,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2725217132439359
        },
        "f4f98a52-5151-49b1-99a0-35e5bc77c1d1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21874682406290108,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29449365337101086
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "ad3a07f1-38b8-4088-b532-0504dd68973b",
        "ad3c85e8-4b2c-4dd6-a611-072c156da1b2",
        "e0a004dc-5a0c-40a9-9606-d3aa73ac80b9",
        "f4dc93e7-1214-4655-827b-f62f1680c15f"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "e8a79c70",
    "union_ids": [
      "51dbe0a1-a379-4fdf-ba41-5466ab10471b",
      "5922d401-2ab6-499e-b6f3-1bf3201d9f99",
      "716e55ff-0806-445a-a909-6a516b3dcc27",
      "81b4ea0e-b1a5-497c-8c58-569ff8dda85b",
      "ad3a07f1-38b8-4088-b532-0504dd68973b",
      "ad3c85e8-4b2c-4dd6-a611-072c156da1b2",
      "cf46295d-132b-432a-9a60-049cce4598b7",
      "d20311d6-9fe1-47c9-b92f-119091068eae",
      "e0a004dc-5a0c-40a9-9606-d3aa73ac80b9",
      "e5a9f8f4-1bd7-4adb-bc58-cbf4945c63c8",
      "ef7cbabf-83c9-4679-92ab-6db0969f3952",
      "f404eed3-832d-4030-b2ef-221262d41b47",
      "f4dc93e7-1214-4655-827b-f62f1680c15f",
      "f4f98a52-5151-49b1-99a0-35e5bc77c1d1"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "06fcefc5-c106-47a5-8ba7-b0fddd387d2d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.3460496806496851,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5280993627298817
        },
        "0f00513a-a879-4fd8-8863-cb368f925953": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23988724196399258,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.30977447915961354
        },
        "13b82502-ab01-4b7e-827e-4c004a242c63": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3659347882961033,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5818695765922066
        },
        "3146f4fb-6146-47d5-b785-7504f029ece8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24713805094036895,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3322761061722723
        },
        "3def31b5-1128-4487-bd95-3b70be6c59ac": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.23441357005345623,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.32882714249109823
        },
        "553e841e-fa5b-49bf-9d45-fbca5c3fc8d0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.23340200130522654,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31920400389791337
        },
        "91cd2782-1335-4425-b948-d28255041b5b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2606413898789579,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3562827797579158
        },
        "a009d45a-194b-414c-bcb7-0b10b117a8a3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.26427562503457885,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.36455125149966916
        },
        "cdb23ac9-1bf4-48a8-a41d-d8f7017f26b8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.24424403282894247,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3014880604126762
        },
        "dcb1880c-b792-4bc6-a850-81a093f5cbac": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.22678182789072365,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2975636615034932
        }
      },
      "100": {
        "06fcefc5-c106-47a5-8ba7-b0fddd387d2d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.3460496806496851,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5280993627298817
        },
        "0f00513a-a879-4fd8-8863-cb368f925953": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23988724196399258,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.30977447915961354
        },
        "13b82502-ab01-4b7e-827e-4c004a242c63": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3659347882961033,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5818695765922066
        },
        "3146f4fb-6146-47d5-b785-7504f029ece8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24713805094036895,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3322761061722723
        },
        "3def31b5-1128-4487-bd95-3b70be6c59ac": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.23441357005345623,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.32882714249109823
        },
        "553e841e-fa5b-49bf-9d45-fbca5c3fc8d0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.23340200130522654,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31920400389791337
        },
        "62fb72fc-0fad-4b88-8f09-9157d574b5eb": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2459413736615495,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.28860274842459277
        },
        "91cd2782-1335-4425-b948-d28255041b5b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2606413898789579,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3562827797579158
        },
        "a009d45a-194b-414c-bcb7-0b10b117a8a3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.26427562503457885,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.36455125149966916
        },
        "c7f29696-b70b-4372-90eb-81350a0505c9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.23355218578864206,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2735043726263259
        },
        "cdb23ac9-1bf4-48a8-a41d-d8f7017f26b8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.24424403282894247,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3014880604126762
        },
        "dcb1880c-b792-4bc6-a850-81a093f5cbac": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.22678182789072365,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2975636615034932
        }
      },
      "25": {
        "06fcefc5-c106-47a5-8ba7-b0fddd387d2d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.3460496806496851,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5280993627298817
        },
        "0f00513a-a879-4fd8-8863-cb368f925953": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23988724196399258,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.30977447915961354
        },
        "13b82502-ab01-4b7e-827e-4c004a242c63": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3659347882961033,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5818695765922066
        },
        "3146f4fb-6146-47d5-b785-7504f029ece8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24713805094036895,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3322761061722723
        },
        "3def31b5-1128-4487-bd95-3b70be6c59ac": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.23441357005345623,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.32882714249109823
        },
        "553e841e-fa5b-49bf-9d45-fbca5c3fc8d0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.23340200130522654,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31920400389791337
        },
        "62fb72fc-0fad-4b88-8f09-9157d574b5eb": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2459413736615495,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.28860274842459277
        },
        "91cd2782-1335-4425-b948-d28255041b5b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2606413898789579,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3562827797579158
        },
        "a009d45a-194b-414c-bcb7-0b10b117a8a3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.26427562503457885,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.36455125149966916
        },
        "c7f29696-b70b-4372-90eb-81350a0505c9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.23355218578864206,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2735043726263259
        },
        "cdb23ac9-1bf4-48a8-a41d-d8f7017f26b8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.24424403282894247,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3014880604126762
        },
        "dcb1880c-b792-4bc6-a850-81a093f5cbac": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.22678182789072365,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2975636615034932
        }
      },
      "50": {
        "06fcefc5-c106-47a5-8ba7-b0fddd387d2d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.3460496806496851,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5280993627298817
        },
        "0f00513a-a879-4fd8-8863-cb368f925953": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23988724196399258,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.30977447915961354
        },
        "13b82502-ab01-4b7e-827e-4c004a242c63": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3659347882961033,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5818695765922066
        },
        "3146f4fb-6146-47d5-b785-7504f029ece8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24713805094036895,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3322761061722723
        },
        "3def31b5-1128-4487-bd95-3b70be6c59ac": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.23441357005345623,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.32882714249109823
        },
        "553e841e-fa5b-49bf-9d45-fbca5c3fc8d0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.23340200130522654,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31920400389791337
        },
        "62fb72fc-0fad-4b88-8f09-9157d574b5eb": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2459413736615495,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.28860274842459277
        },
        "91cd2782-1335-4425-b948-d28255041b5b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2606413898789579,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3562827797579158
        },
        "a009d45a-194b-414c-bcb7-0b10b117a8a3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.26427562503457885,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.36455125149966916
        },
        "c7f29696-b70b-4372-90eb-81350a0505c9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.23355218578864206,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2735043726263259
        },
        "cdb23ac9-1bf4-48a8-a41d-d8f7017f26b8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.24424403282894247,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3014880604126762
        },
        "dcb1880c-b792-4bc6-a850-81a093f5cbac": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.22678182789072365,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2975636615034932
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "62fb72fc-0fad-4b88-8f09-9157d574b5eb",
        "c7f29696-b70b-4372-90eb-81350a0505c9"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "d596882b",
    "union_ids": [
      "06fcefc5-c106-47a5-8ba7-b0fddd387d2d",
      "0f00513a-a879-4fd8-8863-cb368f925953",
      "13b82502-ab01-4b7e-827e-4c004a242c63",
      "3146f4fb-6146-47d5-b785-7504f029ece8",
      "3def31b5-1128-4487-bd95-3b70be6c59ac",
      "553e841e-fa5b-49bf-9d45-fbca5c3fc8d0",
      "62fb72fc-0fad-4b88-8f09-9157d574b5eb",
      "91cd2782-1335-4425-b948-d28255041b5b",
      "a009d45a-194b-414c-bcb7-0b10b117a8a3",
      "c7f29696-b70b-4372-90eb-81350a0505c9",
      "cdb23ac9-1bf4-48a8-a41d-d8f7017f26b8",
      "dcb1880c-b792-4bc6-a850-81a093f5cbac"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "0bb3abf9-1715-4030-add2-87193690ae55": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.1834397157929874,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2084794324442817
        },
        "261f4426-7f5e-4c22-aef8-9b301880641d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.17767013883710128,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.20684027767420254
        },
        "2788e9f1-8acc-42f6-b76b-dbbead15d653": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2368916902530657,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.27743337499866216
        },
        "2d7eeb5b-db12-4c77-b240-5ddfb040fce8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.1990937599665365,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2199875246537608
        },
        "50998c6f-1550-454a-a8e3-0c5981c486bd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20203707780868255,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2060741608625738
        },
        "51318c28-d810-4f4c-aa43-a1df03ead156": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.19255954256207175,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22671908598245039
        },
        "541e461e-3227-473e-9e98-d7283c6f6edf": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.18109716499807438,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21919433524135745
        },
        "78a2b5a4-da62-4769-86e7-f46a6927cb08": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.18606277904120563,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2176855637472367
        },
        "b536b730-b9ac-4412-98eb-11b88f659616": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.1948437376241362,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21148747996896022
        },
        "d9bfb5d8-5eb1-42e2-9823-6121b771fc67": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.18711579006477824,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2118715815457628
        }
      },
      "100": {
        "0bb3abf9-1715-4030-add2-87193690ae55": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.1834397157929874,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2084794324442817
        },
        "261f4426-7f5e-4c22-aef8-9b301880641d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.17767013883710128,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.20684027767420254
        },
        "2788e9f1-8acc-42f6-b76b-dbbead15d653": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2368916902530657,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.27743337499866216
        },
        "290e7871-a131-4ec7-9b15-b956d41a284c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.18446673890179519,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.1946934787477279
        },
        "2d7eeb5b-db12-4c77-b240-5ddfb040fce8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.1990937599665365,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2199875246537608
        },
        "50998c6f-1550-454a-a8e3-0c5981c486bd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20203707780868255,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2060741608625738
        },
        "51318c28-d810-4f4c-aa43-a1df03ead156": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.19255954256207175,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22671908598245039
        },
        "541e461e-3227-473e-9e98-d7283c6f6edf": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.18109716499807438,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21919433524135745
        },
        "78a2b5a4-da62-4769-86e7-f46a6927cb08": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.18606277904120563,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2176855637472367
        },
        "97609778-5624-499d-b7c2-c6aa7d3d56d2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.18366651205782114,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2053330284071767
        },
        "b536b730-b9ac-4412-98eb-11b88f659616": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.1948437376241362,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21148747996896022
        },
        "d9bfb5d8-5eb1-42e2-9823-6121b771fc67": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.18711579006477824,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2118715815457628
        }
      },
      "25": {
        "0bb3abf9-1715-4030-add2-87193690ae55": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.1834397157929874,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2084794324442817
        },
        "261f4426-7f5e-4c22-aef8-9b301880641d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.17767013883710128,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.20684027767420254
        },
        "2788e9f1-8acc-42f6-b76b-dbbead15d653": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2368916902530657,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.27743337499866216
        },
        "290e7871-a131-4ec7-9b15-b956d41a284c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.18446673890179519,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.1946934787477279
        },
        "2d7eeb5b-db12-4c77-b240-5ddfb040fce8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.1990937599665365,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2199875246537608
        },
        "50998c6f-1550-454a-a8e3-0c5981c486bd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20203707780868255,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2060741608625738
        },
        "51318c28-d810-4f4c-aa43-a1df03ead156": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.19255954256207175,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22671908598245039
        },
        "541e461e-3227-473e-9e98-d7283c6f6edf": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.18109716499807438,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21919433524135745
        },
        "78a2b5a4-da62-4769-86e7-f46a6927cb08": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.18606277904120563,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2176855637472367
        },
        "97609778-5624-499d-b7c2-c6aa7d3d56d2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.18366651205782114,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2053330284071767
        },
        "b536b730-b9ac-4412-98eb-11b88f659616": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.1948437376241362,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21148747996896022
        },
        "d9bfb5d8-5eb1-42e2-9823-6121b771fc67": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.18711579006477824,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2118715815457628
        }
      },
      "50": {
        "0bb3abf9-1715-4030-add2-87193690ae55": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.1834397157929874,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2084794324442817
        },
        "261f4426-7f5e-4c22-aef8-9b301880641d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.17767013883710128,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.20684027767420254
        },
        "2788e9f1-8acc-42f6-b76b-dbbead15d653": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2368916902530657,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.27743337499866216
        },
        "290e7871-a131-4ec7-9b15-b956d41a284c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.18446673890179519,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.1946934787477279
        },
        "2d7eeb5b-db12-4c77-b240-5ddfb040fce8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.1990937599665365,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2199875246537608
        },
        "50998c6f-1550-454a-a8e3-0c5981c486bd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20203707780868255,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2060741608625738
        },
        "51318c28-d810-4f4c-aa43-a1df03ead156": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.19255954256207175,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22671908598245039
        },
        "541e461e-3227-473e-9e98-d7283c6f6edf": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.18109716499807438,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21919433524135745
        },
        "78a2b5a4-da62-4769-86e7-f46a6927cb08": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.18606277904120563,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2176855637472367
        },
        "97609778-5624-499d-b7c2-c6aa7d3d56d2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.18366651205782114,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2053330284071767
        },
        "b536b730-b9ac-4412-98eb-11b88f659616": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.1948437376241362,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21148747996896022
        },
        "d9bfb5d8-5eb1-42e2-9823-6121b771fc67": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.18711579006477824,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2118715815457628
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "290e7871-a131-4ec7-9b15-b956d41a284c",
        "97609778-5624-499d-b7c2-c6aa7d3d56d2"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "71a3fd6b",
    "union_ids": [
      "0bb3abf9-1715-4030-add2-87193690ae55",
      "261f4426-7f5e-4c22-aef8-9b301880641d",
      "2788e9f1-8acc-42f6-b76b-dbbead15d653",
      "290e7871-a131-4ec7-9b15-b956d41a284c",
      "2d7eeb5b-db12-4c77-b240-5ddfb040fce8",
      "50998c6f-1550-454a-a8e3-0c5981c486bd",
      "51318c28-d810-4f4c-aa43-a1df03ead156",
      "541e461e-3227-473e-9e98-d7283c6f6edf",
      "78a2b5a4-da62-4769-86e7-f46a6927cb08",
      "97609778-5624-499d-b7c2-c6aa7d3d56d2",
      "b536b730-b9ac-4412-98eb-11b88f659616",
      "d9bfb5d8-5eb1-42e2-9823-6121b771fc67"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "02da77b6-8e64-4e45-b91d-f56478f67a49": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2986236043535856,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4289472039864832
        },
        "06729686-3261-40ed-8ff6-df507bf435c3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3919548295667171,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6183096561293602
        },
        "098a9dc0-55d9-46b4-88ec-680949a9f4f2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.33027277595193877,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48454555285755185
        },
        "64aca065-44ba-4427-b460-cb167428b95c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.29293155551420885,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42186311245892916
        },
        "6f3751a5-fac5-4894-ac58-37a4fc4aacec": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30392802293705024,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4377560503802116
        },
        "8925ddbd-0184-4573-b2c5-a7f91bf5e677": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3743513624916258,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5957027206917173
        },
        "99db9c59-462a-4f38-bc8b-7c4261a8437a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3058844494178099,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42776889549775965
        },
        "cb157e9d-4047-4ee1-b7cf-6fea39d4965a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.32217480416389566,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.46034960498993116
        },
        "d57233f2-1c6c-46a4-b413-388673789d1c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3572959823075697,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5305919612772793
        },
        "f98e98cb-d6bb-4a7d-a0f4-a7c415b4570f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.31033962028630463,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4586792448641437
        }
      },
      "100": {
        "02da77b6-8e64-4e45-b91d-f56478f67a49": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2986236043535856,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4289472039864832
        },
        "06729686-3261-40ed-8ff6-df507bf435c3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3919548295667171,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6183096561293602
        },
        "098a9dc0-55d9-46b4-88ec-680949a9f4f2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.33027277595193877,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48454555285755185
        },
        "372637f6-2ada-4c35-8b08-02e065786280": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2942725503376309,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.39054510592047054
        },
        "64aca065-44ba-4427-b460-cb167428b95c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.29293155551420885,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42186311245892916
        },
        "6f3751a5-fac5-4894-ac58-37a4fc4aacec": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30392802293705024,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4377560503802116
        },
        "8925ddbd-0184-4573-b2c5-a7f91bf5e677": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3743513624916258,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5957027206917173
        },
        "99db9c59-462a-4f38-bc8b-7c4261a8437a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3058844494178099,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42776889549775965
        },
        "cb157e9d-4047-4ee1-b7cf-6fea39d4965a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.32217480416389566,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.46034960498993116
        },
        "d57233f2-1c6c-46a4-b413-388673789d1c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3572959823075697,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5305919612772793
        },
        "f98e98cb-d6bb-4a7d-a0f4-a7c415b4570f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.31033962028630463,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4586792448641437
        }
      },
      "25": {
        "02da77b6-8e64-4e45-b91d-f56478f67a49": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2986236043535856,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4289472039864832
        },
        "06729686-3261-40ed-8ff6-df507bf435c3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3919548295667171,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6183096561293602
        },
        "098a9dc0-55d9-46b4-88ec-680949a9f4f2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.33027277595193877,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48454555285755185
        },
        "372637f6-2ada-4c35-8b08-02e065786280": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2942725503376309,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.39054510592047054
        },
        "64aca065-44ba-4427-b460-cb167428b95c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.29293155551420885,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42186311245892916
        },
        "6f3751a5-fac5-4894-ac58-37a4fc4aacec": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30392802293705024,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4377560503802116
        },
        "8925ddbd-0184-4573-b2c5-a7f91bf5e677": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3743513624916258,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5957027206917173
        },
        "99db9c59-462a-4f38-bc8b-7c4261a8437a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3058844494178099,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42776889549775965
        },
        "cb157e9d-4047-4ee1-b7cf-6fea39d4965a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.32217480416389566,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.46034960498993116
        },
        "d57233f2-1c6c-46a4-b413-388673789d1c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3572959823075697,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5305919612772793
        },
        "f98e98cb-d6bb-4a7d-a0f4-a7c415b4570f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.31033962028630463,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4586792448641437
        }
      },
      "50": {
        "02da77b6-8e64-4e45-b91d-f56478f67a49": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2986236043535856,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4289472039864832
        },
        "06729686-3261-40ed-8ff6-df507bf435c3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3919548295667171,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6183096561293602
        },
        "098a9dc0-55d9-46b4-88ec-680949a9f4f2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.33027277595193877,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48454555285755185
        },
        "372637f6-2ada-4c35-8b08-02e065786280": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2942725503376309,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.39054510592047054
        },
        "64aca065-44ba-4427-b460-cb167428b95c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.29293155551420885,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42186311245892916
        },
        "6f3751a5-fac5-4894-ac58-37a4fc4aacec": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30392802293705024,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4377560503802116
        },
        "8925ddbd-0184-4573-b2c5-a7f91bf5e677": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3743513624916258,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5957027206917173
        },
        "99db9c59-462a-4f38-bc8b-7c4261a8437a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3058844494178099,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42776889549775965
        },
        "cb157e9d-4047-4ee1-b7cf-6fea39d4965a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.32217480416389566,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.46034960498993116
        },
        "d57233f2-1c6c-46a4-b413-388673789d1c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3572959823075697,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5305919612772793
        },
        "f98e98cb-d6bb-4a7d-a0f4-a7c415b4570f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.31033962028630463,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4586792448641437
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "372637f6-2ada-4c35-8b08-02e065786280"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "28dc39ac",
    "union_ids": [
      "02da77b6-8e64-4e45-b91d-f56478f67a49",
      "06729686-3261-40ed-8ff6-df507bf435c3",
      "098a9dc0-55d9-46b4-88ec-680949a9f4f2",
      "372637f6-2ada-4c35-8b08-02e065786280",
      "64aca065-44ba-4427-b460-cb167428b95c",
      "6f3751a5-fac5-4894-ac58-37a4fc4aacec",
      "8925ddbd-0184-4573-b2c5-a7f91bf5e677",
      "99db9c59-462a-4f38-bc8b-7c4261a8437a",
      "cb157e9d-4047-4ee1-b7cf-6fea39d4965a",
      "d57233f2-1c6c-46a4-b413-388673789d1c",
      "f98e98cb-d6bb-4a7d-a0f4-a7c415b4570f"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "0daf6232-e18c-45c7-9b73-c23fea8ed71c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2093623079640347,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.22512461697711117
        },
        "12da26db-49f9-4492-8a7a-18224620d927": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.23806599451749721,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2921319856971343
        },
        "5a613280-b464-4466-8a9b-a0c4ad2a01ef": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.23662220445028215,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.28924440556270414
        },
        "919519ae-b2e6-453e-b6f7-092e70c6294b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.205053101245747,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2501062001073082
        },
        "a6347400-2f8d-49d4-8d15-801cb30a9aed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20909903373298208,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.22019807271117287
        },
        "aec551a0-64a3-41dc-9442-5bb136428929": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.42912129860243964,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6782426019732508
        },
        "b247e743-a309-47e0-afe9-8a0361acb5d0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.18068148096008982,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22136296430436542
        },
        "c9e7b0df-82cb-4268-8f51-81ad51bad685": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20558363469764473,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22216727440207962
        },
        "eb7b5a3f-e648-48e0-9ff2-de22c3c104fc": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20685575183030713,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2267114984154055
        },
        "fc800c5d-9991-4032-96da-0c911dceb625": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20406209957759663,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.23812419438682164
        }
      },
      "100": {
        "0daf6232-e18c-45c7-9b73-c23fea8ed71c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2093623079640347,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.22512461697711117
        },
        "12da26db-49f9-4492-8a7a-18224620d927": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.23806599451749721,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2921319856971343
        },
        "283fbfbc-79e5-48b7-a32b-056452c8281a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20202890807537136,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2060578213959514
        },
        "5a613280-b464-4466-8a9b-a0c4ad2a01ef": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.23662220445028215,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.28924440556270414
        },
        "919519ae-b2e6-453e-b6f7-092e70c6294b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.205053101245747,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2501062001073082
        },
        "a6347400-2f8d-49d4-8d15-801cb30a9aed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20909903373298208,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.22019807271117287
        },
        "aec551a0-64a3-41dc-9442-5bb136428929": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.42912129860243964,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6782426019732508
        },
        "b247e743-a309-47e0-afe9-8a0361acb5d0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.18068148096008982,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22136296430436542
        },
        "c9e7b0df-82cb-4268-8f51-81ad51bad685": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20558363469764473,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22216727440207962
        },
        "eb7b5a3f-e648-48e0-9ff2-de22c3c104fc": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20685575183030713,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2267114984154055
        },
        "fc800c5d-9991-4032-96da-0c911dceb625": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20406209957759663,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.23812419438682164
        }
      },
      "25": {
        "0daf6232-e18c-45c7-9b73-c23fea8ed71c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2093623079640347,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.22512461697711117
        },
        "12da26db-49f9-4492-8a7a-18224620d927": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.23806599451749721,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2921319856971343
        },
        "283fbfbc-79e5-48b7-a32b-056452c8281a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20202890807537136,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2060578213959514
        },
        "5a613280-b464-4466-8a9b-a0c4ad2a01ef": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.23662220445028215,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.28924440556270414
        },
        "919519ae-b2e6-453e-b6f7-092e70c6294b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.205053101245747,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2501062001073082
        },
        "a6347400-2f8d-49d4-8d15-801cb30a9aed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20909903373298208,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.22019807271117287
        },
        "aec551a0-64a3-41dc-9442-5bb136428929": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.42912129860243964,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6782426019732508
        },
        "b247e743-a309-47e0-afe9-8a0361acb5d0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.18068148096008982,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22136296430436542
        },
        "c9e7b0df-82cb-4268-8f51-81ad51bad685": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20558363469764473,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22216727440207962
        },
        "eb7b5a3f-e648-48e0-9ff2-de22c3c104fc": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20685575183030713,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2267114984154055
        },
        "fc800c5d-9991-4032-96da-0c911dceb625": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20406209957759663,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.23812419438682164
        }
      },
      "50": {
        "0daf6232-e18c-45c7-9b73-c23fea8ed71c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2093623079640347,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.22512461697711117
        },
        "12da26db-49f9-4492-8a7a-18224620d927": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.23806599451749721,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2921319856971343
        },
        "283fbfbc-79e5-48b7-a32b-056452c8281a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20202890807537136,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2060578213959514
        },
        "5a613280-b464-4466-8a9b-a0c4ad2a01ef": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.23662220445028215,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.28924440556270414
        },
        "919519ae-b2e6-453e-b6f7-092e70c6294b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.205053101245747,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2501062001073082
        },
        "a6347400-2f8d-49d4-8d15-801cb30a9aed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20909903373298208,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.22019807271117287
        },
        "aec551a0-64a3-41dc-9442-5bb136428929": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.42912129860243964,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6782426019732508
        },
        "b247e743-a309-47e0-afe9-8a0361acb5d0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.18068148096008982,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22136296430436542
        },
        "c9e7b0df-82cb-4268-8f51-81ad51bad685": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20558363469764473,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22216727440207962
        },
        "eb7b5a3f-e648-48e0-9ff2-de22c3c104fc": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20685575183030713,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2267114984154055
        },
        "fc800c5d-9991-4032-96da-0c911dceb625": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20406209957759663,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.23812419438682164
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "283fbfbc-79e5-48b7-a32b-056452c8281a"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "ba358f49",
    "union_ids": [
      "0daf6232-e18c-45c7-9b73-c23fea8ed71c",
      "12da26db-49f9-4492-8a7a-18224620d927",
      "283fbfbc-79e5-48b7-a32b-056452c8281a",
      "5a613280-b464-4466-8a9b-a0c4ad2a01ef",
      "919519ae-b2e6-453e-b6f7-092e70c6294b",
      "a6347400-2f8d-49d4-8d15-801cb30a9aed",
      "aec551a0-64a3-41dc-9442-5bb136428929",
      "b247e743-a309-47e0-afe9-8a0361acb5d0",
      "c9e7b0df-82cb-4268-8f51-81ad51bad685",
      "eb7b5a3f-e648-48e0-9ff2-de22c3c104fc",
      "fc800c5d-9991-4032-96da-0c911dceb625"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "0fdcbc96-bb00-42c0-83d7-5217e8fcd44b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.314617258769215,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4492345223068015
        },
        "453a5504-82f1-4cc2-a192-dd5740588d3e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.4305816797149466,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.677163356092033
        },
        "5ee447c2-9401-494c-b945-533554585402": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.4295952606314519,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6751905179250437
        },
        "7787d413-0db2-4f37-b530-4cc814ea2ece": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.32620098171229117,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.454401968669791
        },
        "83a5e1ad-34c8-4145-943a-098bb7876183": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.32096865958943144,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4619373239472344
        },
        "940fc66e-9df6-46a6-ac99-38256a661626": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33875732701202266,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4795146592692541
        },
        "a05f2796-313c-475d-8680-eb6b6e2658bf": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.34371525247692314,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.48415050605534005
        },
        "ad654575-24aa-4b5c-83e6-eaad1994186f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.40289100808146094,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6357820113945503
        },
        "bc60edf4-291f-4801-a7e2-c3acbf8c9a0f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33427255459230454,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.47054511442981783
        },
        "d65a1d61-effd-4dc5-b504-70976b02deef": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.30991370378756955,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4768274128203478
        }
      },
      "100": {
        "0fdcbc96-bb00-42c0-83d7-5217e8fcd44b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.314617258769215,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4492345223068015
        },
        "453a5504-82f1-4cc2-a192-dd5740588d3e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.4305816797149466,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.677163356092033
        },
        "5ee447c2-9401-494c-b945-533554585402": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.4295952606314519,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6751905179250437
        },
        "7787d413-0db2-4f37-b530-4cc814ea2ece": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.32620098171229117,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.454401968669791
        },
        "83a5e1ad-34c8-4145-943a-098bb7876183": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.32096865958943144,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4619373239472344
        },
        "940fc66e-9df6-46a6-ac99-38256a661626": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33875732701202266,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4795146592692541
        },
        "a05f2796-313c-475d-8680-eb6b6e2658bf": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.34371525247692314,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.48415050605534005
        },
        "ad654575-24aa-4b5c-83e6-eaad1994186f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.40289100808146094,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6357820113945503
        },
        "bc60edf4-291f-4801-a7e2-c3acbf8c9a0f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33427255459230454,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.47054511442981783
        },
        "d65a1d61-effd-4dc5-b504-70976b02deef": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.30991370378756955,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4768274128203478
        },
        "f9a1daed-1a46-44b7-89f6-8dff5f50704c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.3117802822104599,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4431605659944824
        }
      },
      "25": {
        "0fdcbc96-bb00-42c0-83d7-5217e8fcd44b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.314617258769215,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4492345223068015
        },
        "453a5504-82f1-4cc2-a192-dd5740588d3e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.4305816797149466,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.677163356092033
        },
        "5ee447c2-9401-494c-b945-533554585402": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.4295952606314519,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6751905179250437
        },
        "7787d413-0db2-4f37-b530-4cc814ea2ece": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.32620098171229117,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.454401968669791
        },
        "83a5e1ad-34c8-4145-943a-098bb7876183": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.32096865958943144,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4619373239472344
        },
        "940fc66e-9df6-46a6-ac99-38256a661626": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33875732701202266,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4795146592692541
        },
        "a05f2796-313c-475d-8680-eb6b6e2658bf": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.34371525247692314,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.48415050605534005
        },
        "ad654575-24aa-4b5c-83e6-eaad1994186f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.40289100808146094,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6357820113945503
        },
        "bc60edf4-291f-4801-a7e2-c3acbf8c9a0f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33427255459230454,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.47054511442981783
        },
        "d65a1d61-effd-4dc5-b504-70976b02deef": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.30991370378756955,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4768274128203478
        },
        "f9a1daed-1a46-44b7-89f6-8dff5f50704c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.3117802822104599,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4431605659944824
        }
      },
      "50": {
        "0fdcbc96-bb00-42c0-83d7-5217e8fcd44b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.314617258769215,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4492345223068015
        },
        "453a5504-82f1-4cc2-a192-dd5740588d3e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.4305816797149466,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.677163356092033
        },
        "5ee447c2-9401-494c-b945-533554585402": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.4295952606314519,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6751905179250437
        },
        "7787d413-0db2-4f37-b530-4cc814ea2ece": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.32620098171229117,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.454401968669791
        },
        "83a5e1ad-34c8-4145-943a-098bb7876183": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.32096865958943144,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4619373239472344
        },
        "940fc66e-9df6-46a6-ac99-38256a661626": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33875732701202266,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4795146592692541
        },
        "a05f2796-313c-475d-8680-eb6b6e2658bf": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.34371525247692314,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.48415050605534005
        },
        "ad654575-24aa-4b5c-83e6-eaad1994186f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.40289100808146094,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6357820113945503
        },
        "bc60edf4-291f-4801-a7e2-c3acbf8c9a0f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33427255459230454,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.47054511442981783
        },
        "d65a1d61-effd-4dc5-b504-70976b02deef": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.30991370378756955,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4768274128203478
        },
        "f9a1daed-1a46-44b7-89f6-8dff5f50704c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.3117802822104599,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4431605659944824
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "f9a1daed-1a46-44b7-89f6-8dff5f50704c"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "6cb6f249",
    "union_ids": [
      "0fdcbc96-bb00-42c0-83d7-5217e8fcd44b",
      "453a5504-82f1-4cc2-a192-dd5740588d3e",
      "5ee447c2-9401-494c-b945-533554585402",
      "7787d413-0db2-4f37-b530-4cc814ea2ece",
      "83a5e1ad-34c8-4145-943a-098bb7876183",
      "940fc66e-9df6-46a6-ac99-38256a661626",
      "a05f2796-313c-475d-8680-eb6b6e2658bf",
      "ad654575-24aa-4b5c-83e6-eaad1994186f",
      "bc60edf4-291f-4801-a7e2-c3acbf8c9a0f",
      "d65a1d61-effd-4dc5-b504-70976b02deef",
      "f9a1daed-1a46-44b7-89f6-8dff5f50704c"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "06a584bb-f9af-4630-9576-b89d867cf9e6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.34085005783413547,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4917001180524567
        },
        "1fdbe3db-370b-4763-8095-c9d68ea423f6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2841415814034507,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.37468316385594314
        },
        "364fb1e8-5718-4965-a623-01c9ef234726": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33972664535483665,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49945329547804485
        },
        "4d5247b6-81a3-4a86-910b-6a720fa6ba36": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2811096183436003,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3822192414555722
        },
        "76eab279-8537-4a6a-8781-df20b0f275b8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.27618542544937613,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3923708485145665
        },
        "8b3ed52a-0c9b-42cd-9804-0ab202a3704e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2891746278881647,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3803492610215382
        },
        "99db5d90-4d26-4317-9fd3-d4d503be3436": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3264170402250174,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4768340814037091
        },
        "d2b4e12d-5afa-45e7-8327-88aecf98ae46": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3245417921483992,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.47908357952842673
        },
        "e39f6f4a-574c-434d-ae19-2f68c45567db": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.38238991331806915,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5887798275898126
        },
        "ec4b151b-4555-42cc-a529-cd1d5aa81ac3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2986854477654002,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42737089076242873
        }
      },
      "100": {
        "06a584bb-f9af-4630-9576-b89d867cf9e6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.34085005783413547,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4917001180524567
        },
        "1d6b4870-d304-4512-a8dc-80acf9758ee9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.27793574056067394,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3688714758761391
        },
        "1fdbe3db-370b-4763-8095-c9d68ea423f6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2841415814034507,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.37468316385594314
        },
        "364fb1e8-5718-4965-a623-01c9ef234726": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33972664535483665,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49945329547804485
        },
        "4d5247b6-81a3-4a86-910b-6a720fa6ba36": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2811096183436003,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3822192414555722
        },
        "76eab279-8537-4a6a-8781-df20b0f275b8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.27618542544937613,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3923708485145665
        },
        "8b3ed52a-0c9b-42cd-9804-0ab202a3704e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2891746278881647,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3803492610215382
        },
        "99db5d90-4d26-4317-9fd3-d4d503be3436": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3264170402250174,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4768340814037091
        },
        "d2b4e12d-5afa-45e7-8327-88aecf98ae46": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3245417921483992,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.47908357952842673
        },
        "e39f6f4a-574c-434d-ae19-2f68c45567db": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.38238991331806915,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5887798275898126
        },
        "ec4b151b-4555-42cc-a529-cd1d5aa81ac3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2986854477654002,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42737089076242873
        }
      },
      "25": {
        "06a584bb-f9af-4630-9576-b89d867cf9e6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.34085005783413547,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4917001180524567
        },
        "1d6b4870-d304-4512-a8dc-80acf9758ee9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.27793574056067394,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3688714758761391
        },
        "1fdbe3db-370b-4763-8095-c9d68ea423f6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2841415814034507,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.37468316385594314
        },
        "364fb1e8-5718-4965-a623-01c9ef234726": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33972664535483665,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49945329547804485
        },
        "4d5247b6-81a3-4a86-910b-6a720fa6ba36": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2811096183436003,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3822192414555722
        },
        "76eab279-8537-4a6a-8781-df20b0f275b8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.27618542544937613,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3923708485145665
        },
        "8b3ed52a-0c9b-42cd-9804-0ab202a3704e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2891746278881647,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3803492610215382
        },
        "99db5d90-4d26-4317-9fd3-d4d503be3436": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3264170402250174,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4768340814037091
        },
        "d2b4e12d-5afa-45e7-8327-88aecf98ae46": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3245417921483992,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.47908357952842673
        },
        "e39f6f4a-574c-434d-ae19-2f68c45567db": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.38238991331806915,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5887798275898126
        },
        "ec4b151b-4555-42cc-a529-cd1d5aa81ac3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2986854477654002,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42737089076242873
        }
      },
      "50": {
        "06a584bb-f9af-4630-9576-b89d867cf9e6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.34085005783413547,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4917001180524567
        },
        "1d6b4870-d304-4512-a8dc-80acf9758ee9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.27793574056067394,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3688714758761391
        },
        "1fdbe3db-370b-4763-8095-c9d68ea423f6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2841415814034507,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.37468316385594314
        },
        "364fb1e8-5718-4965-a623-01c9ef234726": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33972664535483665,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49945329547804485
        },
        "4d5247b6-81a3-4a86-910b-6a720fa6ba36": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2811096183436003,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3822192414555722
        },
        "76eab279-8537-4a6a-8781-df20b0f275b8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.27618542544937613,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3923708485145665
        },
        "8b3ed52a-0c9b-42cd-9804-0ab202a3704e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2891746278881647,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3803492610215382
        },
        "99db5d90-4d26-4317-9fd3-d4d503be3436": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3264170402250174,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4768340814037091
        },
        "d2b4e12d-5afa-45e7-8327-88aecf98ae46": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3245417921483992,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.47908357952842673
        },
        "e39f6f4a-574c-434d-ae19-2f68c45567db": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.38238991331806915,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5887798275898126
        },
        "ec4b151b-4555-42cc-a529-cd1d5aa81ac3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2986854477654002,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42737089076242873
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "1d6b4870-d304-4512-a8dc-80acf9758ee9"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "2318644b",
    "union_ids": [
      "06a584bb-f9af-4630-9576-b89d867cf9e6",
      "1d6b4870-d304-4512-a8dc-80acf9758ee9",
      "1fdbe3db-370b-4763-8095-c9d68ea423f6",
      "364fb1e8-5718-4965-a623-01c9ef234726",
      "4d5247b6-81a3-4a86-910b-6a720fa6ba36",
      "76eab279-8537-4a6a-8781-df20b0f275b8",
      "8b3ed52a-0c9b-42cd-9804-0ab202a3704e",
      "99db5d90-4d26-4317-9fd3-d4d503be3436",
      "d2b4e12d-5afa-45e7-8327-88aecf98ae46",
      "e39f6f4a-574c-434d-ae19-2f68c45567db",
      "ec4b151b-4555-42cc-a529-cd1d5aa81ac3"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "019d24e8-2273-4028-b4ca-c0bde09338b6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.2704563294296339,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.41221266357995556
        },
        "14eb4289-32e4-4c3f-9149-bf96faa96b7a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2809568876978678,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.39955377681194193
        },
        "1b6b5a89-c894-44b3-8d3c-f01dee3fa5e8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.25203080559564106,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35106160689974764
        },
        "288ca37a-a771-4acc-a96a-ed686c3521e9": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3468464373453485,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5330428701845857
        },
        "2b34b3b2-589a-43eb-908e-3e2bedf033e5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3812395354632066,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5968790679223391
        },
        "2dce6cf0-41e0-41e8-a87c-8c3c600cfd53": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.27097083516130055,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35294167532939125
        },
        "89d7fd4d-6fde-44b7-b11f-62bad4206155": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3284489777354991,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.48265795641513576
        },
        "b2239692-8040-4005-abaa-79588eedf2b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2583731441042482,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35474629250003087
        },
        "b3eb01cd-cc71-4194-bffd-59536a9aa800": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.24365146422919526,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3173029236900189
        },
        "dd84c61f-68bf-4459-b07c-4213109da2ad": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.23296592464026097,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34893185357205636
        }
      },
      "100": {
        "019d24e8-2273-4028-b4ca-c0bde09338b6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.2704563294296339,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.41221266357995556
        },
        "14eb4289-32e4-4c3f-9149-bf96faa96b7a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2809568876978678,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.39955377681194193
        },
        "1b6b5a89-c894-44b3-8d3c-f01dee3fa5e8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.25203080559564106,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35106160689974764
        },
        "288ca37a-a771-4acc-a96a-ed686c3521e9": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3468464373453485,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5330428701845857
        },
        "2b34b3b2-589a-43eb-908e-3e2bedf033e5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3812395354632066,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5968790679223391
        },
        "2dce6cf0-41e0-41e8-a87c-8c3c600cfd53": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.27097083516130055,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35294167532939125
        },
        "89d7fd4d-6fde-44b7-b11f-62bad4206155": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3284489777354991,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.48265795641513576
        },
        "b2239692-8040-4005-abaa-79588eedf2b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2583731441042482,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35474629250003087
        },
        "b3eb01cd-cc71-4194-bffd-59536a9aa800": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.24365146422919526,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3173029236900189
        },
        "dd84c61f-68bf-4459-b07c-4213109da2ad": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.23296592464026097,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34893185357205636
        }
      },
      "25": {
        "019d24e8-2273-4028-b4ca-c0bde09338b6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.2704563294296339,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.41221266357995556
        },
        "14eb4289-32e4-4c3f-9149-bf96faa96b7a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2809568876978678,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.39955377681194193
        },
        "1b6b5a89-c894-44b3-8d3c-f01dee3fa5e8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.25203080559564106,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35106160689974764
        },
        "288ca37a-a771-4acc-a96a-ed686c3521e9": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3468464373453485,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5330428701845857
        },
        "2b34b3b2-589a-43eb-908e-3e2bedf033e5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3812395354632066,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5968790679223391
        },
        "2dce6cf0-41e0-41e8-a87c-8c3c600cfd53": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.27097083516130055,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35294167532939125
        },
        "89d7fd4d-6fde-44b7-b11f-62bad4206155": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3284489777354991,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.48265795641513576
        },
        "b2239692-8040-4005-abaa-79588eedf2b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2583731441042482,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35474629250003087
        },
        "b3eb01cd-cc71-4194-bffd-59536a9aa800": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.24365146422919526,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3173029236900189
        },
        "dd84c61f-68bf-4459-b07c-4213109da2ad": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.23296592464026097,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34893185357205636
        }
      },
      "50": {
        "019d24e8-2273-4028-b4ca-c0bde09338b6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.2704563294296339,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.41221266357995556
        },
        "14eb4289-32e4-4c3f-9149-bf96faa96b7a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2809568876978678,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.39955377681194193
        },
        "1b6b5a89-c894-44b3-8d3c-f01dee3fa5e8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.25203080559564106,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35106160689974764
        },
        "288ca37a-a771-4acc-a96a-ed686c3521e9": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3468464373453485,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5330428701845857
        },
        "2b34b3b2-589a-43eb-908e-3e2bedf033e5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3812395354632066,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5968790679223391
        },
        "2dce6cf0-41e0-41e8-a87c-8c3c600cfd53": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.27097083516130055,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35294167532939125
        },
        "89d7fd4d-6fde-44b7-b11f-62bad4206155": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3284489777354991,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.48265795641513576
        },
        "b2239692-8040-4005-abaa-79588eedf2b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2583731441042482,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35474629250003087
        },
        "b3eb01cd-cc71-4194-bffd-59536a9aa800": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.24365146422919526,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3173029236900189
        },
        "dd84c61f-68bf-4459-b07c-4213109da2ad": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.23296592464026097,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34893185357205636
        }
      }
    },
    "fixed_union_order_changed": false,
    "fixed_union_order_observable": true,
    "missing_from_pool": {
      "10": [],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "1192316e",
    "union_ids": [
      "019d24e8-2273-4028-b4ca-c0bde09338b6",
      "14eb4289-32e4-4c3f-9149-bf96faa96b7a",
      "1b6b5a89-c894-44b3-8d3c-f01dee3fa5e8",
      "288ca37a-a771-4acc-a96a-ed686c3521e9",
      "2b34b3b2-589a-43eb-908e-3e2bedf033e5",
      "2dce6cf0-41e0-41e8-a87c-8c3c600cfd53",
      "89d7fd4d-6fde-44b7-b11f-62bad4206155",
      "b2239692-8040-4005-abaa-79588eedf2b1",
      "b3eb01cd-cc71-4194-bffd-59536a9aa800",
      "dd84c61f-68bf-4459-b07c-4213109da2ad"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "14d61dee-d893-48b1-ace3-209c7c64285f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2883876741753693,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39677535311911016
        },
        "3dbe5baf-230f-4ba6-a2b4-7b509ad36720": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2800285690082731,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.38005714278491776
        },
        "6a8f4e60-0bc6-4bd6-a499-9f06b59cc6b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3256699184095932,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4753398377728607
        },
        "8b4b9dd6-888c-4874-ae9f-3b64c1654122": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30672607688566156,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42445215877811326
        },
        "9c4f8d71-fdfb-454d-8c72-932c93f4b422": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2659528377350895,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.36190567070180735
        },
        "b82a420a-4f35-4079-8273-8d1120f849b0": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.32178541863760834,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4650708322684265
        },
        "d22b792c-7756-4f47-b7dd-8f8f4e697a84": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2843189239018626,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3926378487573995
        },
        "d5276203-1597-40c2-992b-c957b70d682b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3386768719786707,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48735374634152717
        },
        "e3f4053d-b353-4c1b-8b4b-115aeaa2404c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.2711940727871291,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3863881512963041
        },
        "f30f4cdb-0196-4549-ba3d-8dc18b77cf9e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.27699086480179397,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4039817296035879
        }
      },
      "100": {
        "14d61dee-d893-48b1-ace3-209c7c64285f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2883876741753693,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39677535311911016
        },
        "3dbe5baf-230f-4ba6-a2b4-7b509ad36720": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2800285690082731,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.38005714278491776
        },
        "6a8f4e60-0bc6-4bd6-a499-9f06b59cc6b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3256699184095932,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4753398377728607
        },
        "8b4b9dd6-888c-4874-ae9f-3b64c1654122": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30672607688566156,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42445215877811326
        },
        "9c4f8d71-fdfb-454d-8c72-932c93f4b422": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2659528377350895,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.36190567070180735
        },
        "b82a420a-4f35-4079-8273-8d1120f849b0": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.32178541863760834,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4650708322684265
        },
        "d22b792c-7756-4f47-b7dd-8f8f4e697a84": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2843189239018626,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3926378487573995
        },
        "d5276203-1597-40c2-992b-c957b70d682b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3386768719786707,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48735374634152717
        },
        "e3f4053d-b353-4c1b-8b4b-115aeaa2404c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.2711940727871291,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3863881512963041
        },
        "e50b0479-8735-46bc-8812-981ebb508a01": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2728487662972275,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35569753497864076
        },
        "f30f4cdb-0196-4549-ba3d-8dc18b77cf9e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.27699086480179397,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4039817296035879
        }
      },
      "25": {
        "14d61dee-d893-48b1-ace3-209c7c64285f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2883876741753693,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39677535311911016
        },
        "3dbe5baf-230f-4ba6-a2b4-7b509ad36720": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2800285690082731,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.38005714278491776
        },
        "6a8f4e60-0bc6-4bd6-a499-9f06b59cc6b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3256699184095932,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4753398377728607
        },
        "8b4b9dd6-888c-4874-ae9f-3b64c1654122": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30672607688566156,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42445215877811326
        },
        "9c4f8d71-fdfb-454d-8c72-932c93f4b422": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2659528377350895,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.36190567070180735
        },
        "b82a420a-4f35-4079-8273-8d1120f849b0": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.32178541863760834,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4650708322684265
        },
        "d22b792c-7756-4f47-b7dd-8f8f4e697a84": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2843189239018626,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3926378487573995
        },
        "d5276203-1597-40c2-992b-c957b70d682b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3386768719786707,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48735374634152717
        },
        "e3f4053d-b353-4c1b-8b4b-115aeaa2404c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.2711940727871291,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3863881512963041
        },
        "e50b0479-8735-46bc-8812-981ebb508a01": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2728487662972275,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35569753497864076
        },
        "f30f4cdb-0196-4549-ba3d-8dc18b77cf9e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.27699086480179397,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4039817296035879
        }
      },
      "50": {
        "14d61dee-d893-48b1-ace3-209c7c64285f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2883876741753693,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39677535311911016
        },
        "3dbe5baf-230f-4ba6-a2b4-7b509ad36720": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2800285690082731,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.38005714278491776
        },
        "6a8f4e60-0bc6-4bd6-a499-9f06b59cc6b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3256699184095932,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4753398377728607
        },
        "8b4b9dd6-888c-4874-ae9f-3b64c1654122": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30672607688566156,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42445215877811326
        },
        "9c4f8d71-fdfb-454d-8c72-932c93f4b422": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2659528377350895,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.36190567070180735
        },
        "b82a420a-4f35-4079-8273-8d1120f849b0": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.32178541863760834,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4650708322684265
        },
        "d22b792c-7756-4f47-b7dd-8f8f4e697a84": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2843189239018626,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3926378487573995
        },
        "d5276203-1597-40c2-992b-c957b70d682b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3386768719786707,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48735374634152717
        },
        "e3f4053d-b353-4c1b-8b4b-115aeaa2404c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.2711940727871291,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3863881512963041
        },
        "e50b0479-8735-46bc-8812-981ebb508a01": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2728487662972275,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35569753497864076
        },
        "f30f4cdb-0196-4549-ba3d-8dc18b77cf9e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.27699086480179397,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4039817296035879
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "e50b0479-8735-46bc-8812-981ebb508a01"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "8c18457d",
    "union_ids": [
      "14d61dee-d893-48b1-ace3-209c7c64285f",
      "3dbe5baf-230f-4ba6-a2b4-7b509ad36720",
      "6a8f4e60-0bc6-4bd6-a499-9f06b59cc6b1",
      "8b4b9dd6-888c-4874-ae9f-3b64c1654122",
      "9c4f8d71-fdfb-454d-8c72-932c93f4b422",
      "b82a420a-4f35-4079-8273-8d1120f849b0",
      "d22b792c-7756-4f47-b7dd-8f8f4e697a84",
      "d5276203-1597-40c2-992b-c957b70d682b",
      "e3f4053d-b353-4c1b-8b4b-115aeaa2404c",
      "e50b0479-8735-46bc-8812-981ebb508a01",
      "f30f4cdb-0196-4549-ba3d-8dc18b77cf9e"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "1d2bd65e-8367-43bf-82f3-3fabd7a1b469": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.34595904596346827,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5119180966953081
        },
        "31b056b6-4d6b-4fef-91d1-e48908c8535b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.2960380640789239,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.43807613078045216
        },
        "4674229e-bdff-43dc-a2e2-51a923e5b824": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37452730319495053,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5690546111582726
        },
        "6627f14a-55b6-48d3-a3f8-b4e633163370": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.41429408048869953,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6485881657457706
        },
        "839f396f-8a27-4835-ad0d-20b6329893b0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3611871672185142,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5523743296686567
        },
        "8785b62d-061c-4449-b066-a674f6359892": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3511896747932168,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5291293495864335
        },
        "b2ada9bd-a860-4782-b3c7-9d51dd1b3be0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.3112602673778776,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4795205400009639
        },
        "d20f0ecd-753f-43e3-bf40-db52357f6386": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33613380024798356,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49226760526433866
        },
        "d5899419-4729-4d07-a2df-6be736a9677e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3112963064953058,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44659261394428595
        },
        "da4076ce-179d-4d28-90e2-85d144abedf0": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.3359857797410214,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5218215649895119
        }
      },
      "100": {
        "1d2bd65e-8367-43bf-82f3-3fabd7a1b469": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.34595904596346827,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5119180966953081
        },
        "31b056b6-4d6b-4fef-91d1-e48908c8535b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.2960380640789239,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.43807613078045216
        },
        "4674229e-bdff-43dc-a2e2-51a923e5b824": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37452730319495053,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5690546111582726
        },
        "4d57319a-37d7-4a09-be1e-3f35527f42bb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.30843568456295545,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42327137017495264
        },
        "6627f14a-55b6-48d3-a3f8-b4e633163370": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.41429408048869953,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6485881657457706
        },
        "839f396f-8a27-4835-ad0d-20b6329893b0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3611871672185142,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5523743296686567
        },
        "8785b62d-061c-4449-b066-a674f6359892": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3511896747932168,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5291293495864335
        },
        "b2ada9bd-a860-4782-b3c7-9d51dd1b3be0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.3112602673778776,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4795205400009639
        },
        "d20f0ecd-753f-43e3-bf40-db52357f6386": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33613380024798356,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49226760526433866
        },
        "d5899419-4729-4d07-a2df-6be736a9677e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3112963064953058,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44659261394428595
        },
        "da4076ce-179d-4d28-90e2-85d144abedf0": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.3359857797410214,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5218215649895119
        }
      },
      "25": {
        "1d2bd65e-8367-43bf-82f3-3fabd7a1b469": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.34595904596346827,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5119180966953081
        },
        "31b056b6-4d6b-4fef-91d1-e48908c8535b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.2960380640789239,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.43807613078045216
        },
        "4674229e-bdff-43dc-a2e2-51a923e5b824": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37452730319495053,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5690546111582726
        },
        "4d57319a-37d7-4a09-be1e-3f35527f42bb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.30843568456295545,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42327137017495264
        },
        "6627f14a-55b6-48d3-a3f8-b4e633163370": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.41429408048869953,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6485881657457706
        },
        "839f396f-8a27-4835-ad0d-20b6329893b0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3611871672185142,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5523743296686567
        },
        "8785b62d-061c-4449-b066-a674f6359892": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3511896747932168,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5291293495864335
        },
        "b2ada9bd-a860-4782-b3c7-9d51dd1b3be0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.3112602673778776,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4795205400009639
        },
        "d20f0ecd-753f-43e3-bf40-db52357f6386": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33613380024798356,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49226760526433866
        },
        "d5899419-4729-4d07-a2df-6be736a9677e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3112963064953058,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44659261394428595
        },
        "da4076ce-179d-4d28-90e2-85d144abedf0": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.3359857797410214,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5218215649895119
        }
      },
      "50": {
        "1d2bd65e-8367-43bf-82f3-3fabd7a1b469": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.34595904596346827,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5119180966953081
        },
        "31b056b6-4d6b-4fef-91d1-e48908c8535b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.2960380640789239,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.43807613078045216
        },
        "4674229e-bdff-43dc-a2e2-51a923e5b824": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37452730319495053,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5690546111582726
        },
        "4d57319a-37d7-4a09-be1e-3f35527f42bb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.30843568456295545,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42327137017495264
        },
        "6627f14a-55b6-48d3-a3f8-b4e633163370": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.41429408048869953,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6485881657457706
        },
        "839f396f-8a27-4835-ad0d-20b6329893b0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3611871672185142,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5523743296686567
        },
        "8785b62d-061c-4449-b066-a674f6359892": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3511896747932168,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5291293495864335
        },
        "b2ada9bd-a860-4782-b3c7-9d51dd1b3be0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.3112602673778776,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4795205400009639
        },
        "d20f0ecd-753f-43e3-bf40-db52357f6386": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33613380024798356,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.49226760526433866
        },
        "d5899419-4729-4d07-a2df-6be736a9677e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3112963064953058,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44659261394428595
        },
        "da4076ce-179d-4d28-90e2-85d144abedf0": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.3359857797410214,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5218215649895119
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "4d57319a-37d7-4a09-be1e-3f35527f42bb"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "gpt4_4edbafa2",
    "union_ids": [
      "1d2bd65e-8367-43bf-82f3-3fabd7a1b469",
      "31b056b6-4d6b-4fef-91d1-e48908c8535b",
      "4674229e-bdff-43dc-a2e2-51a923e5b824",
      "4d57319a-37d7-4a09-be1e-3f35527f42bb",
      "6627f14a-55b6-48d3-a3f8-b4e633163370",
      "839f396f-8a27-4835-ad0d-20b6329893b0",
      "8785b62d-061c-4449-b066-a674f6359892",
      "b2ada9bd-a860-4782-b3c7-9d51dd1b3be0",
      "d20f0ecd-753f-43e3-bf40-db52357f6386",
      "d5899419-4729-4d07-a2df-6be736a9677e",
      "da4076ce-179d-4d28-90e2-85d144abedf0"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "1ad28629-08c7-4603-a33e-ca1dcc1f3ad2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.3424086194796849,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5418172442045784
        },
        "21ac11f9-9689-4571-af6f-3c5c7c858260": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.4128114389547352,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.645622882677842
        },
        "23332c07-93c4-4d08-b471-8ea9dd5d0b98": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3206053824137345,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4572107614896088
        },
        "56e84e01-2e6c-4a0d-8733-fd5fefc95a95": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.30186102140490717,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4537220428098143
        },
        "6421b58d-b903-49d3-8974-94bf9836f493": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3531604929423079,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5083209911298245
        },
        "645b29cc-0e75-4ead-aeef-b0d8e2adfafb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.31579366949401466,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.43798734003707107
        },
        "89da3ae0-f84d-41d2-9a42-2062006e0e94": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3066093390188548,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.41521868328291833
        },
        "a6166635-6416-49aa-9742-e5bf43ea607d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.31118159708274884,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42436319941070644
        },
        "a8e3e3b2-4281-4426-8e25-e2a82328f9f5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.38061214042254077,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5632242860902902
        },
        "fe789a22-c311-4fad-86fd-9055ea1f828d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3554170446802919,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5084340856889377
        }
      },
      "100": {
        "1ad28629-08c7-4603-a33e-ca1dcc1f3ad2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.3424086194796849,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5418172442045784
        },
        "21ac11f9-9689-4571-af6f-3c5c7c858260": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.4128114389547352,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.645622882677842
        },
        "23332c07-93c4-4d08-b471-8ea9dd5d0b98": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3206053824137345,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4572107614896088
        },
        "56e84e01-2e6c-4a0d-8733-fd5fefc95a95": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.30186102140490717,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4537220428098143
        },
        "6421b58d-b903-49d3-8974-94bf9836f493": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3531604929423079,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5083209911298245
        },
        "645b29cc-0e75-4ead-aeef-b0d8e2adfafb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.31579366949401466,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.43798734003707107
        },
        "89da3ae0-f84d-41d2-9a42-2062006e0e94": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3066093390188548,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.41521868328291833
        },
        "a6166635-6416-49aa-9742-e5bf43ea607d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.31118159708274884,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42436319941070644
        },
        "a8e3e3b2-4281-4426-8e25-e2a82328f9f5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.38061214042254077,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5632242860902902
        },
        "fe789a22-c311-4fad-86fd-9055ea1f828d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3554170446802919,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5084340856889377
        }
      },
      "25": {
        "1ad28629-08c7-4603-a33e-ca1dcc1f3ad2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.3424086194796849,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5418172442045784
        },
        "21ac11f9-9689-4571-af6f-3c5c7c858260": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.4128114389547352,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.645622882677842
        },
        "23332c07-93c4-4d08-b471-8ea9dd5d0b98": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3206053824137345,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4572107614896088
        },
        "56e84e01-2e6c-4a0d-8733-fd5fefc95a95": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.30186102140490717,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4537220428098143
        },
        "6421b58d-b903-49d3-8974-94bf9836f493": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3531604929423079,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5083209911298245
        },
        "645b29cc-0e75-4ead-aeef-b0d8e2adfafb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.31579366949401466,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.43798734003707107
        },
        "89da3ae0-f84d-41d2-9a42-2062006e0e94": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3066093390188548,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.41521868328291833
        },
        "a6166635-6416-49aa-9742-e5bf43ea607d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.31118159708274884,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42436319941070644
        },
        "a8e3e3b2-4281-4426-8e25-e2a82328f9f5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.38061214042254077,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5632242860902902
        },
        "fe789a22-c311-4fad-86fd-9055ea1f828d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3554170446802919,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5084340856889377
        }
      },
      "50": {
        "1ad28629-08c7-4603-a33e-ca1dcc1f3ad2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.3424086194796849,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5418172442045784
        },
        "21ac11f9-9689-4571-af6f-3c5c7c858260": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.4128114389547352,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.645622882677842
        },
        "23332c07-93c4-4d08-b471-8ea9dd5d0b98": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3206053824137345,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4572107614896088
        },
        "56e84e01-2e6c-4a0d-8733-fd5fefc95a95": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.30186102140490717,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4537220428098143
        },
        "6421b58d-b903-49d3-8974-94bf9836f493": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3531604929423079,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5083209911298245
        },
        "645b29cc-0e75-4ead-aeef-b0d8e2adfafb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.31579366949401466,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.43798734003707107
        },
        "89da3ae0-f84d-41d2-9a42-2062006e0e94": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3066093390188548,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.41521868328291833
        },
        "a6166635-6416-49aa-9742-e5bf43ea607d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.31118159708274884,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42436319941070644
        },
        "a8e3e3b2-4281-4426-8e25-e2a82328f9f5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.38061214042254077,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5632242860902902
        },
        "fe789a22-c311-4fad-86fd-9055ea1f828d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3554170446802919,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5084340856889377
        }
      }
    },
    "fixed_union_order_changed": false,
    "fixed_union_order_observable": true,
    "missing_from_pool": {
      "10": [],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "6613b389",
    "union_ids": [
      "1ad28629-08c7-4603-a33e-ca1dcc1f3ad2",
      "21ac11f9-9689-4571-af6f-3c5c7c858260",
      "23332c07-93c4-4d08-b471-8ea9dd5d0b98",
      "56e84e01-2e6c-4a0d-8733-fd5fefc95a95",
      "6421b58d-b903-49d3-8974-94bf9836f493",
      "645b29cc-0e75-4ead-aeef-b0d8e2adfafb",
      "89da3ae0-f84d-41d2-9a42-2062006e0e94",
      "a6166635-6416-49aa-9742-e5bf43ea607d",
      "a8e3e3b2-4281-4426-8e25-e2a82328f9f5",
      "fe789a22-c311-4fad-86fd-9055ea1f828d"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "06b9595c-8a60-495f-a82d-4ddbad5395b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3741533175750079,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5899066360083227
        },
        "260a48da-5052-46da-ab90-9a50900e6643": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.29645486114043296,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43990971798933154
        },
        "3da13db5-4610-43e8-a834-616ac6f434cd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24163928999994116,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.32127858429141676
        },
        "55932962-766c-4326-adf2-1f8a92a2c155": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.22254593686462953,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2621398747206035
        },
        "5c86cad6-2b6c-480e-a334-23e022bd06cd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21770051370774612,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30670103213618005
        },
        "71b47d81-73b4-43d5-bc43-3fd9e2b9218b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2353891074594788,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3177782106274232
        },
        "84606e07-5268-4419-b8f7-0d170ff924de": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.26275430751646145,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3635086193244573
        },
        "86283522-09be-4417-93fa-1a65abffa541": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.22156365085670424,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29462730171340845
        },
        "8afc88ec-0996-4543-b573-ebba2cb9860a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.23545618486643938,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34221237445356656
        },
        "a3fe9a19-10f8-4ece-8678-142bda2572fb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.30939730763965606,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4711946165667724
        }
      },
      "100": {
        "06b9595c-8a60-495f-a82d-4ddbad5395b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3741533175750079,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5899066360083227
        },
        "260a48da-5052-46da-ab90-9a50900e6643": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.29645486114043296,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43990971798933154
        },
        "3da13db5-4610-43e8-a834-616ac6f434cd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24163928999994116,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.32127858429141676
        },
        "55932962-766c-4326-adf2-1f8a92a2c155": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.22254593686462953,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2621398747206035
        },
        "5c86cad6-2b6c-480e-a334-23e022bd06cd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21770051370774612,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30670103213618005
        },
        "71b47d81-73b4-43d5-bc43-3fd9e2b9218b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2353891074594788,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3177782106274232
        },
        "84606e07-5268-4419-b8f7-0d170ff924de": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.26275430751646145,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3635086193244573
        },
        "86283522-09be-4417-93fa-1a65abffa541": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.22156365085670424,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29462730171340845
        },
        "8afc88ec-0996-4543-b573-ebba2cb9860a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.23545618486643938,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34221237445356656
        },
        "a3fe9a19-10f8-4ece-8678-142bda2572fb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.30939730763965606,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4711946165667724
        }
      },
      "25": {
        "06b9595c-8a60-495f-a82d-4ddbad5395b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3741533175750079,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5899066360083227
        },
        "260a48da-5052-46da-ab90-9a50900e6643": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.29645486114043296,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43990971798933154
        },
        "3da13db5-4610-43e8-a834-616ac6f434cd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24163928999994116,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.32127858429141676
        },
        "55932962-766c-4326-adf2-1f8a92a2c155": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.22254593686462953,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2621398747206035
        },
        "5c86cad6-2b6c-480e-a334-23e022bd06cd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21770051370774612,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30670103213618005
        },
        "71b47d81-73b4-43d5-bc43-3fd9e2b9218b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2353891074594788,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3177782106274232
        },
        "84606e07-5268-4419-b8f7-0d170ff924de": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.26275430751646145,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3635086193244573
        },
        "86283522-09be-4417-93fa-1a65abffa541": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.22156365085670424,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29462730171340845
        },
        "8afc88ec-0996-4543-b573-ebba2cb9860a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.23545618486643938,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34221237445356656
        },
        "a3fe9a19-10f8-4ece-8678-142bda2572fb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.30939730763965606,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4711946165667724
        }
      },
      "50": {
        "06b9595c-8a60-495f-a82d-4ddbad5395b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3741533175750079,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5899066360083227
        },
        "260a48da-5052-46da-ab90-9a50900e6643": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.29645486114043296,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43990971798933154
        },
        "3da13db5-4610-43e8-a834-616ac6f434cd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24163928999994116,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.32127858429141676
        },
        "55932962-766c-4326-adf2-1f8a92a2c155": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.22254593686462953,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2621398747206035
        },
        "5c86cad6-2b6c-480e-a334-23e022bd06cd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21770051370774612,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30670103213618005
        },
        "71b47d81-73b4-43d5-bc43-3fd9e2b9218b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2353891074594788,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3177782106274232
        },
        "84606e07-5268-4419-b8f7-0d170ff924de": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.26275430751646145,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3635086193244573
        },
        "86283522-09be-4417-93fa-1a65abffa541": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.22156365085670424,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29462730171340845
        },
        "8afc88ec-0996-4543-b573-ebba2cb9860a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.23545618486643938,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34221237445356656
        },
        "a3fe9a19-10f8-4ece-8678-142bda2572fb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.30939730763965606,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4711946165667724
        }
      }
    },
    "fixed_union_order_changed": false,
    "fixed_union_order_observable": true,
    "missing_from_pool": {
      "10": [],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "gpt4_af6db32f",
    "union_ids": [
      "06b9595c-8a60-495f-a82d-4ddbad5395b1",
      "260a48da-5052-46da-ab90-9a50900e6643",
      "3da13db5-4610-43e8-a834-616ac6f434cd",
      "55932962-766c-4326-adf2-1f8a92a2c155",
      "5c86cad6-2b6c-480e-a334-23e022bd06cd",
      "71b47d81-73b4-43d5-bc43-3fd9e2b9218b",
      "84606e07-5268-4419-b8f7-0d170ff924de",
      "86283522-09be-4417-93fa-1a65abffa541",
      "8afc88ec-0996-4543-b573-ebba2cb9860a",
      "a3fe9a19-10f8-4ece-8678-142bda2572fb"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "0bfb4843-b1bc-4b46-a18f-254032fb872d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.40832943176956676,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6266588659233193
        },
        "103d6b40-e82f-479f-b7b2-1cb02ed3943b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.3294861580113176,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4829723134000308
        },
        "3f07610c-3721-4e25-8116-b8205bc861a9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.37562557934265867,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5812511539169457
        },
        "52e7cd4e-fc21-425f-8876-27a34e32d891": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30121436709860816,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42422873891790414
        },
        "62c82885-c459-46b4-bdb6-d2d5a3248f2d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37219290443252123,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5823858131565769
        },
        "7619a299-107f-4af8-a559-45820bab25a3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.370707738817736,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5434154828806808
        },
        "852ac327-5fc1-45a6-8296-d1bc9e319553": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30043951274707975,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43887902978569393
        },
        "86117a59-efb8-4933-b303-9214da8f8c86": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37969913489095947,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5973982740734534
        },
        "9a0f6f86-496a-47e6-8813-9231594cbf2f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.40031447947394033,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6602289640977219
        },
        "ae19a3f3-ff23-42ec-96c5-e6855a205b6d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2937575013996678,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42551500709087
        }
      },
      "100": {
        "0bfb4843-b1bc-4b46-a18f-254032fb872d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.40832943176956676,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6266588659233193
        },
        "103d6b40-e82f-479f-b7b2-1cb02ed3943b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.3294861580113176,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4829723134000308
        },
        "3f07610c-3721-4e25-8116-b8205bc861a9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.37562557934265867,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5812511539169457
        },
        "52e7cd4e-fc21-425f-8876-27a34e32d891": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30121436709860816,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42422873891790414
        },
        "62c82885-c459-46b4-bdb6-d2d5a3248f2d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37219290443252123,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5823858131565769
        },
        "7619a299-107f-4af8-a559-45820bab25a3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.370707738817736,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5434154828806808
        },
        "852ac327-5fc1-45a6-8296-d1bc9e319553": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30043951274707975,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43887902978569393
        },
        "86117a59-efb8-4933-b303-9214da8f8c86": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37969913489095947,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5973982740734534
        },
        "9a0f6f86-496a-47e6-8813-9231594cbf2f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.40031447947394033,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6602289640977219
        },
        "ae19a3f3-ff23-42ec-96c5-e6855a205b6d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2937575013996678,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42551500709087
        },
        "ae9a76ee-7088-4037-b167-6a141703e833": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2953832656868749,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4125665360944376
        }
      },
      "25": {
        "0bfb4843-b1bc-4b46-a18f-254032fb872d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.40832943176956676,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6266588659233193
        },
        "103d6b40-e82f-479f-b7b2-1cb02ed3943b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.3294861580113176,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4829723134000308
        },
        "3f07610c-3721-4e25-8116-b8205bc861a9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.37562557934265867,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5812511539169457
        },
        "52e7cd4e-fc21-425f-8876-27a34e32d891": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30121436709860816,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42422873891790414
        },
        "62c82885-c459-46b4-bdb6-d2d5a3248f2d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37219290443252123,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5823858131565769
        },
        "7619a299-107f-4af8-a559-45820bab25a3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.370707738817736,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5434154828806808
        },
        "852ac327-5fc1-45a6-8296-d1bc9e319553": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30043951274707975,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43887902978569393
        },
        "86117a59-efb8-4933-b303-9214da8f8c86": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37969913489095947,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5973982740734534
        },
        "9a0f6f86-496a-47e6-8813-9231594cbf2f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.40031447947394033,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6602289640977219
        },
        "ae19a3f3-ff23-42ec-96c5-e6855a205b6d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2937575013996678,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42551500709087
        },
        "ae9a76ee-7088-4037-b167-6a141703e833": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2953832656868749,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4125665360944376
        }
      },
      "50": {
        "0bfb4843-b1bc-4b46-a18f-254032fb872d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.40832943176956676,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6266588659233193
        },
        "103d6b40-e82f-479f-b7b2-1cb02ed3943b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.3294861580113176,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4829723134000308
        },
        "3f07610c-3721-4e25-8116-b8205bc861a9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.37562557934265867,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5812511539169457
        },
        "52e7cd4e-fc21-425f-8876-27a34e32d891": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30121436709860816,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42422873891790414
        },
        "62c82885-c459-46b4-bdb6-d2d5a3248f2d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37219290443252123,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5823858131565769
        },
        "7619a299-107f-4af8-a559-45820bab25a3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.370707738817736,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5434154828806808
        },
        "852ac327-5fc1-45a6-8296-d1bc9e319553": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30043951274707975,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43887902978569393
        },
        "86117a59-efb8-4933-b303-9214da8f8c86": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37969913489095947,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5973982740734534
        },
        "9a0f6f86-496a-47e6-8813-9231594cbf2f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.40031447947394033,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6602289640977219
        },
        "ae19a3f3-ff23-42ec-96c5-e6855a205b6d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2937575013996678,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42551500709087
        },
        "ae9a76ee-7088-4037-b167-6a141703e833": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2953832656868749,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4125665360944376
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "ae9a76ee-7088-4037-b167-6a141703e833"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "gpt4_b0863698",
    "union_ids": [
      "0bfb4843-b1bc-4b46-a18f-254032fb872d",
      "103d6b40-e82f-479f-b7b2-1cb02ed3943b",
      "3f07610c-3721-4e25-8116-b8205bc861a9",
      "52e7cd4e-fc21-425f-8876-27a34e32d891",
      "62c82885-c459-46b4-bdb6-d2d5a3248f2d",
      "7619a299-107f-4af8-a559-45820bab25a3",
      "852ac327-5fc1-45a6-8296-d1bc9e319553",
      "86117a59-efb8-4933-b303-9214da8f8c86",
      "9a0f6f86-496a-47e6-8813-9231594cbf2f",
      "ae19a3f3-ff23-42ec-96c5-e6855a205b6d",
      "ae9a76ee-7088-4037-b167-6a141703e833"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "284cbc98-f881-498a-ad3a-a8106656fe7a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.29089462808119965,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5102892587850036
        },
        "6731ee99-ca2e-4498-987d-3d25b9feac39": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2426143876811769,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3950287761491351
        },
        "93baf16e-fdf4-470c-8ee5-94530d9f88f8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.23415022186660936,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3803004424219165
        },
        "948cc885-1185-48e1-a3d4-292535bd4c76": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21589222534735825,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36028445331732084
        },
        "9aa7f59d-8f5c-4a69-9d8e-f05e97ba164f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.23214897773352666,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3927979580896577
        },
        "c9e8262b-d295-4c89-945f-30fadaa49f00": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.34406274970248096,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6041254981532643
        },
        "cc15c8a8-c7f8-45c7-b6eb-6d0f3e3aa3ff": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.22982654851252246,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3551530983363471
        },
        "ef8a644a-f7bf-4093-94df-bef919024c46": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.23329637013847163,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39159274027694324
        },
        "f09df78b-c706-40a3-8daf-5741de361829": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.45790517309571926,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7574103470497454
        },
        "fd58c1ec-7871-4276-9675-eb401dc5f550": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.4205200981138663,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7460401974198255
        }
      },
      "100": {
        "284cbc98-f881-498a-ad3a-a8106656fe7a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.29089462808119965,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5102892587850036
        },
        "6731ee99-ca2e-4498-987d-3d25b9feac39": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2426143876811769,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3950287761491351
        },
        "885d0c21-0320-4b11-b85f-01b6aa779eb4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24391410165767274,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3096282080360333
        },
        "93baf16e-fdf4-470c-8ee5-94530d9f88f8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.23415022186660936,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3803004424219165
        },
        "948cc885-1185-48e1-a3d4-292535bd4c76": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21589222534735825,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36028445331732084
        },
        "9aa7f59d-8f5c-4a69-9d8e-f05e97ba164f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.23214897773352666,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3927979580896577
        },
        "c9e8262b-d295-4c89-945f-30fadaa49f00": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.34406274970248096,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6041254981532643
        },
        "cc15c8a8-c7f8-45c7-b6eb-6d0f3e3aa3ff": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.22982654851252246,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3551530983363471
        },
        "ef8a644a-f7bf-4093-94df-bef919024c46": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.23329637013847163,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39159274027694324
        },
        "f09df78b-c706-40a3-8daf-5741de361829": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.45790517309571926,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7574103470497454
        },
        "fd58c1ec-7871-4276-9675-eb401dc5f550": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.4205200981138663,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7460401974198255
        }
      },
      "25": {
        "284cbc98-f881-498a-ad3a-a8106656fe7a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.29089462808119965,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5102892587850036
        },
        "6731ee99-ca2e-4498-987d-3d25b9feac39": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2426143876811769,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3950287761491351
        },
        "885d0c21-0320-4b11-b85f-01b6aa779eb4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24391410165767274,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3096282080360333
        },
        "93baf16e-fdf4-470c-8ee5-94530d9f88f8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.23415022186660936,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3803004424219165
        },
        "948cc885-1185-48e1-a3d4-292535bd4c76": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21589222534735825,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36028445331732084
        },
        "9aa7f59d-8f5c-4a69-9d8e-f05e97ba164f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.23214897773352666,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3927979580896577
        },
        "c9e8262b-d295-4c89-945f-30fadaa49f00": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.34406274970248096,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6041254981532643
        },
        "cc15c8a8-c7f8-45c7-b6eb-6d0f3e3aa3ff": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.22982654851252246,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3551530983363471
        },
        "ef8a644a-f7bf-4093-94df-bef919024c46": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.23329637013847163,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39159274027694324
        },
        "f09df78b-c706-40a3-8daf-5741de361829": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.45790517309571926,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7574103470497454
        },
        "fd58c1ec-7871-4276-9675-eb401dc5f550": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.4205200981138663,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7460401974198255
        }
      },
      "50": {
        "284cbc98-f881-498a-ad3a-a8106656fe7a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.29089462808119965,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5102892587850036
        },
        "6731ee99-ca2e-4498-987d-3d25b9feac39": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2426143876811769,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3950287761491351
        },
        "885d0c21-0320-4b11-b85f-01b6aa779eb4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24391410165767274,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3096282080360333
        },
        "93baf16e-fdf4-470c-8ee5-94530d9f88f8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.23415022186660936,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3803004424219165
        },
        "948cc885-1185-48e1-a3d4-292535bd4c76": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21589222534735825,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36028445331732084
        },
        "9aa7f59d-8f5c-4a69-9d8e-f05e97ba164f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.23214897773352666,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3927979580896577
        },
        "c9e8262b-d295-4c89-945f-30fadaa49f00": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.34406274970248096,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6041254981532643
        },
        "cc15c8a8-c7f8-45c7-b6eb-6d0f3e3aa3ff": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.22982654851252246,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3551530983363471
        },
        "ef8a644a-f7bf-4093-94df-bef919024c46": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.23329637013847163,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39159274027694324
        },
        "f09df78b-c706-40a3-8daf-5741de361829": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.45790517309571926,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7574103470497454
        },
        "fd58c1ec-7871-4276-9675-eb401dc5f550": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.4205200981138663,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7460401974198255
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "885d0c21-0320-4b11-b85f-01b6aa779eb4"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "852ce960",
    "union_ids": [
      "284cbc98-f881-498a-ad3a-a8106656fe7a",
      "6731ee99-ca2e-4498-987d-3d25b9feac39",
      "885d0c21-0320-4b11-b85f-01b6aa779eb4",
      "93baf16e-fdf4-470c-8ee5-94530d9f88f8",
      "948cc885-1185-48e1-a3d4-292535bd4c76",
      "9aa7f59d-8f5c-4a69-9d8e-f05e97ba164f",
      "c9e8262b-d295-4c89-945f-30fadaa49f00",
      "cc15c8a8-c7f8-45c7-b6eb-6d0f3e3aa3ff",
      "ef8a644a-f7bf-4093-94df-bef919024c46",
      "f09df78b-c706-40a3-8daf-5741de361829",
      "fd58c1ec-7871-4276-9675-eb401dc5f550"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "22038adb-e7a4-47bd-bee0-5d07aeed9d51": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.36559593619377573,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6219918763929836
        },
        "2e366f51-16ca-442a-b317-bb6d92b00cbb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3535792095788908,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5249984158533
        },
        "44d51c1b-30e7-4164-9818-a98638fad566": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3803742861718942,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5726485747041323
        },
        "5ba44dc8-a2c2-46c4-9527-77f4787f4d3c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.44324037425291807,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.69838075086618
        },
        "878e846f-a90b-4d60-a908-ff93b5c6eb5a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3304942487606731,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4953884945172722
        },
        "8edd0158-e0b1-4f4a-9af1-667baae874fd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.41278499089005166,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6434099784756218
        },
        "92c96a4d-51fc-44b5-bf0f-0fa2a19c1f7d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.45108065099133643,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7365612989785988
        },
        "9fbdfd6f-ccb9-405a-ab8c-ee5fc792e53e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3903932558311083,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6124865069415286
        },
        "b4ae068b-4c06-4a4d-be7f-cc8f47551315": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.42488627546126145,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7167725525914529
        },
        "fe247f89-28df-4900-9dba-4f61811d911c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3304314136930593,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.478702824081637
        }
      },
      "100": {
        "22038adb-e7a4-47bd-bee0-5d07aeed9d51": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.36559593619377573,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6219918763929836
        },
        "2e366f51-16ca-442a-b317-bb6d92b00cbb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3535792095788908,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5249984158533
        },
        "44d51c1b-30e7-4164-9818-a98638fad566": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3803742861718942,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5726485747041323
        },
        "5ba44dc8-a2c2-46c4-9527-77f4787f4d3c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.44324037425291807,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.69838075086618
        },
        "878e846f-a90b-4d60-a908-ff93b5c6eb5a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3304942487606731,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4953884945172722
        },
        "8edd0158-e0b1-4f4a-9af1-667baae874fd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.41278499089005166,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6434099784756218
        },
        "92c96a4d-51fc-44b5-bf0f-0fa2a19c1f7d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.45108065099133643,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7365612989785988
        },
        "9fbdfd6f-ccb9-405a-ab8c-ee5fc792e53e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3903932558311083,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6124865069415286
        },
        "b4ae068b-4c06-4a4d-be7f-cc8f47551315": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.42488627546126145,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7167725525914529
        },
        "fe247f89-28df-4900-9dba-4f61811d911c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3304314136930593,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.478702824081637
        }
      },
      "25": {
        "22038adb-e7a4-47bd-bee0-5d07aeed9d51": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.36559593619377573,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6219918763929836
        },
        "2e366f51-16ca-442a-b317-bb6d92b00cbb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3535792095788908,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5249984158533
        },
        "44d51c1b-30e7-4164-9818-a98638fad566": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3803742861718942,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5726485747041323
        },
        "5ba44dc8-a2c2-46c4-9527-77f4787f4d3c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.44324037425291807,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.69838075086618
        },
        "878e846f-a90b-4d60-a908-ff93b5c6eb5a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3304942487606731,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4953884945172722
        },
        "8edd0158-e0b1-4f4a-9af1-667baae874fd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.41278499089005166,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6434099784756218
        },
        "92c96a4d-51fc-44b5-bf0f-0fa2a19c1f7d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.45108065099133643,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7365612989785988
        },
        "9fbdfd6f-ccb9-405a-ab8c-ee5fc792e53e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3903932558311083,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6124865069415286
        },
        "b4ae068b-4c06-4a4d-be7f-cc8f47551315": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.42488627546126145,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7167725525914529
        },
        "fe247f89-28df-4900-9dba-4f61811d911c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3304314136930593,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.478702824081637
        }
      },
      "50": {
        "22038adb-e7a4-47bd-bee0-5d07aeed9d51": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.36559593619377573,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6219918763929836
        },
        "2e366f51-16ca-442a-b317-bb6d92b00cbb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3535792095788908,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5249984158533
        },
        "44d51c1b-30e7-4164-9818-a98638fad566": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3803742861718942,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5726485747041323
        },
        "5ba44dc8-a2c2-46c4-9527-77f4787f4d3c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.44324037425291807,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.69838075086618
        },
        "878e846f-a90b-4d60-a908-ff93b5c6eb5a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3304942487606731,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4953884945172722
        },
        "8edd0158-e0b1-4f4a-9af1-667baae874fd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.41278499089005166,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6434099784756218
        },
        "92c96a4d-51fc-44b5-bf0f-0fa2a19c1f7d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.45108065099133643,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7365612989785988
        },
        "9fbdfd6f-ccb9-405a-ab8c-ee5fc792e53e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3903932558311083,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6124865069415286
        },
        "b4ae068b-4c06-4a4d-be7f-cc8f47551315": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.42488627546126145,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7167725525914529
        },
        "fe247f89-28df-4900-9dba-4f61811d911c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3304314136930593,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.478702824081637
        }
      }
    },
    "fixed_union_order_changed": false,
    "fixed_union_order_observable": true,
    "missing_from_pool": {
      "10": [],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "6a1eabeb",
    "union_ids": [
      "22038adb-e7a4-47bd-bee0-5d07aeed9d51",
      "2e366f51-16ca-442a-b317-bb6d92b00cbb",
      "44d51c1b-30e7-4164-9818-a98638fad566",
      "5ba44dc8-a2c2-46c4-9527-77f4787f4d3c",
      "878e846f-a90b-4d60-a908-ff93b5c6eb5a",
      "8edd0158-e0b1-4f4a-9af1-667baae874fd",
      "92c96a4d-51fc-44b5-bf0f-0fa2a19c1f7d",
      "9fbdfd6f-ccb9-405a-ab8c-ee5fc792e53e",
      "b4ae068b-4c06-4a4d-be7f-cc8f47551315",
      "fe247f89-28df-4900-9dba-4f61811d911c"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 13,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 13,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 14,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "16305a4c-9d34-4463-83c8-34591fae7de3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.22876797128340082,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33153594590466173
        },
        "1faafe1b-f1b0-48d2-9361-1a5cf8c53ed3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.26967789520277036,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35935579517391225
        },
        "21561fab-fe8d-4ba2-ad49-21e9e0215bfa": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3454520761208204,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5512541539940173
        },
        "3f917a80-3186-45ea-92ad-447387eca6ec": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2094982374225801,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31399647484516024
        },
        "417ac9c6-eb63-44bd-a17a-d5ba2ccd5322": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3446618856778742,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5633237746936085
        },
        "543029e8-201e-4ad2-a648-204013b62223": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2412009057269845,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3564018147918291
        },
        "62fe6faa-ec15-4e0f-84cd-ead67fb3ea69": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.22090981660229658,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31581963654245326
        },
        "b4e1d469-b4fc-4fe8-a66f-e85143c8c66c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.22045111196872028,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3261022249387986
        },
        "c5b1bf27-c38c-4c62-a090-a5cd19303418": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.4870188217917862,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7900376402457122
        },
        "ff59a191-8489-48f3-b136-68cae8b41cb8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.4495629787114942,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7703259550864864
        }
      },
      "100": {
        "16305a4c-9d34-4463-83c8-34591fae7de3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.22876797128340082,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33153594590466173
        },
        "1faafe1b-f1b0-48d2-9361-1a5cf8c53ed3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.26967789520277036,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35935579517391225
        },
        "21561fab-fe8d-4ba2-ad49-21e9e0215bfa": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3454520761208204,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5512541539940173
        },
        "3f917a80-3186-45ea-92ad-447387eca6ec": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2094982374225801,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31399647484516024
        },
        "417ac9c6-eb63-44bd-a17a-d5ba2ccd5322": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3446618856778742,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5633237746936085
        },
        "543029e8-201e-4ad2-a648-204013b62223": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2412009057269845,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3564018147918291
        },
        "62fe6faa-ec15-4e0f-84cd-ead67fb3ea69": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.22090981660229658,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31581963654245326
        },
        "8853b45b-94a7-40f2-a6ad-daf228b260c0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.23537381152122006,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.30514762003836604
        },
        "8df23015-afa0-4070-af86-1c208f63f853": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2290204622443952,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.27984092920947823
        },
        "b4e1d469-b4fc-4fe8-a66f-e85143c8c66c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.22045111196872028,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3261022249387986
        },
        "c5b1bf27-c38c-4c62-a090-a5cd19303418": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.4870188217917862,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7900376402457122
        },
        "d1837777-5d06-4a66-8899-f9e421cbb2c9": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.23060193366773257,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2579238684369589
        },
        "f93fd873-3711-4c06-a2a4-fc85249b8d70": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2277193735488364,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.28119874804181033
        },
        "ff59a191-8489-48f3-b136-68cae8b41cb8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.4495629787114942,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7703259550864864
        }
      },
      "25": {
        "16305a4c-9d34-4463-83c8-34591fae7de3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.22876797128340082,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33153594590466173
        },
        "1faafe1b-f1b0-48d2-9361-1a5cf8c53ed3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.26967789520277036,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35935579517391225
        },
        "21561fab-fe8d-4ba2-ad49-21e9e0215bfa": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3454520761208204,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5512541539940173
        },
        "3f917a80-3186-45ea-92ad-447387eca6ec": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2094982374225801,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31399647484516024
        },
        "417ac9c6-eb63-44bd-a17a-d5ba2ccd5322": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3446618856778742,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5633237746936085
        },
        "543029e8-201e-4ad2-a648-204013b62223": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2412009057269845,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3564018147918291
        },
        "62fe6faa-ec15-4e0f-84cd-ead67fb3ea69": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.22090981660229658,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31581963654245326
        },
        "8853b45b-94a7-40f2-a6ad-daf228b260c0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.23537381152122006,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.30514762003836604
        },
        "8df23015-afa0-4070-af86-1c208f63f853": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2290204622443952,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.27984092920947823
        },
        "b4e1d469-b4fc-4fe8-a66f-e85143c8c66c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.22045111196872028,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3261022249387986
        },
        "c5b1bf27-c38c-4c62-a090-a5cd19303418": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.4870188217917862,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7900376402457122
        },
        "f93fd873-3711-4c06-a2a4-fc85249b8d70": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2277193735488364,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.28119874804181033
        },
        "ff59a191-8489-48f3-b136-68cae8b41cb8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.4495629787114942,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7703259550864864
        }
      },
      "50": {
        "16305a4c-9d34-4463-83c8-34591fae7de3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.22876797128340082,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33153594590466173
        },
        "1faafe1b-f1b0-48d2-9361-1a5cf8c53ed3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.26967789520277036,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35935579517391225
        },
        "21561fab-fe8d-4ba2-ad49-21e9e0215bfa": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3454520761208204,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5512541539940173
        },
        "3f917a80-3186-45ea-92ad-447387eca6ec": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2094982374225801,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31399647484516024
        },
        "417ac9c6-eb63-44bd-a17a-d5ba2ccd5322": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3446618856778742,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5633237746936085
        },
        "543029e8-201e-4ad2-a648-204013b62223": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2412009057269845,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3564018147918291
        },
        "62fe6faa-ec15-4e0f-84cd-ead67fb3ea69": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.22090981660229658,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31581963654245326
        },
        "8853b45b-94a7-40f2-a6ad-daf228b260c0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.23537381152122006,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.30514762003836604
        },
        "8df23015-afa0-4070-af86-1c208f63f853": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2290204622443952,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.27984092920947823
        },
        "b4e1d469-b4fc-4fe8-a66f-e85143c8c66c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.22045111196872028,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3261022249387986
        },
        "c5b1bf27-c38c-4c62-a090-a5cd19303418": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.4870188217917862,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7900376402457122
        },
        "d1837777-5d06-4a66-8899-f9e421cbb2c9": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.23060193366773257,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2579238684369589
        },
        "f93fd873-3711-4c06-a2a4-fc85249b8d70": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2277193735488364,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.28119874804181033
        },
        "ff59a191-8489-48f3-b136-68cae8b41cb8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.4495629787114942,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7703259550864864
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "8853b45b-94a7-40f2-a6ad-daf228b260c0",
        "8df23015-afa0-4070-af86-1c208f63f853",
        "d1837777-5d06-4a66-8899-f9e421cbb2c9",
        "f93fd873-3711-4c06-a2a4-fc85249b8d70"
      ],
      "100": [],
      "25": [
        "d1837777-5d06-4a66-8899-f9e421cbb2c9"
      ],
      "50": []
    },
    "question_id": "5831f84d",
    "union_ids": [
      "16305a4c-9d34-4463-83c8-34591fae7de3",
      "1faafe1b-f1b0-48d2-9361-1a5cf8c53ed3",
      "21561fab-fe8d-4ba2-ad49-21e9e0215bfa",
      "3f917a80-3186-45ea-92ad-447387eca6ec",
      "417ac9c6-eb63-44bd-a17a-d5ba2ccd5322",
      "543029e8-201e-4ad2-a648-204013b62223",
      "62fe6faa-ec15-4e0f-84cd-ead67fb3ea69",
      "8853b45b-94a7-40f2-a6ad-daf228b260c0",
      "8df23015-afa0-4070-af86-1c208f63f853",
      "b4e1d469-b4fc-4fe8-a66f-e85143c8c66c",
      "c5b1bf27-c38c-4c62-a090-a5cd19303418",
      "d1837777-5d06-4a66-8899-f9e421cbb2c9",
      "f93fd873-3711-4c06-a2a4-fc85249b8d70",
      "ff59a191-8489-48f3-b136-68cae8b41cb8"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "2c42d9b9-278a-4f92-a88b-eff1d56340a9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33884716211260313,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.587694326609392
        },
        "61bb8559-3e5c-47d8-87f7-43d4673e2e97": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24069297848693905,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.38238595959648247
        },
        "69df47c8-7c07-4c66-b92a-eeb08e5fee86": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.34534782509113227,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5206956454138929
        },
        "73e8ea4e-cd4e-4367-bb76-dd1c1e1c83a8": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24761397708300928,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.40072795666941363
        },
        "8704fc92-4956-4765-8b58-45c69722a419": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36590544093240307,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6328108844874105
        },
        "bdc77f19-cddf-4379-824d-e1c975246ef3": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.25351977282379834,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.40308954840133127
        },
        "bebb6efc-b90f-4cbc-b801-7c389e866f5a": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33355902802483695,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.572618058553069
        },
        "dbf7baaf-73f0-4dc1-b9e2-0350b635edb3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.30001023108131764,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.45002046216263525
        },
        "dc5cdf58-8b98-432b-8bf7-d13815680f28": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.42025473142865477,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6605094676256811
        },
        "eeb759bf-5c7b-4e3d-b04e-b437599e10e7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.30310926748380856,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45221853759022146
        }
      },
      "100": {
        "2c42d9b9-278a-4f92-a88b-eff1d56340a9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33884716211260313,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.587694326609392
        },
        "61bb8559-3e5c-47d8-87f7-43d4673e2e97": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24069297848693905,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.38238595959648247
        },
        "69df47c8-7c07-4c66-b92a-eeb08e5fee86": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.34534782509113227,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5206956454138929
        },
        "73e8ea4e-cd4e-4367-bb76-dd1c1e1c83a8": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24761397708300928,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.40072795666941363
        },
        "8704fc92-4956-4765-8b58-45c69722a419": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36590544093240307,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6328108844874105
        },
        "bdc77f19-cddf-4379-824d-e1c975246ef3": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.25351977282379834,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.40308954840133127
        },
        "bebb6efc-b90f-4cbc-b801-7c389e866f5a": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33355902802483695,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.572618058553069
        },
        "dbf7baaf-73f0-4dc1-b9e2-0350b635edb3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.30001023108131764,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.45002046216263525
        },
        "dc5cdf58-8b98-432b-8bf7-d13815680f28": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.42025473142865477,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6605094676256811
        },
        "eeb759bf-5c7b-4e3d-b04e-b437599e10e7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.30310926748380856,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45221853759022146
        }
      },
      "25": {
        "2c42d9b9-278a-4f92-a88b-eff1d56340a9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33884716211260313,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.587694326609392
        },
        "61bb8559-3e5c-47d8-87f7-43d4673e2e97": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24069297848693905,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.38238595959648247
        },
        "69df47c8-7c07-4c66-b92a-eeb08e5fee86": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.34534782509113227,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5206956454138929
        },
        "73e8ea4e-cd4e-4367-bb76-dd1c1e1c83a8": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24761397708300928,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.40072795666941363
        },
        "8704fc92-4956-4765-8b58-45c69722a419": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36590544093240307,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6328108844874105
        },
        "bdc77f19-cddf-4379-824d-e1c975246ef3": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.25351977282379834,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.40308954840133127
        },
        "bebb6efc-b90f-4cbc-b801-7c389e866f5a": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33355902802483695,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.572618058553069
        },
        "dbf7baaf-73f0-4dc1-b9e2-0350b635edb3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.30001023108131764,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.45002046216263525
        },
        "dc5cdf58-8b98-432b-8bf7-d13815680f28": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.42025473142865477,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6605094676256811
        },
        "eeb759bf-5c7b-4e3d-b04e-b437599e10e7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.30310926748380856,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45221853759022146
        }
      },
      "50": {
        "2c42d9b9-278a-4f92-a88b-eff1d56340a9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33884716211260313,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.587694326609392
        },
        "61bb8559-3e5c-47d8-87f7-43d4673e2e97": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24069297848693905,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.38238595959648247
        },
        "69df47c8-7c07-4c66-b92a-eeb08e5fee86": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.34534782509113227,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5206956454138929
        },
        "73e8ea4e-cd4e-4367-bb76-dd1c1e1c83a8": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24761397708300928,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.40072795666941363
        },
        "8704fc92-4956-4765-8b58-45c69722a419": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36590544093240307,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6328108844874105
        },
        "bdc77f19-cddf-4379-824d-e1c975246ef3": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.25351977282379834,
          "recency_boost": 0.5,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.40308954840133127
        },
        "bebb6efc-b90f-4cbc-b801-7c389e866f5a": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33355902802483695,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.572618058553069
        },
        "dbf7baaf-73f0-4dc1-b9e2-0350b635edb3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.30001023108131764,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.45002046216263525
        },
        "dc5cdf58-8b98-432b-8bf7-d13815680f28": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.42025473142865477,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6605094676256811
        },
        "eeb759bf-5c7b-4e3d-b04e-b437599e10e7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.30310926748380856,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45221853759022146
        }
      }
    },
    "fixed_union_order_changed": false,
    "fixed_union_order_observable": true,
    "missing_from_pool": {
      "10": [],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "f685340e",
    "union_ids": [
      "2c42d9b9-278a-4f92-a88b-eff1d56340a9",
      "61bb8559-3e5c-47d8-87f7-43d4673e2e97",
      "69df47c8-7c07-4c66-b92a-eeb08e5fee86",
      "73e8ea4e-cd4e-4367-bb76-dd1c1e1c83a8",
      "8704fc92-4956-4765-8b58-45c69722a419",
      "bdc77f19-cddf-4379-824d-e1c975246ef3",
      "bebb6efc-b90f-4cbc-b801-7c389e866f5a",
      "dbf7baaf-73f0-4dc1-b9e2-0350b635edb3",
      "dc5cdf58-8b98-432b-8bf7-d13815680f28",
      "eeb759bf-5c7b-4e3d-b04e-b437599e10e7"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "082219ac-8951-4241-8860-8583816616b9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2785685744477463,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3915371458914185
        },
        "19c7ba05-02d1-45ce-9f8a-5f18e417a5c8": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.39641170865755393,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.61894341416083
        },
        "217c5011-91a8-4cec-b41e-bfde9058dc64": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.39139575831524953,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.617191513626425
        },
        "24241fc4-42fe-4d4c-9301-61521775d646": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3714713431111045,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5690626830679313
        },
        "2818ea8f-8333-43a2-afff-b37cb8ed8df3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.27367096614851416,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3889419331553352
        },
        "30964df1-c8db-4371-9f83-028aeb66e55b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3143559235662876,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.46311184412850115
        },
        "87d203e5-c1fe-4ab7-a6a9-445a861c0ccb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.29721267312230293,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4288253432405318
        },
        "cc5a012a-2d05-4718-bb81-cb2abe516e1e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.35849790551155647,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5639958067315785
        },
        "e218c46e-253e-4374-8f58-31853a894d12": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.35215730849395266,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5387146139838312
        },
        "f2a08124-a3f4-4f9c-aae1-735c9137d03d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3943689494944671,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6267379032804686
        }
      },
      "100": {
        "082219ac-8951-4241-8860-8583816616b9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2785685744477463,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3915371458914185
        },
        "19c7ba05-02d1-45ce-9f8a-5f18e417a5c8": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.39641170865755393,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.61894341416083
        },
        "217c5011-91a8-4cec-b41e-bfde9058dc64": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.39139575831524953,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.617191513626425
        },
        "24241fc4-42fe-4d4c-9301-61521775d646": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3714713431111045,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5690626830679313
        },
        "2818ea8f-8333-43a2-afff-b37cb8ed8df3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.27367096614851416,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3889419331553352
        },
        "30964df1-c8db-4371-9f83-028aeb66e55b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3143559235662876,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.46311184412850115
        },
        "87d203e5-c1fe-4ab7-a6a9-445a861c0ccb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.29721267312230293,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4288253432405318
        },
        "cc5a012a-2d05-4718-bb81-cb2abe516e1e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.35849790551155647,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5639958067315785
        },
        "e218c46e-253e-4374-8f58-31853a894d12": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.35215730849395266,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5387146139838312
        },
        "f2a08124-a3f4-4f9c-aae1-735c9137d03d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3943689494944671,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6267379032804686
        }
      },
      "25": {
        "082219ac-8951-4241-8860-8583816616b9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2785685744477463,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3915371458914185
        },
        "19c7ba05-02d1-45ce-9f8a-5f18e417a5c8": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.39641170865755393,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.61894341416083
        },
        "217c5011-91a8-4cec-b41e-bfde9058dc64": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.39139575831524953,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.617191513626425
        },
        "24241fc4-42fe-4d4c-9301-61521775d646": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3714713431111045,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5690626830679313
        },
        "2818ea8f-8333-43a2-afff-b37cb8ed8df3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.27367096614851416,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3889419331553352
        },
        "30964df1-c8db-4371-9f83-028aeb66e55b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3143559235662876,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.46311184412850115
        },
        "87d203e5-c1fe-4ab7-a6a9-445a861c0ccb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.29721267312230293,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4288253432405318
        },
        "cc5a012a-2d05-4718-bb81-cb2abe516e1e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.35849790551155647,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5639958067315785
        },
        "e218c46e-253e-4374-8f58-31853a894d12": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.35215730849395266,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5387146139838312
        },
        "f2a08124-a3f4-4f9c-aae1-735c9137d03d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3943689494944671,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6267379032804686
        }
      },
      "50": {
        "082219ac-8951-4241-8860-8583816616b9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2785685744477463,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3915371458914185
        },
        "19c7ba05-02d1-45ce-9f8a-5f18e417a5c8": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.39641170865755393,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.61894341416083
        },
        "217c5011-91a8-4cec-b41e-bfde9058dc64": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.39139575831524953,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.617191513626425
        },
        "24241fc4-42fe-4d4c-9301-61521775d646": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3714713431111045,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5690626830679313
        },
        "2818ea8f-8333-43a2-afff-b37cb8ed8df3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.27367096614851416,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3889419331553352
        },
        "30964df1-c8db-4371-9f83-028aeb66e55b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3143559235662876,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.46311184412850115
        },
        "87d203e5-c1fe-4ab7-a6a9-445a861c0ccb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.29721267312230293,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4288253432405318
        },
        "cc5a012a-2d05-4718-bb81-cb2abe516e1e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.35849790551155647,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5639958067315785
        },
        "e218c46e-253e-4374-8f58-31853a894d12": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.35215730849395266,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5387146139838312
        },
        "f2a08124-a3f4-4f9c-aae1-735c9137d03d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3943689494944671,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6267379032804686
        }
      }
    },
    "fixed_union_order_changed": false,
    "fixed_union_order_observable": true,
    "missing_from_pool": {
      "10": [],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "184da446",
    "union_ids": [
      "082219ac-8951-4241-8860-8583816616b9",
      "19c7ba05-02d1-45ce-9f8a-5f18e417a5c8",
      "217c5011-91a8-4cec-b41e-bfde9058dc64",
      "24241fc4-42fe-4d4c-9301-61521775d646",
      "2818ea8f-8333-43a2-afff-b37cb8ed8df3",
      "30964df1-c8db-4371-9f83-028aeb66e55b",
      "87d203e5-c1fe-4ab7-a6a9-445a861c0ccb",
      "cc5a012a-2d05-4718-bb81-cb2abe516e1e",
      "e218c46e-253e-4374-8f58-31853a894d12",
      "f2a08124-a3f4-4f9c-aae1-735c9137d03d"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "092a10ba-678a-47c6-bcb7-98f4d34bb494": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.42367396570509425,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6853479357017229
        },
        "24dea862-9f1c-4068-b5a5-ee2dc6ed86eb": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3012095517153268,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4153091083873759
        },
        "38a24238-05e6-4b33-9dec-72bb9baa6643": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36549699953914394,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5689940033698223
        },
        "577322be-c4bf-4cca-be67-fb920f7dfb7c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.25536589376073626,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.38113178237163126
        },
        "9c580666-793d-4c1d-bf3b-b82ab748f808": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.27367722322975807,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.40875444881986
        },
        "a84c9dde-c0f6-4681-a48b-58b6e6986603": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.26886589750095846,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37573179929345135
        },
        "da102a97-ccfa-4a69-b44e-9f7a84849955": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2713404816653839,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3806809676223022
        },
        "dd8fb131-6b72-43f3-9ce4-1d909a87c613": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3531158392643552,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5323516753744326
        },
        "df42df35-a550-4e8e-b9aa-6d7fa83906b0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2826165349214521,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4302330698429042
        },
        "f99c3ebd-f283-41c5-813b-e1f31cde9e60": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.37730776055315735,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5633475176366091
        }
      },
      "100": {
        "092a10ba-678a-47c6-bcb7-98f4d34bb494": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.42367396570509425,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6853479357017229
        },
        "24dea862-9f1c-4068-b5a5-ee2dc6ed86eb": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3012095517153268,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4153091083873759
        },
        "38a24238-05e6-4b33-9dec-72bb9baa6643": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36549699953914394,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5689940033698223
        },
        "577322be-c4bf-4cca-be67-fb920f7dfb7c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.25536589376073626,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.38113178237163126
        },
        "7df35f0c-773c-4e14-8481-62be52f1ef18": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2678051285055744,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37361026130268327
        },
        "9c580666-793d-4c1d-bf3b-b82ab748f808": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.27367722322975807,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.40875444881986
        },
        "a84c9dde-c0f6-4681-a48b-58b6e6986603": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.26886589750095846,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37573179929345135
        },
        "da102a97-ccfa-4a69-b44e-9f7a84849955": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2713404816653839,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3806809676223022
        },
        "dd8fb131-6b72-43f3-9ce4-1d909a87c613": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3531158392643552,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5323516753744326
        },
        "df42df35-a550-4e8e-b9aa-6d7fa83906b0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2826165349214521,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4302330698429042
        },
        "f99c3ebd-f283-41c5-813b-e1f31cde9e60": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.37730776055315735,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5633475176366091
        }
      },
      "25": {
        "092a10ba-678a-47c6-bcb7-98f4d34bb494": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.42367396570509425,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6853479357017229
        },
        "24dea862-9f1c-4068-b5a5-ee2dc6ed86eb": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3012095517153268,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4153091083873759
        },
        "38a24238-05e6-4b33-9dec-72bb9baa6643": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36549699953914394,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5689940033698223
        },
        "577322be-c4bf-4cca-be67-fb920f7dfb7c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.25536589376073626,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.38113178237163126
        },
        "7df35f0c-773c-4e14-8481-62be52f1ef18": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2678051285055744,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37361026130268327
        },
        "9c580666-793d-4c1d-bf3b-b82ab748f808": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.27367722322975807,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.40875444881986
        },
        "a84c9dde-c0f6-4681-a48b-58b6e6986603": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.26886589750095846,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37573179929345135
        },
        "da102a97-ccfa-4a69-b44e-9f7a84849955": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2713404816653839,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3806809676223022
        },
        "dd8fb131-6b72-43f3-9ce4-1d909a87c613": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3531158392643552,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5323516753744326
        },
        "df42df35-a550-4e8e-b9aa-6d7fa83906b0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2826165349214521,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4302330698429042
        },
        "f99c3ebd-f283-41c5-813b-e1f31cde9e60": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.37730776055315735,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5633475176366091
        }
      },
      "50": {
        "092a10ba-678a-47c6-bcb7-98f4d34bb494": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.42367396570509425,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6853479357017229
        },
        "24dea862-9f1c-4068-b5a5-ee2dc6ed86eb": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3012095517153268,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4153091083873759
        },
        "38a24238-05e6-4b33-9dec-72bb9baa6643": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36549699953914394,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5689940033698223
        },
        "577322be-c4bf-4cca-be67-fb920f7dfb7c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.25536589376073626,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.38113178237163126
        },
        "7df35f0c-773c-4e14-8481-62be52f1ef18": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2678051285055744,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37361026130268327
        },
        "9c580666-793d-4c1d-bf3b-b82ab748f808": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.27367722322975807,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.40875444881986
        },
        "a84c9dde-c0f6-4681-a48b-58b6e6986603": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.26886589750095846,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37573179929345135
        },
        "da102a97-ccfa-4a69-b44e-9f7a84849955": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2713404816653839,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3806809676223022
        },
        "dd8fb131-6b72-43f3-9ce4-1d909a87c613": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3531158392643552,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5323516753744326
        },
        "df42df35-a550-4e8e-b9aa-6d7fa83906b0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2826165349214521,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4302330698429042
        },
        "f99c3ebd-f283-41c5-813b-e1f31cde9e60": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.37730776055315735,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5633475176366091
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "7df35f0c-773c-4e14-8481-62be52f1ef18"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "f685340e_abs",
    "union_ids": [
      "092a10ba-678a-47c6-bcb7-98f4d34bb494",
      "24dea862-9f1c-4068-b5a5-ee2dc6ed86eb",
      "38a24238-05e6-4b33-9dec-72bb9baa6643",
      "577322be-c4bf-4cca-be67-fb920f7dfb7c",
      "7df35f0c-773c-4e14-8481-62be52f1ef18",
      "9c580666-793d-4c1d-bf3b-b82ab748f808",
      "a84c9dde-c0f6-4681-a48b-58b6e6986603",
      "da102a97-ccfa-4a69-b44e-9f7a84849955",
      "dd8fb131-6b72-43f3-9ce4-1d909a87c613",
      "df42df35-a550-4e8e-b9aa-6d7fa83906b0",
      "f99c3ebd-f283-41c5-813b-e1f31cde9e60"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "001f4a5e-3b12-4241-8c2d-cb9a681bd023": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.28404139942469897,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3700828040946066
        },
        "162eee93-c5e8-4822-9d18-bd59fbfee068": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.27860908642906246,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4142181781033336
        },
        "1726fab2-501a-4d4c-a818-57ffec73ec97": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.35531223218351243,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5306244691353964
        },
        "1a41c654-f224-43f5-93f2-e4d88c590357": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.3278174280283138,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4840348623508781
        },
        "301f1f0a-1333-4a08-b837-ca1bdaa0c3d8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.27615860216467475,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36531719908414073
        },
        "63f86da9-9525-4ec4-9ac8-67676a5d341d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3102559512636479,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44051190729566736
        },
        "7f009f0a-bd6f-463c-a8e4-1b27abec4444": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3077349567429679,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4174699187311446
        },
        "add5d05c-90bb-471d-a878-38dc315accd9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2842391249848245,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3704782552148578
        },
        "ddd6e9c8-48fa-49c8-b8c2-487389c399c7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.29202332720498403,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3860466596551768
        },
        "e4b21469-e6ed-4079-ab91-7ec920085a6a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2813607866306891,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39272156849300655
        }
      },
      "100": {
        "001f4a5e-3b12-4241-8c2d-cb9a681bd023": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.28404139942469897,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3700828040946066
        },
        "162eee93-c5e8-4822-9d18-bd59fbfee068": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.27860908642906246,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4142181781033336
        },
        "1726fab2-501a-4d4c-a818-57ffec73ec97": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.35531223218351243,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5306244691353964
        },
        "1a41c654-f224-43f5-93f2-e4d88c590357": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.3278174280283138,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4840348623508781
        },
        "301f1f0a-1333-4a08-b837-ca1bdaa0c3d8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.27615860216467475,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36531719908414073
        },
        "49fb57d7-cebe-489e-8d6e-5a45560ffb7c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2795373981597421,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36107480156469285
        },
        "63f86da9-9525-4ec4-9ac8-67676a5d341d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3102559512636479,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44051190729566736
        },
        "7f009f0a-bd6f-463c-a8e4-1b27abec4444": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3077349567429679,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4174699187311446
        },
        "add5d05c-90bb-471d-a878-38dc315accd9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2842391249848245,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3704782552148578
        },
        "ddd6e9c8-48fa-49c8-b8c2-487389c399c7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.29202332720498403,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3860466596551768
        },
        "e4b21469-e6ed-4079-ab91-7ec920085a6a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2813607866306891,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39272156849300655
        }
      },
      "25": {
        "001f4a5e-3b12-4241-8c2d-cb9a681bd023": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.28404139942469897,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3700828040946066
        },
        "162eee93-c5e8-4822-9d18-bd59fbfee068": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.27860908642906246,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4142181781033336
        },
        "1726fab2-501a-4d4c-a818-57ffec73ec97": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.35531223218351243,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5306244691353964
        },
        "1a41c654-f224-43f5-93f2-e4d88c590357": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.3278174280283138,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4840348623508781
        },
        "301f1f0a-1333-4a08-b837-ca1bdaa0c3d8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.27615860216467475,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36531719908414073
        },
        "49fb57d7-cebe-489e-8d6e-5a45560ffb7c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2795373981597421,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36107480156469285
        },
        "63f86da9-9525-4ec4-9ac8-67676a5d341d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3102559512636479,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44051190729566736
        },
        "7f009f0a-bd6f-463c-a8e4-1b27abec4444": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3077349567429679,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4174699187311446
        },
        "add5d05c-90bb-471d-a878-38dc315accd9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2842391249848245,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3704782552148578
        },
        "ddd6e9c8-48fa-49c8-b8c2-487389c399c7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.29202332720498403,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3860466596551768
        },
        "e4b21469-e6ed-4079-ab91-7ec920085a6a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2813607866306891,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39272156849300655
        }
      },
      "50": {
        "001f4a5e-3b12-4241-8c2d-cb9a681bd023": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.28404139942469897,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3700828040946066
        },
        "162eee93-c5e8-4822-9d18-bd59fbfee068": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.27860908642906246,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4142181781033336
        },
        "1726fab2-501a-4d4c-a818-57ffec73ec97": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.35531223218351243,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5306244691353964
        },
        "1a41c654-f224-43f5-93f2-e4d88c590357": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.3278174280283138,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4840348623508781
        },
        "301f1f0a-1333-4a08-b837-ca1bdaa0c3d8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.27615860216467475,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36531719908414073
        },
        "49fb57d7-cebe-489e-8d6e-5a45560ffb7c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2795373981597421,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36107480156469285
        },
        "63f86da9-9525-4ec4-9ac8-67676a5d341d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3102559512636479,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44051190729566736
        },
        "7f009f0a-bd6f-463c-a8e4-1b27abec4444": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3077349567429679,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4174699187311446
        },
        "add5d05c-90bb-471d-a878-38dc315accd9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2842391249848245,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3704782552148578
        },
        "ddd6e9c8-48fa-49c8-b8c2-487389c399c7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.29202332720498403,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3860466596551768
        },
        "e4b21469-e6ed-4079-ab91-7ec920085a6a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2813607866306891,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39272156849300655
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "49fb57d7-cebe-489e-8d6e-5a45560ffb7c"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "982b5123_abs",
    "union_ids": [
      "001f4a5e-3b12-4241-8c2d-cb9a681bd023",
      "162eee93-c5e8-4822-9d18-bd59fbfee068",
      "1726fab2-501a-4d4c-a818-57ffec73ec97",
      "1a41c654-f224-43f5-93f2-e4d88c590357",
      "301f1f0a-1333-4a08-b837-ca1bdaa0c3d8",
      "49fb57d7-cebe-489e-8d6e-5a45560ffb7c",
      "63f86da9-9525-4ec4-9ac8-67676a5d341d",
      "7f009f0a-bd6f-463c-a8e4-1b27abec4444",
      "add5d05c-90bb-471d-a878-38dc315accd9",
      "ddd6e9c8-48fa-49c8-b8c2-487389c399c7",
      "e4b21469-e6ed-4079-ab91-7ec920085a6a"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "1a966e3a-32f9-42c5-86ba-938af1554ea5": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2610470014436208,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.32259400539063665
        },
        "2a893a06-c2a3-48e3-9167-81f9fa52449d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.2737049437762082,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3874098851682306
        },
        "2e3ca05a-aa3b-4a40-89b6-63a81dcad0e3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2645110705961472,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34902214596066594
        },
        "41b0947e-5e12-4036-9e1a-bd2b2f0052ed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2504209423233039,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31084188703079363
        },
        "5020302b-217e-4560-9295-9f0864c68139": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.24556359228047242,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31512718551461916
        },
        "5d3cb356-bbec-4121-bfac-e612b1f309bc": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3410850873088522,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4826701771210994
        },
        "632e920b-6989-4c8f-b5d7-2d075eb20ed2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.25253411740442133,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35506823480884264
        },
        "9dcfcaf2-2252-427a-bfdc-154c279e9861": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.31211397704641974,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.444227958861211
        },
        "baabfc44-68cf-4b4a-ab5e-3edae0b075bc": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2550324525559454,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3600649051118908
        },
        "dc554896-b5df-4c66-98ef-c2bf0edadc77": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2524377992241501,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30687560369350897
        }
      },
      "100": {
        "1a966e3a-32f9-42c5-86ba-938af1554ea5": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2610470014436208,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.32259400539063665
        },
        "2a893a06-c2a3-48e3-9167-81f9fa52449d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.2737049437762082,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3874098851682306
        },
        "2e3ca05a-aa3b-4a40-89b6-63a81dcad0e3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2645110705961472,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34902214596066594
        },
        "41b0947e-5e12-4036-9e1a-bd2b2f0052ed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2504209423233039,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31084188703079363
        },
        "5020302b-217e-4560-9295-9f0864c68139": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.24556359228047242,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31512718551461916
        },
        "5d3cb356-bbec-4121-bfac-e612b1f309bc": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3410850873088522,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4826701771210994
        },
        "632e920b-6989-4c8f-b5d7-2d075eb20ed2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.25253411740442133,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35506823480884264
        },
        "9dcfcaf2-2252-427a-bfdc-154c279e9861": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.31211397704641974,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.444227958861211
        },
        "baabfc44-68cf-4b4a-ab5e-3edae0b075bc": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2550324525559454,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3600649051118908
        },
        "dc554896-b5df-4c66-98ef-c2bf0edadc77": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2524377992241501,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30687560369350897
        }
      },
      "25": {
        "1a966e3a-32f9-42c5-86ba-938af1554ea5": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2610470014436208,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.32259400539063665
        },
        "2a893a06-c2a3-48e3-9167-81f9fa52449d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.2737049437762082,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3874098851682306
        },
        "2e3ca05a-aa3b-4a40-89b6-63a81dcad0e3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2645110705961472,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34902214596066594
        },
        "41b0947e-5e12-4036-9e1a-bd2b2f0052ed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2504209423233039,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31084188703079363
        },
        "5020302b-217e-4560-9295-9f0864c68139": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.24556359228047242,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31512718551461916
        },
        "5d3cb356-bbec-4121-bfac-e612b1f309bc": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3410850873088522,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4826701771210994
        },
        "632e920b-6989-4c8f-b5d7-2d075eb20ed2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.25253411740442133,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35506823480884264
        },
        "9dcfcaf2-2252-427a-bfdc-154c279e9861": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.31211397704641974,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.444227958861211
        },
        "baabfc44-68cf-4b4a-ab5e-3edae0b075bc": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2550324525559454,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3600649051118908
        },
        "dc554896-b5df-4c66-98ef-c2bf0edadc77": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2524377992241501,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30687560369350897
        }
      },
      "50": {
        "1a966e3a-32f9-42c5-86ba-938af1554ea5": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2610470014436208,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.32259400539063665
        },
        "2a893a06-c2a3-48e3-9167-81f9fa52449d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.2737049437762082,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3874098851682306
        },
        "2e3ca05a-aa3b-4a40-89b6-63a81dcad0e3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2645110705961472,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34902214596066594
        },
        "41b0947e-5e12-4036-9e1a-bd2b2f0052ed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2504209423233039,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31084188703079363
        },
        "5020302b-217e-4560-9295-9f0864c68139": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.24556359228047242,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31512718551461916
        },
        "5d3cb356-bbec-4121-bfac-e612b1f309bc": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3410850873088522,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4826701771210994
        },
        "632e920b-6989-4c8f-b5d7-2d075eb20ed2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.25253411740442133,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35506823480884264
        },
        "9dcfcaf2-2252-427a-bfdc-154c279e9861": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.31211397704641974,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.444227958861211
        },
        "baabfc44-68cf-4b4a-ab5e-3edae0b075bc": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2550324525559454,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3600649051118908
        },
        "dc554896-b5df-4c66-98ef-c2bf0edadc77": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2524377992241501,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30687560369350897
        }
      }
    },
    "fixed_union_order_changed": false,
    "fixed_union_order_observable": true,
    "missing_from_pool": {
      "10": [],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "gpt4_93159ced_abs",
    "union_ids": [
      "1a966e3a-32f9-42c5-86ba-938af1554ea5",
      "2a893a06-c2a3-48e3-9167-81f9fa52449d",
      "2e3ca05a-aa3b-4a40-89b6-63a81dcad0e3",
      "41b0947e-5e12-4036-9e1a-bd2b2f0052ed",
      "5020302b-217e-4560-9295-9f0864c68139",
      "5d3cb356-bbec-4121-bfac-e612b1f309bc",
      "632e920b-6989-4c8f-b5d7-2d075eb20ed2",
      "9dcfcaf2-2252-427a-bfdc-154c279e9861",
      "baabfc44-68cf-4b4a-ab5e-3edae0b075bc",
      "dc554896-b5df-4c66-98ef-c2bf0edadc77"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "4ef84232-efc6-452b-84a5-c5a26c814c9d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2598902074523748,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3357804115668894
        },
        "50c675df-e7e1-4260-a414-6000c17cdbf1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2630028318208449,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3460056684100613
        },
        "6f6d01c5-ffba-42c7-8e9d-c56c0ff6f435": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.254755493312102,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.359510986624204
        },
        "727f8aca-3839-409f-9a00-51ccb99a1a38": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2675887738653595,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34617755273750916
        },
        "77117d07-f01f-4a5e-a670-db586d6c47fd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.27860097288765684,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3702019405301049
        },
        "89240038-7348-4561-a460-53605d14a56f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.29588516383434477,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.40697032867004757
        },
        "b7acbee0-889a-4a2b-93dd-4c5f1288e013": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.28173999135379213,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.38747998008497986
        },
        "bd56d0ae-bfc4-4486-bd38-bafc4ec22b00": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.24596915363015004,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3489383125055088
        },
        "e5558e40-e813-48e6-b80a-2d6524a17605": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3015878663491208,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4191757293603815
        },
        "fc106bed-f14e-41a8-9de1-1356be75419c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2606163077819117,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3312326179480092
        }
      },
      "100": {
        "3dc28b09-3e43-4667-b617-47834afe81b7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8399999737739563,
          "final_score": 0.2463773874365068,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.32475478011822234
        },
        "4ef84232-efc6-452b-84a5-c5a26c814c9d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2598902074523748,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3357804115668894
        },
        "50c675df-e7e1-4260-a414-6000c17cdbf1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2630028318208449,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3460056684100613
        },
        "6f6d01c5-ffba-42c7-8e9d-c56c0ff6f435": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.254755493312102,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.359510986624204
        },
        "727f8aca-3839-409f-9a00-51ccb99a1a38": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2675887738653595,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34617755273750916
        },
        "77117d07-f01f-4a5e-a670-db586d6c47fd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.27860097288765684,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3702019405301049
        },
        "89240038-7348-4561-a460-53605d14a56f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.29588516383434477,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.40697032867004757
        },
        "b7acbee0-889a-4a2b-93dd-4c5f1288e013": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.28173999135379213,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.38747998008497986
        },
        "bd56d0ae-bfc4-4486-bd38-bafc4ec22b00": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.24596915363015004,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3489383125055088
        },
        "e5558e40-e813-48e6-b80a-2d6524a17605": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3015878663491208,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4191757293603815
        },
        "fc106bed-f14e-41a8-9de1-1356be75419c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2606163077819117,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3312326179480092
        }
      },
      "25": {
        "3dc28b09-3e43-4667-b617-47834afe81b7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8399999737739563,
          "final_score": 0.2463773874365068,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.32475478011822234
        },
        "4ef84232-efc6-452b-84a5-c5a26c814c9d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2598902074523748,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3357804115668894
        },
        "50c675df-e7e1-4260-a414-6000c17cdbf1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2630028318208449,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3460056684100613
        },
        "6f6d01c5-ffba-42c7-8e9d-c56c0ff6f435": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.254755493312102,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.359510986624204
        },
        "727f8aca-3839-409f-9a00-51ccb99a1a38": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2675887738653595,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34617755273750916
        },
        "77117d07-f01f-4a5e-a670-db586d6c47fd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.27860097288765684,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3702019405301049
        },
        "89240038-7348-4561-a460-53605d14a56f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.29588516383434477,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.40697032867004757
        },
        "b7acbee0-889a-4a2b-93dd-4c5f1288e013": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.28173999135379213,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.38747998008497986
        },
        "bd56d0ae-bfc4-4486-bd38-bafc4ec22b00": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.24596915363015004,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3489383125055088
        },
        "e5558e40-e813-48e6-b80a-2d6524a17605": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3015878663491208,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4191757293603815
        },
        "fc106bed-f14e-41a8-9de1-1356be75419c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2606163077819117,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3312326179480092
        }
      },
      "50": {
        "3dc28b09-3e43-4667-b617-47834afe81b7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8399999737739563,
          "final_score": 0.2463773874365068,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.32475478011822234
        },
        "4ef84232-efc6-452b-84a5-c5a26c814c9d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2598902074523748,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3357804115668894
        },
        "50c675df-e7e1-4260-a414-6000c17cdbf1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2630028318208449,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3460056684100613
        },
        "6f6d01c5-ffba-42c7-8e9d-c56c0ff6f435": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.254755493312102,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.359510986624204
        },
        "727f8aca-3839-409f-9a00-51ccb99a1a38": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2675887738653595,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34617755273750916
        },
        "77117d07-f01f-4a5e-a670-db586d6c47fd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.27860097288765684,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3702019405301049
        },
        "89240038-7348-4561-a460-53605d14a56f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.29588516383434477,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.40697032867004757
        },
        "b7acbee0-889a-4a2b-93dd-4c5f1288e013": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.28173999135379213,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.38747998008497986
        },
        "bd56d0ae-bfc4-4486-bd38-bafc4ec22b00": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.24596915363015004,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3489383125055088
        },
        "e5558e40-e813-48e6-b80a-2d6524a17605": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3015878663491208,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4191757293603815
        },
        "fc106bed-f14e-41a8-9de1-1356be75419c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2606163077819117,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3312326179480092
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "3dc28b09-3e43-4667-b617-47834afe81b7"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "80ec1f4f_abs",
    "union_ids": [
      "3dc28b09-3e43-4667-b617-47834afe81b7",
      "4ef84232-efc6-452b-84a5-c5a26c814c9d",
      "50c675df-e7e1-4260-a414-6000c17cdbf1",
      "6f6d01c5-ffba-42c7-8e9d-c56c0ff6f435",
      "727f8aca-3839-409f-9a00-51ccb99a1a38",
      "77117d07-f01f-4a5e-a670-db586d6c47fd",
      "89240038-7348-4561-a460-53605d14a56f",
      "b7acbee0-889a-4a2b-93dd-4c5f1288e013",
      "bd56d0ae-bfc4-4486-bd38-bafc4ec22b00",
      "e5558e40-e813-48e6-b80a-2d6524a17605",
      "fc106bed-f14e-41a8-9de1-1356be75419c"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "1d3d7bbe-b13a-4822-8639-fe8418be07e0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.251384489260262,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31276898090470984
        },
        "219aa257-7a95-49a5-bf0a-27176330bb57": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2559960272038313,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3219920567918484
        },
        "5cd8ae53-8f8a-4168-b690-c8ac88e50680": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23083336506594454,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.29166672536351745
        },
        "702c7228-b28d-4ab5-8b02-07feb8d7dc7d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3383919972845582,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4867839969533022
        },
        "813b251c-f671-4790-b37d-30fcd7854385": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.234522723499343,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2790454493828718
        },
        "99f92ba4-f088-4e5f-b8e3-5105e69c6892": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2428086592043175,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.29561732079282077
        },
        "b65eaa01-e020-41f7-be8f-7a95fb306d46": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24636476840809962,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3127295415845708
        },
        "d9402cb1-a287-4555-bf26-c5fac0b09c3d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.2737594018994669,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4045188090441425
        },
        "e5a1282f-cc21-42f4-8aa3-02739aaf3ace": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.23363954811105825,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.28727910099048803
        },
        "f9a116f3-8abd-4c7b-bc17-5bbb16828af5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.26611380519303107,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3522276151544337
        }
      },
      "100": {
        "1d3d7bbe-b13a-4822-8639-fe8418be07e0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.251384489260262,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31276898090470984
        },
        "219aa257-7a95-49a5-bf0a-27176330bb57": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2559960272038313,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3219920567918484
        },
        "5cd8ae53-8f8a-4168-b690-c8ac88e50680": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23083336506594454,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.29166672536351745
        },
        "702c7228-b28d-4ab5-8b02-07feb8d7dc7d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3383919972845582,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4867839969533022
        },
        "813b251c-f671-4790-b37d-30fcd7854385": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.234522723499343,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2790454493828718
        },
        "99f92ba4-f088-4e5f-b8e3-5105e69c6892": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2428086592043175,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.29561732079282077
        },
        "b65eaa01-e020-41f7-be8f-7a95fb306d46": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24636476840809962,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3127295415845708
        },
        "d9402cb1-a287-4555-bf26-c5fac0b09c3d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.2737594018994669,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4045188090441425
        },
        "e5a1282f-cc21-42f4-8aa3-02739aaf3ace": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.23363954811105825,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.28727910099048803
        },
        "f9a116f3-8abd-4c7b-bc17-5bbb16828af5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.26611380519303107,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3522276151544337
        }
      },
      "25": {
        "1d3d7bbe-b13a-4822-8639-fe8418be07e0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.251384489260262,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31276898090470984
        },
        "219aa257-7a95-49a5-bf0a-27176330bb57": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2559960272038313,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3219920567918484
        },
        "5cd8ae53-8f8a-4168-b690-c8ac88e50680": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23083336506594454,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.29166672536351745
        },
        "702c7228-b28d-4ab5-8b02-07feb8d7dc7d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3383919972845582,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4867839969533022
        },
        "813b251c-f671-4790-b37d-30fcd7854385": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.234522723499343,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2790454493828718
        },
        "99f92ba4-f088-4e5f-b8e3-5105e69c6892": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2428086592043175,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.29561732079282077
        },
        "b65eaa01-e020-41f7-be8f-7a95fb306d46": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24636476840809962,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3127295415845708
        },
        "d9402cb1-a287-4555-bf26-c5fac0b09c3d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.2737594018994669,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4045188090441425
        },
        "e5a1282f-cc21-42f4-8aa3-02739aaf3ace": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.23363954811105825,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.28727910099048803
        },
        "f9a116f3-8abd-4c7b-bc17-5bbb16828af5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.26611380519303107,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3522276151544337
        }
      },
      "50": {
        "1d3d7bbe-b13a-4822-8639-fe8418be07e0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.251384489260262,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31276898090470984
        },
        "219aa257-7a95-49a5-bf0a-27176330bb57": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2559960272038313,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3219920567918484
        },
        "5cd8ae53-8f8a-4168-b690-c8ac88e50680": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23083336506594454,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.29166672536351745
        },
        "702c7228-b28d-4ab5-8b02-07feb8d7dc7d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3383919972845582,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4867839969533022
        },
        "813b251c-f671-4790-b37d-30fcd7854385": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.234522723499343,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2790454493828718
        },
        "99f92ba4-f088-4e5f-b8e3-5105e69c6892": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2428086592043175,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.29561732079282077
        },
        "b65eaa01-e020-41f7-be8f-7a95fb306d46": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24636476840809962,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3127295415845708
        },
        "d9402cb1-a287-4555-bf26-c5fac0b09c3d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.2737594018994669,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4045188090441425
        },
        "e5a1282f-cc21-42f4-8aa3-02739aaf3ace": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.23363954811105825,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.28727910099048803
        },
        "f9a116f3-8abd-4c7b-bc17-5bbb16828af5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.26611380519303107,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3522276151544337
        }
      }
    },
    "fixed_union_order_changed": false,
    "fixed_union_order_observable": true,
    "missing_from_pool": {
      "10": [],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "gpt4_372c3eed_abs",
    "union_ids": [
      "1d3d7bbe-b13a-4822-8639-fe8418be07e0",
      "219aa257-7a95-49a5-bf0a-27176330bb57",
      "5cd8ae53-8f8a-4168-b690-c8ac88e50680",
      "702c7228-b28d-4ab5-8b02-07feb8d7dc7d",
      "813b251c-f671-4790-b37d-30fcd7854385",
      "99f92ba4-f088-4e5f-b8e3-5105e69c6892",
      "b65eaa01-e020-41f7-be8f-7a95fb306d46",
      "d9402cb1-a287-4555-bf26-c5fac0b09c3d",
      "e5a1282f-cc21-42f4-8aa3-02739aaf3ace",
      "f9a116f3-8abd-4c7b-bc17-5bbb16828af5"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "0e5f73f6-b2ad-49bf-bd74-3ed492e16704": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.19643165775650515,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21862331645714783
        },
        "14bafa68-6ecd-4dfb-be2c-f957dd2357e9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20167314623697907,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22334629724232968
        },
        "32509761-3a25-4a38-8919-a1dffca85575": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.195058742258611,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2218174797965341
        },
        "33b3882f-838b-486a-84c8-8dd4feb1f40f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3300174034147538,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4800348115978791
        },
        "656cdf94-c29d-4d86-b258-d35252f711ed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.387350357045843,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5847007164758717
        },
        "83d918f5-bfde-4aa8-9761-3d01bc355090": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.21251998057485544,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2380399559045021
        },
        "85d5ba8e-433a-445f-ac6e-d4bf63795c16": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.32592683958066165,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48785368059183476
        },
        "b718a167-47e4-4d78-8590-1afd1464e477": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.1961651159269758,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2240302271332637
        },
        "ce44d3d2-c4cb-4382-9b80-5d25c73e470a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20270885209490525,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21841769894460172
        },
        "ed7609db-d418-4a30-8163-13bf7d6eda4f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21403509453298922,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23007019431118714
        }
      },
      "100": {
        "0e5f73f6-b2ad-49bf-bd74-3ed492e16704": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.19643165775650515,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21862331645714783
        },
        "14bafa68-6ecd-4dfb-be2c-f957dd2357e9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20167314623697907,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22334629724232968
        },
        "32509761-3a25-4a38-8919-a1dffca85575": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.195058742258611,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2218174797965341
        },
        "33b3882f-838b-486a-84c8-8dd4feb1f40f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3300174034147538,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4800348115978791
        },
        "344e92e6-4007-4662-8f0b-23472d6620bd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.20954302273673148,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21668604180181683
        },
        "656cdf94-c29d-4d86-b258-d35252f711ed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.387350357045843,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5847007164758717
        },
        "83d918f5-bfde-4aa8-9761-3d01bc355090": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.21251998057485544,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2380399559045021
        },
        "85d5ba8e-433a-445f-ac6e-d4bf63795c16": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.32592683958066165,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48785368059183476
        },
        "b718a167-47e4-4d78-8590-1afd1464e477": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.1961651159269758,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2240302271332637
        },
        "ce44d3d2-c4cb-4382-9b80-5d25c73e470a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20270885209490525,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21841769894460172
        },
        "ed7609db-d418-4a30-8163-13bf7d6eda4f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21403509453298922,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23007019431118714
        }
      },
      "25": {
        "0e5f73f6-b2ad-49bf-bd74-3ed492e16704": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.19643165775650515,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21862331645714783
        },
        "14bafa68-6ecd-4dfb-be2c-f957dd2357e9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20167314623697907,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22334629724232968
        },
        "32509761-3a25-4a38-8919-a1dffca85575": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.195058742258611,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2218174797965341
        },
        "33b3882f-838b-486a-84c8-8dd4feb1f40f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3300174034147538,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4800348115978791
        },
        "344e92e6-4007-4662-8f0b-23472d6620bd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.20954302273673148,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21668604180181683
        },
        "656cdf94-c29d-4d86-b258-d35252f711ed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.387350357045843,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5847007164758717
        },
        "83d918f5-bfde-4aa8-9761-3d01bc355090": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.21251998057485544,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2380399559045021
        },
        "85d5ba8e-433a-445f-ac6e-d4bf63795c16": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.32592683958066165,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48785368059183476
        },
        "b718a167-47e4-4d78-8590-1afd1464e477": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.1961651159269758,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2240302271332637
        },
        "ce44d3d2-c4cb-4382-9b80-5d25c73e470a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20270885209490525,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21841769894460172
        },
        "ed7609db-d418-4a30-8163-13bf7d6eda4f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21403509453298922,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23007019431118714
        }
      },
      "50": {
        "0e5f73f6-b2ad-49bf-bd74-3ed492e16704": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.19643165775650515,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21862331645714783
        },
        "14bafa68-6ecd-4dfb-be2c-f957dd2357e9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.20167314623697907,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.22334629724232968
        },
        "32509761-3a25-4a38-8919-a1dffca85575": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.195058742258611,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2218174797965341
        },
        "33b3882f-838b-486a-84c8-8dd4feb1f40f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3300174034147538,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4800348115978791
        },
        "344e92e6-4007-4662-8f0b-23472d6620bd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.20954302273673148,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21668604180181683
        },
        "656cdf94-c29d-4d86-b258-d35252f711ed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.387350357045843,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5847007164758717
        },
        "83d918f5-bfde-4aa8-9761-3d01bc355090": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.21251998057485544,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2380399559045021
        },
        "85d5ba8e-433a-445f-ac6e-d4bf63795c16": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.32592683958066165,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48785368059183476
        },
        "b718a167-47e4-4d78-8590-1afd1464e477": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.1961651159269758,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2240302271332637
        },
        "ce44d3d2-c4cb-4382-9b80-5d25c73e470a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20270885209490525,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.21841769894460172
        },
        "ed7609db-d418-4a30-8163-13bf7d6eda4f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21403509453298922,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.23007019431118714
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "344e92e6-4007-4662-8f0b-23472d6620bd"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "ad7109d1",
    "union_ids": [
      "0e5f73f6-b2ad-49bf-bd74-3ed492e16704",
      "14bafa68-6ecd-4dfb-be2c-f957dd2357e9",
      "32509761-3a25-4a38-8919-a1dffca85575",
      "33b3882f-838b-486a-84c8-8dd4feb1f40f",
      "344e92e6-4007-4662-8f0b-23472d6620bd",
      "656cdf94-c29d-4d86-b258-d35252f711ed",
      "83d918f5-bfde-4aa8-9761-3d01bc355090",
      "85d5ba8e-433a-445f-ac6e-d4bf63795c16",
      "b718a167-47e4-4d78-8590-1afd1464e477",
      "ce44d3d2-c4cb-4382-9b80-5d25c73e470a",
      "ed7609db-d418-4a30-8163-13bf7d6eda4f"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "2443499b-0e4a-4c4c-9a1b-94d62ad2c16a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7599999904632568,
          "final_score": 0.35926407404406524,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.551328150186214
        },
        "3307fe0e-00e3-4434-909b-55005369426e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2659868877893543,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3735737764370155
        },
        "4b95908d-ed33-4ea0-a172-2f85bbb8cceb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3114997577665901,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45469951081249227
        },
        "530a4537-44f0-4bdf-a287-c35779be1684": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.271324104087444,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3842482090331949
        },
        "9f6f0779-c00b-46fe-93a7-fedc61ae85b8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.44398090665909085,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7379618133181817
        },
        "a3507840-f91a-4387-b00d-c0a72befee14": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.275959578743023,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3836191527653581
        },
        "abd639ac-73e0-4321-a31d-4e6e47509935": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6800000071525574,
          "final_score": 0.3919324087877795,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6342648160019964
        },
        "d74b991f-1a8e-4faf-babe-609101c43456": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6000000238418579,
          "final_score": 0.24194008084795265,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37588015740437086
        },
        "def57d94-00f8-414f-af13-f6f7f96789a5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.27696587609776946,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.40093174790400443
        },
        "f169f49a-51f3-42f8-8fd9-afb479c18da3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3169882001764369,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45577640507356165
        }
      },
      "100": {
        "165b525e-362d-4714-8678-0ff9d2c2dd29": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.2632397854689179,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3680795685774918
        },
        "2443499b-0e4a-4c4c-9a1b-94d62ad2c16a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7599999904632568,
          "final_score": 0.35926407404406524,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.551328150186214
        },
        "3307fe0e-00e3-4434-909b-55005369426e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2659868877893543,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3735737764370155
        },
        "4b95908d-ed33-4ea0-a172-2f85bbb8cceb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3114997577665901,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45469951081249227
        },
        "530a4537-44f0-4bdf-a287-c35779be1684": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.271324104087444,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3842482090331949
        },
        "9f6f0779-c00b-46fe-93a7-fedc61ae85b8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.44398090665909085,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7379618133181817
        },
        "a3507840-f91a-4387-b00d-c0a72befee14": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.275959578743023,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3836191527653581
        },
        "abd639ac-73e0-4321-a31d-4e6e47509935": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6800000071525574,
          "final_score": 0.3919324087877795,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6342648160019964
        },
        "d74b991f-1a8e-4faf-babe-609101c43456": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6000000238418579,
          "final_score": 0.24194008084795265,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37588015740437086
        },
        "def57d94-00f8-414f-af13-f6f7f96789a5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.27696587609776946,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.40093174790400443
        },
        "f169f49a-51f3-42f8-8fd9-afb479c18da3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3169882001764369,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45577640507356165
        }
      },
      "25": {
        "165b525e-362d-4714-8678-0ff9d2c2dd29": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.2632397854689179,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3680795685774918
        },
        "2443499b-0e4a-4c4c-9a1b-94d62ad2c16a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7599999904632568,
          "final_score": 0.35926407404406524,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.551328150186214
        },
        "3307fe0e-00e3-4434-909b-55005369426e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2659868877893543,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3735737764370155
        },
        "4b95908d-ed33-4ea0-a172-2f85bbb8cceb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3114997577665901,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45469951081249227
        },
        "530a4537-44f0-4bdf-a287-c35779be1684": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.271324104087444,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3842482090331949
        },
        "9f6f0779-c00b-46fe-93a7-fedc61ae85b8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.44398090665909085,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7379618133181817
        },
        "a3507840-f91a-4387-b00d-c0a72befee14": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.275959578743023,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3836191527653581
        },
        "abd639ac-73e0-4321-a31d-4e6e47509935": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6800000071525574,
          "final_score": 0.3919324087877795,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6342648160019964
        },
        "d74b991f-1a8e-4faf-babe-609101c43456": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6000000238418579,
          "final_score": 0.24194008084795265,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37588015740437086
        },
        "def57d94-00f8-414f-af13-f6f7f96789a5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.27696587609776946,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.40093174790400443
        },
        "f169f49a-51f3-42f8-8fd9-afb479c18da3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3169882001764369,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45577640507356165
        }
      },
      "50": {
        "165b525e-362d-4714-8678-0ff9d2c2dd29": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.2632397854689179,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3680795685774918
        },
        "2443499b-0e4a-4c4c-9a1b-94d62ad2c16a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7599999904632568,
          "final_score": 0.35926407404406524,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.551328150186214
        },
        "3307fe0e-00e3-4434-909b-55005369426e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2659868877893543,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3735737764370155
        },
        "4b95908d-ed33-4ea0-a172-2f85bbb8cceb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3114997577665901,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45469951081249227
        },
        "530a4537-44f0-4bdf-a287-c35779be1684": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.271324104087444,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3842482090331949
        },
        "9f6f0779-c00b-46fe-93a7-fedc61ae85b8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.44398090665909085,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7379618133181817
        },
        "a3507840-f91a-4387-b00d-c0a72befee14": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.275959578743023,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3836191527653581
        },
        "abd639ac-73e0-4321-a31d-4e6e47509935": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6800000071525574,
          "final_score": 0.3919324087877795,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6342648160019964
        },
        "d74b991f-1a8e-4faf-babe-609101c43456": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6000000238418579,
          "final_score": 0.24194008084795265,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.37588015740437086
        },
        "def57d94-00f8-414f-af13-f6f7f96789a5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.27696587609776946,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.40093174790400443
        },
        "f169f49a-51f3-42f8-8fd9-afb479c18da3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3169882001764369,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45577640507356165
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "165b525e-362d-4714-8678-0ff9d2c2dd29"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "2bf43736",
    "union_ids": [
      "165b525e-362d-4714-8678-0ff9d2c2dd29",
      "2443499b-0e4a-4c4c-9a1b-94d62ad2c16a",
      "3307fe0e-00e3-4434-909b-55005369426e",
      "4b95908d-ed33-4ea0-a172-2f85bbb8cceb",
      "530a4537-44f0-4bdf-a287-c35779be1684",
      "9f6f0779-c00b-46fe-93a7-fedc61ae85b8",
      "a3507840-f91a-4387-b00d-c0a72befee14",
      "abd639ac-73e0-4321-a31d-4e6e47509935",
      "d74b991f-1a8e-4faf-babe-609101c43456",
      "def57d94-00f8-414f-af13-f6f7f96789a5",
      "f169f49a-51f3-42f8-8fd9-afb479c18da3"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "175fd0f6-2042-4353-9e18-b242ab66636a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.25776495875794253,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.33512991908944767
        },
        "17c57b77-c5c5-4e89-ab50-3ce997787986": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.21147339144939375,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2529467781304159
        },
        "2bdd7788-26cd-42e0-a81f-54c4e224186b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.22370992062340023,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2604198360015917
        },
        "5491d021-d3ed-41ec-8428-8e0708c91c1f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.21400428229154828,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.25700856672886374
        },
        "5f25ccf7-3ed7-400c-9a69-ce06ffe98eea": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.19932846379437252,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.21865693235711658
        },
        "642dc7b1-a555-4945-abad-9aa2e2076a28": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3132819007904284,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.45656379681248516
        },
        "98e8dbef-eb10-444b-8920-25c787370c22": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.22271823758866882,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30243648042254634
        },
        "9b8f39e5-f034-4cf9-911e-bd97bd9cbad7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.21386072346174823,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.23772144930768224
        },
        "ab80f6a0-ebaa-42bc-bbbb-1717ffdae97e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.24670491481962323,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34940982391720055
        },
        "d7fe5a27-e404-499c-aee4-bb06f62ed5ae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2049207250402647,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2198414524647152
        }
      },
      "100": {
        "175fd0f6-2042-4353-9e18-b242ab66636a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.25776495875794253,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.33512991908944767
        },
        "17c57b77-c5c5-4e89-ab50-3ce997787986": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.21147339144939375,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2529467781304159
        },
        "2bdd7788-26cd-42e0-a81f-54c4e224186b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.22370992062340023,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2604198360015917
        },
        "5491d021-d3ed-41ec-8428-8e0708c91c1f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.21400428229154828,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.25700856672886374
        },
        "5f25ccf7-3ed7-400c-9a69-ce06ffe98eea": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.19932846379437252,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.21865693235711658
        },
        "642dc7b1-a555-4945-abad-9aa2e2076a28": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3132819007904284,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.45656379681248516
        },
        "98e8dbef-eb10-444b-8920-25c787370c22": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.22271823758866882,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30243648042254634
        },
        "9b8f39e5-f034-4cf9-911e-bd97bd9cbad7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.21386072346174823,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.23772144930768224
        },
        "ab80f6a0-ebaa-42bc-bbbb-1717ffdae97e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.24670491481962323,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34940982391720055
        },
        "d7fe5a27-e404-499c-aee4-bb06f62ed5ae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2049207250402647,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2198414524647152
        }
      },
      "25": {
        "175fd0f6-2042-4353-9e18-b242ab66636a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.25776495875794253,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.33512991908944767
        },
        "17c57b77-c5c5-4e89-ab50-3ce997787986": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.21147339144939375,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2529467781304159
        },
        "2bdd7788-26cd-42e0-a81f-54c4e224186b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.22370992062340023,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2604198360015917
        },
        "5491d021-d3ed-41ec-8428-8e0708c91c1f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.21400428229154828,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.25700856672886374
        },
        "5f25ccf7-3ed7-400c-9a69-ce06ffe98eea": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.19932846379437252,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.21865693235711658
        },
        "642dc7b1-a555-4945-abad-9aa2e2076a28": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3132819007904284,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.45656379681248516
        },
        "98e8dbef-eb10-444b-8920-25c787370c22": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.22271823758866882,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30243648042254634
        },
        "9b8f39e5-f034-4cf9-911e-bd97bd9cbad7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.21386072346174823,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.23772144930768224
        },
        "ab80f6a0-ebaa-42bc-bbbb-1717ffdae97e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.24670491481962323,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34940982391720055
        },
        "d7fe5a27-e404-499c-aee4-bb06f62ed5ae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2049207250402647,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2198414524647152
        }
      },
      "50": {
        "175fd0f6-2042-4353-9e18-b242ab66636a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.25776495875794253,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.33512991908944767
        },
        "17c57b77-c5c5-4e89-ab50-3ce997787986": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.21147339144939375,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2529467781304159
        },
        "2bdd7788-26cd-42e0-a81f-54c4e224186b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.22370992062340023,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2604198360015917
        },
        "5491d021-d3ed-41ec-8428-8e0708c91c1f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.21400428229154828,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.25700856672886374
        },
        "5f25ccf7-3ed7-400c-9a69-ce06ffe98eea": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.19932846379437252,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.21865693235711658
        },
        "642dc7b1-a555-4945-abad-9aa2e2076a28": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3132819007904284,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.45656379681248516
        },
        "98e8dbef-eb10-444b-8920-25c787370c22": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.22271823758866882,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30243648042254634
        },
        "9b8f39e5-f034-4cf9-911e-bd97bd9cbad7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.21386072346174823,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.23772144930768224
        },
        "ab80f6a0-ebaa-42bc-bbbb-1717ffdae97e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7200000286102295,
          "final_score": 0.24670491481962323,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34940982391720055
        },
        "d7fe5a27-e404-499c-aee4-bb06f62ed5ae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2049207250402647,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2198414524647152
        }
      }
    },
    "fixed_union_order_changed": false,
    "fixed_union_order_observable": true,
    "missing_from_pool": {
      "10": [],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "75f70248",
    "union_ids": [
      "175fd0f6-2042-4353-9e18-b242ab66636a",
      "17c57b77-c5c5-4e89-ab50-3ce997787986",
      "2bdd7788-26cd-42e0-a81f-54c4e224186b",
      "5491d021-d3ed-41ec-8428-8e0708c91c1f",
      "5f25ccf7-3ed7-400c-9a69-ce06ffe98eea",
      "642dc7b1-a555-4945-abad-9aa2e2076a28",
      "98e8dbef-eb10-444b-8920-25c787370c22",
      "9b8f39e5-f034-4cf9-911e-bd97bd9cbad7",
      "ab80f6a0-ebaa-42bc-bbbb-1717ffdae97e",
      "d7fe5a27-e404-499c-aee4-bb06f62ed5ae"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "09a6c536-2946-4dd0-9a39-ca2bbe6ee001": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.3217433001628942,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48748660604783434
        },
        "29b95fba-7ea0-4aaf-9ee6-7bb874366725": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.29827267551683084,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43254535246417314
        },
        "2c205eb2-cb3c-4b02-8f55-064938f28100": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3323509795672361,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.480701955796612
        },
        "44fa6e5f-5e10-423b-a9b5-0e26a8890839": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6800000071525574,
          "final_score": 0.32508197064979705,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5141639398690826
        },
        "48f8d91f-d92f-4eb1-bb9a-db5e9f65a851": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.28553681082587057,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42807362689694983
        },
        "5c453832-dab2-428e-be70-5c8e154e7b67": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3277955690098777,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.47959113897342975
        },
        "7f5a9eed-c00d-4f19-9c2a-9323774485b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.36100404451251106,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.538008085687162
        },
        "98bd5667-ef5d-4c2b-8acd-6c2a5083c332": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3523115244760518,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5206230456142434
        },
        "df5f3891-670d-4ded-9b93-f7dbeb9c3d56": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36480751118360005,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5496150271355716
        },
        "efde81c7-4c31-412a-b1a1-26714fe262fb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3019091780556999,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4278183570650741
        }
      },
      "100": {
        "09a6c536-2946-4dd0-9a39-ca2bbe6ee001": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.3217433001628942,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48748660604783434
        },
        "29b95fba-7ea0-4aaf-9ee6-7bb874366725": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.29827267551683084,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43254535246417314
        },
        "2c205eb2-cb3c-4b02-8f55-064938f28100": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3323509795672361,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.480701955796612
        },
        "44fa6e5f-5e10-423b-a9b5-0e26a8890839": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6800000071525574,
          "final_score": 0.32508197064979705,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5141639398690826
        },
        "48f8d91f-d92f-4eb1-bb9a-db5e9f65a851": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.28553681082587057,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42807362689694983
        },
        "4abb472f-ae34-4de9-a0ee-4297c403bd1f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2913097803734205,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.383119563250236
        },
        "5c453832-dab2-428e-be70-5c8e154e7b67": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3277955690098777,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.47959113897342975
        },
        "7f5a9eed-c00d-4f19-9c2a-9323774485b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.36100404451251106,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.538008085687162
        },
        "98bd5667-ef5d-4c2b-8acd-6c2a5083c332": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3523115244760518,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5206230456142434
        },
        "df5f3891-670d-4ded-9b93-f7dbeb9c3d56": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36480751118360005,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5496150271355716
        },
        "efde81c7-4c31-412a-b1a1-26714fe262fb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3019091780556999,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4278183570650741
        }
      },
      "25": {
        "09a6c536-2946-4dd0-9a39-ca2bbe6ee001": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.3217433001628942,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48748660604783434
        },
        "29b95fba-7ea0-4aaf-9ee6-7bb874366725": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.29827267551683084,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43254535246417314
        },
        "2c205eb2-cb3c-4b02-8f55-064938f28100": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3323509795672361,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.480701955796612
        },
        "44fa6e5f-5e10-423b-a9b5-0e26a8890839": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6800000071525574,
          "final_score": 0.32508197064979705,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5141639398690826
        },
        "48f8d91f-d92f-4eb1-bb9a-db5e9f65a851": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.28553681082587057,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42807362689694983
        },
        "4abb472f-ae34-4de9-a0ee-4297c403bd1f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2913097803734205,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.383119563250236
        },
        "5c453832-dab2-428e-be70-5c8e154e7b67": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3277955690098777,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.47959113897342975
        },
        "7f5a9eed-c00d-4f19-9c2a-9323774485b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.36100404451251106,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.538008085687162
        },
        "98bd5667-ef5d-4c2b-8acd-6c2a5083c332": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3523115244760518,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5206230456142434
        },
        "df5f3891-670d-4ded-9b93-f7dbeb9c3d56": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36480751118360005,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5496150271355716
        },
        "efde81c7-4c31-412a-b1a1-26714fe262fb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3019091780556999,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4278183570650741
        }
      },
      "50": {
        "09a6c536-2946-4dd0-9a39-ca2bbe6ee001": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.3217433001628942,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48748660604783434
        },
        "29b95fba-7ea0-4aaf-9ee6-7bb874366725": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.29827267551683084,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43254535246417314
        },
        "2c205eb2-cb3c-4b02-8f55-064938f28100": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3323509795672361,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.480701955796612
        },
        "44fa6e5f-5e10-423b-a9b5-0e26a8890839": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6800000071525574,
          "final_score": 0.32508197064979705,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5141639398690826
        },
        "48f8d91f-d92f-4eb1-bb9a-db5e9f65a851": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.28553681082587057,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42807362689694983
        },
        "4abb472f-ae34-4de9-a0ee-4297c403bd1f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2913097803734205,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.383119563250236
        },
        "5c453832-dab2-428e-be70-5c8e154e7b67": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3277955690098777,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.47959113897342975
        },
        "7f5a9eed-c00d-4f19-9c2a-9323774485b1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.36100404451251106,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.538008085687162
        },
        "98bd5667-ef5d-4c2b-8acd-6c2a5083c332": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3523115244760518,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5206230456142434
        },
        "df5f3891-670d-4ded-9b93-f7dbeb9c3d56": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36480751118360005,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5496150271355716
        },
        "efde81c7-4c31-412a-b1a1-26714fe262fb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3019091780556999,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4278183570650741
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "4abb472f-ae34-4de9-a0ee-4297c403bd1f"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "a3332713",
    "union_ids": [
      "09a6c536-2946-4dd0-9a39-ca2bbe6ee001",
      "29b95fba-7ea0-4aaf-9ee6-7bb874366725",
      "2c205eb2-cb3c-4b02-8f55-064938f28100",
      "44fa6e5f-5e10-423b-a9b5-0e26a8890839",
      "48f8d91f-d92f-4eb1-bb9a-db5e9f65a851",
      "4abb472f-ae34-4de9-a0ee-4297c403bd1f",
      "5c453832-dab2-428e-be70-5c8e154e7b67",
      "7f5a9eed-c00d-4f19-9c2a-9323774485b1",
      "98bd5667-ef5d-4c2b-8acd-6c2a5083c332",
      "df5f3891-670d-4ded-9b93-f7dbeb9c3d56",
      "efde81c7-4c31-412a-b1a1-26714fe262fb"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "01ce1843-9cbe-4b62-8dda-f8a690f30e69": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.32991037829436676,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48382075754240783
        },
        "26f6a267-e94d-4937-85ca-bab5517609e6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.34836028801565283,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5127205726934455
        },
        "52557b05-63ca-4ec5-9bc1-4bb24a53f24d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33220555112090916,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48441110701018986
        },
        "539afb80-6ec1-4165-98f2-ca058debb600": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3309167768757765,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4818335585199245
        },
        "8a949d2f-1409-4991-ad3c-c6ea95c97e86": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.4304031330277523,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7108062660555046
        },
        "9c5c0f6f-5996-4aff-973d-abc5b235b1d8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3541810022522064,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5183620068885986
        },
        "a620faa5-2196-4ebf-b6f7-6599da45f551": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3429078430245242,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5098156870027227
        },
        "ab4df132-176f-464f-b732-d88118b3f553": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3367966119093353,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48959322048081044
        },
        "e272b326-7dfd-48cc-90c9-61e3434ac57f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3270167519431162,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.46043350493527413
        },
        "e65f7073-1d85-4bc9-bfc5-2fe9e54dacc8": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.44325560740007997,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6933112112954067
        }
      },
      "100": {
        "01ce1843-9cbe-4b62-8dda-f8a690f30e69": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.32991037829436676,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48382075754240783
        },
        "26f6a267-e94d-4937-85ca-bab5517609e6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.34836028801565283,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5127205726934455
        },
        "52557b05-63ca-4ec5-9bc1-4bb24a53f24d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33220555112090916,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48441110701018986
        },
        "539afb80-6ec1-4165-98f2-ca058debb600": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3309167768757765,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4818335585199245
        },
        "8a949d2f-1409-4991-ad3c-c6ea95c97e86": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.4304031330277523,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7108062660555046
        },
        "9c5c0f6f-5996-4aff-973d-abc5b235b1d8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3541810022522064,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5183620068885986
        },
        "a620faa5-2196-4ebf-b6f7-6599da45f551": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3429078430245242,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5098156870027227
        },
        "ab4df132-176f-464f-b732-d88118b3f553": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3367966119093353,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48959322048081044
        },
        "e272b326-7dfd-48cc-90c9-61e3434ac57f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3270167519431162,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.46043350493527413
        },
        "e65f7073-1d85-4bc9-bfc5-2fe9e54dacc8": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.44325560740007997,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6933112112954067
        }
      },
      "25": {
        "01ce1843-9cbe-4b62-8dda-f8a690f30e69": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.32991037829436676,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48382075754240783
        },
        "26f6a267-e94d-4937-85ca-bab5517609e6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.34836028801565283,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5127205726934455
        },
        "52557b05-63ca-4ec5-9bc1-4bb24a53f24d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33220555112090916,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48441110701018986
        },
        "539afb80-6ec1-4165-98f2-ca058debb600": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3309167768757765,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4818335585199245
        },
        "8a949d2f-1409-4991-ad3c-c6ea95c97e86": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.4304031330277523,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7108062660555046
        },
        "9c5c0f6f-5996-4aff-973d-abc5b235b1d8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3541810022522064,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5183620068885986
        },
        "a620faa5-2196-4ebf-b6f7-6599da45f551": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3429078430245242,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5098156870027227
        },
        "ab4df132-176f-464f-b732-d88118b3f553": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3367966119093353,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48959322048081044
        },
        "e272b326-7dfd-48cc-90c9-61e3434ac57f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3270167519431162,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.46043350493527413
        },
        "e65f7073-1d85-4bc9-bfc5-2fe9e54dacc8": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.44325560740007997,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6933112112954067
        }
      },
      "50": {
        "01ce1843-9cbe-4b62-8dda-f8a690f30e69": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.32991037829436676,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48382075754240783
        },
        "26f6a267-e94d-4937-85ca-bab5517609e6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.34836028801565283,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5127205726934455
        },
        "52557b05-63ca-4ec5-9bc1-4bb24a53f24d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.33220555112090916,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48441110701018986
        },
        "539afb80-6ec1-4165-98f2-ca058debb600": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3309167768757765,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4818335585199245
        },
        "8a949d2f-1409-4991-ad3c-c6ea95c97e86": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.4304031330277523,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7108062660555046
        },
        "9c5c0f6f-5996-4aff-973d-abc5b235b1d8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3541810022522064,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5183620068885986
        },
        "a620faa5-2196-4ebf-b6f7-6599da45f551": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3429078430245242,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5098156870027227
        },
        "ab4df132-176f-464f-b732-d88118b3f553": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3367966119093353,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.48959322048081044
        },
        "e272b326-7dfd-48cc-90c9-61e3434ac57f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3270167519431162,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.46043350493527413
        },
        "e65f7073-1d85-4bc9-bfc5-2fe9e54dacc8": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.44325560740007997,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6933112112954067
        }
      }
    },
    "fixed_union_order_changed": false,
    "fixed_union_order_observable": true,
    "missing_from_pool": {
      "10": [],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "0bb5a684",
    "union_ids": [
      "01ce1843-9cbe-4b62-8dda-f8a690f30e69",
      "26f6a267-e94d-4937-85ca-bab5517609e6",
      "52557b05-63ca-4ec5-9bc1-4bb24a53f24d",
      "539afb80-6ec1-4165-98f2-ca058debb600",
      "8a949d2f-1409-4991-ad3c-c6ea95c97e86",
      "9c5c0f6f-5996-4aff-973d-abc5b235b1d8",
      "a620faa5-2196-4ebf-b6f7-6599da45f551",
      "ab4df132-176f-464f-b732-d88118b3f553",
      "e272b326-7dfd-48cc-90c9-61e3434ac57f",
      "e65f7073-1d85-4bc9-bfc5-2fe9e54dacc8"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "07e0bff2-4426-42a2-be11-9accd9862854": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.24827504804395745,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3678501008086027
        },
        "4550fec0-3cd0-4351-8ee4-1946e1795698": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.38943709737130683,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6168741990341481
        },
        "5c11a833-0d25-48d4-b825-8084d18497da": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2316155948921574,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3102311854927804
        },
        "72241f20-486b-49a1-92d1-8cf18427bb8c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.35408708474387396,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5339341704318854
        },
        "72a88f81-0626-454a-b15c-a45908eb0451": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.25705596017467114,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3359119250700301
        },
        "87425670-259e-46da-a36d-3be6c4cb1032": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.26363908546068787,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35627817306714293
        },
        "9b68b573-b961-415d-a9c5-f054b073ebed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2652130707098574,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34232614378005866
        },
        "af81b5e8-5d05-4f1c-af41-b137c7754a6e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3209086327498741,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4798172697912826
        },
        "b914bab8-28ab-4eeb-b4b6-a6b43e64b878": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.23251535706343654,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3105907197916985
        },
        "b995571c-7a91-4884-b227-1fdfbec0b0ee": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.27320319908017926,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3583064005207024
        }
      },
      "100": {
        "07e0bff2-4426-42a2-be11-9accd9862854": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.24827504804395745,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3678501008086027
        },
        "4550fec0-3cd0-4351-8ee4-1946e1795698": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.38943709737130683,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6168741990341481
        },
        "5c11a833-0d25-48d4-b825-8084d18497da": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2316155948921574,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3102311854927804
        },
        "72241f20-486b-49a1-92d1-8cf18427bb8c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.35408708474387396,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5339341704318854
        },
        "72a88f81-0626-454a-b15c-a45908eb0451": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.25705596017467114,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3359119250700301
        },
        "76ac6ca5-31ec-44c8-8202-39eab132ce6e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.23992271730538822,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.30884543675654363
        },
        "87425670-259e-46da-a36d-3be6c4cb1032": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.26363908546068787,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35627817306714293
        },
        "9b68b573-b961-415d-a9c5-f054b073ebed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2652130707098574,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34232614378005866
        },
        "aecfa69a-801b-4791-8c3f-c5f70175b4c9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.23981740554701458,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.301434815814717
        },
        "af81b5e8-5d05-4f1c-af41-b137c7754a6e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3209086327498741,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4798172697912826
        },
        "b914bab8-28ab-4eeb-b4b6-a6b43e64b878": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.23251535706343654,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3105907197916985
        },
        "b995571c-7a91-4884-b227-1fdfbec0b0ee": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.27320319908017926,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3583064005207024
        }
      },
      "25": {
        "07e0bff2-4426-42a2-be11-9accd9862854": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.24827504804395745,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3678501008086027
        },
        "4550fec0-3cd0-4351-8ee4-1946e1795698": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.38943709737130683,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6168741990341481
        },
        "5c11a833-0d25-48d4-b825-8084d18497da": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2316155948921574,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3102311854927804
        },
        "72241f20-486b-49a1-92d1-8cf18427bb8c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.35408708474387396,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5339341704318854
        },
        "72a88f81-0626-454a-b15c-a45908eb0451": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.25705596017467114,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3359119250700301
        },
        "76ac6ca5-31ec-44c8-8202-39eab132ce6e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.23992271730538822,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.30884543675654363
        },
        "87425670-259e-46da-a36d-3be6c4cb1032": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.26363908546068787,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35627817306714293
        },
        "9b68b573-b961-415d-a9c5-f054b073ebed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2652130707098574,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34232614378005866
        },
        "aecfa69a-801b-4791-8c3f-c5f70175b4c9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.23981740554701458,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.301434815814717
        },
        "af81b5e8-5d05-4f1c-af41-b137c7754a6e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3209086327498741,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4798172697912826
        },
        "b914bab8-28ab-4eeb-b4b6-a6b43e64b878": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.23251535706343654,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3105907197916985
        },
        "b995571c-7a91-4884-b227-1fdfbec0b0ee": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.27320319908017926,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3583064005207024
        }
      },
      "50": {
        "07e0bff2-4426-42a2-be11-9accd9862854": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.24827504804395745,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3678501008086027
        },
        "4550fec0-3cd0-4351-8ee4-1946e1795698": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.38943709737130683,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6168741990341481
        },
        "5c11a833-0d25-48d4-b825-8084d18497da": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2316155948921574,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3102311854927804
        },
        "72241f20-486b-49a1-92d1-8cf18427bb8c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.35408708474387396,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5339341704318854
        },
        "72a88f81-0626-454a-b15c-a45908eb0451": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.25705596017467114,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3359119250700301
        },
        "76ac6ca5-31ec-44c8-8202-39eab132ce6e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.23992271730538822,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.30884543675654363
        },
        "87425670-259e-46da-a36d-3be6c4cb1032": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.26363908546068787,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35627817306714293
        },
        "9b68b573-b961-415d-a9c5-f054b073ebed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2652130707098574,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34232614378005866
        },
        "aecfa69a-801b-4791-8c3f-c5f70175b4c9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.23981740554701458,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.301434815814717
        },
        "af81b5e8-5d05-4f1c-af41-b137c7754a6e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3209086327498741,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4798172697912826
        },
        "b914bab8-28ab-4eeb-b4b6-a6b43e64b878": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.23251535706343654,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3105907197916985
        },
        "b995571c-7a91-4884-b227-1fdfbec0b0ee": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.27320319908017926,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3583064005207024
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "76ac6ca5-31ec-44c8-8202-39eab132ce6e",
        "aecfa69a-801b-4791-8c3f-c5f70175b4c9"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "0977f2af",
    "union_ids": [
      "07e0bff2-4426-42a2-be11-9accd9862854",
      "4550fec0-3cd0-4351-8ee4-1946e1795698",
      "5c11a833-0d25-48d4-b825-8084d18497da",
      "72241f20-486b-49a1-92d1-8cf18427bb8c",
      "72a88f81-0626-454a-b15c-a45908eb0451",
      "76ac6ca5-31ec-44c8-8202-39eab132ce6e",
      "87425670-259e-46da-a36d-3be6c4cb1032",
      "9b68b573-b961-415d-a9c5-f054b073ebed",
      "aecfa69a-801b-4791-8c3f-c5f70175b4c9",
      "af81b5e8-5d05-4f1c-af41-b137c7754a6e",
      "b914bab8-28ab-4eeb-b4b6-a6b43e64b878",
      "b995571c-7a91-4884-b227-1fdfbec0b0ee"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "1aef5b81-83f1-4a31-9e6a-a256bbdbdd16": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2706317729749605,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3322635485725254
        },
        "87615982-5548-464c-bc4d-815ce91f565e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.21332360051323032,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2866472034106464
        },
        "96f1fc1a-0315-4c40-8467-64f0e395c41f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21957700813883885,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24555401732671944
        },
        "9a464679-7e3d-4a3b-8834-b41141de6720": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3004524044209379,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42090481361024734
        },
        "a32cf1b9-3016-43f0-b1c5-baa31bfda749": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.22278249590505667,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2565649968169035
        },
        "a335b455-3094-46b7-9fb5-06a63b2fc1e9": {
          "access_boost": 1.0,
          "bm25_normalized": 1.0,
          "bm25_score": 0.2669127583503723,
          "confidence": 0.949999988079071,
          "final_score": 0.6555404963084384,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5210809950010626
        },
        "ab10c3c5-34b3-47f0-b371-425a23f275e5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21418193868149749,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2483638821313665
        },
        "bebac294-f1c7-4e59-946a-dfe85c937fcf": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30621127770925044,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4324225601868724
        },
        "f02b861e-1e34-4495-9efe-5c470a0dc74f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.292589227261741,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4051784592918536
        },
        "f86c0555-998b-49d6-820d-05d02238fb29": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.24610153777664934,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34920308079850737
        }
      },
      "100": {
        "1aef5b81-83f1-4a31-9e6a-a256bbdbdd16": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2706317729749605,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3322635485725254
        },
        "87615982-5548-464c-bc4d-815ce91f565e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.21332360051323032,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2866472034106464
        },
        "96f1fc1a-0315-4c40-8467-64f0e395c41f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21957700813883885,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24555401732671944
        },
        "9a464679-7e3d-4a3b-8834-b41141de6720": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3004524044209379,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42090481361024734
        },
        "a32cf1b9-3016-43f0-b1c5-baa31bfda749": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.22278249590505667,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2565649968169035
        },
        "a335b455-3094-46b7-9fb5-06a63b2fc1e9": {
          "access_boost": 1.0,
          "bm25_normalized": 1.0,
          "bm25_score": 0.2669127583503723,
          "confidence": 0.949999988079071,
          "final_score": 0.6555404963084384,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5210809950010626
        },
        "ab10c3c5-34b3-47f0-b371-425a23f275e5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21418193868149749,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2483638821313665
        },
        "bebac294-f1c7-4e59-946a-dfe85c937fcf": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30621127770925044,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4324225601868724
        },
        "f02b861e-1e34-4495-9efe-5c470a0dc74f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.292589227261741,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4051784592918536
        },
        "f86c0555-998b-49d6-820d-05d02238fb29": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.24610153777664934,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34920308079850737
        }
      },
      "25": {
        "1aef5b81-83f1-4a31-9e6a-a256bbdbdd16": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2706317729749605,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3322635485725254
        },
        "87615982-5548-464c-bc4d-815ce91f565e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.21332360051323032,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2866472034106464
        },
        "96f1fc1a-0315-4c40-8467-64f0e395c41f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21957700813883885,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24555401732671944
        },
        "9a464679-7e3d-4a3b-8834-b41141de6720": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3004524044209379,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42090481361024734
        },
        "a32cf1b9-3016-43f0-b1c5-baa31bfda749": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.22278249590505667,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2565649968169035
        },
        "a335b455-3094-46b7-9fb5-06a63b2fc1e9": {
          "access_boost": 1.0,
          "bm25_normalized": 1.0,
          "bm25_score": 0.2669127583503723,
          "confidence": 0.949999988079071,
          "final_score": 0.6555404963084384,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5210809950010626
        },
        "ab10c3c5-34b3-47f0-b371-425a23f275e5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21418193868149749,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2483638821313665
        },
        "bebac294-f1c7-4e59-946a-dfe85c937fcf": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30621127770925044,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4324225601868724
        },
        "f02b861e-1e34-4495-9efe-5c470a0dc74f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.292589227261741,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4051784592918536
        },
        "f86c0555-998b-49d6-820d-05d02238fb29": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.24610153777664934,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34920308079850737
        }
      },
      "50": {
        "1aef5b81-83f1-4a31-9e6a-a256bbdbdd16": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.2706317729749605,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3322635485725254
        },
        "87615982-5548-464c-bc4d-815ce91f565e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.21332360051323032,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2866472034106464
        },
        "96f1fc1a-0315-4c40-8467-64f0e395c41f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21957700813883885,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24555401732671944
        },
        "9a464679-7e3d-4a3b-8834-b41141de6720": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3004524044209379,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.42090481361024734
        },
        "a32cf1b9-3016-43f0-b1c5-baa31bfda749": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.22278249590505667,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2565649968169035
        },
        "a335b455-3094-46b7-9fb5-06a63b2fc1e9": {
          "access_boost": 1.0,
          "bm25_normalized": 1.0,
          "bm25_score": 0.2669127583503723,
          "confidence": 0.949999988079071,
          "final_score": 0.6555404963084384,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5210809950010626
        },
        "ab10c3c5-34b3-47f0-b371-425a23f275e5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21418193868149749,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2483638821313665
        },
        "bebac294-f1c7-4e59-946a-dfe85c937fcf": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30621127770925044,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4324225601868724
        },
        "f02b861e-1e34-4495-9efe-5c470a0dc74f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.292589227261741,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4051784592918536
        },
        "f86c0555-998b-49d6-820d-05d02238fb29": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.24610153777664934,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34920308079850737
        }
      }
    },
    "fixed_union_order_changed": false,
    "fixed_union_order_observable": true,
    "missing_from_pool": {
      "10": [],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "c5e8278d",
    "union_ids": [
      "1aef5b81-83f1-4a31-9e6a-a256bbdbdd16",
      "87615982-5548-464c-bc4d-815ce91f565e",
      "96f1fc1a-0315-4c40-8467-64f0e395c41f",
      "9a464679-7e3d-4a3b-8834-b41141de6720",
      "a32cf1b9-3016-43f0-b1c5-baa31bfda749",
      "a335b455-3094-46b7-9fb5-06a63b2fc1e9",
      "ab10c3c5-34b3-47f0-b371-425a23f275e5",
      "bebac294-f1c7-4e59-946a-dfe85c937fcf",
      "f02b861e-1e34-4495-9efe-5c470a0dc74f",
      "f86c0555-998b-49d6-820d-05d02238fb29"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "73ba3104-556b-409a-bb1a-d90692d199b7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2726725509861082,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.35174510302125817
        },
        "85465a7d-48f4-4e25-a9e9-4831ad8bd5ee": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2340185890708417,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2700371833868921
        },
        "8fc7ec0b-9740-430b-a158-79ec221024d2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2576470655018328,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33929413195733993
        },
        "b2f0b88c-5ebb-42b7-b60d-ead2cf41f0ae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.23595301548818098,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2919060357447335
        },
        "ba36a2f0-534a-4cc0-afd8-adfe06df4f69": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3247007108275722,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4844014216551443
        },
        "c5feaa95-2721-4e1d-8bad-5b90dbf032b9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6000000238418579,
          "final_score": 0.25579256626536373,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.37958512728551874
        },
        "c80e0eeb-7156-461c-82d1-c2631b017d2b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.3568722489847281,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5377444953468518
        },
        "d525198c-a5f9-400a-92f1-e5621ec6752b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3899014853842888,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5928029655233689
        },
        "d8d89762-5a23-4a7b-adc3-2d98b2c1e6bf": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.30706063707356124,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4271212689019137
        },
        "d9070e53-054a-4d24-a60b-28656415251a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.24094872624870067,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2978974491595412
        }
      },
      "100": {
        "73ba3104-556b-409a-bb1a-d90692d199b7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2726725509861082,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.35174510302125817
        },
        "85465a7d-48f4-4e25-a9e9-4831ad8bd5ee": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2340185890708417,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2700371833868921
        },
        "8fc7ec0b-9740-430b-a158-79ec221024d2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2576470655018328,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33929413195733993
        },
        "b2f0b88c-5ebb-42b7-b60d-ead2cf41f0ae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.23595301548818098,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2919060357447335
        },
        "ba36a2f0-534a-4cc0-afd8-adfe06df4f69": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3247007108275722,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4844014216551443
        },
        "c5feaa95-2721-4e1d-8bad-5b90dbf032b9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6000000238418579,
          "final_score": 0.25579256626536373,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.37958512728551874
        },
        "c80e0eeb-7156-461c-82d1-c2631b017d2b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.3568722489847281,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5377444953468518
        },
        "d525198c-a5f9-400a-92f1-e5621ec6752b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3899014853842888,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5928029655233689
        },
        "d8d89762-5a23-4a7b-adc3-2d98b2c1e6bf": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.30706063707356124,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4271212689019137
        },
        "d9070e53-054a-4d24-a60b-28656415251a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.24094872624870067,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2978974491595412
        }
      },
      "25": {
        "73ba3104-556b-409a-bb1a-d90692d199b7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2726725509861082,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.35174510302125817
        },
        "85465a7d-48f4-4e25-a9e9-4831ad8bd5ee": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2340185890708417,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2700371833868921
        },
        "8fc7ec0b-9740-430b-a158-79ec221024d2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2576470655018328,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33929413195733993
        },
        "b2f0b88c-5ebb-42b7-b60d-ead2cf41f0ae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.23595301548818098,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2919060357447335
        },
        "ba36a2f0-534a-4cc0-afd8-adfe06df4f69": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3247007108275722,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4844014216551443
        },
        "c5feaa95-2721-4e1d-8bad-5b90dbf032b9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6000000238418579,
          "final_score": 0.25579256626536373,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.37958512728551874
        },
        "c80e0eeb-7156-461c-82d1-c2631b017d2b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.3568722489847281,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5377444953468518
        },
        "d525198c-a5f9-400a-92f1-e5621ec6752b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3899014853842888,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5928029655233689
        },
        "d8d89762-5a23-4a7b-adc3-2d98b2c1e6bf": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.30706063707356124,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4271212689019137
        },
        "d9070e53-054a-4d24-a60b-28656415251a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.24094872624870067,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2978974491595412
        }
      },
      "50": {
        "73ba3104-556b-409a-bb1a-d90692d199b7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2726725509861082,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.35174510302125817
        },
        "85465a7d-48f4-4e25-a9e9-4831ad8bd5ee": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2340185890708417,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2700371833868921
        },
        "8fc7ec0b-9740-430b-a158-79ec221024d2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2576470655018328,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33929413195733993
        },
        "b2f0b88c-5ebb-42b7-b60d-ead2cf41f0ae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.23595301548818098,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2919060357447335
        },
        "ba36a2f0-534a-4cc0-afd8-adfe06df4f69": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3247007108275722,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4844014216551443
        },
        "c5feaa95-2721-4e1d-8bad-5b90dbf032b9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6000000238418579,
          "final_score": 0.25579256626536373,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.37958512728551874
        },
        "c80e0eeb-7156-461c-82d1-c2631b017d2b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.3568722489847281,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5377444953468518
        },
        "d525198c-a5f9-400a-92f1-e5621ec6752b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.3899014853842888,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5928029655233689
        },
        "d8d89762-5a23-4a7b-adc3-2d98b2c1e6bf": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.30706063707356124,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4271212689019137
        },
        "d9070e53-054a-4d24-a60b-28656415251a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.24094872624870067,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2978974491595412
        }
      }
    },
    "fixed_union_order_changed": false,
    "fixed_union_order_observable": true,
    "missing_from_pool": {
      "10": [],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "c4f10528",
    "union_ids": [
      "73ba3104-556b-409a-bb1a-d90692d199b7",
      "85465a7d-48f4-4e25-a9e9-4831ad8bd5ee",
      "8fc7ec0b-9740-430b-a158-79ec221024d2",
      "b2f0b88c-5ebb-42b7-b60d-ead2cf41f0ae",
      "ba36a2f0-534a-4cc0-afd8-adfe06df4f69",
      "c5feaa95-2721-4e1d-8bad-5b90dbf032b9",
      "c80e0eeb-7156-461c-82d1-c2631b017d2b",
      "d525198c-a5f9-400a-92f1-e5621ec6752b",
      "d8d89762-5a23-4a7b-adc3-2d98b2c1e6bf",
      "d9070e53-054a-4d24-a60b-28656415251a"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "00f4accc-5394-41be-a95f-9c5486f98f59": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6800000071525574,
          "final_score": 0.2686675165090784,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4026950316019504
        },
        "0459936f-9f3c-4ba6-abc8-a88ec744ee85": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.36449009187880466,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5547401847017469
        },
        "06be3472-db5c-4951-976a-72df4fd8b115": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30857551248829884,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4389510296972855
        },
        "4824a4fe-ce98-47c4-a22e-a67eba3a980c": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3314413697896365,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.47993074057061746
        },
        "546f85bf-8197-4aac-bc00-0fb59c6fb275": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.33915461456418594,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.510009224407684
        },
        "767cbb03-c22b-485e-b193-28f355f41d06": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.32661801795149914,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.46196803243329265
        },
        "b32f4322-8ec4-442a-8cba-e513ca254bc6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.28652490253670876,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.41104980936495195
        },
        "c44381aa-346e-46dc-9c5c-ac59d37f6fae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3110803467114886,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.443960698143665
        },
        "c8de1612-a035-4bf6-ae18-16b3bcb809bd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.2908110527234463,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.427182111111718
        },
        "ef469f50-44a5-4b78-9143-85a2d6d3868a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3424333883522642,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5106267776486659
        }
      },
      "100": {
        "00f4accc-5394-41be-a95f-9c5486f98f59": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6800000071525574,
          "final_score": 0.2686675165090784,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4026950316019504
        },
        "0459936f-9f3c-4ba6-abc8-a88ec744ee85": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.36449009187880466,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5547401847017469
        },
        "06be3472-db5c-4951-976a-72df4fd8b115": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30857551248829884,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4389510296972855
        },
        "4824a4fe-ce98-47c4-a22e-a67eba3a980c": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3314413697896365,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.47993074057061746
        },
        "546f85bf-8197-4aac-bc00-0fb59c6fb275": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.33915461456418594,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.510009224407684
        },
        "75dc35e6-263e-4877-8c4e-4774228c92f7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2795356716029772,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.39077133848526646
        },
        "767cbb03-c22b-485e-b193-28f355f41d06": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.32661801795149914,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.46196803243329265
        },
        "b32f4322-8ec4-442a-8cba-e513ca254bc6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.28652490253670876,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.41104980936495195
        },
        "c44381aa-346e-46dc-9c5c-ac59d37f6fae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3110803467114886,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.443960698143665
        },
        "c8de1612-a035-4bf6-ae18-16b3bcb809bd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.2908110527234463,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.427182111111718
        },
        "ef469f50-44a5-4b78-9143-85a2d6d3868a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3424333883522642,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5106267776486659
        }
      },
      "25": {
        "00f4accc-5394-41be-a95f-9c5486f98f59": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6800000071525574,
          "final_score": 0.2686675165090784,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4026950316019504
        },
        "0459936f-9f3c-4ba6-abc8-a88ec744ee85": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.36449009187880466,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5547401847017469
        },
        "06be3472-db5c-4951-976a-72df4fd8b115": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30857551248829884,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4389510296972855
        },
        "4824a4fe-ce98-47c4-a22e-a67eba3a980c": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3314413697896365,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.47993074057061746
        },
        "546f85bf-8197-4aac-bc00-0fb59c6fb275": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.33915461456418594,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.510009224407684
        },
        "75dc35e6-263e-4877-8c4e-4774228c92f7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2795356716029772,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.39077133848526646
        },
        "767cbb03-c22b-485e-b193-28f355f41d06": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.32661801795149914,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.46196803243329265
        },
        "b32f4322-8ec4-442a-8cba-e513ca254bc6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.28652490253670876,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.41104980936495195
        },
        "c44381aa-346e-46dc-9c5c-ac59d37f6fae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3110803467114886,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.443960698143665
        },
        "c8de1612-a035-4bf6-ae18-16b3bcb809bd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.2908110527234463,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.427182111111718
        },
        "ef469f50-44a5-4b78-9143-85a2d6d3868a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3424333883522642,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5106267776486659
        }
      },
      "50": {
        "00f4accc-5394-41be-a95f-9c5486f98f59": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6800000071525574,
          "final_score": 0.2686675165090784,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4026950316019504
        },
        "0459936f-9f3c-4ba6-abc8-a88ec744ee85": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.36449009187880466,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5547401847017469
        },
        "06be3472-db5c-4951-976a-72df4fd8b115": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30857551248829884,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4389510296972855
        },
        "4824a4fe-ce98-47c4-a22e-a67eba3a980c": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3314413697896365,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.47993074057061746
        },
        "546f85bf-8197-4aac-bc00-0fb59c6fb275": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.33915461456418594,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.510009224407684
        },
        "75dc35e6-263e-4877-8c4e-4774228c92f7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2795356716029772,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.39077133848526646
        },
        "767cbb03-c22b-485e-b193-28f355f41d06": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.32661801795149914,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.46196803243329265
        },
        "b32f4322-8ec4-442a-8cba-e513ca254bc6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.28652490253670876,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.41104980936495195
        },
        "c44381aa-346e-46dc-9c5c-ac59d37f6fae": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3110803467114886,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.443960698143665
        },
        "c8de1612-a035-4bf6-ae18-16b3bcb809bd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.2908110527234463,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.427182111111718
        },
        "ef469f50-44a5-4b78-9143-85a2d6d3868a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3424333883522642,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5106267776486659
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "75dc35e6-263e-4877-8c4e-4774228c92f7"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "0a34ad58",
    "union_ids": [
      "00f4accc-5394-41be-a95f-9c5486f98f59",
      "0459936f-9f3c-4ba6-abc8-a88ec744ee85",
      "06be3472-db5c-4951-976a-72df4fd8b115",
      "4824a4fe-ce98-47c4-a22e-a67eba3a980c",
      "546f85bf-8197-4aac-bc00-0fb59c6fb275",
      "75dc35e6-263e-4877-8c4e-4774228c92f7",
      "767cbb03-c22b-485e-b193-28f355f41d06",
      "b32f4322-8ec4-442a-8cba-e513ca254bc6",
      "c44381aa-346e-46dc-9c5c-ac59d37f6fae",
      "c8de1612-a035-4bf6-ae18-16b3bcb809bd",
      "ef469f50-44a5-4b78-9143-85a2d6d3868a"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "2ec1dd4c-87bb-4322-8d9f-631ee4bd0d50": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.22641276816023787,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3028255363204757
        },
        "46185836-d336-46f8-81a0-ab4a957b1b75": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2272100961751946,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.27442019711876076
        },
        "4d98b884-17ce-4d4f-85f8-12ac0d02c8b6": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.24449004811619812,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31048009122560605
        },
        "534ebef3-fa7b-4dcd-880d-abf021109b4c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36383025238535777,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5476605095390871
        },
        "89199f49-2a82-439b-a85a-e4f66944f823": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2753137311647331,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3706274670978378
        },
        "9e3940dd-feea-4602-82b5-64aabe7468f0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.22908322440407147,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2881664440397713
        },
        "a072122e-3e37-4030-8923-4fa720b4face": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.24201752855991904,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33403505711983805
        },
        "a3c6a894-384e-453c-978c-48810669db99": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.29940753150726274,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.418815067782897
        },
        "e5da93b6-f1ab-4cbb-8d1a-3303b3733f13": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2917283832265729,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.40345677122151735
        },
        "fe52793e-6880-4dc2-83f7-fa8923892c5c": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24844813602273413,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3078962770522584
        }
      },
      "100": {
        "2ec1dd4c-87bb-4322-8d9f-631ee4bd0d50": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.22641276816023787,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3028255363204757
        },
        "46185836-d336-46f8-81a0-ab4a957b1b75": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2272100961751946,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.27442019711876076
        },
        "4d98b884-17ce-4d4f-85f8-12ac0d02c8b6": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.24449004811619812,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31048009122560605
        },
        "534ebef3-fa7b-4dcd-880d-abf021109b4c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36383025238535777,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5476605095390871
        },
        "6d98d034-2f26-42dd-8b25-000c8ed1beb5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2294430146633525,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2608860345719137
        },
        "89199f49-2a82-439b-a85a-e4f66944f823": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2753137311647331,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3706274670978378
        },
        "9e3940dd-feea-4602-82b5-64aabe7468f0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.22908322440407147,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2881664440397713
        },
        "a072122e-3e37-4030-8923-4fa720b4face": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.24201752855991904,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33403505711983805
        },
        "a3c6a894-384e-453c-978c-48810669db99": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.29940753150726274,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.418815067782897
        },
        "b9ba3303-2a07-4f4d-8918-a3f5d9f767bf": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.23220037489828133,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.26200074612491653
        },
        "e5da93b6-f1ab-4cbb-8d1a-3303b3733f13": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2917283832265729,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.40345677122151735
        },
        "fe52793e-6880-4dc2-83f7-fa8923892c5c": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24844813602273413,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3078962770522584
        }
      },
      "25": {
        "2ec1dd4c-87bb-4322-8d9f-631ee4bd0d50": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.22641276816023787,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3028255363204757
        },
        "46185836-d336-46f8-81a0-ab4a957b1b75": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2272100961751946,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.27442019711876076
        },
        "4d98b884-17ce-4d4f-85f8-12ac0d02c8b6": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.24449004811619812,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31048009122560605
        },
        "534ebef3-fa7b-4dcd-880d-abf021109b4c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36383025238535777,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5476605095390871
        },
        "6d98d034-2f26-42dd-8b25-000c8ed1beb5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2294430146633525,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2608860345719137
        },
        "89199f49-2a82-439b-a85a-e4f66944f823": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2753137311647331,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3706274670978378
        },
        "9e3940dd-feea-4602-82b5-64aabe7468f0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.22908322440407147,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2881664440397713
        },
        "a072122e-3e37-4030-8923-4fa720b4face": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.24201752855991904,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33403505711983805
        },
        "a3c6a894-384e-453c-978c-48810669db99": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.29940753150726274,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.418815067782897
        },
        "b9ba3303-2a07-4f4d-8918-a3f5d9f767bf": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.23220037489828133,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.26200074612491653
        },
        "e5da93b6-f1ab-4cbb-8d1a-3303b3733f13": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2917283832265729,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.40345677122151735
        },
        "fe52793e-6880-4dc2-83f7-fa8923892c5c": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24844813602273413,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3078962770522584
        }
      },
      "50": {
        "2ec1dd4c-87bb-4322-8d9f-631ee4bd0d50": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.22641276816023787,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3028255363204757
        },
        "46185836-d336-46f8-81a0-ab4a957b1b75": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2272100961751946,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.27442019711876076
        },
        "4d98b884-17ce-4d4f-85f8-12ac0d02c8b6": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.24449004811619812,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.31048009122560605
        },
        "534ebef3-fa7b-4dcd-880d-abf021109b4c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36383025238535777,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5476605095390871
        },
        "6d98d034-2f26-42dd-8b25-000c8ed1beb5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2294430146633525,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.2608860345719137
        },
        "89199f49-2a82-439b-a85a-e4f66944f823": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2753137311647331,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3706274670978378
        },
        "9e3940dd-feea-4602-82b5-64aabe7468f0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.22908322440407147,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2881664440397713
        },
        "a072122e-3e37-4030-8923-4fa720b4face": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.24201752855991904,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.33403505711983805
        },
        "a3c6a894-384e-453c-978c-48810669db99": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.29940753150726274,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.418815067782897
        },
        "b9ba3303-2a07-4f4d-8918-a3f5d9f767bf": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.23220037489828133,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.26200074612491653
        },
        "e5da93b6-f1ab-4cbb-8d1a-3303b3733f13": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2917283832265729,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.40345677122151735
        },
        "fe52793e-6880-4dc2-83f7-fa8923892c5c": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24844813602273413,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3078962770522584
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "6d98d034-2f26-42dd-8b25-000c8ed1beb5",
        "b9ba3303-2a07-4f4d-8918-a3f5d9f767bf"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "92a0aa75",
    "union_ids": [
      "2ec1dd4c-87bb-4322-8d9f-631ee4bd0d50",
      "46185836-d336-46f8-81a0-ab4a957b1b75",
      "4d98b884-17ce-4d4f-85f8-12ac0d02c8b6",
      "534ebef3-fa7b-4dcd-880d-abf021109b4c",
      "6d98d034-2f26-42dd-8b25-000c8ed1beb5",
      "89199f49-2a82-439b-a85a-e4f66944f823",
      "9e3940dd-feea-4602-82b5-64aabe7468f0",
      "a072122e-3e37-4030-8923-4fa720b4face",
      "a3c6a894-384e-453c-978c-48810669db99",
      "b9ba3303-2a07-4f4d-8918-a3f5d9f767bf",
      "e5da93b6-f1ab-4cbb-8d1a-3303b3733f13",
      "fe52793e-6880-4dc2-83f7-fa8923892c5c"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "021a2799-b28e-4266-bec8-a9d8861880c7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3163638885136908,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44872777368952144
        },
        "7ccb4317-266f-4b4a-9c73-b2e772eee326": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.31334673693295356,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44669347863427866
        },
        "8a1cdcdc-1632-4cfa-a8b2-6a081881c246": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37068665471316775,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.561373314194707
        },
        "a1e5f445-ddbe-4f2f-80e4-52501db8f66b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.31893329213491517,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4538665809319702
        },
        "acd02ebe-4dda-45ee-8389-3ba4bfc3ef22": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3330225087846403,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5160450175692806
        },
        "aefe31b0-83b8-4c5a-a7e8-581c35ca9890": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3177081506931799,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4514162980484997
        },
        "c1860d19-7ab7-472c-aedb-ec7f09e1733e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37473237508724283,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5694647549428572
        },
        "d32cdc67-f181-4faa-b5c2-97e9b5e0b715": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.28938842194483,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.41477684532017145
        },
        "e204204b-fd1f-4532-8709-7fb677a03c28": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.321984504749943,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.47996901093039746
        },
        "fe0a7485-d25d-4353-992b-9f10c95fc83f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3187414164965596,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4534828296552591
        }
      },
      "100": {
        "021a2799-b28e-4266-bec8-a9d8861880c7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3163638885136908,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44872777368952144
        },
        "7ccb4317-266f-4b4a-9c73-b2e772eee326": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.31334673693295356,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44669347863427866
        },
        "8a1cdcdc-1632-4cfa-a8b2-6a081881c246": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37068665471316775,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.561373314194707
        },
        "a1e5f445-ddbe-4f2f-80e4-52501db8f66b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.31893329213491517,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4538665809319702
        },
        "acd02ebe-4dda-45ee-8389-3ba4bfc3ef22": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3330225087846403,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5160450175692806
        },
        "aefe31b0-83b8-4c5a-a7e8-581c35ca9890": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3177081506931799,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4514162980484997
        },
        "c1860d19-7ab7-472c-aedb-ec7f09e1733e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37473237508724283,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5694647549428572
        },
        "d32cdc67-f181-4faa-b5c2-97e9b5e0b715": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.28938842194483,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.41477684532017145
        },
        "e204204b-fd1f-4532-8709-7fb677a03c28": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.321984504749943,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.47996901093039746
        },
        "fe0a7485-d25d-4353-992b-9f10c95fc83f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3187414164965596,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4534828296552591
        }
      },
      "25": {
        "021a2799-b28e-4266-bec8-a9d8861880c7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3163638885136908,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44872777368952144
        },
        "7ccb4317-266f-4b4a-9c73-b2e772eee326": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.31334673693295356,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44669347863427866
        },
        "8a1cdcdc-1632-4cfa-a8b2-6a081881c246": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37068665471316775,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.561373314194707
        },
        "a1e5f445-ddbe-4f2f-80e4-52501db8f66b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.31893329213491517,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4538665809319702
        },
        "acd02ebe-4dda-45ee-8389-3ba4bfc3ef22": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3330225087846403,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5160450175692806
        },
        "aefe31b0-83b8-4c5a-a7e8-581c35ca9890": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3177081506931799,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4514162980484997
        },
        "c1860d19-7ab7-472c-aedb-ec7f09e1733e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37473237508724283,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5694647549428572
        },
        "d32cdc67-f181-4faa-b5c2-97e9b5e0b715": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.28938842194483,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.41477684532017145
        },
        "e204204b-fd1f-4532-8709-7fb677a03c28": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.321984504749943,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.47996901093039746
        },
        "fe0a7485-d25d-4353-992b-9f10c95fc83f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3187414164965596,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4534828296552591
        }
      },
      "50": {
        "021a2799-b28e-4266-bec8-a9d8861880c7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3163638885136908,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44872777368952144
        },
        "7ccb4317-266f-4b4a-9c73-b2e772eee326": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.31334673693295356,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44669347863427866
        },
        "8a1cdcdc-1632-4cfa-a8b2-6a081881c246": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37068665471316775,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.561373314194707
        },
        "a1e5f445-ddbe-4f2f-80e4-52501db8f66b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.31893329213491517,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4538665809319702
        },
        "acd02ebe-4dda-45ee-8389-3ba4bfc3ef22": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3330225087846403,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5160450175692806
        },
        "aefe31b0-83b8-4c5a-a7e8-581c35ca9890": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3177081506931799,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4514162980484997
        },
        "c1860d19-7ab7-472c-aedb-ec7f09e1733e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.37473237508724283,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5694647549428572
        },
        "d32cdc67-f181-4faa-b5c2-97e9b5e0b715": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.28938842194483,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.41477684532017145
        },
        "e204204b-fd1f-4532-8709-7fb677a03c28": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.321984504749943,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.47996901093039746
        },
        "fe0a7485-d25d-4353-992b-9f10c95fc83f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.3187414164965596,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4534828296552591
        }
      }
    },
    "fixed_union_order_changed": false,
    "fixed_union_order_observable": true,
    "missing_from_pool": {
      "10": [],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "gpt4_65aabe59",
    "union_ids": [
      "021a2799-b28e-4266-bec8-a9d8861880c7",
      "7ccb4317-266f-4b4a-9c73-b2e772eee326",
      "8a1cdcdc-1632-4cfa-a8b2-6a081881c246",
      "a1e5f445-ddbe-4f2f-80e4-52501db8f66b",
      "acd02ebe-4dda-45ee-8389-3ba4bfc3ef22",
      "aefe31b0-83b8-4c5a-a7e8-581c35ca9890",
      "c1860d19-7ab7-472c-aedb-ec7f09e1733e",
      "d32cdc67-f181-4faa-b5c2-97e9b5e0b715",
      "e204204b-fd1f-4532-8709-7fb677a03c28",
      "fe0a7485-d25d-4353-992b-9f10c95fc83f"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 13,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 13,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 13,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "04d3e5e0-1be5-43f2-b1d8-69f7997ad618": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7599999904632568,
          "final_score": 0.29755748729783726,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.427914976693758
        },
        "0b8c38e5-9251-455e-ab4e-8fad63ee4d04": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.30722500781082757,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.48575002034234294
        },
        "0ee37418-ed24-434f-a708-9a7d6cf2d33c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36444833440233126,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5308966740498712
        },
        "207abc9a-89d8-43e6-a7b5-a3e5faf8fdb6": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3329509223881109,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.476901849783012
        },
        "2b3d395d-1d82-483f-8457-1a8f048096c5": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3717545300865053,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5460040626513717
        },
        "63a9a0e6-f522-4767-b246-794b09606acd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2940853436575715,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4261706916066774
        },
        "884adff5-2d67-42f6-8c28-64f542e71624": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2887587055173382,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4290174110346763
        },
        "c7744e48-45d5-461d-8ee3-f5c46af02ae5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.30755026520368384,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4864005351280555
        },
        "edc35a87-3809-4e05-81f0-5c19c9e43e8f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3732619314170428,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5386238683415547
        },
        "f1ee4596-3a2b-41a4-8900-bba36c25f1bb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.29003063553646646,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45136127579362073
        }
      },
      "100": {
        "04d3e5e0-1be5-43f2-b1d8-69f7997ad618": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7599999904632568,
          "final_score": 0.29755748729783726,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.427914976693758
        },
        "0b8c38e5-9251-455e-ab4e-8fad63ee4d04": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.30722500781082757,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.48575002034234294
        },
        "0ee37418-ed24-434f-a708-9a7d6cf2d33c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36444833440233126,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5308966740498712
        },
        "207abc9a-89d8-43e6-a7b5-a3e5faf8fdb6": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3329509223881109,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.476901849783012
        },
        "2b3d395d-1d82-483f-8457-1a8f048096c5": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3717545300865053,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5460040626513717
        },
        "36d1de2c-7680-43d1-9abd-073a928b4239": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.29758046860141796,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.40156093825187766
        },
        "63a9a0e6-f522-4767-b246-794b09606acd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2940853436575715,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4261706916066774
        },
        "884adff5-2d67-42f6-8c28-64f542e71624": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2887587055173382,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4290174110346763
        },
        "ba7020e8-167a-4bfd-8f84-cab517661689": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.29820812549472553,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3984162562346598
        },
        "c7744e48-45d5-461d-8ee3-f5c46af02ae5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.30755026520368384,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4864005351280555
        },
        "e3402d57-fec2-4090-9565-024289b0a084": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3016735532135073,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.40534711167222326
        },
        "edc35a87-3809-4e05-81f0-5c19c9e43e8f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3732619314170428,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5386238683415547
        },
        "f1ee4596-3a2b-41a4-8900-bba36c25f1bb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.29003063553646646,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45136127579362073
        }
      },
      "25": {
        "04d3e5e0-1be5-43f2-b1d8-69f7997ad618": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7599999904632568,
          "final_score": 0.29755748729783726,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.427914976693758
        },
        "0b8c38e5-9251-455e-ab4e-8fad63ee4d04": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.30722500781082757,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.48575002034234294
        },
        "0ee37418-ed24-434f-a708-9a7d6cf2d33c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36444833440233126,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5308966740498712
        },
        "207abc9a-89d8-43e6-a7b5-a3e5faf8fdb6": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3329509223881109,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.476901849783012
        },
        "2b3d395d-1d82-483f-8457-1a8f048096c5": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3717545300865053,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5460040626513717
        },
        "36d1de2c-7680-43d1-9abd-073a928b4239": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.29758046860141796,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.40156093825187766
        },
        "63a9a0e6-f522-4767-b246-794b09606acd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2940853436575715,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4261706916066774
        },
        "884adff5-2d67-42f6-8c28-64f542e71624": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2887587055173382,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4290174110346763
        },
        "ba7020e8-167a-4bfd-8f84-cab517661689": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.29820812549472553,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3984162562346598
        },
        "c7744e48-45d5-461d-8ee3-f5c46af02ae5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.30755026520368384,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4864005351280555
        },
        "e3402d57-fec2-4090-9565-024289b0a084": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3016735532135073,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.40534711167222326
        },
        "edc35a87-3809-4e05-81f0-5c19c9e43e8f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3732619314170428,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5386238683415547
        },
        "f1ee4596-3a2b-41a4-8900-bba36c25f1bb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.29003063553646646,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45136127579362073
        }
      },
      "50": {
        "04d3e5e0-1be5-43f2-b1d8-69f7997ad618": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7599999904632568,
          "final_score": 0.29755748729783726,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.427914976693758
        },
        "0b8c38e5-9251-455e-ab4e-8fad63ee4d04": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.30722500781082757,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.48575002034234294
        },
        "0ee37418-ed24-434f-a708-9a7d6cf2d33c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.36444833440233126,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5308966740498712
        },
        "207abc9a-89d8-43e6-a7b5-a3e5faf8fdb6": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3329509223881109,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.476901849783012
        },
        "2b3d395d-1d82-483f-8457-1a8f048096c5": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3717545300865053,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5460040626513717
        },
        "36d1de2c-7680-43d1-9abd-073a928b4239": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.29758046860141796,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.40156093825187766
        },
        "63a9a0e6-f522-4767-b246-794b09606acd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2940853436575715,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4261706916066774
        },
        "884adff5-2d67-42f6-8c28-64f542e71624": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.2887587055173382,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4290174110346763
        },
        "ba7020e8-167a-4bfd-8f84-cab517661689": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.29820812549472553,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3984162562346598
        },
        "c7744e48-45d5-461d-8ee3-f5c46af02ae5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.30755026520368384,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4864005351280555
        },
        "e3402d57-fec2-4090-9565-024289b0a084": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3016735532135073,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.40534711167222326
        },
        "edc35a87-3809-4e05-81f0-5c19c9e43e8f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3732619314170428,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5386238683415547
        },
        "f1ee4596-3a2b-41a4-8900-bba36c25f1bb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.29003063553646646,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45136127579362073
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "36d1de2c-7680-43d1-9abd-073a928b4239",
        "ba7020e8-167a-4bfd-8f84-cab517661689",
        "e3402d57-fec2-4090-9565-024289b0a084"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "3ba21379",
    "union_ids": [
      "04d3e5e0-1be5-43f2-b1d8-69f7997ad618",
      "0b8c38e5-9251-455e-ab4e-8fad63ee4d04",
      "0ee37418-ed24-434f-a708-9a7d6cf2d33c",
      "207abc9a-89d8-43e6-a7b5-a3e5faf8fdb6",
      "2b3d395d-1d82-483f-8457-1a8f048096c5",
      "36d1de2c-7680-43d1-9abd-073a928b4239",
      "63a9a0e6-f522-4767-b246-794b09606acd",
      "884adff5-2d67-42f6-8c28-64f542e71624",
      "ba7020e8-167a-4bfd-8f84-cab517661689",
      "c7744e48-45d5-461d-8ee3-f5c46af02ae5",
      "e3402d57-fec2-4090-9565-024289b0a084",
      "edc35a87-3809-4e05-81f0-5c19c9e43e8f",
      "f1ee4596-3a2b-41a4-8900-bba36c25f1bb"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "16490b98-77b1-47ec-8b8a-634bb48ca106": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.4400463001307394,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7040926012151532
        },
        "3038fb00-a69e-4b99-8928-73d7ab25c124": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3451679216362578,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5319358441308225
        },
        "515d7909-9b3f-4b82-9c66-628da8c81bf4": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3683008209286227,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5665016463633565
        },
        "51c29afb-8c21-40cd-8c7c-a12165bc40b5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.42976300918804156,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.703526024098129
        },
        "83b53f6c-17fd-4b8c-a07a-cec487a3722a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3510802870649739,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5437605749882547
        },
        "be7a792c-a320-40b6-9efb-f1e83001317c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3664752519418922,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5745505047420912
        },
        "ec78d9d0-2492-4429-a8bd-22bab3cc5e06": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.35919716829673615,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.575394341838681
        },
        "f0fe4a42-45b6-4cf6-a659-90fc988760da": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.34533524538349614,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5286704950585267
        },
        "f1bb1bfa-8f4b-472e-8818-67e3dc135603": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.5068408134959135,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.8336816317601985
        },
        "f9bf21be-a6ec-4513-8542-2580d900204f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.39539346801608655,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6477869412773818
        }
      },
      "100": {
        "16490b98-77b1-47ec-8b8a-634bb48ca106": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.4400463001307394,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7040926012151532
        },
        "3038fb00-a69e-4b99-8928-73d7ab25c124": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3451679216362578,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5319358441308225
        },
        "515d7909-9b3f-4b82-9c66-628da8c81bf4": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3683008209286227,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5665016463633565
        },
        "51c29afb-8c21-40cd-8c7c-a12165bc40b5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.42976300918804156,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.703526024098129
        },
        "83b53f6c-17fd-4b8c-a07a-cec487a3722a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3510802870649739,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5437605749882547
        },
        "8eded7a1-84ed-452d-b965-f489d21e03ed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.34896831406331064,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5263366344208718
        },
        "be7a792c-a320-40b6-9efb-f1e83001317c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3664752519418922,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5745505047420912
        },
        "ec599e45-efcf-4e49-bc66-0fa3b22a7c18": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3466241778942135,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5190083567325645
        },
        "ec78d9d0-2492-4429-a8bd-22bab3cc5e06": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.35919716829673615,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.575394341838681
        },
        "f0fe4a42-45b6-4cf6-a659-90fc988760da": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.34533524538349614,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5286704950585267
        },
        "f1bb1bfa-8f4b-472e-8818-67e3dc135603": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.5068408134959135,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.8336816317601985
        },
        "f9bf21be-a6ec-4513-8542-2580d900204f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.39539346801608655,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6477869412773818
        }
      },
      "25": {
        "16490b98-77b1-47ec-8b8a-634bb48ca106": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.4400463001307394,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7040926012151532
        },
        "3038fb00-a69e-4b99-8928-73d7ab25c124": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3451679216362578,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5319358441308225
        },
        "515d7909-9b3f-4b82-9c66-628da8c81bf4": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3683008209286227,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5665016463633565
        },
        "51c29afb-8c21-40cd-8c7c-a12165bc40b5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.42976300918804156,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.703526024098129
        },
        "83b53f6c-17fd-4b8c-a07a-cec487a3722a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3510802870649739,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5437605749882547
        },
        "8eded7a1-84ed-452d-b965-f489d21e03ed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.34896831406331064,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5263366344208718
        },
        "be7a792c-a320-40b6-9efb-f1e83001317c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3664752519418922,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5745505047420912
        },
        "ec599e45-efcf-4e49-bc66-0fa3b22a7c18": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3466241778942135,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5190083567325645
        },
        "ec78d9d0-2492-4429-a8bd-22bab3cc5e06": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.35919716829673615,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.575394341838681
        },
        "f0fe4a42-45b6-4cf6-a659-90fc988760da": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.34533524538349614,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5286704950585267
        },
        "f1bb1bfa-8f4b-472e-8818-67e3dc135603": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.5068408134959135,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.8336816317601985
        },
        "f9bf21be-a6ec-4513-8542-2580d900204f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.39539346801608655,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6477869412773818
        }
      },
      "50": {
        "16490b98-77b1-47ec-8b8a-634bb48ca106": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.4400463001307394,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7040926012151532
        },
        "3038fb00-a69e-4b99-8928-73d7ab25c124": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3451679216362578,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5319358441308225
        },
        "515d7909-9b3f-4b82-9c66-628da8c81bf4": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3683008209286227,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5665016463633565
        },
        "51c29afb-8c21-40cd-8c7c-a12165bc40b5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.42976300918804156,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.703526024098129
        },
        "83b53f6c-17fd-4b8c-a07a-cec487a3722a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3510802870649739,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5437605749882547
        },
        "8eded7a1-84ed-452d-b965-f489d21e03ed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.34896831406331064,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5263366344208718
        },
        "be7a792c-a320-40b6-9efb-f1e83001317c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3664752519418922,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5745505047420912
        },
        "ec599e45-efcf-4e49-bc66-0fa3b22a7c18": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.3466241778942135,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5190083567325645
        },
        "ec78d9d0-2492-4429-a8bd-22bab3cc5e06": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.35919716829673615,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.575394341838681
        },
        "f0fe4a42-45b6-4cf6-a659-90fc988760da": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.34533524538349614,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5286704950585267
        },
        "f1bb1bfa-8f4b-472e-8818-67e3dc135603": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.5068408134959135,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.8336816317601985
        },
        "f9bf21be-a6ec-4513-8542-2580d900204f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.39539346801608655,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.6477869412773818
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "8eded7a1-84ed-452d-b965-f489d21e03ed",
        "ec599e45-efcf-4e49-bc66-0fa3b22a7c18"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "545bd2b5",
    "union_ids": [
      "16490b98-77b1-47ec-8b8a-634bb48ca106",
      "3038fb00-a69e-4b99-8928-73d7ab25c124",
      "515d7909-9b3f-4b82-9c66-628da8c81bf4",
      "51c29afb-8c21-40cd-8c7c-a12165bc40b5",
      "83b53f6c-17fd-4b8c-a07a-cec487a3722a",
      "8eded7a1-84ed-452d-b965-f489d21e03ed",
      "be7a792c-a320-40b6-9efb-f1e83001317c",
      "ec599e45-efcf-4e49-bc66-0fa3b22a7c18",
      "ec78d9d0-2492-4429-a8bd-22bab3cc5e06",
      "f0fe4a42-45b6-4cf6-a659-90fc988760da",
      "f1bb1bfa-8f4b-472e-8818-67e3dc135603",
      "f9bf21be-a6ec-4513-8542-2580d900204f"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "0b93d1ba-44af-4182-a2e3-c9f0100d2db1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.24635747336436076,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34871494458295427
        },
        "1ebf802f-1f16-433e-bc1b-b1048746c9a5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2452249830909713,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3144499671356169
        },
        "64b235a4-11a7-4281-96ce-22a553a11d61": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.27621569239947086,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39243138241475595
        },
        "6e10d1f8-6b2b-4fb3-85cc-d99d2d24913f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.249881706768513,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.31760341023254446
        },
        "801a3954-d2a9-464c-ad6f-2a5f987ca098": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2833262392725657,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39065247949880577
        },
        "80e149da-2464-42aa-a6f6-bd44a6461a5b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.3012112858864399,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42202257334644244
        },
        "9054771d-9544-4db7-a4b1-4ca099e70cd9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21146986948880853,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29423974369830486
        },
        "9fc71c3d-a29f-40ae-958a-f6c405f6ee4b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.22749603120669573,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.280752063357529
        },
        "a178b1e6-ebe2-4d1d-995c-db0314773cc8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.25290788431977534,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34181577007006214
        },
        "cbcf9b67-ea9c-4edc-8ce9-fa0df97b3796": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2504221617493823,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3368443249292761
        }
      },
      "100": {
        "0b93d1ba-44af-4182-a2e3-c9f0100d2db1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.24635747336436076,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34871494458295427
        },
        "1ebf802f-1f16-433e-bc1b-b1048746c9a5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2452249830909713,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3144499671356169
        },
        "64b235a4-11a7-4281-96ce-22a553a11d61": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.27621569239947086,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39243138241475595
        },
        "67186f5c-2c7c-443a-88df-666cdadb9be4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23330152638500606,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.27960304752480336
        },
        "6e10d1f8-6b2b-4fb3-85cc-d99d2d24913f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.249881706768513,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.31760341023254446
        },
        "801a3954-d2a9-464c-ad6f-2a5f987ca098": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2833262392725657,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39065247949880577
        },
        "80e149da-2464-42aa-a6f6-bd44a6461a5b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.3012112858864399,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42202257334644244
        },
        "9054771d-9544-4db7-a4b1-4ca099e70cd9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21146986948880853,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29423974369830486
        },
        "9fc71c3d-a29f-40ae-958a-f6c405f6ee4b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.22749603120669573,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.280752063357529
        },
        "a178b1e6-ebe2-4d1d-995c-db0314773cc8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.25290788431977534,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34181577007006214
        },
        "cbcf9b67-ea9c-4edc-8ce9-fa0df97b3796": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2504221617493823,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3368443249292761
        }
      },
      "25": {
        "0b93d1ba-44af-4182-a2e3-c9f0100d2db1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.24635747336436076,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34871494458295427
        },
        "1ebf802f-1f16-433e-bc1b-b1048746c9a5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2452249830909713,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3144499671356169
        },
        "64b235a4-11a7-4281-96ce-22a553a11d61": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.27621569239947086,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39243138241475595
        },
        "67186f5c-2c7c-443a-88df-666cdadb9be4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23330152638500606,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.27960304752480336
        },
        "6e10d1f8-6b2b-4fb3-85cc-d99d2d24913f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.249881706768513,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.31760341023254446
        },
        "801a3954-d2a9-464c-ad6f-2a5f987ca098": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2833262392725657,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39065247949880577
        },
        "80e149da-2464-42aa-a6f6-bd44a6461a5b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.3012112858864399,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42202257334644244
        },
        "9054771d-9544-4db7-a4b1-4ca099e70cd9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21146986948880853,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29423974369830486
        },
        "9fc71c3d-a29f-40ae-958a-f6c405f6ee4b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.22749603120669573,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.280752063357529
        },
        "a178b1e6-ebe2-4d1d-995c-db0314773cc8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.25290788431977534,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34181577007006214
        },
        "cbcf9b67-ea9c-4edc-8ce9-fa0df97b3796": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2504221617493823,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3368443249292761
        }
      },
      "50": {
        "0b93d1ba-44af-4182-a2e3-c9f0100d2db1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.24635747336436076,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34871494458295427
        },
        "1ebf802f-1f16-433e-bc1b-b1048746c9a5": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2452249830909713,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3144499671356169
        },
        "64b235a4-11a7-4281-96ce-22a553a11d61": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.800000011920929,
          "final_score": 0.27621569239947086,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39243138241475595
        },
        "67186f5c-2c7c-443a-88df-666cdadb9be4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23330152638500606,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.27960304752480336
        },
        "6e10d1f8-6b2b-4fb3-85cc-d99d2d24913f": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.249881706768513,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.31760341023254446
        },
        "801a3954-d2a9-464c-ad6f-2a5f987ca098": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2833262392725657,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.39065247949880577
        },
        "80e149da-2464-42aa-a6f6-bd44a6461a5b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.3012112858864399,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.42202257334644244
        },
        "9054771d-9544-4db7-a4b1-4ca099e70cd9": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.21146986948880853,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.29423974369830486
        },
        "9fc71c3d-a29f-40ae-958a-f6c405f6ee4b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.22749603120669573,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.280752063357529
        },
        "a178b1e6-ebe2-4d1d-995c-db0314773cc8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.25290788431977534,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34181577007006214
        },
        "cbcf9b67-ea9c-4edc-8ce9-fa0df97b3796": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2504221617493823,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3368443249292761
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "67186f5c-2c7c-443a-88df-666cdadb9be4"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "1de5cff2",
    "union_ids": [
      "0b93d1ba-44af-4182-a2e3-c9f0100d2db1",
      "1ebf802f-1f16-433e-bc1b-b1048746c9a5",
      "64b235a4-11a7-4281-96ce-22a553a11d61",
      "67186f5c-2c7c-443a-88df-666cdadb9be4",
      "6e10d1f8-6b2b-4fb3-85cc-d99d2d24913f",
      "801a3954-d2a9-464c-ad6f-2a5f987ca098",
      "80e149da-2464-42aa-a6f6-bd44a6461a5b",
      "9054771d-9544-4db7-a4b1-4ca099e70cd9",
      "9fc71c3d-a29f-40ae-958a-f6c405f6ee4b",
      "a178b1e6-ebe2-4d1d-995c-db0314773cc8",
      "cbcf9b67-ea9c-4edc-8ce9-fa0df97b3796"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "25fdb5fc-7c71-429a-9a97-5831055ac3f0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.22622718663649755,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.25885437432203684
        },
        "4ae7dfad-2956-4d7c-95ef-ca359017b5b2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21960619333900655,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.25921239144638464
        },
        "4bb80987-a276-468a-80de-91b7bf3325d6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21816116623829768,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.26032233343026967
        },
        "759f8180-46c8-41c0-895f-74f3e9f519a8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21159909735332427,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24719819566032286
        },
        "83a64854-6a87-4726-b0de-83f563a554f8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.20934341136364515,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24708682902154078
        },
        "a66c25c6-e8d8-41e8-aacb-1ee4fddc4e79": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20041897421769625,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24783794414385807
        },
        "c0b7eb8f-807d-45e7-906c-c65fdedf8485": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2589269585537858,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3330539181089296
        },
        "c3442f8b-8115-41b0-ba1b-fd8992030ff9": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.22320427836187862,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.26790855171696704
        },
        "e344f0ec-1beb-4483-bd2d-c02a5643d898": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.244952400744821,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.305104802491
        },
        "e8b17a5e-070e-46a6-85a9-2e97143cb5f6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.21339095013388854,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2627819016982885
        }
      },
      "100": {
        "25fdb5fc-7c71-429a-9a97-5831055ac3f0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.22622718663649755,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.25885437432203684
        },
        "4ae7dfad-2956-4d7c-95ef-ca359017b5b2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21960619333900655,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.25921239144638464
        },
        "4bb80987-a276-468a-80de-91b7bf3325d6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21816116623829768,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.26032233343026967
        },
        "759f8180-46c8-41c0-895f-74f3e9f519a8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21159909735332427,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24719819566032286
        },
        "83a64854-6a87-4726-b0de-83f563a554f8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.20934341136364515,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24708682902154078
        },
        "a66c25c6-e8d8-41e8-aacb-1ee4fddc4e79": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20041897421769625,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24783794414385807
        },
        "b9734ae7-c4c8-43ab-9c45-03bd56addfc0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20707691658084648,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24415382839332134
        },
        "c0b7eb8f-807d-45e7-906c-c65fdedf8485": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2589269585537858,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3330539181089296
        },
        "c3442f8b-8115-41b0-ba1b-fd8992030ff9": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.22320427836187862,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.26790855171696704
        },
        "e344f0ec-1beb-4483-bd2d-c02a5643d898": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.244952400744821,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.305104802491
        },
        "e8b17a5e-070e-46a6-85a9-2e97143cb5f6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.21339095013388854,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2627819016982885
        }
      },
      "25": {
        "25fdb5fc-7c71-429a-9a97-5831055ac3f0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.22622718663649755,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.25885437432203684
        },
        "4ae7dfad-2956-4d7c-95ef-ca359017b5b2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21960619333900655,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.25921239144638464
        },
        "4bb80987-a276-468a-80de-91b7bf3325d6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21816116623829768,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.26032233343026967
        },
        "759f8180-46c8-41c0-895f-74f3e9f519a8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21159909735332427,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24719819566032286
        },
        "83a64854-6a87-4726-b0de-83f563a554f8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.20934341136364515,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24708682902154078
        },
        "a66c25c6-e8d8-41e8-aacb-1ee4fddc4e79": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20041897421769625,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24783794414385807
        },
        "b9734ae7-c4c8-43ab-9c45-03bd56addfc0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20707691658084648,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24415382839332134
        },
        "c0b7eb8f-807d-45e7-906c-c65fdedf8485": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2589269585537858,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3330539181089296
        },
        "c3442f8b-8115-41b0-ba1b-fd8992030ff9": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.22320427836187862,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.26790855171696704
        },
        "e344f0ec-1beb-4483-bd2d-c02a5643d898": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.244952400744821,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.305104802491
        },
        "e8b17a5e-070e-46a6-85a9-2e97143cb5f6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.21339095013388854,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2627819016982885
        }
      },
      "50": {
        "25fdb5fc-7c71-429a-9a97-5831055ac3f0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.22622718663649755,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.25885437432203684
        },
        "4ae7dfad-2956-4d7c-95ef-ca359017b5b2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.21960619333900655,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.25921239144638464
        },
        "4bb80987-a276-468a-80de-91b7bf3325d6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21816116623829768,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.26032233343026967
        },
        "759f8180-46c8-41c0-895f-74f3e9f519a8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.21159909735332427,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24719819566032286
        },
        "83a64854-6a87-4726-b0de-83f563a554f8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.20934341136364515,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.24708682902154078
        },
        "a66c25c6-e8d8-41e8-aacb-1ee4fddc4e79": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20041897421769625,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24783794414385807
        },
        "b9734ae7-c4c8-43ab-9c45-03bd56addfc0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.20707691658084648,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.24415382839332134
        },
        "c0b7eb8f-807d-45e7-906c-c65fdedf8485": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2589269585537858,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3330539181089296
        },
        "c3442f8b-8115-41b0-ba1b-fd8992030ff9": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.22320427836187862,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.26790855171696704
        },
        "e344f0ec-1beb-4483-bd2d-c02a5643d898": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.244952400744821,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.305104802491
        },
        "e8b17a5e-070e-46a6-85a9-2e97143cb5f6": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.21339095013388854,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.2627819016982885
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "b9734ae7-c4c8-43ab-9c45-03bd56addfc0"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "09d032c9",
    "union_ids": [
      "25fdb5fc-7c71-429a-9a97-5831055ac3f0",
      "4ae7dfad-2956-4d7c-95ef-ca359017b5b2",
      "4bb80987-a276-468a-80de-91b7bf3325d6",
      "759f8180-46c8-41c0-895f-74f3e9f519a8",
      "83a64854-6a87-4726-b0de-83f563a554f8",
      "a66c25c6-e8d8-41e8-aacb-1ee4fddc4e79",
      "b9734ae7-c4c8-43ab-9c45-03bd56addfc0",
      "c0b7eb8f-807d-45e7-906c-c65fdedf8485",
      "c3442f8b-8115-41b0-ba1b-fd8992030ff9",
      "e344f0ec-1beb-4483-bd2d-c02a5643d898",
      "e8b17a5e-070e-46a6-85a9-2e97143cb5f6"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 12,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "1e6674e7-9c4a-400e-b14a-1463f31ec359": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3258873146140685,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45377463447334565
        },
        "3378d787-1280-4e31-9166-e30b4daa3ff1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.31394241477594714,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4378848319360801
        },
        "45d7aac5-be52-4503-a51e-bc9ad41c4048": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3212421323701946,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.46248426950876076
        },
        "6214c7f5-93a9-45c3-bd0d-75646d750b0e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3957530576719835,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6015061177281528
        },
        "6c0f1d29-ebb5-4e87-9a1c-66f7558284cd": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3816433567078485,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.563786715919092
        },
        "77ab1540-a54f-4b1a-9377-12e25fa02777": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.30717590706062714,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4603518167438586
        },
        "bc9cf2d1-8832-4e90-89b5-4d00e645368e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3115841232808465,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44316825133006454
        },
        "c4f9507a-63a2-421d-ac08-037ab068fb07": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.31082927321489234,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.445658547383459
        },
        "d6b15114-6861-48c9-bb7e-da3c7ff312fe": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3134891627796887,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4469783303277489
        },
        "f252e81a-2225-4182-b4f5-1331fd754adc": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.2960186468593007,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.44903729896381006
        }
      },
      "100": {
        "1e6674e7-9c4a-400e-b14a-1463f31ec359": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3258873146140685,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45377463447334565
        },
        "3378d787-1280-4e31-9166-e30b4daa3ff1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.31394241477594714,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4378848319360801
        },
        "45d7aac5-be52-4503-a51e-bc9ad41c4048": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3212421323701946,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.46248426950876076
        },
        "6214c7f5-93a9-45c3-bd0d-75646d750b0e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3957530576719835,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6015061177281528
        },
        "6c0f1d29-ebb5-4e87-9a1c-66f7558284cd": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3816433567078485,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.563786715919092
        },
        "6eaf9153-fecd-4899-b738-670eb94b9cd2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.30861846007757665,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4272369225393391
        },
        "77ab1540-a54f-4b1a-9377-12e25fa02777": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.30717590706062714,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4603518167438586
        },
        "9bcdc991-d8b2-427c-9bbe-30248a4b1d9d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3116701886656024,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43334037971539063
        },
        "bc9cf2d1-8832-4e90-89b5-4d00e645368e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3115841232808465,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44316825133006454
        },
        "c4f9507a-63a2-421d-ac08-037ab068fb07": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.31082927321489234,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.445658547383459
        },
        "d6b15114-6861-48c9-bb7e-da3c7ff312fe": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3134891627796887,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4469783303277489
        },
        "f252e81a-2225-4182-b4f5-1331fd754adc": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.2960186468593007,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.44903729896381006
        }
      },
      "25": {
        "1e6674e7-9c4a-400e-b14a-1463f31ec359": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3258873146140685,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45377463447334565
        },
        "3378d787-1280-4e31-9166-e30b4daa3ff1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.31394241477594714,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4378848319360801
        },
        "45d7aac5-be52-4503-a51e-bc9ad41c4048": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3212421323701946,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.46248426950876076
        },
        "6214c7f5-93a9-45c3-bd0d-75646d750b0e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3957530576719835,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6015061177281528
        },
        "6c0f1d29-ebb5-4e87-9a1c-66f7558284cd": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3816433567078485,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.563786715919092
        },
        "6eaf9153-fecd-4899-b738-670eb94b9cd2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.30861846007757665,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4272369225393391
        },
        "77ab1540-a54f-4b1a-9377-12e25fa02777": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.30717590706062714,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4603518167438586
        },
        "9bcdc991-d8b2-427c-9bbe-30248a4b1d9d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3116701886656024,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43334037971539063
        },
        "bc9cf2d1-8832-4e90-89b5-4d00e645368e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3115841232808465,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44316825133006454
        },
        "c4f9507a-63a2-421d-ac08-037ab068fb07": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.31082927321489234,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.445658547383459
        },
        "d6b15114-6861-48c9-bb7e-da3c7ff312fe": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3134891627796887,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4469783303277489
        },
        "f252e81a-2225-4182-b4f5-1331fd754adc": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.2960186468593007,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.44903729896381006
        }
      },
      "50": {
        "1e6674e7-9c4a-400e-b14a-1463f31ec359": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3258873146140685,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.45377463447334565
        },
        "3378d787-1280-4e31-9166-e30b4daa3ff1": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.31394241477594714,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4378848319360801
        },
        "45d7aac5-be52-4503-a51e-bc9ad41c4048": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3212421323701946,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.46248426950876076
        },
        "6214c7f5-93a9-45c3-bd0d-75646d750b0e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3957530576719835,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6015061177281528
        },
        "6c0f1d29-ebb5-4e87-9a1c-66f7558284cd": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3816433567078485,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.563786715919092
        },
        "6eaf9153-fecd-4899-b738-670eb94b9cd2": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.30861846007757665,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4272369225393391
        },
        "77ab1540-a54f-4b1a-9377-12e25fa02777": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.30717590706062714,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4603518167438586
        },
        "9bcdc991-d8b2-427c-9bbe-30248a4b1d9d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.3116701886656024,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.43334037971539063
        },
        "bc9cf2d1-8832-4e90-89b5-4d00e645368e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3115841232808465,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.44316825133006454
        },
        "c4f9507a-63a2-421d-ac08-037ab068fb07": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.31082927321489234,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.445658547383459
        },
        "d6b15114-6861-48c9-bb7e-da3c7ff312fe": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3134891627796887,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4469783303277489
        },
        "f252e81a-2225-4182-b4f5-1331fd754adc": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.2960186468593007,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.44903729896381006
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "6eaf9153-fecd-4899-b738-670eb94b9cd2",
        "9bcdc991-d8b2-427c-9bbe-30248a4b1d9d"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "cc06de0d",
    "union_ids": [
      "1e6674e7-9c4a-400e-b14a-1463f31ec359",
      "3378d787-1280-4e31-9166-e30b4daa3ff1",
      "45d7aac5-be52-4503-a51e-bc9ad41c4048",
      "6214c7f5-93a9-45c3-bd0d-75646d750b0e",
      "6c0f1d29-ebb5-4e87-9a1c-66f7558284cd",
      "6eaf9153-fecd-4899-b738-670eb94b9cd2",
      "77ab1540-a54f-4b1a-9377-12e25fa02777",
      "9bcdc991-d8b2-427c-9bbe-30248a4b1d9d",
      "bc9cf2d1-8832-4e90-89b5-4d00e645368e",
      "c4f9507a-63a2-421d-ac08-037ab068fb07",
      "d6b15114-6861-48c9-bb7e-da3c7ff312fe",
      "f252e81a-2225-4182-b4f5-1331fd754adc"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "1ee576fb-6e17-42b6-a54b-522abd788ecd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.4221009328036295,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6942018656072589
        },
        "2f6af82f-f703-45db-ac6d-6d51d99b7a0a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.25728556744054054,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3245711372652669
        },
        "39547a58-1758-4ee5-a952-871a32ccbb98": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2519298693608993,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3238597434901701
        },
        "79be11e0-5484-46ec-9fe1-d1c8c7404992": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.23949880554328076,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3214976110865615
        },
        "a1b697b2-483a-4eeb-82a2-12d79e4c2f76": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3047764430840705,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4295528909365125
        },
        "a2146041-1b4b-4f97-bb77-b327738a9d91": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2711722856064241,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34874457226188993
        },
        "a8de16c6-51d6-43c8-a969-8a23205d40d0": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.26324425654550854,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3269885155944121
        },
        "a9c35688-a5ea-40f3-9635-c03e8f6059fa": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.38823045470752715,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5894609041698455
        },
        "b467bb79-3f7d-4872-a547-61fa76e6549e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.371951784625818,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5459035744968447
        },
        "ebe5b7ad-2b99-4b10-9494-26c031a27f5f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.4831548688430329,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7668097401894608
        }
      },
      "100": {
        "1ee576fb-6e17-42b6-a54b-522abd788ecd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.4221009328036295,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6942018656072589
        },
        "2f6af82f-f703-45db-ac6d-6d51d99b7a0a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.25728556744054054,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3245711372652669
        },
        "39547a58-1758-4ee5-a952-871a32ccbb98": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2519298693608993,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3238597434901701
        },
        "6cdcd78a-e9fe-43ab-90ae-d9fe78a6095c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24496959279185704,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3099391903520856
        },
        "79be11e0-5484-46ec-9fe1-d1c8c7404992": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.23949880554328076,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3214976110865615
        },
        "a1b697b2-483a-4eeb-82a2-12d79e4c2f76": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3047764430840705,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4295528909365125
        },
        "a2146041-1b4b-4f97-bb77-b327738a9d91": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2711722856064241,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34874457226188993
        },
        "a8de16c6-51d6-43c8-a969-8a23205d40d0": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.26324425654550854,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3269885155944121
        },
        "a9c35688-a5ea-40f3-9635-c03e8f6059fa": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.38823045470752715,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5894609041698455
        },
        "b467bb79-3f7d-4872-a547-61fa76e6549e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.371951784625818,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5459035744968447
        },
        "ebe5b7ad-2b99-4b10-9494-26c031a27f5f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.4831548688430329,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7668097401894608
        }
      },
      "25": {
        "1ee576fb-6e17-42b6-a54b-522abd788ecd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.4221009328036295,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6942018656072589
        },
        "2f6af82f-f703-45db-ac6d-6d51d99b7a0a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.25728556744054054,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3245711372652669
        },
        "39547a58-1758-4ee5-a952-871a32ccbb98": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2519298693608993,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3238597434901701
        },
        "6cdcd78a-e9fe-43ab-90ae-d9fe78a6095c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24496959279185704,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3099391903520856
        },
        "79be11e0-5484-46ec-9fe1-d1c8c7404992": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.23949880554328076,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3214976110865615
        },
        "a1b697b2-483a-4eeb-82a2-12d79e4c2f76": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3047764430840705,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4295528909365125
        },
        "a2146041-1b4b-4f97-bb77-b327738a9d91": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2711722856064241,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34874457226188993
        },
        "a8de16c6-51d6-43c8-a969-8a23205d40d0": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.26324425654550854,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3269885155944121
        },
        "a9c35688-a5ea-40f3-9635-c03e8f6059fa": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.38823045470752715,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5894609041698455
        },
        "b467bb79-3f7d-4872-a547-61fa76e6549e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.371951784625818,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5459035744968447
        },
        "ebe5b7ad-2b99-4b10-9494-26c031a27f5f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.4831548688430329,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7668097401894608
        }
      },
      "50": {
        "1ee576fb-6e17-42b6-a54b-522abd788ecd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.4221009328036295,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6942018656072589
        },
        "2f6af82f-f703-45db-ac6d-6d51d99b7a0a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.25728556744054054,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3245711372652669
        },
        "39547a58-1758-4ee5-a952-871a32ccbb98": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2519298693608993,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3238597434901701
        },
        "6cdcd78a-e9fe-43ab-90ae-d9fe78a6095c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.24496959279185704,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3099391903520856
        },
        "79be11e0-5484-46ec-9fe1-d1c8c7404992": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.23949880554328076,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3214976110865615
        },
        "a1b697b2-483a-4eeb-82a2-12d79e4c2f76": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3047764430840705,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4295528909365125
        },
        "a2146041-1b4b-4f97-bb77-b327738a9d91": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2711722856064241,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34874457226188993
        },
        "a8de16c6-51d6-43c8-a969-8a23205d40d0": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.26324425654550854,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3269885155944121
        },
        "a9c35688-a5ea-40f3-9635-c03e8f6059fa": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.38823045470752715,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5894609041698455
        },
        "b467bb79-3f7d-4872-a547-61fa76e6549e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.371951784625818,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.5459035744968447
        },
        "ebe5b7ad-2b99-4b10-9494-26c031a27f5f": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.4831548688430329,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.7668097401894608
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "6cdcd78a-e9fe-43ab-90ae-d9fe78a6095c"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "gpt4_93159ced",
    "union_ids": [
      "1ee576fb-6e17-42b6-a54b-522abd788ecd",
      "2f6af82f-f703-45db-ac6d-6d51d99b7a0a",
      "39547a58-1758-4ee5-a952-871a32ccbb98",
      "6cdcd78a-e9fe-43ab-90ae-d9fe78a6095c",
      "79be11e0-5484-46ec-9fe1-d1c8c7404992",
      "a1b697b2-483a-4eeb-82a2-12d79e4c2f76",
      "a2146041-1b4b-4f97-bb77-b327738a9d91",
      "a8de16c6-51d6-43c8-a969-8a23205d40d0",
      "a9c35688-a5ea-40f3-9635-c03e8f6059fa",
      "b467bb79-3f7d-4872-a547-61fa76e6549e",
      "ebe5b7ad-2b99-4b10-9494-26c031a27f5f"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "08f54c01-c1bd-4859-b904-1ee083e0b35c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.30037439339666694,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4477487825017994
        },
        "10585158-dffe-438f-886a-8b32569241f3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3454609641357265,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6009219306556388
        },
        "22e80e3d-29a2-45bb-bb15-226411f492a0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2890934394372559,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3999868835951996
        },
        "38b2330a-fc8b-4eb2-8062-e1a6ef577220": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.4147246455090545,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6764492867265746
        },
        "41e8fafa-00f0-4c9a-8877-81554464611a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.360532213876048,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5590644320436304
        },
        "880cee80-8c93-46a8-95ce-c3aa23ea30c8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.34562854542816424,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6012570932405142
        },
        "8b385530-c355-41a3-9659-5d448ac365e7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.28469913072652764,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5043982638372411
        },
        "99de9f65-7365-4257-9adf-c41e3d10dd86": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3495035765267064,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6090071554375985
        },
        "aa6eaf3a-6c62-4cc9-898d-7b9d2401187b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23059381915074412,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34218763496362814
        },
        "d01c4120-2220-430a-8f47-8f13a4e05b57": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23853445868389117,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35806891402992225
        }
      },
      "100": {
        "08f54c01-c1bd-4859-b904-1ee083e0b35c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.30037439339666694,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4477487825017994
        },
        "10585158-dffe-438f-886a-8b32569241f3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3454609641357265,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6009219306556388
        },
        "22e80e3d-29a2-45bb-bb15-226411f492a0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2890934394372559,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3999868835951996
        },
        "38b2330a-fc8b-4eb2-8062-e1a6ef577220": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.4147246455090545,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6764492867265746
        },
        "41e8fafa-00f0-4c9a-8877-81554464611a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.360532213876048,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5590644320436304
        },
        "880cee80-8c93-46a8-95ce-c3aa23ea30c8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.34562854542816424,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6012570932405142
        },
        "8b385530-c355-41a3-9659-5d448ac365e7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.28469913072652764,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5043982638372411
        },
        "99de9f65-7365-4257-9adf-c41e3d10dd86": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3495035765267064,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6090071554375985
        },
        "aa6eaf3a-6c62-4cc9-898d-7b9d2401187b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23059381915074412,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34218763496362814
        },
        "d01c4120-2220-430a-8f47-8f13a4e05b57": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23853445868389117,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35806891402992225
        },
        "ece3a3d4-b35c-4559-9bc7-188cc0cf2425": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.23376241966563627,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3352248428360256
        }
      },
      "25": {
        "08f54c01-c1bd-4859-b904-1ee083e0b35c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.30037439339666694,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4477487825017994
        },
        "10585158-dffe-438f-886a-8b32569241f3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3454609641357265,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6009219306556388
        },
        "22e80e3d-29a2-45bb-bb15-226411f492a0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2890934394372559,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3999868835951996
        },
        "38b2330a-fc8b-4eb2-8062-e1a6ef577220": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.4147246455090545,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6764492867265746
        },
        "41e8fafa-00f0-4c9a-8877-81554464611a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.360532213876048,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5590644320436304
        },
        "880cee80-8c93-46a8-95ce-c3aa23ea30c8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.34562854542816424,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6012570932405142
        },
        "8b385530-c355-41a3-9659-5d448ac365e7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.28469913072652764,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5043982638372411
        },
        "99de9f65-7365-4257-9adf-c41e3d10dd86": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3495035765267064,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6090071554375985
        },
        "aa6eaf3a-6c62-4cc9-898d-7b9d2401187b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23059381915074412,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34218763496362814
        },
        "d01c4120-2220-430a-8f47-8f13a4e05b57": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23853445868389117,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35806891402992225
        },
        "ece3a3d4-b35c-4559-9bc7-188cc0cf2425": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.23376241966563627,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3352248428360256
        }
      },
      "50": {
        "08f54c01-c1bd-4859-b904-1ee083e0b35c": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.30037439339666694,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4477487825017994
        },
        "10585158-dffe-438f-886a-8b32569241f3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3454609641357265,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6009219306556388
        },
        "22e80e3d-29a2-45bb-bb15-226411f492a0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.2890934394372559,
          "recency_boost": 0.9,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3999868835951996
        },
        "38b2330a-fc8b-4eb2-8062-e1a6ef577220": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.4147246455090545,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6764492867265746
        },
        "41e8fafa-00f0-4c9a-8877-81554464611a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.360532213876048,
          "recency_boost": 0.9,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5590644320436304
        },
        "880cee80-8c93-46a8-95ce-c3aa23ea30c8": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.34562854542816424,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6012570932405142
        },
        "8b385530-c355-41a3-9659-5d448ac365e7": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.28469913072652764,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5043982638372411
        },
        "99de9f65-7365-4257-9adf-c41e3d10dd86": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.3495035765267064,
          "recency_boost": 0.5,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6090071554375985
        },
        "aa6eaf3a-6c62-4cc9-898d-7b9d2401187b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23059381915074412,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34218763496362814
        },
        "d01c4120-2220-430a-8f47-8f13a4e05b57": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.23853445868389117,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.35806891402992225
        },
        "ece3a3d4-b35c-4559-9bc7-188cc0cf2425": {
          "access_boost": 1.05,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.23376241966563627,
          "recency_boost": 0.7,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3352248428360256
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "ece3a3d4-b35c-4559-9bc7-188cc0cf2425"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "59524333",
    "union_ids": [
      "08f54c01-c1bd-4859-b904-1ee083e0b35c",
      "10585158-dffe-438f-886a-8b32569241f3",
      "22e80e3d-29a2-45bb-bb15-226411f492a0",
      "38b2330a-fc8b-4eb2-8062-e1a6ef577220",
      "41e8fafa-00f0-4c9a-8877-81554464611a",
      "880cee80-8c93-46a8-95ce-c3aa23ea30c8",
      "8b385530-c355-41a3-9659-5d448ac365e7",
      "99de9f65-7365-4257-9adf-c41e3d10dd86",
      "aa6eaf3a-6c62-4cc9-898d-7b9d2401187b",
      "d01c4120-2220-430a-8f47-8f13a4e05b57",
      "ece3a3d4-b35c-4559-9bc7-188cc0cf2425"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 11,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "296a4a90-0767-43b4-9b8a-fe676be269ba": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.286531956299544,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4330639149832738
        },
        "4157efcf-50c4-4080-8575-98e308a3977a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3372567602324158,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5245135204648316
        },
        "49d6e1bb-cb30-47fd-9722-476f97194294": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2940562111203431,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4011124169954774
        },
        "5840026c-531a-4288-9acd-6cc1f4070bfd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.2711488734037666,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3862977525295791
        },
        "6145852d-2038-42fd-881f-2eb3137b1c5e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2444612062578842,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.308522414089331
        },
        "8606babc-ef3e-46da-9df0-09cff6add00b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.39593703500295,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6018740723900858
        },
        "b3da96b4-ba4b-43a5-9d42-e6799adf7fee": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2554933378415073,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34098667091464296
        },
        "e2a32213-17dd-47d7-add1-e52c940fa397": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30821884438300595,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.41843769401122066
        },
        "e33781f5-fe0c-42a6-b6bf-beca159f5eec": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2501419202425763,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.31328383523994385
        },
        "ecea6a1e-424f-461c-9983-485bc1e748eb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.2726841407976468,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4023682868405023
        }
      },
      "100": {
        "296a4a90-0767-43b4-9b8a-fe676be269ba": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.286531956299544,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4330639149832738
        },
        "4157efcf-50c4-4080-8575-98e308a3977a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3372567602324158,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5245135204648316
        },
        "49d6e1bb-cb30-47fd-9722-476f97194294": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2940562111203431,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4011124169954774
        },
        "5840026c-531a-4288-9acd-6cc1f4070bfd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.2711488734037666,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3862977525295791
        },
        "6145852d-2038-42fd-881f-2eb3137b1c5e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2444612062578842,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.308522414089331
        },
        "8606babc-ef3e-46da-9df0-09cff6add00b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.39593703500295,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6018740723900858
        },
        "93e6225c-fccd-472a-af57-2ccd3143e746": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2489806709788338,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30436134300670936
        },
        "b3da96b4-ba4b-43a5-9d42-e6799adf7fee": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2554933378415073,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34098667091464296
        },
        "e2a32213-17dd-47d7-add1-e52c940fa397": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30821884438300595,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.41843769401122066
        },
        "e33781f5-fe0c-42a6-b6bf-beca159f5eec": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2501419202425763,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.31328383523994385
        },
        "ecea6a1e-424f-461c-9983-485bc1e748eb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.2726841407976468,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4023682868405023
        }
      },
      "25": {
        "296a4a90-0767-43b4-9b8a-fe676be269ba": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.286531956299544,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4330639149832738
        },
        "4157efcf-50c4-4080-8575-98e308a3977a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3372567602324158,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5245135204648316
        },
        "49d6e1bb-cb30-47fd-9722-476f97194294": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2940562111203431,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4011124169954774
        },
        "5840026c-531a-4288-9acd-6cc1f4070bfd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.2711488734037666,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3862977525295791
        },
        "6145852d-2038-42fd-881f-2eb3137b1c5e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2444612062578842,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.308522414089331
        },
        "8606babc-ef3e-46da-9df0-09cff6add00b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.39593703500295,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6018740723900858
        },
        "93e6225c-fccd-472a-af57-2ccd3143e746": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2489806709788338,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30436134300670936
        },
        "b3da96b4-ba4b-43a5-9d42-e6799adf7fee": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2554933378415073,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34098667091464296
        },
        "e2a32213-17dd-47d7-add1-e52c940fa397": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30821884438300595,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.41843769401122066
        },
        "e33781f5-fe0c-42a6-b6bf-beca159f5eec": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2501419202425763,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.31328383523994385
        },
        "ecea6a1e-424f-461c-9983-485bc1e748eb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.2726841407976468,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4023682868405023
        }
      },
      "50": {
        "296a4a90-0767-43b4-9b8a-fe676be269ba": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.699999988079071,
          "final_score": 0.286531956299544,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.4330639149832738
        },
        "4157efcf-50c4-4080-8575-98e308a3977a": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.75,
          "final_score": 0.3372567602324158,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.5245135204648316
        },
        "49d6e1bb-cb30-47fd-9722-476f97194294": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2940562111203431,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4011124169954774
        },
        "5840026c-531a-4288-9acd-6cc1f4070bfd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.7799999713897705,
          "final_score": 0.2711488734037666,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.3862977525295791
        },
        "6145852d-2038-42fd-881f-2eb3137b1c5e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.2444612062578842,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.308522414089331
        },
        "8606babc-ef3e-46da-9df0-09cff6add00b": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.949999988079071,
          "final_score": 0.39593703500295,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.6018740723900858
        },
        "93e6225c-fccd-472a-af57-2ccd3143e746": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8799999952316284,
          "final_score": 0.2489806709788338,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.30436134300670936
        },
        "b3da96b4-ba4b-43a5-9d42-e6799adf7fee": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2554933378415073,
          "recency_boost": 1.0,
          "source_boost": 1.0,
          "trust_score": 0.5,
          "vector_sim": 0.34098667091464296
        },
        "e2a32213-17dd-47d7-add1-e52c940fa397": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8999999761581421,
          "final_score": 0.30821884438300595,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.41843769401122066
        },
        "e33781f5-fe0c-42a6-b6bf-beca159f5eec": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8500000238418579,
          "final_score": 0.2501419202425763,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.31328383523994385
        },
        "ecea6a1e-424f-461c-9983-485bc1e748eb": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.6499999761581421,
          "final_score": 0.2726841407976468,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.4023682868405023
        }
      }
    },
    "fixed_union_order_changed": null,
    "fixed_union_order_observable": false,
    "missing_from_pool": {
      "10": [
        "93e6225c-fccd-472a-af57-2ccd3143e746"
      ],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "25e5aa4f",
    "union_ids": [
      "296a4a90-0767-43b4-9b8a-fe676be269ba",
      "4157efcf-50c4-4080-8575-98e308a3977a",
      "49d6e1bb-cb30-47fd-9722-476f97194294",
      "5840026c-531a-4288-9acd-6cc1f4070bfd",
      "6145852d-2038-42fd-881f-2eb3137b1c5e",
      "8606babc-ef3e-46da-9df0-09cff6add00b",
      "93e6225c-fccd-472a-af57-2ccd3143e746",
      "b3da96b4-ba4b-43a5-9d42-e6799adf7fee",
      "e2a32213-17dd-47d7-add1-e52c940fa397",
      "e33781f5-fe0c-42a6-b6bf-beca159f5eec",
      "ecea6a1e-424f-461c-9983-485bc1e748eb"
    ]
  },
  {
    "comparisons": [
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "25"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "10",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "50"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "25",
        "order_changed": false,
        "right": "100"
      },
      {
        "changed_components": {},
        "common_count": 10,
        "left": "50",
        "order_changed": false,
        "right": "100"
      }
    ],
    "components": {
      "10": {
        "2066e828-3268-41a6-b743-ba47f41a8991": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2734124182920376,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3444248329124291
        },
        "26879e6b-212c-4940-9e02-5405bae1ccf3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.27405999348329074,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34571998329493536
        },
        "40d9c571-35c1-45bb-b06c-b09780fa59d4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.280194002948465,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.35798800222528393
        },
        "591f31ee-3daa-4baf-9ee1-4796f07e7a82": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2850401903485417,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3676803770254373
        },
        "66ce8a34-1913-42ee-a46f-d770f120a6dd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2822650173535976,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3621300310355491
        },
        "756ad3b2-28c5-4106-a0ea-f5eac2d73b64": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.33527021649515987,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.49014043456388234
        },
        "9aea77f5-0cf2-42f2-9703-ae664999c5ed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2602697199685644,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3181394362654827
        },
        "a4902171-e6d6-4550-9e46-68d7be95166e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.27314664651078335,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3438932893499206
        },
        "e4b0037f-6cf5-4f86-85cc-e0bc718928f0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.28414006158030675,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36588011948896737
        },
        "e60a9f69-bd3d-47eb-962b-1077bdab313d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.28508278683661964,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36776557000159316
        }
      },
      "100": {
        "2066e828-3268-41a6-b743-ba47f41a8991": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2734124182920376,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3444248329124291
        },
        "26879e6b-212c-4940-9e02-5405bae1ccf3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.27405999348329074,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34571998329493536
        },
        "40d9c571-35c1-45bb-b06c-b09780fa59d4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.280194002948465,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.35798800222528393
        },
        "591f31ee-3daa-4baf-9ee1-4796f07e7a82": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2850401903485417,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3676803770254373
        },
        "66ce8a34-1913-42ee-a46f-d770f120a6dd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2822650173535976,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3621300310355491
        },
        "756ad3b2-28c5-4106-a0ea-f5eac2d73b64": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.33527021649515987,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.49014043456388234
        },
        "9aea77f5-0cf2-42f2-9703-ae664999c5ed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2602697199685644,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3181394362654827
        },
        "a4902171-e6d6-4550-9e46-68d7be95166e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.27314664651078335,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3438932893499206
        },
        "e4b0037f-6cf5-4f86-85cc-e0bc718928f0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.28414006158030675,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36588011948896737
        },
        "e60a9f69-bd3d-47eb-962b-1077bdab313d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.28508278683661964,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36776557000159316
        }
      },
      "25": {
        "2066e828-3268-41a6-b743-ba47f41a8991": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2734124182920376,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3444248329124291
        },
        "26879e6b-212c-4940-9e02-5405bae1ccf3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.27405999348329074,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34571998329493536
        },
        "40d9c571-35c1-45bb-b06c-b09780fa59d4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.280194002948465,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.35798800222528393
        },
        "591f31ee-3daa-4baf-9ee1-4796f07e7a82": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2850401903485417,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3676803770254373
        },
        "66ce8a34-1913-42ee-a46f-d770f120a6dd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2822650173535976,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3621300310355491
        },
        "756ad3b2-28c5-4106-a0ea-f5eac2d73b64": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.33527021649515987,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.49014043456388234
        },
        "9aea77f5-0cf2-42f2-9703-ae664999c5ed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2602697199685644,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3181394362654827
        },
        "a4902171-e6d6-4550-9e46-68d7be95166e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.27314664651078335,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3438932893499206
        },
        "e4b0037f-6cf5-4f86-85cc-e0bc718928f0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.28414006158030675,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36588011948896737
        },
        "e60a9f69-bd3d-47eb-962b-1077bdab313d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.28508278683661964,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36776557000159316
        }
      },
      "50": {
        "2066e828-3268-41a6-b743-ba47f41a8991": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2734124182920376,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3444248329124291
        },
        "26879e6b-212c-4940-9e02-5405bae1ccf3": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.27405999348329074,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.34571998329493536
        },
        "40d9c571-35c1-45bb-b06c-b09780fa59d4": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.280194002948465,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.35798800222528393
        },
        "591f31ee-3daa-4baf-9ee1-4796f07e7a82": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2850401903485417,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3676803770254373
        },
        "66ce8a34-1913-42ee-a46f-d770f120a6dd": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2822650173535976,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3621300310355491
        },
        "756ad3b2-28c5-4106-a0ea-f5eac2d73b64": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.8199999928474426,
          "final_score": 0.33527021649515987,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.49014043456388234
        },
        "9aea77f5-0cf2-42f2-9703-ae664999c5ed": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.2602697199685644,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3181394362654827
        },
        "a4902171-e6d6-4550-9e46-68d7be95166e": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.27314664651078335,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.3438932893499206
        },
        "e4b0037f-6cf5-4f86-85cc-e0bc718928f0": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.28414006158030675,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36588011948896737
        },
        "e60a9f69-bd3d-47eb-962b-1077bdab313d": {
          "access_boost": 1.0,
          "bm25_normalized": 0.0,
          "bm25_score": 0.0,
          "confidence": 0.9200000166893005,
          "final_score": 0.28508278683661964,
          "recency_boost": 1.0,
          "source_boost": 1.1,
          "trust_score": 0.5,
          "vector_sim": 0.36776557000159316
        }
      }
    },
    "fixed_union_order_changed": false,
    "fixed_union_order_observable": true,
    "missing_from_pool": {
      "10": [],
      "100": [],
      "25": [],
      "50": []
    },
    "question_id": "7a8d0b71",
    "union_ids": [
      "2066e828-3268-41a6-b743-ba47f41a8991",
      "26879e6b-212c-4940-9e02-5405bae1ccf3",
      "40d9c571-35c1-45bb-b06c-b09780fa59d4",
      "591f31ee-3daa-4baf-9ee1-4796f07e7a82",
      "66ce8a34-1913-42ee-a46f-d770f120a6dd",
      "756ad3b2-28c5-4106-a0ea-f5eac2d73b64",
      "9aea77f5-0cf2-42f2-9703-ae664999c5ed",
      "a4902171-e6d6-4550-9e46-68d7be95166e",
      "e4b0037f-6cf5-4f86-85cc-e0bc718928f0",
      "e60a9f69-bd3d-47eb-962b-1077bdab313d"
    ]
  }
]
```

## Gather20 versus rank50 mismatches

0770505d and 07c6d26d: unexplained unless primary source/data adjudication establishes a cause. Dedicated harness/exact passes below retain production scores and actual vector limits (gather20 versus rank50), exposing pool membership/scoring differences without claiming causation from correlation.

```json
[
  {
    "components": {
      "gather20": {
        "access_boost": 1.0,
        "bm25_normalized": 0.0,
        "bm25_score": 0.0,
        "confidence": 0.9200000166893005,
        "final_score": 0.3229737758034085,
        "recency_boost": 1.0,
        "source_boost": 1.1,
        "trust_score": 0.5,
        "vector_sim": 0.4435475479351709
      },
      "rank50": {
        "access_boost": 1.0,
        "bm25_normalized": 0.0,
        "bm25_score": 0.0,
        "confidence": 0.9200000166893005,
        "final_score": 0.3229737758034085,
        "recency_boost": 1.0,
        "source_boost": 1.1,
        "trust_score": 0.5,
        "vector_sim": 0.4435475479351709
      }
    },
    "explanation": "unexplained",
    "gather20_rank": 12,
    "gather20_rendered": true,
    "gather_vector_limit": 20,
    "memory_id": "0770505d-0b96-4908-af07-2e692c034b9b",
    "mode": "harness",
    "question_id": "0bb5a684",
    "rank50": 12,
    "rank_vector_limit": 50
  },
  {
    "components": {
      "gather20": {
        "access_boost": 1.0,
        "bm25_normalized": 0.0,
        "bm25_score": 0.0,
        "confidence": 0.9200000166893005,
        "final_score": 0.3229737758034085,
        "recency_boost": 1.0,
        "source_boost": 1.1,
        "trust_score": 0.5,
        "vector_sim": 0.4435475479351709
      },
      "rank50": {
        "access_boost": 1.0,
        "bm25_normalized": 0.0,
        "bm25_score": 0.0,
        "confidence": 0.9200000166893005,
        "final_score": 0.3229737758034085,
        "recency_boost": 1.0,
        "source_boost": 1.1,
        "trust_score": 0.5,
        "vector_sim": 0.4435475479351709
      }
    },
    "explanation": "unexplained",
    "gather20_rank": 12,
    "gather20_rendered": true,
    "gather_vector_limit": 20,
    "memory_id": "0770505d-0b96-4908-af07-2e692c034b9b",
    "mode": "exact",
    "question_id": "0bb5a684",
    "rank50": 12,
    "rank_vector_limit": 50
  },
  {
    "components": {
      "gather20": {
        "access_boost": 1.0,
        "bm25_normalized": 0.0,
        "bm25_score": 0.0,
        "confidence": 0.699999988079071,
        "final_score": 0.21965633393512285,
        "recency_boost": 0.9,
        "source_boost": 1.0,
        "trust_score": 0.5,
        "vector_sim": 0.31331267001601293
      },
      "rank50": {
        "access_boost": 1.0,
        "bm25_normalized": 0.0,
        "bm25_score": 0.0,
        "confidence": 0.699999988079071,
        "final_score": 0.21965633393512285,
        "recency_boost": 0.9,
        "source_boost": 1.0,
        "trust_score": 0.5,
        "vector_sim": 0.31331267001601293
      }
    },
    "explanation": "unexplained",
    "gather20_rank": 19,
    "gather20_rendered": true,
    "gather_vector_limit": 20,
    "memory_id": "07c6d26d-8bd3-438b-aecc-6d8451f0ec5d",
    "mode": "harness",
    "question_id": "0a34ad58",
    "rank50": 21,
    "rank_vector_limit": 50
  },
  {
    "components": {
      "gather20": {
        "access_boost": 1.0,
        "bm25_normalized": 0.0,
        "bm25_score": 0.0,
        "confidence": 0.699999988079071,
        "final_score": 0.21965633393512285,
        "recency_boost": 0.9,
        "source_boost": 1.0,
        "trust_score": 0.5,
        "vector_sim": 0.31331267001601293
      },
      "rank50": {
        "access_boost": 1.0,
        "bm25_normalized": 0.0,
        "bm25_score": 0.0,
        "confidence": 0.699999988079071,
        "final_score": 0.21965633393512285,
        "recency_boost": 0.9,
        "source_boost": 1.0,
        "trust_score": 0.5,
        "vector_sim": 0.31331267001601293
      }
    },
    "explanation": "unexplained",
    "gather20_rank": 19,
    "gather20_rendered": true,
    "gather_vector_limit": 20,
    "memory_id": "07c6d26d-8bd3-438b-aecc-6d8451f0ec5d",
    "mode": "exact",
    "question_id": "0a34ad58",
    "rank50": 21,
    "rank_vector_limit": 50
  }
]
```

## Decision — decide_exactness()

Sort key: (n_changed, priority_vector padded with 3, fixed probes/pool/width step_vector). Priorities probes=0/pool=1/width=2. Steps harness=0/probes10=1/exact=2; pool10=0/25=1/50=2/100=3; width5=0/10=1. Duplicate probes10 has step 0 when harness probes=10 and is dropped.
NONE_OF_THE_ABOVE is retained as a safety net but is unreachable on a complete valid grid: exact/pool100/k10 necessarily triggers query formulation, candidate, coverage regression or budget limited.

```json
{
  "candidates": [],
  "flags": [
    "PARITY_GAP",
    "QUERY_FORMULATION_CONFIRMED"
  ],
  "minimal": null,
  "missing_target_ranks": [
    {
      "bm25_rank": null,
      "exact_cosine_rank": 18,
      "memory_id": "0807f4f5-3e15-43bf-b776-857b726ad7e2",
      "question_id": "28dc39ac"
    },
    {
      "bm25_rank": null,
      "exact_cosine_rank": 14,
      "memory_id": "11672c8b-34a5-4b99-9fc5-b92c5fc17bc5",
      "question_id": "28dc39ac"
    },
    {
      "bm25_rank": null,
      "exact_cosine_rank": 15,
      "memory_id": "55eab6cb-17c0-4201-be69-9b3b70700029",
      "question_id": "6613b389"
    },
    {
      "bm25_rank": null,
      "exact_cosine_rank": 17,
      "memory_id": "94c365d4-d4cb-47c1-8661-9b9ff73a09f6",
      "question_id": "6613b389"
    },
    {
      "bm25_rank": null,
      "exact_cosine_rank": 16,
      "memory_id": "0770505d-0b96-4908-af07-2e692c034b9b",
      "question_id": "0bb5a684"
    },
    {
      "bm25_rank": null,
      "exact_cosine_rank": 24,
      "memory_id": "3c418fde-f8c6-4383-8513-9df463894cd9",
      "question_id": "0bb5a684"
    },
    {
      "bm25_rank": null,
      "exact_cosine_rank": 19,
      "memory_id": "07c6d26d-8bd3-438b-aecc-6d8451f0ec5d",
      "question_id": "0a34ad58"
    },
    {
      "bm25_rank": null,
      "exact_cosine_rank": 14,
      "memory_id": "3bb5fed7-48de-4aa3-9cbc-5ec9d1fb9d42",
      "question_id": "0a34ad58"
    },
    {
      "bm25_rank": null,
      "exact_cosine_rank": 33,
      "memory_id": "ddabe83c-c72c-4e28-b6b2-287102a9c442",
      "question_id": "0a34ad58"
    }
  ]
}
```

## Primary notes

Written by the primary (Opus) after the live run. The generated sections above are unedited.

- **Pre-registration and rulings.** The decision rules were recorded before any
  code or results existed, in
  `retrieval-exactness-preregistration-20261103.md` in the task evidence area.
  - Owner rulings: BUDGET_LIMITED, NONE_OF_THE_ABOVE, and the lexicographic
    minimal-change rule.
  - Primary clarification: tie-breaking across all changed variables.
  - The flags above are exactly what `decide_exactness()` returned.
- **PARITY_GAP: real, but inert for this corpus.**
  - All three benchmark connection types (the fact_harness pool, the campaign
    evaluation recipe and the sweep connection) run at `ivfflat.probes = 1`. That
    is pgvector's default: nothing set it before load, and there is no server,
    database or role setting. The production pool runs at 10.
  - In every T0 plan (3 questions, vector and BM25, probes 1 and 10), the planner
    chose an exact `idx_memories_user_id` bitmap scan plus sort, **never
    IVFFlat**. Per-user memory counts are 393–617.
  - T1 recall against the brute-force reference is 1.0 at every probes setting,
    for all 50 questions.
  - In T2, the harness, probes10 and exact rows are identical at every pool and
    width.
  - So prior campaigns' retrieval was effectively exact, and the probes gap did
    not affect their results. It remains a latent risk if larger per-user tables
    make the planner choose IVFFlat, and it should be fixed (or explicitly
    pinned) in a separate change. Plan shapes were checked for 3 questions; the
    50-question recall equality is the broader evidence.
- **QUERY_FORMULATION_CONFIRMED.**
  - Pool size and exactness change nothing: every config exposes 0/5 cases at
    k=5 and 1/5 at k=10. Exact search with pool 100 and k=10 exposes 1/5.
  - The 9 missing target memories have exact cosine ranks 14–33, and **none has
    any BM25 match**. All-terms BM25 contributes nothing here: `bm25_normalized`
    is 0 throughout T3.
  - Common-set composite order did not change with pool size (T3), so mechanism
    2 (pool-dependent BM25 normalisation) is inert on this corpus.
- **The sweep's rank/render mismatches.** The generated section reports
  "unexplained", because the generator does not infer causes. The primary's
  reading of this evidence, offered as an interpretation, not a tested causal
  claim:
  - Candidates are cut by **raw cosine** at `vector_limit = max(10, k)` *before*
    the composite rescoring (vector similarity, confidence, recency and source
    boosts).
  - `0770505d` has exact cosine rank 16. At k=15 (limit 15) it is not a
    candidate. At k=20 it is, and rescores to 12th (gather20 rank 12 = rank50
    rank 12).
  - `07c6d26d` has cosine rank 19. At k=20 it rescores to 19th and is rendered.
    At limit 50, two candidates from beyond cosine rank 20 outscore it on the
    composite, giving rank 21.
  - Both are consistent with the T1 cosine ranks and the T2 grid. Widening the
    pool at k ≤ 10 still exposes nothing new.
- **Proposed next step (not built, not run).** This is a query-decomposition
  design with an offline exposure pre-gate. It needs separate approval:
  1. For each multi-part question, produce saved sub-queries. The method
     (hand-written fixtures versus an LLM decomposer) is an owner decision.
  2. Embed the sub-queries once, under explicit approval. Embedding goes through
     the metered path, which writes account-ledger rows on the benchmark DB, so
     that write needs the owner's approval.
  3. Rerun this harness's exposure measurement on the union of per-sub-query
     retrievals, rendered at k ≤ 10.
  4. The pre-gate before any paid answer run: ≥ 4/5 omission cases strictly
     exposed, no COVERAGE_REGRESSION, no target budget drops, and abstention
     distractors reported.
- **Size.** The sanitized evidence (~8 MB) and this report are large, mostly
  because of the per-question T3 score components (IDs and numbers only). The
  raw output and its sha are retained outside Git.
- **Unchanged.** There is no diff under `orchestrator/memory/**`,
  `orchestrator/db.py`, the campaign modules or `fact_harness.py`.
  `INITIAL_VECTOR_CANDIDATES` = 10 and `MAX_MEMORY_ITEMS` = 5. Provider calls:
  0. Fingerprint `5cb6ab14…` before and after. No restore and no persistent
  setting.

