"""MCP client setup for the EKS review server.

Builds the subprocess environment explicitly instead of inheriting the
parent's full env. The MCP subprocess only needs AWS credentials/config
(for EKS/EC2/STS calls) plus a small set of system + proxy vars. Bedrock
secrets, agent-only config, and unrelated host env (third-party API
tokens, GH tokens, etc.) are stripped to limit credential exposure.
"""

import atexit
import logging
import os
from typing import IO, Optional

from mcp import stdio_client, StdioServerParameters
from strands.tools.mcp import MCPClient

from eks_review_agent.config import EKS_MCP_SERVER_DIR

logger = logging.getLogger("eksreview")

# Lazily-opened devnull handle — created on first MCP client construction
# and closed at process exit. Avoids leaking a file descriptor when the
# module is imported but no MCP client is ever created (e.g. in tests
# that import config/utility helpers).
_devnull: Optional[IO[str]] = None


def _get_devnull() -> IO[str]:
    """Open the shared devnull handle on first use and register cleanup."""
    global _devnull
    if _devnull is None:
        _devnull = open(os.devnull, "w")
        atexit.register(_close_devnull)
    return _devnull


def _close_devnull() -> None:
    global _devnull
    if _devnull is not None:
        try:
            _devnull.close()
        except OSError:
            pass
        _devnull = None


# ── Subprocess env policy ───────────────────────────────────────────
# The MCP server talks to EKS/EC2/STS and the Kubernetes API. It does
# not call Bedrock and does not read agent-only configuration.

# Vars the subprocess never needs.
# - BEDROCK_AWS_*: Bedrock-only credentials, consumed by the agent process.
# - Agent-only config: model selection, log level, paths used by the CLI.
_DENIED_PREFIXES: tuple[str, ...] = ("BEDROCK_AWS_",)
_DENIED_KEYS: frozenset[str] = frozenset({
    "MODEL_ID", "MODEL_TEMPERATURE", "MODEL_MAX_TOKENS",
    "LOG_LEVEL",
    "EKS_MCP_SERVER_DIR",
    "EKS_REVIEW_OFFLINE",
    "REPORTS_DIR", "SKILLS_DIR", "SESSIONS_DIR", "KNOWLEDGE_DIR",
    "KNOWLEDGE_CHUNK_SIZE", "KNOWLEDGE_CHUNK_OVERLAP", "KNOWLEDGE_MAX_FILES",
    "CONVERSATION_SUMMARY_RATIO", "CONVERSATION_PRESERVE_MESSAGES",
})

# System and runtime vars the subprocess does need.
# AWS_* is allowed by prefix (covers boto3's full credential chain:
# static keys, profiles, SSO, web identity, container creds, CA bundle, etc.).
_SYSTEM_ALLOW: frozenset[str] = frozenset({
    # Identity / shell basics
    "HOME", "PATH", "USER", "LOGNAME", "SHELL",
    # Locale and time
    "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    # Temp directory resolution
    "TMPDIR", "TEMP", "TMP",
    # Corporate proxies
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy",
    # TLS bundle overrides (corp networks with TLS inspection)
    "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "CURL_CA_BUNDLE",
    # Python runtime
    "PYTHONPATH", "PYTHONUNBUFFERED", "PYTHONIOENCODING",
    # uv (used to launch the MCP server)
    "UV_CACHE_DIR", "UV_PROJECT_ENVIRONMENT", "UV_PYTHON",
    "VIRTUAL_ENV",
})


def _filter_env_for_mcp() -> dict[str, str]:
    """Build the env passed to the MCP subprocess.

    Allowlist policy:
      - All AWS_* vars (boto3 credential/config chain) EXCEPT _DENIED_PREFIXES.
      - The explicit _SYSTEM_ALLOW set (system, proxy, TLS, runtime).
    Everything else is dropped so unrelated secrets in the user's shell
    (GITHUB_TOKEN, OPENAI_API_KEY, etc.) never reach the subprocess.

    FASTMCP_LOG_LEVEL is always set to suppress noisy stderr.
    """
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _DENIED_KEYS:
            continue
        if any(key.startswith(prefix) for prefix in _DENIED_PREFIXES):
            continue
        if key.startswith("AWS_") or key in _SYSTEM_ALLOW:
            env[key] = value

    env["FASTMCP_LOG_LEVEL"] = "ERROR"
    return env


def create_mcp_client() -> MCPClient:
    """Create the MCP client that connects to the EKS review server.

    Validates the server directory exists before attempting connection.
    Passes a filtered env (see _filter_env_for_mcp) to the subprocess and
    suppresses MCP server stderr logs from the terminal.

    Raises:
        FileNotFoundError: If the MCP server directory doesn't exist.
    """
    if not os.path.isdir(EKS_MCP_SERVER_DIR):
        raise FileNotFoundError(
            f"MCP server directory not found: {EKS_MCP_SERVER_DIR}\n"
            f"Set EKS_MCP_SERVER_DIR to the correct path."
        )

    logger.info("MCP server directory: %s", EKS_MCP_SERVER_DIR)

    mcp_env = _filter_env_for_mcp()
    logger.info(
        "MCP subprocess env: %d vars allowed, %d filtered",
        len(mcp_env),
        len(os.environ) + 1 - len(mcp_env),  # +1 for FASTMCP_LOG_LEVEL we add
    )

    return MCPClient(
        lambda: stdio_client(
            StdioServerParameters(
                command="uv",
                args=[
                    "--directory",
                    EKS_MCP_SERVER_DIR,
                    "run",
                    "awslabs.eks-review-mcp-server",
                ],
                env=mcp_env,
            ),
            errlog=_get_devnull(),
        )
    )
