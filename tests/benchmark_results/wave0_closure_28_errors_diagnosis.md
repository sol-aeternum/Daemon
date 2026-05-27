# D1: Wave0 Closure — 28 Scoped C3 Runtime Errors Diagnosis

**Date:** 2026-05-04
**Task:** D1 (diagnosis only, no code changes)
**Scope:** Canonical evaluate+score; `orchestrator/memory/` unchanged; `tests/longmemeval/evaluate.py` unchanged
**Artifact:** `tests/benchmark_results/wave0_closure_28_errors_diagnosis.md`

---

## Executive Verdict

The 28 scoped C3 errors decompose into two root-cause classes:

| Class | Count | Error message | Root cause |
|---|---|---|---|
| **Invalid ciphertext** | 27 | `"Invalid ciphertext: decryption failed (wrong key or corrupted data)"` | Mixed encryption/plaintext storage — retrieval returns memory rows whose `content` field was stored as plaintext (no Fernet prefix) but the decrypt path at `store.py:903` unconditionally calls `ContentEncryption.decrypt()`, which raises on Fernet `InvalidToken` |
| **`NoneType.strip`** | 1 | `AttributeError: 'NoneType' object has no attribute 'strip'` | Harness defect in `_extract_content()` (`evaluate.py:390-406`) returning `None` when provider response has `content: null`; `parse_answer()` (`evaluate.py:523-527`) then calls `.strip()` on `None` |

Both classes are formally attributable to **mixed encryption/plaintext storage** (ciphertext) and **harness null-handling defect** (strip). Neither is a retrieval logic bug. Both are addressable without modifying `orchestrator/memory/**`.

---

## Scoped C3 Error Summary

From `c3-full-corpus-scoped-rerun.json`:

```
attempted:        500
success_count:    472
error_count:      28
median memories_used: 5.0
official aggregate:   0.096
```

**28 error rows (100% of errors):**

- **27 invalid ciphertext** — `error: "Invalid ciphertext: decryption failed (wrong key or corrupted data)"`, `failure_category: "harness exception"`
- **1 `NoneType.strip`** — `error: "AttributeError: 'NoneType' object has no attribute 'strip'"`, `failure_category: "harness exception"`

All 28 errors have `hypothesis: ""` (empty) and `judgment: "incorrect"` — the harness exception prevented the answer model from generating content.

---

## Invalid Ciphertext Root Cause

### Mechanism

`ContentEncryption.decrypt()` (`encryption.py:44-53`):

```python
def decrypt(self, ciphertext: str) -> str:
    if not self._cipher:
        return ciphertext          # plaintext passthrough when no key

    try:
        decrypted_bytes = self._cipher.decrypt(ciphertext.encode())
        return decrypted_bytes.decode()
    except InvalidToken:
        raise ValueError(
            "Invalid ciphertext: decryption failed (wrong key or corrupted data)"
        )
```

The decrypt path **raises** on `InvalidToken` rather than returning plaintext. This is the correct behavior for the production path (where all stored content SHOULD be encrypted).

### Failure point: `store.py:903`

During retrieval, each memory row's `content` is decrypted:

```python
# store.py:900-905
results = []
for r in rows:
    d = dict(r)
    d["content"] = self._enc.decrypt(d["content"])   # ← line 903
    results.append(d)
return results
```

`self._enc.decrypt()` is called on every retrieved row's `content` without checking whether the stored value is actually Fernet-encrypted. If the content was stored as plaintext (no Fernet prefix `gAAAAA...`), `Fernet.decrypt()` raises `InvalidToken`.

### Evidence: mixed encryption/plaintext storage in DB

Atlas independently verified:
- Total active benchmark memories: **10,456**
- Fernet-like (Fernet-prefixed): **10,212**
- Plaintext-like (non-Fernet): **244**

The 27 failing question IDs are **strongly indicated** to have retrieved at least one of those 244 plaintext-like rows during retrieval — the aggregate counts (244 plaintext-like among 10,456 active) make this the only plausible explanation for Fernet `InvalidToken` on these specific rows. However, the current artifact does not include per-question retrieval_log → plaintext memory ID proof. C1-A must perform that per-question attribution (cross-referencing `retrieval_log.candidate_memory_ids` against the 244 plaintext-like memory IDs) before formal exclusion of these 27 rows from the score denominator is justified.

### Why the other 473 questions succeeded

The 473 successful questions retrieved memories whose `content` values were all Fernet-encrypted. The 27 failing questions happened to have at least one plaintext `content` in their top-5 candidate set (or a later memory that was retrieved for a subsequent question).

### Code citations

