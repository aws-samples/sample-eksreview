# ADR-008: BM25 over embeddings for the local knowledge base

## Status
Accepted (2026-05-23)

## Context

The agent supports a local knowledge base — users can index PDFs,
markdown, code, configs via `/knowledge add`. The agent searches
through it using `knowledge_search` to retrieve relevant context
during conversations.

Two reasonable approaches:

1. **BM25** (term-frequency / inverse-document-frequency). Classic
   information retrieval. Fast, deterministic, no model
   dependency, results explainable.
2. **Vector embeddings** (Bedrock Titan, etc.). Semantic similarity.
   Strong on paraphrased queries. Requires per-document embeddings
   and a per-query embedding call.

## Decision

Use BM25 with sentence-aware chunking. Storage is SQLite. The
in-memory inverted index gives O(k) search where k is the number
of query terms.

`knowledge_base.py` owns:
- `_chunk_text_semantic` — splits on `.!?`, paragraph breaks, and
  markdown headers, then groups sentences into ~1024-char chunks
  with sentence-level overlap.
- `_tokenize` — lowercase `\w+`, term-frequency dict.
- `search` — standard BM25 with `k1=1.5`, `b=0.75`.

## Alternatives considered

- **Vector embeddings via Bedrock Titan.** Higher recall on
  semantic queries ("how do I make pods more reliable" → finds
  PDB and probe content even without those words appearing).
  Rejected for now because:
  - Adds a Bedrock cost per indexed chunk and per query.
  - Adds a dependency on Titan availability in the user's
    Bedrock region.
  - Queries become non-deterministic — embeddings can drift across
    model versions.
  - The corpus is small (one PDF + user-indexed docs); BM25
    handles it.

- **Hybrid (BM25 + reranking).** Reranks BM25 results with an LLM
  call. Better quality, but adds latency to every search. Not
  worth the cost for a corpus this size.

- **No knowledge base, just `file_read` on demand.** Tested first.
  Forces the LLM to know the file structure ahead of time.
  Doesn't scale past 5–10 files.

## Consequences

**Positive**
- No Bedrock cost for indexing or searching.
- Deterministic and explainable — users can see why a chunk
  matched (the score is BM25, not a black box).
- Works offline. Air-gapped users with `EKS_REVIEW_OFFLINE=1`
  still get full search.
- The SQLite DB is portable. Users can ship `.knowledge/` as a
  pre-warmed cache.

**Negative**
- Paraphrased queries miss matches. "Pod reliability" doesn't
  match a doc that talks about "PDB" without ever using the word
  "reliability".
- Tokenizer splits version strings (`v1.16.0` → `["v1", "16",
  "0"]`). Acceptable for BM25 but worth knowing — documented in
  `docs/architecture.md` and inline.

**Neutral**
- If the corpus grows to thousands of documents or users
  consistently report missing-on-paraphrase, embeddings become
  worth reconsidering. This ADR would be superseded.

## References

- `eks_review_agent/knowledge/knowledge_base.py` — implementation.
- `tests/test_knowledge_base.py` — BM25 search tests.
- `docs/architecture.md` — Knowledge base section.
