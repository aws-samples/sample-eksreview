# ADR-001: Sliding window conversation manager over summarizer

## Status
Accepted (2026-05-23)

## Context

Long EKS review sessions blow past Claude's context window. A single
review produces tens of thousands of tokens of MCP output, plus the
agent's conversational follow-up. We need a strategy for managing
older history without losing the user's ability to ask "what about
finding #4?" three turns later.

Strands provides two built-in conversation managers:
`SlidingWindowConversationManager` and
`SummarizingConversationManager`. We need to pick one.

## Decision

Use `SlidingWindowConversationManager` with `window_size=50`,
`should_truncate_results=True`, `per_turn=True`.

The window keeps the most recent 50 messages. Older tool results are
compressed to ~200 chars in place. The agent recovers full data on
demand via `report_search` (BM25-style search into saved reports) and
`file_read` on the saved markdown.

`window_size` is exposed as `CONVERSATION_WINDOW_SIZE` in `config.py`
so it's tuneable without code changes. Wired in `agent.py`.

## Alternatives considered

- **`SummarizingConversationManager`.** Runs an LLM call to
  compress old turns into a summary. Higher fidelity than truncation
  — the agent retains paraphrased context rather than just a path to
  a file. Rejected because it adds 1–2 seconds of latency per turn
  plus another billable Bedrock call, and because the summary's
  quality depends on the summarizer model's interpretation of the
  conversation. The `agent.py` factory has a commented-out fallback
  block ready to switch back if needed.

- **No truncation, manual `/restart`.** Worst UX: users have to
  notice token exhaustion themselves and start fresh, losing all
  state.

- **Periodic full restart.** Loses session state including
  `last_reviewed_cluster`, breaking `/fix` and `/investigate`.

## Consequences

**Positive**
- Sessions can run effectively unbounded. A reviewer can do five
  cluster audits in one sitting without context exhaustion.
- Each turn is fast and predictable — no extra LLM call.
- The retrieval pattern (search saved reports for details) is
  cheap and aligns with how the agent already structures its work.

**Negative**
- The agent must remember to use `report_search` instead of relying
  on conversational memory. The system prompt enforces this, but
  it's a discipline the agent has to keep up.
- "What was the third finding?" type questions require the agent to
  search rather than recall.

**Neutral**
- If cost ever inverts (summarization gets cheap, MCP results get
  small), switching back is a single uncomment in `agent.py`.

## References

- `eks_review_agent/agent.py` — wires the manager.
- `eks_review_agent/config.py::CONVERSATION_WINDOW_SIZE` — tunable.
- `docs/architecture.md` — Two-tier orchestration section.