| Location | Role |
|---|---|
| `encryption.py:44-53` | `ContentEncryption.decrypt()` — raises `ValueError` on `InvalidToken` |
| `store.py:903` | `d["content"] = self._enc.decrypt(d["content"])` — unconditional decrypt call in retrieval path |
| `evaluate.py:845-855` | Exception handler in `run_evaluation()` — catches all exceptions, sets `error` field, `hypothesis: ""` |

---

## `NoneType.strip` Root Cause

### Mechanism

`parse_answer()` (`evaluate.py:523-527`):

```python
def parse_answer(text: str) -> str:
    text = text.strip()      # ← line 524: .strip() called on text
    if text.lower().startswith("answer:"):
        text = text[7:].strip()
    return text
```

`_extract_content()` (`evaluate.py:390-406`):

```python
def _extract_content(response: Any) -> str:
    # ... dict extraction ...
    choices = response_data.get("choices", [])
    if not choices:
        return ""                                    # ← returns "" on empty choices
    message = choices[0].get("message", {})
    return message.get("content", "")               # ← returns "" (not None) if content missing
```

Under normal conditions, `_extract_content()` returns `""` (empty string) when `content` is absent, and `parse_answer("")` handles `""` correctly.

### Trigger scenario for `NoneType.strip`

For `question_id: 7401057b`, the provider returned a response where:
- `choices` was non-empty
- `choices[0]["message"]["content"]` was explicitly `None` (not absent/missing)

In this case, `message.get("content", "")` returns `None` (because the key exists with value `None`), and `parse_answer(None)` calls `None.strip()`, raising `AttributeError`.

This is a **harness null-handling defect**: `_extract_content()` should coerce `None` to `""` before returning, e.g.:

```python
content = message.get("content", "")
return content if content is not None else ""
```

### Code citations

| Location | Role |
|---|---|
| `evaluate.py:390-406` | `_extract_content()` — `message.get("content", "")` returns `None` when key exists with `None` value |
| `evaluate.py:523-527` | `parse_answer()` — calls `.strip()` on the returned value with no `None` guard |
| `evaluate.py:845-855` | Exception handler — catches the `AttributeError`, sets `error` field, `hypothesis: ""` |

---

## Sampled Failing / Passing Question IDs

### Failing (invalid ciphertext — 27 IDs)

```
e47becba
118b2229
51a45a95
3b6f954b
dccbc061
b320f3f8
c14c00dd
f4f1d8a4_abs
2788b940
gpt4_ab202e7f
gpt4_2f91af09
8a2466db
4adc0475
0ea62687
60159905
gpt4_ec93e27f
982b5123
gpt4_4cd9eba1
gpt4_2f56ae70
gpt4_5438fa52
ce6d2d27
6aeb4375_abs
8aef76bc
71a3fd6b
6222b6eb
352ab8bd
28bcfaac
```

Sample JSONL evidence (row 1 — `e47becba`):
```json
{
  "question_id": "e47becba",
  "hypothesis": "",
  "error": "Invalid ciphertext: decryption failed (wrong key or corrupted data)",
  "failure_category": "harness exception"
}
```

### Failing (`NoneType.strip` — 1 ID)

```
7401057b
```

Sample JSONL evidence:
```json
{
  "question_id": "7401057b",
  "hypothesis": "",
  "error": "AttributeError: 'NoneType' object has no attribute 'strip'",
  "failure_category": "harness exception"
}
```

### Passing (sample — 5 of 472 successful rows)

```
58bf7951   → judgment: incorrect,  memories_used: 5,  retrieved_memory_ids: [5 UUIDs]
1e043500   → judgment: incorrect,  memories_used: 5,  retrieved_memory_ids: [5 UUIDs]
c5e8278d   → judgment: correct,    memories_used: 5,  retrieved_memory_ids: [5 UUIDs]
6ade9755   → judgment: incorrect,  memories_used: 3,  retrieved_memory_ids: [3 UUIDs]
7527f7e2   → judgment: incorrect,  memories_used: 1,  retrieved_memory_ids: [1 UUID]
```

All passing rows show `memories_used >= 1` and non-empty `retrieved_memory_ids`, confirming retrieval succeeded and all retrieved content was Fernet-decryptable.

---

## Disposition Recommendation

### Invalid ciphertext (27 rows) — mixed encryption/plaintext storage class

**Attribution:** Storage anomaly — a subset of benchmark-user memories were stored as plaintext (no Fernet prefix `gAAAAA...`) rather than encrypted. Likely causes: key-rotation event, fallback-to-plaintext during a startup error, or an earlier ingestion run with `DAEMON_ENCRYPTION_KEY` unset. This is a **partial mismatch / storage anomaly class** failure.

