# ADR-006: Skill-driven report compilation

## Status
Accepted (2026-05-23)

## Context

The sub-agent that compiles assessment reports needs detailed
instructions: the markdown structure, severity-to-priority mapping,
how to handle resource lists, when to mark something as "Not
determined", what NOT to fabricate. That's roughly 150 lines of
prescriptive guidance.

There are three places to put it: inline in the orchestrator's
`_get_report_system_prompt`, in a Strands skill (`SKILL.md` under
`skills/`), or split between the two.

## Decision

Put the lengthy formatting and rules in a Strands skill
(`skills/eks-report-compiler/SKILL.md`). The orchestrator's system
prompt is short and instructs the sub-agent to "activate the
'eks-report-compiler' skill to load the report format and rules,"
then call `save_report`.

Same pattern for the upgrade pipeline — `skills/eks-upgrade-readiness/SKILL.md`
holds the rules; `_get_upgrade_system_prompt` is short.

The CLI pipeline tools (`eks-investigation`, `eks-knowledge`) are
also implemented as skills so they share the same activation
mechanism.

## Alternatives considered

- **Inline everything in the system prompt.** Easier to read in
  one place, but the system prompt becomes ~3000 words. Strands
  auto-truncates very long system prompts, and prompt drift
  becomes harder to track because the prompt is regenerated on
  every sub-agent run.

- **Skill files only, no skill activation.** Strands' skill
  system loads all skills in a directory and exposes them by
  name. Without explicit activation, the agent has to figure out
  which skill applies.

- **Per-domain skills** (security skill, networking skill,
  etc.). Tested early. Rejected because the report structure is
  uniform across domains — one skill that handles all six is
  shorter and more consistent.

## Consequences

**Positive**
- The report format is one markdown file. Updating the format
  doesn't touch any Python code.
- Tests can pin the format using golden-file fixtures
  (`tests/fixtures/sample_assessment_report.md`) and verify the
  parsers match.
- Skills are loaded once per process — no runtime cost during
  the sub-agent's actual work.
- Adding a new sub-agent pipeline (cost, security deep-dive)
  reuses the skill activation pattern.

**Negative**
- Two sources of truth for "what does the agent do here?" — the
  prompt and the skill. The orchestrator's system prompt has to
  remind the sub-agent to activate the skill explicitly. If the
  reminder is dropped, the sub-agent ignores the skill and
  produces a malformed report.
- Skills aren't auto-discovered as code — they're plain markdown
  files referenced by name. A typo in the skill name silently
  loses the format.
- The original `eks-operations-review` skill (intended for the
  main agent to drive a review itself) was kept around as
  vestigial documentation for a year. It's now removed (#22 in
  the deep review), and the system prompt no longer mentions it.

**Neutral**
- `tests/fixtures/sample_*_report.md` lock the format the parsers
  expect. If a skill change makes the LLM produce a different
  shape, the parser tests fail immediately.

## References

- `skills/eks-report-compiler/SKILL.md` — assessment report rules.
- `skills/eks-upgrade-readiness/SKILL.md` — upgrade report rules.
- `eks_review_agent/orchestration/review_orchestrator.py::_get_report_system_prompt`
  — short prompt that activates the skill.
- `tests/test_golden_reports.py` — parser tests against fixtures.
