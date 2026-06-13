# Environment Variables

eksreview reads configuration from environment variables. Defaults are sensible; most users only need AWS credentials and a region.

| Variable | Default | Description |
|---|---|---|
| `AWS_REGION` / `AWS_DEFAULT_REGION` | `us-east-1` | Region for EKS/EC2 API calls |
| `MODEL_ID` | global Claude Opus profile | Bedrock model override. By default the agent uses **global** cross-region inference profiles (`global.` prefix). Set this to a regional system inference profile (e.g. `us.anthropic.claude-sonnet-4-6`) to pin a geography; `/model` switches then stay in that same region. See [Models & Regions](models.md). |
| `MODEL_TEMPERATURE` | `0.1` | Sampling temperature (ignored by models that don't support it) |
| `MODEL_MAX_TOKENS` | `128000` | Max output tokens (capped per model) |
| `BEDROCK_AWS_REGION` | Same as `AWS_REGION` | Region for Bedrock, if different from the cluster's. See [Models & Regions](models.md). |
| `BEDROCK_AWS_ACCESS_KEY_ID` | — | Cross-account credentials for Bedrock |
| `BEDROCK_AWS_SECRET_ACCESS_KEY` | — | Cross-account credentials for Bedrock |
| `BEDROCK_AWS_SESSION_TOKEN` | — | Cross-account session token for Bedrock |
| `AWS_BEARER_TOKEN_BEDROCK` | — | Bedrock API key (short- or long-term); takes precedence over `BEDROCK_AWS_*` for Bedrock calls |
| `EKS_MCP_SERVER_DIR` | bundled `./mcp-server` | Override the MCP server path (dev only) |
| `EKS_REVIEW_NO_SHELL` | — | Set to `1` to disable command execution (same as `--no-shell`) |
| `EKS_REVIEW_OFFLINE` | — | Set to `1` to skip the EKS Best Practices PDF sync at startup |
| `LOG_LEVEL` | `WARNING` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `REPORTS_DIR` | `reports` | Where reports are written |
| `KNOWLEDGE_DIR` | `.knowledge` | Knowledge base storage |
| `SESSIONS_DIR` | `.sessions` | Conversation session storage |
| `MCP_RATE_LIMIT_SOFT` | `200` | Warn after this many MCP calls in a session |
| `MCP_RATE_LIMIT_HARD` | `500` | Refuse further MCP calls after this many in a session |
| `MCP_RATE_LIMIT_BURST` | `60` | Max MCP calls allowed within the burst window |
| `MCP_RATE_LIMIT_BURST_WINDOW` | `60` | Burst window, in seconds |
| `SKILLS_DIR` | `./skills/` | Directory the agent loads review/report skills from |
| `KNOWLEDGE_CHUNK_SIZE` | `2048` | Knowledge base chunk size, in characters |
| `KNOWLEDGE_CHUNK_OVERLAP` | `256` | Overlap between adjacent knowledge chunks |
| `KNOWLEDGE_MAX_FILES` | `10000` | Maximum files indexed per knowledge entry |
| `CONVERSATION_SUMMARY_RATIO` | `0.4` | Advanced: sliding-window conversation summary ratio |
| `CONVERSATION_PRESERVE_MESSAGES` | `10` | Advanced: recent messages always kept verbatim |
| `CONVERSATION_WINDOW_SIZE` | `50` | Advanced: recent messages kept in the conversation window |

!!! note
    On POSIX systems the `reports/`, `.knowledge/`, and `.sessions/` directories are created with owner-only (`0700`) permissions because they can contain cluster security posture and IAM details.

## Corporate networks (TLS-inspecting proxies)

If you sit behind an HTTPS-inspecting proxy that re-signs traffic with an internal CA:

```bash
export HTTPS_PROXY=http://corp-proxy.internal:3128
export NO_PROXY=169.254.169.254,localhost
export AWS_CA_BUNDLE=/etc/ssl/certs/corp-ca.pem
export REQUESTS_CA_BUNDLE=/etc/ssl/certs/corp-ca.pem
export SSL_CERT_FILE=/etc/ssl/certs/corp-ca.pem
```

Both the agent and the MCP subprocess honor these.

---

**Related:** [Models & Regions](models.md) · [Credentials & Cross-Account](credentials.md) · [Command-line Flags](cli-flags.md)
