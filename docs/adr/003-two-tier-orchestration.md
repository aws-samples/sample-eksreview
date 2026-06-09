# ADR-003: Two-tier orchestration with ephemeral sub-agents

## Status
Accepted (2026-05-23)

## Context

Running a full EKS review involves calling 6 MCP tools, each
returning kilobytes of JSON, then synthesizing the results into a
structured markdown report. If the main conversational agent does
all of this in its own context window, three problems compound:

1. The raw MCP JSON eats tens of thousands of tokens — the user
   can do one or two reviews before the window fills.
2. The main agent's prompt has to do double duty — chat
   conversationally AND know how to compile structured reports.
3. Costly LLM cycles are spent shuttling tool data the model
   doesn't actually need to "think about."

## Decision

Split orchestration into two tiers:

1. **Pure-Python data collection.** `mcp_checks.py` calls each MCP
   tool directly. No LLM is involved in the data-gathering step.
2. **Ephemeral sub-agents for synthesis.** `subagent_pipeline.run_subagent_pipeline`
   spins up a short-lived Strands `Agent` with `NullConversationManager`,
   a tight tool list (`save_report`, `knowledge_search`, `file_*`,
   plus optional `shell` and `http_request` for the upgrade flow),
   and a purpose-built system prompt activating the relevant skill.
   The sub-agent compiles the report and saves it to disk.
3. **Summary extraction.** The orchestrator regex-extracts just the
   executive summary (~200 tokens) from the saved report and returns
   that to the main agent. Detailed findings are retrieved on
   demand via `report_search`.

Two pipelines today (`review_orchestrator`, `upgrade_orchestrator`),
both implementing the same `SubAgentPipelineConfig` shape.

## Alternatives considered

- **Single agent, all tools in one context.** Hits all three
  problems above. Tested early; rejected.

- **Pre-compute reports without LLM at all.** Tried for a partial
  version; the synthesis step (correlating findings across
  domains, classifying remediation type, writing prose) is exactly
  what an LLM is good at. Pure-Python output reads like a stack
  trace, not a report.

- **One sub-agent per MCP tool.** Over-engineered. The 6 MCP tools
  are already cheap to call directly; spinning up 6 sub-agents
  adds latency and complexity for no gain.

## Consequences

**Positive**
- Main agent's context stays small. Long sessions are practical.
- Sub-agents have purpose-built prompts and minimal tool surface,
  so their behavior is easier to reason about.
- Token budget per review is bounded — the sub-agent runs once and
  exits.
- New pipelines (e.g. cost analysis, security deep-dive) plug in
  with a `SubAgentPipelineConfig` and a few prompt strings.

**Negative**
- Two control flows to maintain (main agent + sub-agent). The
  shared `subagent_pipeline.py` minimizes drift but doesn't
  eliminate it.
- Sub-agents can't ask the user follow-up questions — they're
  one-shot. If a sub-agent needs more info, it has to handle the
  case in its prompt or fail back to the main agent.

**Neutral**
- Sub-agents share the active model (set via `/model`). If we
  ever want a cheaper sub-agent (Haiku for compilation), that's
  a config knob in `subagent_pipeline.create_subagent_model`.

## References

- `eks_review_agent/orchestration/subagent_pipeline.py` — shared pipeline.
- `eks_review_agent/orchestration/review_orchestrator.py` — review pipeline config.
- `eks_review_agent/orchestration/upgrade_orchestrator.py` — upgrade pipeline config.
- `docs/architecture.md` — Two-tier orchestration section.
