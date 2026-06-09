# Architecture Decision Records (ADRs)

This directory captures the *why* behind significant decisions in the
eksreview codebase. Each ADR is a short markdown file explaining one
decision, the alternatives considered, and the tradeoffs accepted.

## When to write an ADR

Add an ADR when a change:

- Is hard to reverse (significantly affects code structure, public
  surface, or persisted state).
- Has clear alternatives that a reasonable engineer might pick
  differently.
- Affects the safety model, performance characteristics, or cost
  profile.
- Locks in an external dependency in a non-obvious way.

You don't need an ADR for renames, bug fixes, dependency bumps, or
test-only changes. If you're not sure, ask in code review.

## Format

Use the [Michael Nygard ADR template](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions):

```
# ADR-NNN: Short title

## Status
Accepted | Superseded by ADR-XXX | Rejected | Proposed | Deprecated

## Context
The forces in play that motivated this decision.

## Decision
What we chose.

## Alternatives considered
The other plausible options and why we didn't pick them.

## Consequences
The good, bad, and neutral effects of this decision. Be honest about
tradeoffs.
```

## Numbering

Use the next available number, zero-padded to three digits. Don't
renumber existing ADRs — they're permanent records. If an ADR is
later overturned, add a new ADR that supersedes it and update the
status of the old one to "Superseded by ADR-XXX".

## Index

- [ADR-001](001-sliding-window-conversation-manager.md) — Sliding
  window conversation manager over summarizer
- [ADR-002](002-stdio-mcp-not-http.md) — stdio MCP transport instead
  of HTTP
- [ADR-003](003-two-tier-orchestration.md) — Two-tier orchestration
  with ephemeral sub-agents
- [ADR-004](004-mcp-subprocess-env-allowlist.md) — Allowlist filter on
  MCP subprocess environment
- [ADR-005](005-layered-safety-stack.md) — Layered safety stack over
  monolithic gate
- [ADR-006](006-skill-driven-report-compilation.md) — Skill-driven
  report compilation
- [ADR-007](007-no-shell-as-opt-in.md) — `--no-shell` as opt-in, not
  default
- [ADR-008](008-bm25-over-embeddings.md) — BM25 over embeddings for
  the local knowledge base
- [ADR-009](009-pluto-live-fetch-with-validation.md) — Live pluto
  fetch with validation, not commit pinning
- [ADR-010](010-process-global-rate-limit.md) — Process-global rate
  limit, not per-cluster or per-user
- [ADR-011](011-remove-confirm-action-tool.md) — Remove `confirm_action`,
  rely on the shell tool's native consent
