# Wave 0 — Embedding Determinism Reference Pack

**Date:** 2026-04-21
**Scope:** Voyage AI embedding nondeterminism references for LongMemEval benchmark reproducibility
**Status:** Source-backed; official/primary-source only

---

## 1. Official Voyage AI Documentation

### 1.1 Voyage AI Embeddings API (Primary Source)

**Source:** [Voyage AI Text Embeddings Documentation](https://docs.voyageai.com/docs/embeddings)
**Source:** [Voyage AI Python API Reference](https://docs.voyageai.com/docs/embeddings)

**Key API signature:**

```python
vo.embed(
    texts: str or List[str],
    model: str,                    # e.g. "voyage-4-large", "voyage-4-lite"
    input_type: Optional[str],      # None, "query", or "document"
    truncation: Optional[bool],     # defaults to True
    output_dimension: Optional[int],
    output_dtype: Optional[str]     # "float", "int8", "uint8", "binary", "ubinary"
)
```

**Critical observation — no `seed` parameter exists in the Voyage API.**

Unlike the OpenAI chat completion API, Voyage AI provides **no seed parameter, no fingerprint field, and no reproducibility guarantee** for embedding outputs. The API is stateless in the sense that identical inputs *should* produce identical outputs, but:

1. This is not guaranteed contractually
2. The service does not expose any mechanism to verify whether outputs are deterministic across calls
3. Community evidence (see §2) shows that conditional nondeterminism does occur

### 1.2 Daemon Embedding Configuration

From `orchestrator/memory/embedding.py`:

- **Document embeddings:** `voyage-4-large` via `embed_texts()` → `embeddings_document_model` setting
- **Query embeddings:** `voyage-4-lite` via `embed_query()` → `embeddings_query_model` setting
- **No seed parameter** is passed or available
- **No fingerprint** is returned or captured

From `orchestrator/config.py` and the pinned config:
- Query embedding model: `embedding_query_model` via `Settings`
- Document embedding model: `embedding_document_model` via `Settings`
- Both are hardcoded constants in the benchmark pinning table

---

## 2. Community Evidence of Embedding Nondeterminism

### 2.1 Voyage AI Community Discussion — Different Embeddings for Same Input

**Source:** [Voyage AI Community — I'm getting different embeddings for the SAME encoding](https://docs.voyageai.com/discuss/6854e9cdbbba01001836c09f) (Feb 2024)

**Key observation (paraphrased from discussion):**

> "You CONSISTENTLY get a different embedding for the word 'modern' if it is requested as part of an array of inputs. You OCCASIONALLY get a different embedding for the word 'modern' regardless of the array/string differences."

**Practical implications from the community report:**
1. Batch-vs-single embedding of the same text can produce different vectors
2. The same single-string call can occasionally produce different outputs on repeated invocations
3. Cosine similarity between two calls of the same string can be as low as 0.9924 (distance ~0.0076) — below what would be expected for true determinism

### 2.2 Academic Evidence — Endpoint Nondeterminism in ML APIs

**Source:** [arXiv:2603.19022v1 — Behavioral Fingerprints for LLM Endpoint Stability and Identity](https://arxiv.org/html/2603.19022v1)

**Key citations:**

> "Even if users fix visible settings (e.g., temperature), endpoint behavior can vary due to system level nondeterminism 'beneath the surface': variance in inference engines, kernels, caching, batch sizes, and hardware — and providers may route requests across heterogeneous environments."

> "These system-level factors, which can result in nondeterministic behavior even at temperature 0, must be considered on top of discrete, deliberate updates such as a model version change, undermining reproducibility in both production and evaluation environments."

> "Small variance in early steps can compound into large workflow variance."

---

## 3. Nature of Embedding Nondeterminism in Daemon's Pipeline

### 3.1 Where Embeddings Enter the Pipeline

1. **Document embedding** (at ingestion time): Each corpus session's messages are embedded via `voyage-4-large` and stored in pgvector
2. **Query embedding** (at retrieval time): The benchmark question is embedded via `voyage-4-lite` for retrieval

A change in either embedding vector can cause:
- Different retrieved memory sets (top-k similarity threshold crossing)
- Different dedup outcomes (different content → different similarity scores)
- Different judge inputs (retrieval → answer → judge chain)

### 3.2 Known Specifics

- **No seed parameter** exists for Voyage embeddings — reproducibility cannot be enforced at the API level
- **No `system_fingerprint` equivalent** — Voyage provides no signal when backend model configuration changes
- **Conditional nondeterminism confirmed** — batch vs. single, and repeated identical calls, can produce different vectors
- **Magnitude of variance** — community evidence suggests cosine similarity drops to ~0.992–0.993 on nondeterministic calls (distance 0.007–0.008), which can shift rank-1 retrieval outcomes

---

## 4. Implications for LongMemEval Embedding Reproducibility

| Factor | Controllable? | Variance Impact |
|---|---|---|
| Embedding model selection | Yes (hardcoded) | Minimal — changing models invalidates comparison |
| `input_type` ("query" vs "document") | Yes (pinned) | Must be consistent; affects prompt prepending |
| Truncation behavior | Yes (pinned True) | Must be consistent; affects vectorized content |
| `output_dimension` | Yes (pinned) | Must be consistent |
| `seed` parameter | **No** | Does not exist in Voyage API |
| Backend model updates | No | Can change embedding behavior without notice |
| Batch vs. single call | Partially | Daemon uses batch for documents, single for queries — both are at risk |
| Provider infrastructure variance | No | CUDA kernel, batch scheduling, routing — outside user control |

**Conclusion:** Embeddings are the **highest-risk component** for reproducibility because:
1. No seed/fingerprint mechanism exists
2. Community evidence confirms real-world nondeterminism
3. Small vector changes can cascade into rank-1 retrieval changes and downstream judgment differences
4. The variance is **measured risk, not assumed certainty** — but must be treated as a structural constraint

---

## 5. Citation List

1. Voyage AI. "Text Embeddings Documentation." https://docs.voyageai.com/docs/embeddings
2. Voyage AI. "Python API Reference." https://docs.voyageai.com/docs/embeddings
3. Voyage AI Community. "I'm getting different embeddings for the SAME encoding." https://docs.voyageai.com/discuss/6854e9cdbbba01001836c09f (Feb 2024)
4. He and Thinking Machines Lab. "Behavioral Fingerprints for LLM Endpoint Stability and Identity." arXiv:2603.19022v1 (2026). https://arxiv.org/html/2603.19022v1
