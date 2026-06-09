# ADR-010: Process-global rate limit, not per-cluster or per-user

## Status
Accepted (2026-05-23)

## Context

The agent can make MCP tool calls that hit AWS APIs (EKS, EC2, STS)
and the K8s API. Two failure modes worth defending against:

1. **Runaway LLM loop.** A confused agent gets stuck repeatedly
   calling the same MCP tool. The user notices the spinner spinning
   forever; behind the scenes we burn through API quota and Bedrock
   tokens.
2. **Malicious prompt.** A prompt-injected agent intentionally
   spams MCP calls.

We need a tripwire. The question is what scope it covers — per
cluster, per user, per session, or per process.

## Decision

Process-global rate limiter. One `MCPRateLimiter` singleton in
`rate_limiter.py`. Three thresholds:

| Limit | Default | Behavior |
|-------|---------|----------|
| Soft  | 200 calls | One-shot warning |
| Hard  | 500 calls | Refuse further calls |
| Burst | 60 calls in 60s sliding window | Refuse |

Limits apply to all MCP-touching code paths: `mcp_checks`,
`upgrade_orchestrator`, and direct agent-side MCP tool calls
intercepted in `observability.on_before_tool`.

Refusals from the observability hook use Strands'
`event.cancel_tool` API rather than raising, so the LLM sees a
structured tool error rather than crashing the agent loop (verified
in ADR-005 / finding #18).

## Alternatives considered

- **Per-cluster limit.** More granular — a user reviewing five
  clusters in one session wouldn't share a budget. Rejected
  because we have one process per session today, and the runaway
  threat is per-process. If we ever go multi-tenant, this ADR
  would be revisited.

- **Per-user limit.** Same scope as per-cluster for our current
  deployment model (one user per process).

- **No limit, document the risk.** Defeats the tripwire purpose.

- **Tighter defaults.** The original implementation had soft=100,
  hard=200, burst=30. That tripped on heavy interactive use
  (10+ reviews back-to-back). Loosened to current values when we
  realized the limits were hitting legitimate use, not attacks.

## Consequences

**Positive**
- Real-world overhead is negligible — microseconds per call.
- Defaults are loose enough that normal interactive sessions
  never see them. Tunable via `MCP_RATE_LIMIT_*` env vars when
  needed.
- All three call sites share one counter, so a runaway loop in
  any path triggers the limit.
- The burst guard catches tight loops independently of the
  long-session soft/hard limits.

**Negative**
- Process-global means resetting requires restarting. The
  message tells the user this.
- A truly determined attacker who spaces out calls below the
  burst window can still hit the hard cap eventually. The
  alternative — no cap — is worse.
- Limits don't apply to the LLM's Bedrock calls themselves, only
  to MCP. Bedrock has its own quotas; this isn't the layer to
  defend cost.

**Neutral**
- The limiter is a `threading.Lock`-protected singleton. If we
  ever go multiprocess, this ADR needs revisiting.
- Sub-agents share the limiter automatically because they live
  in the same Python process. Documented in `rate_limiter.py`'s
  module docstring.

## References

- M2 finding in the security review.
- `eks_review_agent/core/rate_limiter.py` — implementation.
- `eks_review_agent/core/observability.py::on_before_tool` —
  Strands hook integration with `event.cancel_tool`.
- ADR-005 — layered safety stack (rate limit is layer-adjacent).