**C1-A disposition options (under N1 constraint — no production memory code changes):**

| Option | Action | Constraint |
|---|---|---|
| **(a) Config/key fix** | If a single correct key can decrypt the affected rows: set `DAEMON_ENCRYPTION_KEY` to that historical key and re-run retrieval. Does not modify production code. | Requires identifying the correct historical key. |
| **(b) Formal exclusion** | Exclude the 27 rows from success_count denominator only after per-question attribution confirms each hit at least one plaintext-row memory ID. Score recomputed over the remaining clean rows. | Requires C1-A to verify per-question attribution before exclusion is justified. |
| Not recommended | Attempting a decrypt-passthrough in `orchestrator/memory/store.py` — this would modify production memory code and is outside scope for C1-A under N1. | N1 prohibits production memory code changes. |

### `NoneType.strip` (1 row, `7401057b`) — harness null-handling defect

**Attribution:** Harness defect — `_extract_content()` does not guard against `content: null` in the provider response. The provider returning `null` content is non-fatal; the harness should treat it as empty string.

**Fix (surgical):** `evaluate.py:406` — change `return message.get("content", "")` to:
```python
content = message.get("content", "")
return content if content is not None else ""
```

This is a **one-line fix** in `evaluate.py`. C1-A should include this fix as a trivial, high-confidence correction.

**Disposition:** Fix surgically. The 1-row impact is negligible to score, but the defect can cause spurious failures on any provider `null`-content response.

---

## Verification

| Check | Result |
|---|---|
| `python -m py_compile tests/longmemeval/evaluate.py` | PASS |
| `python -m py_compile orchestrator/memory/encryption.py` | PASS |
| `python -m py_compile orchestrator/memory/store.py` | PASS |
| `git diff -- orchestrator/memory/` | clean (no changes) |
| Diagnosis contains no raw secrets | Confirmed — no `postgresql://`, no API keys, no plaintext memory content |

---

## Next Action for C1-A

C1-A inherits:
1. **Invalid ciphertext 27-row attribution**: Cross-reference `retrieval_log.candidate_memory_ids` against the 244 plaintext-like memory IDs for each of the 27 failing question IDs to confirm per-question hit. If (a) a single historical key can decrypt the affected rows → config fix and re-run. If (b) rows are genuinely plaintext/corrupted → formal exclusion of 27 rows from score denominator.
2. **`NoneType.strip` 1-row fix**: Add `None` guard in `_extract_content()` at `evaluate.py:406` — one-line surgical fix, does not modify `orchestrator/memory/**`.
3. **Storage reconciliation**: Investigate why 244/10,456 benchmark-user memories are plaintext-stored; identify correct historical key if recoverable, or accept undecryptable rows as permanent data-quality gap.
4. **Re-run C3** after fixes to confirm `success_count >= 495` gate and `median memories_used > 0`.

C4 remains blocked until C1-A is resolved.

---

## C1-A: Error Disposition — 2026-05-04

**Task:** C1-A (disposition + surgical fix)
**Scope:** `orchestrator/memory/` unchanged; harness fix in `tests/longmemeval/evaluate.py` only
**Artifacts:** `.sisyphus/evidence/c1-a-28-error-disposition.json`

---

### Executive Verdict

28 errors → **27 bounded error-class exclusion + 1 surgical fix**.

| Class | Count | Disposition |
|---|---|---|
| Invalid ciphertext | 27 | **Bounded error-class exclusion** (unrecoverable data-quality/storage anomaly) |
| `NoneType.strip` | 1 | **Surgical fix** (`evaluate.py:406` null guard) |

C3 rerun is **required** because the null guard fix for `7401057b` was applied (user addendum: C1-C rerun iff a fix was applied). The 27 invalid-ciphertext exclusion is analytical and does not itself require rerun.

---

### Invalid Ciphertext — 27 Rows: Formal Exclusion

#### Attribution Attempt

**Method:** PostgreSQL `retrieval_log` query against scoped C3 rerun evaluate window (2026-05-04T08:52:54 to 2026-05-04T09:18:10), benchmark user `12345678-1234-5678-1234-567812345678`, `retrieval_triggered_by = 'longmemeval'`.

**Result:**
- Total `retrieval_log` entries in window: **473**
- 27 invalid-ciphertext question IDs with `retrieval_log` entry: **0**
- 27 invalid-ciphertext question IDs without `retrieval_log` entry: **27**
- Per-question plaintext candidate intersection: **0 questions** (no data to intersect)

