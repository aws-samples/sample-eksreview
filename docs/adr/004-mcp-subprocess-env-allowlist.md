# ADR-004: Allowlist filter on MCP subprocess environment

## Status
Accepted (2026-05-23)

## Context

Originally `mcp.py` started the MCP server as
`{**os.environ, "FASTMCP_LOG_LEVEL": "ERROR"}`. The subprocess
inherited every variable in the parent shell — including
`BEDROCK_AWS_SECRET_ACCESS_KEY`, `GITHUB_TOKEN`, `OPENAI_API_KEY`,
and so on.

This was finding C2 in the security review. The MCP server doesn't
need any of those — it talks to EKS / EC2 / STS / K8s and uses
boto3 with the standard AWS credential chain. Passing through
unrelated secrets widens the blast radius if the server ever has a
vulnerability or gets repackaged.

We need to filter the env. The report's literal recommendation
("only `AWS_REGION`, `AWS_PROFILE`, `HOME`, `PATH`, `HTTPS_PROXY`,
`HTTP_PROXY`, `NO_PROXY`") would break every credential method
except `~/.aws/credentials` — static keys, SSO, IRSA, container
roles all need additional vars.

## Decision

Replace `_filter_env_for_mcp` in `mcp.py` with a hybrid policy:

- **Allow** all `AWS_*` env vars by prefix (covers boto3's full
  credential chain without enumerating every var; new ones AWS
  adds in the future just work).
- **Allow** an explicit small list of system + proxy + TLS +
  runtime vars: `HOME`, `PATH`, `USER`, `SHELL`, locale, `TZ`,
  `TMPDIR`, `HTTP_PROXY` family, `REQUESTS_CA_BUNDLE`,
  `SSL_CERT_*`, `CURL_CA_BUNDLE`, `PYTHONPATH`,
  `PYTHONUNBUFFERED`, `UV_*`, `VIRTUAL_ENV`.
- **Deny** `BEDROCK_AWS_*` (Bedrock creds belong to the agent
  process; the subprocess never calls Bedrock).
- **Deny** agent-only config (`MODEL_ID`, `LOG_LEVEL`,
  `EKS_MCP_SERVER_DIR`, `EKS_REVIEW_OFFLINE`, knowledge /
  conversation tunables).
- Drop everything else.

Always set `FASTMCP_LOG_LEVEL=ERROR` for cleaner stderr.

## Alternatives considered

- **Pure denylist (drop only `BEDROCK_AWS_*`).** Simpler, but
  leaks every other unrelated secret in the user's shell. We'd
  rather fail closed.

- **Pure allowlist (only the explicit set).** The report's
  literal recommendation. Breaks IRSA, SSO, static keys.
  Rejected because it's correct only for one auth method.

- **No filter, document the risk.** Defeats the purpose of the
  finding.

## Consequences

**Positive**
- Bedrock credentials cannot reach the MCP subprocess.
- Third-party secrets in the shell (GitHub, OpenAI, etc.) are
  stripped automatically.
- Every boto3 credential method works without per-method
  configuration.
- The subprocess env is small and predictable, which makes
  debugging credential-resolution issues much easier (and the
  filter logs the count of allowed vs filtered vars at startup).

**Negative**
- New AWS env vars or runtime tools may need to be added to the
  allowlist over time. Trivial maintenance, but real.
- Users who put non-`AWS_*` env into `~/.bashrc` and expect the
  subprocess to inherit it may be surprised. Documented in the
  README's env-var section.

**Neutral**
- The set of allowed vars is testable in isolation — see
  `tests/test_mcp.py` for the matrix.

## References

- C2 finding in the security review.
- `eks_review_agent/orchestration/mcp.py::_filter_env_for_mcp` — implementation.
- `tests/test_mcp.py` — covers credential method, proxy, CA
  bundle, and denylist scenarios.
