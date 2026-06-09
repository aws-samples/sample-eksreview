# ADR-007: `--no-shell` as opt-in, not default

## Status
Accepted (2026-05-23)

## Context

The agent uses the `shell` tool (from `strands_tools`) to execute
commands during `/fix` (apply remediations) and `/investigate`
(gather diagnostic data). That tool is the most powerful surface in
the agent — it can run arbitrary kubectl / aws / helm / shell
commands.

Some users want a strict read-only mode for audits, regulated
environments, or clusters they don't own. The original M4 finding
in the security review recommended a `--no-shell` flag.

We had two options for the default:

1. Shell on by default; `--no-shell` to disable.
2. Shell off by default; `--with-shell` to enable.

## Decision

Shell is on by default. `--no-shell` (and `EKS_REVIEW_NO_SHELL=1`)
opts into read-only mode.

When set, the flag filters `shell` out of:
- The main agent's tool list (`agent.py::create_agent`).
- The upgrade sub-agent's tool list
  (`upgrade_orchestrator._build_pipeline_config`).
The review sub-agent's tool list never had `shell`, so it's
unaffected.

Banner shows "Mode: --no-shell" when active.

## Alternatives considered

- **Off by default.** Most users came to the agent expecting
  `/fix` to actually fix things. Defaulting to read-only would
  break that workflow for the common case. Rejected because the
  defense-in-depth stack (ADR-005) already gates destructive
  shell commands at four layers, so the residual risk of shell-on
  is bounded.

- **Always on, no opt-out.** Simpler, but excludes legitimate
  read-only use cases (CI, audits). Rejected.

- **Enable shell only after explicit `/enable-shell` slash
  command.** Tested as a third option. Adds a confusing UX layer:
  users can't tell if they're in shell mode or not, and the
  enable command itself becomes a target for prompt injection.

## Consequences

**Positive**
- The common case (`/fix` actually fixing things) just works.
- `--no-shell` is a single flag for users who need strict mode,
  applied process-wide so there's no accidental shell access in
  any sub-agent.
- The flag and its env-var equivalent are in
  `docs/architecture.md` and the README, so users discover it.

**Negative**
- New users running `/fix` against production by accident still
  go through the shell tool's consent prompt and the LLM steering
  judge, but those are not perfect. A bad day looks like a confirmed
  destructive command on a cluster the user shouldn't have been
  touching.
- Two code paths to maintain (with shell, without). Tests cover
  both.

**Neutral**
- If audit mode becomes the dominant use case, this ADR can be
  superseded by flipping the default. The flag mechanism stays.

## References

- M4 finding in the security review.
- `eks_review_agent/agent.py::create_agent` — main filter.
- `eks_review_agent/orchestration/upgrade_orchestrator.py::_build_pipeline_config`
  — sub-agent filter.
- `docs/architecture.md` — Read-only mode section.
