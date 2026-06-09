# ADR-011: Remove `confirm_action`, rely on the shell tool's native consent

## Status
Accepted (2026-06-08). Supersedes the Layer 2 portion of
[ADR-005](005-layered-safety-stack.md).

## Context

ADR-005 defined a four-layer safety stack. Layer 2 was
`tools.confirm_action` — a custom tool the agent was instructed to call
before any mutating operation. It presented the agent's
natural-language *description* of the action, ran a description-level
blocklist, and prompted the user `[y/*]`.

In practice, every mutating action in this agent runs through the
`shell` tool (kubectl / aws / helm). The `shell` tool from
`strands_tools` already has its own interactive consent prompt that
shows the **verbatim command** and asks `Do you want to proceed with
execution? [y/*]` before executing.

This produced two problems:

1. **Double confirmation.** A single `/fix` shell command triggered
   both `confirm_action`'s prompt *and* the shell tool's prompt — two
   yes/no questions for one action. Poor UX.
2. **The weaker prompt gated on the wrong thing.** `confirm_action`
   asked the user to approve the agent's *description* ("restrict the
   endpoint"), not the literal command. A description can drift from
   what actually runs; the shell tool's prompt cannot.

## Decision

Remove `confirm_action` entirely. The `shell` tool's native consent
prompt becomes the single interactive gate, and it shows the exact
command. The destructive-command hard block
(`observability.on_before_tool`, ADR-005 Layer 4) is unchanged and
remains the real enforcement boundary.

Concretely:

- Deleted `tools.confirm_action` and its tests.
- Removed it from the agent tool list, the upgrade sub-agent tool
  list, both skill `allowed-tools`, the steering `_ALWAYS_SAFE_TOOLS`
  set, and the steering prompt's `safe_tools`.
- Updated the system prompt, `/fix` prompt, and skills to stop
  instructing the agent to call it — they now defer to the shell
  tool's own confirmation and tell the agent not to add a second one.
- Updated the steering prompt so modifying commands PROCEED (the shell
  consent + Layer 4 are the gates) instead of requiring
  `confirm_action`.
- Removed the now-dead `EKS_REVIEW_AUTO_CONFIRM` env var (only
  `confirm_action` read it).

The safety stack is now three active gates: injection tripwire (Layer
1), LLM steering (Layer 2, renumbered), shell tool consent (Layer 3,
new), and the shell-command hard blocklist (Layer 4).

## Alternatives considered

- **Keep `confirm_action`, suppress the shell prompt** (set
  `BYPASS_TOOL_CONSENT=true`). Keeps the friendly description-based
  prompt, single confirmation. Rejected because the description-based
  gate is the weaker of the two — the user should approve the literal
  command, not a paraphrase.
- **Keep both prompts.** Rejected — redundant and annoying; trains
  users to reflexively `y` through prompts, which is itself a risk.
- **Keep `confirm_action` as a dormant secondary tripwire.** Rejected
  to avoid dead, confusing surface area in an open-source sample.

## Consequences

**Positive**
- One confirmation per command, showing the exact command to run.
- Less code and fewer safety layers to keep in sync.
- The interactive gate now operates on the literal command, closing
  the description-paraphrase gap ADR-005 called out for Layer 2.

**Negative**
- The shell tool's consent prompt can be suppressed by
  `BYPASS_TOOL_CONSENT=true` or `STRANDS_NON_INTERACTIVE=true`
  (upstream env vars). eksreview does not set them, but a user could.
  An always-on confirmation enforced in
  `observability.on_before_tool` (independent of these env vars) is
  on the roadmap. Layer 4 still hard-blocks destructive commands
  regardless.
- The non-TTY auto-confirm path (`EKS_REVIEW_AUTO_CONFIRM`) is gone;
  non-interactive automation now relies on the shell tool's own
  non-interactive behavior.

**Neutral**
- Layer 4 (`_DESTRUCTIVE_SHELL_PATTERNS`) is untouched and remains
  the authoritative hard gate.

## References

- [ADR-005](005-layered-safety-stack.md) — the original four-layer
  stack this partially supersedes.
- `docs/architecture.md` — The safety stack section (updated).
- `eks_review_agent/core/observability.py::_DESTRUCTIVE_SHELL_PATTERNS`
  — the unchanged Layer 4 hard gate.