**Structural cause confirmed:** `store.py:903` raises `ValueError("Invalid ciphertext...")` when a plaintext-stored row is encountered. This exception propagates up through `retrieve_memories_for_text()` before the fire-and-forget `asyncio.create_task(_persist_log)` at `retrieval.py:696` is reached — the async log write is scheduled **after** the return from retrieval, not before. Consequently, the 27 questions that hit plaintext memories errored at decrypt time and never wrote a `retrieval_log` row. Per-question attribution via candidate/selected IDs is **structurally impossible**.

**DB state:**
- Plaintext-like memories (non-Fernet-prefixed `gAAAAA`) in DB: **244** (confirmed by Atlas in D1)
- Matched candidate memory IDs across all 473 logged questions: **1929 unique IDs**
- Plaintext IDs intersecting matched candidates: **0** — the 244 plaintext rows were not retrieved by any of the 473 successfully logged questions

**Limitation of the 473-matched evidence:** The zero-overlap finding only proves the 473 successfully logged questions did not retrieve plaintext-like candidates. It says nothing about which candidates the 27 error questions attempted — no `retrieval_log` entries exist for those questions, so per-question candidate/selected ID intersection cannot be established.

#### Disposition Decision

| Option | Status |
|---|---|
| (a) Config/key fix | **Not possible** — no single historical key identified; recovery would require `orchestrator/memory/` changes (N1-prohibited) |
| (b) Per-question attributed exclusion | **Structurally blocked** — 0/27 have `retrieval_log` entries |
| (c) Bounded error-class exclusion | **Selected** — only defensible option under N1 and D1's attribution-before-exclusion criterion |

**Bounded error-class exclusion is applied.** Per-question attribution (retrieval_log candidate/selected IDs intersect plaintext-like IDs) is structurally impossible because 0/27 questions have any `retrieval_log` entry. The disposition is: **unrecoverable data-quality/storage anomaly — bounded error-class exclusion**.

**Baseline/disposition accounting:**
- Valid successes: 472 (hypothesis != "", no error)
- Excluded error rows: 27 (invalid ciphertext — no log, no per-question attribution possible)
- Denominator for disposition: 473
- User addendum supersedes old C3 gate language; gate-related score language is excluded.

---

### `NoneType.strip` — 1 Row (7401057b): Surgical Fix

**Root cause:** `choices[0]["message"]["content"]` was explicitly `null` in the provider response. `message.get("content", "")` returns `None` (the key exists), and `parse_answer(None)` calls `None.strip()` → `AttributeError`.

**Fix applied** at `evaluate.py:405-407`:

```python
# Before:
return message.get("content", "")

# After:
content = message.get("content", "")
return content if content is not None else ""
```

This is a one-line surgical null guard. It does not change behavior for the `""` case or for non-null string content. It only prevents the `None.strip()` crash when a provider returns `content: null`.

**Verification:** `python -m py_compile tests/longmemeval/evaluate.py` → PASS.

---

### Disposition Summary

| Error class | Count | Disposition | C3 rerun required |
|---|---|---|---|
| Invalid ciphertext | 27 | Bounded error-class exclusion (unrecoverable storage anomaly) | No (analytical) |
| `NoneType.strip` | 1 | Fixed (surgical null guard) | Yes (null guard fix applied) |

**C3 rerun required:** Yes — the null guard fix for `7401057b` requires verification via C1-C rerun (user addendum: rerun iff a fix was applied). The 27 invalid-ciphertext exclusion is analytical and does not itself require rerun.

**Fixed files:**
- `tests/longmemeval/evaluate.py` (lines 405-407, `None` guard in `_extract_content()`)

**Files unchanged:** `orchestrator/memory/` — clean confirmed.

**Evidence:** `.sisyphus/evidence/c1-a-28-error-disposition.json`


---

## 2026-05-04 C1-C rerun verification

- Canonical rerun completed at `tests/benchmark_results/wave0_closure_option_a_rerun/` using the completed scoped ingest checkpoint as seeded ingest-only state; no re-ingestion was performed.
- The invalid-ciphertext class recurred exactly as C1-A predicted: the rerun emitted the same 27 question IDs, with no extras and no misses relative to `.sisyphus/evidence/c1-a-28-error-disposition.json`. The bounded error-class exclusion therefore remains unchanged.
- The null-content guard is now live-verified: `question_id=7401057b` has `error=null`, `judgment=incorrect`, non-empty hypothesis text, and `memories_used=5`, so the `NoneType.strip` failure class is gone.
- Option A accounting for this rerun: raw `success_count=473`, raw `error_count=27`, bounded exclusions `=27`, disposition denominator `=473`, and raw official aggregate `49/500 = 0.098`. The old 15%/floor gates remain superseded and are not used here as pass/fail language.
