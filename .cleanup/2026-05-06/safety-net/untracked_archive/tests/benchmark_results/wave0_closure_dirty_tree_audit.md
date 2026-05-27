# Wave 0 Closure Dirty Tree Audit

**Date:** 2026-05-01
**Task:** I1 — Audit and resolve dirty `orchestrator/memory/**` working tree
**Scope:** `orchestrator/memory/dedup.py`, `extraction.py`, `injection.py`, `retrieval.py`, `store.py`
**Status:** COMPLETE

---

## Executive Summary

Initial dirty files under `orchestrator/memory/`:

- `orchestrator/memory/dedup.py`
- `orchestrator/memory/extraction.py`
- `orchestrator/memory/injection.py`
- `orchestrator/memory/retrieval.py`
- `orchestrator/memory/store.py`

Disposition summary:

- **Reverted:** `dedup.py`, `extraction.py`
- **Stashed:** `injection.py`, `retrieval.py`, `store.py`
- **Preservation stash:** `stash@{0}` — `wave0-task1-memory-preserve-20260501T094245Z`

Rationale: the live memory-tree diff was not uniform. Two files were pure Wave 0 benchmark-path modifications and were restored directly to `HEAD`. Three files contained pre-existing unrelated product work, or mixed unrelated + benchmark hunks, so they were removed from the working tree with a path-limited stash to preserve out-of-scope work without leaving any live `orchestrator/memory/**` diff behind.

---

## File-by-File Audit

### 1. `orchestrator/memory/dedup.py`

**Classification:** (a) Wave 0 modification

**Exact diff excerpts:**

```diff
+# Benchmark-mode deterministic sampling controls
+DEDUP_BENCHMARK_SEED = 42
+DEDUP_BENCHMARK_MODE = (
+    os.environ.get("BENCHMARK_MODE", "").lower() in ("1", "true", "yes")
+)
+
+class DedupBenchmarkSamplingError(Exception):
+    """Raised when dedup benchmark-mode contract is violated (model/fingerprint drift)."""
```

```diff
+# Benchmark-mode: model ID for deterministic routing.
+BENCHMARK_CONTRADICTION_MODEL = "openrouter/deepseek/deepseek-chat-v3-5"
+BENCHMARK_CONTRADICTION_ENDPOINT_SLUG = "openrouter/deepseek/deepseek-chat-v3-5"
```

```diff
-        response = await litellm.acompletion(
-            model=get_settings().background_reasoning_model,
+        call_params: dict[str, Any] = {
+            "model": get_settings().background_reasoning_model,
...
+        if benchmark_mode:
+            call_params["model"] = BENCHMARK_CONTRADICTION_MODEL
+            call_params["seed"] = DEDUP_BENCHMARK_SEED
+            call_params["max_retries"] = 0
```

**Disposition:** reverted with `GIT_MASTER=1 git restore --source=HEAD --worktree --staged -- orchestrator/memory/dedup.py`

**Why:** this file is benchmark-mode plumbing and benchmark fail-fast instrumentation only. It matches the repo's documented Wave 0 benchmark instrumentation pattern (`BENCHMARK_MODE`, dated pinning, fingerprint drift exceptions, fail-fast routing).

---

### 2. `orchestrator/memory/extraction.py`

**Classification:** (a) Wave 0 modification

**Exact diff excerpts:**

```diff
+# Benchmark-mode deterministic sampling controls
+BENCHMARK_SEED = 42
+BENCHMARK_MODE = os.environ.get("BENCHMARK_MODE", "").lower() in ("1", "true", "yes")
+
+class BenchmarkSamplingError(Exception):
+    """Raised when benchmark-mode contract is violated (model/fingerprint drift)."""
```

```diff
+# Standard alias model (non-benchmark mode)
+EXTRACTION_MODEL = "openrouter/openai/gpt-4o-mini"
+# Benchmark-mode: dated snapshot model ID for deterministic routing.
+BENCHMARK_EXTRACTION_MODEL = "openrouter/openai/gpt-4o-mini-2024-07-18"
+BENCHMARK_EXTRACTION_ENDPOINT_SLUG = "openrouter/openai/gpt-4o-mini-2024-07-18"
```

```diff
+        if benchmark_mode:
+            call_params["seed"] = BENCHMARK_SEED
+            call_params["max_retries"] = 0  # Disable LiteLLM retries — fail-fast must be immediate
...
+        if benchmark_mode:
+            seen_model = response_data.get("model")
+            seen_fingerprint = response_data.get("system_fingerprint")
+            if bm_call_key in _BM_METADATA:
+                prev = _BM_METADATA[bm_call_key]
```

**Disposition:** reverted with `GIT_MASTER=1 git restore --source=HEAD --worktree --staged -- orchestrator/memory/extraction.py`

