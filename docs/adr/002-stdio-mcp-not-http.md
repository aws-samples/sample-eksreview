# ADR-002: stdio MCP transport instead of HTTP

## Status
Accepted (2026-05-23)

## Context

The bundled MCP server (`mcp-server/`) exposes EKS review tools to
the agent. MCP supports two transports: stdio (subprocess + pipes)
and HTTP (`streamable-http`). We need to choose the one the agent
uses and document the tradeoffs of the other.

Originally the codebase shipped a `server_devops.py` HTTP variant
that bound to `0.0.0.0` without authentication — flagged as
finding C1 in the security review and removed.

## Decision

Use stdio. The agent launches the MCP server as a subprocess via
`uv --directory mcp-server run awslabs.eks-review-mcp-server` and
communicates over the resulting pipes. The HTTP variant is not
shipped.

## Alternatives considered

- **HTTP transport on `127.0.0.1`.** Would let multiple agents
  share one server, and would let an external orchestrator drive
  the MCP. Rejected because no current consumer needs that, and
  the failure mode (forgetting to bind localhost-only and
  shipping `0.0.0.0`) is the issue C1 was raised for.

- **HTTP with authentication.** Requires designing and rotating
  an API key or mTLS, plus a network listener. Adds operational
  surface for no current benefit.

- **Embed MCP code directly in the agent process.** Loses the
  process boundary that makes credential filtering possible
  (ADR-004).

## Consequences

**Positive**
- No network listener — nothing to expose accidentally.
- The agent fully controls the subprocess lifecycle: starts on
  agent boot, dies with the agent, env is filtered.
- One agent → one MCP server → one cluster context. Simple
  mental model.

**Negative**
- The MCP server can't be shared across multiple agents.
- Cold start adds ~1–2 seconds (subprocess launch + tool
  discovery).

**Neutral**
- If multi-tenant access ever becomes a real requirement, an
  HTTP front-end on `127.0.0.1` with a rotating API key is the
  defensible reintroduction. ADR-002 would be superseded.

## References

- `eks_review_agent/orchestration/mcp.py::create_mcp_client` — stdio launch.
- C1 finding in the security review — why HTTP variant was
  removed.
- `mcp-server/awslabs/eks_review_mcp_server/server.py` — stdio
  server entrypoint.
