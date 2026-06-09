# ADR-005: Layered safety stack over monolithic gate

## Status
Accepted (2026-05-23). Layer 2 (`confirm_action`) superseded by
[ADR-011](011-remove-confirm-action-tool.md) — it was removed in favor
of the shell tool's native consent prompt. The other three layers
stand; see ADR-011 for the current stack.

## Context

The agent can run shell commands during `/fix` and `/investigate`.
An LLM that's been confused or prompt-injected might attempt to
execute something destructive. We need to gate that.

The naive design is one gate: either an LLM judge or a static
allowlist. Each has known failure modes:

- **LLM judge alone** — quality bound by the gate model.
  Persuasion attacks land.
- **Static allowlist alone** — brittle. Misses novel command
  shapes. Frustrates users when they hit a false negative on a
  legitimate command.
- **`confirm_action` alone** — operates on the agent's
  description of the action. A polite paraphrase
  ("remove the test node group cleanly") bypasses substring
  matching.

## Decision

Use four independent layers, each catching a different class of
failure. A destructive action has to defeat all four to land. See
`docs/architecture.md` for the full diagram.

1. **Injection tripwire** in `prompts.detect_prompt_injection` —
   Unicode-normalized keyword check on user-supplied free text in
   `/fix`, `/investigate`, `/upgrade`. Catches obvious attacks.
2. **`confirm_action` blocklist** in `tools.confirm_action` —
   description-level substring match plus interactive
   confirmation. Blocks the obvious "delete-cluster" / "rm -rf"
   shapes regardless of user input.
3. **LLM steering handler** in `plugins.create_steering_handler`
   — Haiku judges shell tool calls. Bypasses the
   `_ALWAYS_SAFE_TOOLS` set for performance, so only `shell` is
   evaluated.
4. **Shell-command blocklist** in `observability.on_before_tool` —
   token-based pattern match against the literal shell command.
   Sets `event.cancel_tool` (Strands' framework API) when matched.

## Alternatives considered

- **One LLM gate, no static rules.** Faster to build, no false
  positives on novel commands. Rejected because LLM gates are
  bound by the gate model's quality and can be persuaded.

- **One static gate, no LLM.** Predictable, no extra cost. Rejected
  because static rules can't keep up with new tool shapes (e.g.
  `eksctl` vs `aws eks` vs `kubectl` for the same destructive
  operation, all with different surface).

- **`confirm_action` + LLM gate, no shell-command match.** This was
  the state before the M4/#12 fixes. Bypassed by polite description
  paraphrasing.

## Consequences

**Positive**
- Each layer is independently testable — most have unit tests
  under `tests/`.
- A future weakness in any one layer doesn't compromise the
  whole. Adding or removing a layer is a localized change.
- The user-visible failure mode for legitimate refusals is a
  clear message ("Refused — '...' is blocked by safety policy").
- The hardcoded patterns in layers 2 and 4 are in code, not in
  the LLM prompt — so they're auditable, version-controlled, and
  testable.

**Negative**
- Four layers means four places to update when adding a new
  destructive pattern. The architecture doc maps them.
- `_ALWAYS_SAFE_TOOLS` (the steering bypass) is one more list to
  maintain. False entries are a real risk — a tool added to that
  set skips the LLM judge entirely.
- Latency: ~6 seconds per shell call from the Haiku judge. The
  bypass mitigates this for read-only tools.

**Neutral**
- The layers are not strictly ordered — they fire at different
  points in the call. The "stack" diagram in `docs/architecture.md`
  shows the conceptual order.

## References

- M4 / `#12` resolution in the security review.
- `docs/architecture.md` — The safety stack section.
- `eks_review_agent/core/observability.py::_DESTRUCTIVE_SHELL_PATTERNS`
  — Layer 4 list.
- `eks_review_agent/tools.py::BLOCKED_PATTERNS` — Layer 2 list.
- `eks_review_agent/core/plugins.py::_ALWAYS_SAFE_TOOLS` — Layer 3
  bypass set.