**Why:** this file is the extraction-side counterpart to the Wave 0 benchmark determinism controls: env-var gating, dated snapshot pinning, provider-order fail-fast, metadata tracking, and benchmark-only exceptions.

---

### 3. `orchestrator/memory/injection.py`

**Classification:** (b) pre-existing unrelated work

**Exact diff excerpts:**

```diff
-from orchestrator.prompts import DAEMON_SYSTEM_PROMPT
+from orchestrator.prompts import (
+    DAEMON_SYSTEM_PROMPT,
+    MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL,
+)
```

```diff
     memory_block = memory_context.strip()
     if memory_block:
         parts.append(memory_block)
+        parts.append(MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL.strip())
```

**Disposition:** stashed in `stash@{0}` / `wave0-task1-memory-preserve-20260501T094245Z`

**Why:** this is unrelated prompt/guardrail product work, not Wave 0 dirty-tree cleanup. It had to leave the working tree, but it also had to be preserved.

---

### 4. `orchestrator/memory/retrieval.py`

**Classification:** mixed — (b) pre-existing unrelated work + (a) Wave 0 modification

**Exact diff excerpts:**

**(b) Pre-existing unrelated work**

```diff
+TEMPORAL_QUERY_FILTER_ENABLED = True
+
+@dataclass(frozen=True)
+class _TemporalQueryWindow:
+    start: dt.datetime
+    end: dt.datetime
+    detector: str
```

```diff
+def _detect_temporal_query_window(
+    query_text: str | None,
+    *,
+    query_reference_time: dt.datetime | str | None = None,
+) -> _TemporalQueryWindow | None:
```

**(a) Wave 0 modification**

```diff
-    ranked = sorted(
-        filtered,
-        key=lambda item: _as_float(item.get("final_score"), 0.0),
-        reverse=True,
-    )[:target_limit]
+    ranked = sorted(
+        filtered,
+        key=lambda item: (-_as_float(item.get("final_score"), 0.0), str(item.get("id", ""))),
+        reverse=False,
+    )[:target_limit]
```

**Disposition:** stashed in `stash@{0}` / `wave0-task1-memory-preserve-20260501T094245Z`

**Why:** the file mixes unrelated temporal-query feature work with Wave 0 deterministic ranking stabilization. A path-limited stash was the safest reversible option that preserved the unrelated work while removing all live `orchestrator/memory/` diffs.

---

### 5. `orchestrator/memory/store.py`

**Classification:** mixed — (b) pre-existing unrelated work + (a) Wave 0 modification + (c) local-only debug instrumentation

**Exact diff excerpts:**

**(b) Pre-existing unrelated work**

```diff
+    async def increment_advisor_call_count(
+        self,
+        conversation_id: uuid.UUID,
+    ) -> int:
```

```diff
+        advisor_traces: dict[str, Any] | None = None,
...
+        encrypted_advisor_traces = (
+            self._enc.encrypt(json.dumps(advisor_traces))
+            if advisor_traces is not None
+            else None
+        )
```

**(a) Wave 0 modification**

```diff
-                ORDER BY embedding <=> $2::vector
+                ORDER BY embedding <=> $2::vector, id ASC
```

```diff
-                ORDER BY bm25_score DESC
+                ORDER BY bm25_score DESC, id ASC
```

**(c) Local-only debug instrumentation**

```diff
+    # Benchmark test user IDs - excluded from background jobs to prevent runaway API usage
+    _BENCHMARK_USER_IDS: frozenset[uuid.UUID] = frozenset([
+        uuid.UUID("12345678-1234-5678-1234-567812345678"),  # longmemeval@daemon.test
+    ])
```

```diff
               AND memory_slot IS NOT NULL
+              AND user_id != $1
```

**Disposition:** stashed in `stash@{0}` / `wave0-task1-memory-preserve-20260501T094245Z`

**Why:** the file contains unrelated advisor persistence work, Wave 0 deterministic ordering fixes, and benchmark-only user exclusion. Because those hunks are interleaved in one file, path-limited stash was safer than a blind restore.

---

## Verification

Commands run after cleanup:

```bash
GIT_MASTER=1 git diff -- orchestrator/memory/
GIT_MASTER=1 git diff --stat -- orchestrator/memory/
GIT_MASTER=1 git status --short --untracked-files=all -- "orchestrator/memory/"
```

**Expected/observed result:** all three commands produced no live diff output for `orchestrator/memory/**`.

Evidence file: `.sisyphus/evidence/task-1-dirty-tree-clean.txt`

---

## Follow-up Note

The preservation stash contains the out-of-scope `injection.py` changes plus the mixed `retrieval.py` and `store.py` files. Any future reapplication should happen on an isolated branch or worktree so the unrelated product work can be split from the Wave 0 benchmark hunks before further memory work proceeds.
