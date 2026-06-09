"""Centralized configuration loaded from environment variables with sensible defaults.

All values are read lazily or at import time without side effects.
Directory creation and validation happen in validate_config().
"""

import logging
import os
from pathlib import Path

_log = logging.getLogger("eksreview")


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        _log.warning("Invalid value for %s: %r, using default %s", key, raw, default)
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        _log.warning("Invalid value for %s: %r, using default %s", key, raw, default)
        return default


# ── MCP Server ──────────────────────────────────
# Auto-detect mcp-server/ relative to the project root.
# Only use EKS_MCP_SERVER_DIR env var as an override (for development).
_default_mcp_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp-server")
_env_mcp_dir = os.environ.get("EKS_MCP_SERVER_DIR", "")
EKS_MCP_SERVER_DIR: str = _env_mcp_dir if _env_mcp_dir else _default_mcp_dir

# ── AWS / Model ─────────────────────────────────
# Region resolution: AWS_REGION > AWS_DEFAULT_REGION > fallback to us-east-1
# boto3 with region_name=None probes EC2 IMDS which hangs on local machines.
AWS_REGION: str = (
    os.environ.get("AWS_REGION")
    or os.environ.get("AWS_DEFAULT_REGION")
    or "us-east-1"
)
MODEL_ID: str = os.environ.get("MODEL_ID", "us.anthropic.claude-opus-4-8")
MODEL_TEMPERATURE: float = _env_float("MODEL_TEMPERATURE", 0.1)
MODEL_MAX_TOKENS: int = _env_int("MODEL_MAX_TOKENS", 128000)

# ── Bedrock credentials (optional, separate from EKS account) ──
# Bedrock can run in a different account and region than the EKS cluster.
# BEDROCK_AWS_REGION defaults to AWS_REGION if set, otherwise boto3 resolves it.
BEDROCK_AWS_ACCESS_KEY_ID: str | None = os.environ.get("BEDROCK_AWS_ACCESS_KEY_ID")
BEDROCK_AWS_SECRET_ACCESS_KEY: str | None = os.environ.get("BEDROCK_AWS_SECRET_ACCESS_KEY")
BEDROCK_AWS_SESSION_TOKEN: str | None = os.environ.get("BEDROCK_AWS_SESSION_TOKEN")
BEDROCK_AWS_REGION: str = os.environ.get("BEDROCK_AWS_REGION") or AWS_REGION

# ── Paths ───────────────────────────────────────
REPORTS_DIR: Path = Path(os.environ.get("REPORTS_DIR", "reports"))
SKILLS_DIR: str = os.environ.get("SKILLS_DIR", "./skills/")
SESSIONS_DIR: str = os.environ.get("SESSIONS_DIR", ".sessions")

# ── Knowledge Base ──────────────────────────────
KNOWLEDGE_DIR: Path = Path(os.environ.get("KNOWLEDGE_DIR", ".knowledge"))
KNOWLEDGE_CHUNK_SIZE: int = _env_int("KNOWLEDGE_CHUNK_SIZE", 2048)
KNOWLEDGE_CHUNK_OVERLAP: int = _env_int("KNOWLEDGE_CHUNK_OVERLAP", 256)
KNOWLEDGE_MAX_FILES: int = _env_int("KNOWLEDGE_MAX_FILES", 10000)

# ── Conversation ────────────────────────────────
CONVERSATION_SUMMARY_RATIO: float = _env_float("CONVERSATION_SUMMARY_RATIO", 0.4)
CONVERSATION_PRESERVE_MESSAGES: int = _env_int("CONVERSATION_PRESERVE_MESSAGES", 10)
# Sliding-window manager: keep this many recent messages and truncate older
# tool results in place. The agent recovers full data via report_search and
# file_read on saved reports if needed.
CONVERSATION_WINDOW_SIZE: int = _env_int("CONVERSATION_WINDOW_SIZE", 50)

# ── Logging ─────────────────────────────────────
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "WARNING")


def validate_config() -> None:
    """Validate required config and create directories.

    Call this once at startup (in main.py), not at import time.
    Exits with an error message if required config is missing.

    Hardens permissions on data directories that may contain sensitive
    cluster information (reports, sessions, knowledge base).
    """
    import sys

    if not EKS_MCP_SERVER_DIR or not os.path.isdir(EKS_MCP_SERVER_DIR):
        print(
            "  error: MCP server directory not found.\n"
            f"  Checked: {EKS_MCP_SERVER_DIR}\n"
            "  Set EKS_MCP_SERVER_DIR to the path of your EKS review MCP server.\n"
            "  Example: export EKS_MCP_SERVER_DIR=/path/to/mcp-server"
        )
        sys.exit(1)

    REPORTS_DIR.mkdir(exist_ok=True)
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    Path(SESSIONS_DIR).mkdir(parents=True, exist_ok=True)

    # Restrict directories to owner-only (0o700) on POSIX systems.
    # Reports, sessions, and the knowledge base contain cluster security
    # posture, IAM ARNs, and indexed user content that should not be
    # readable by other local users on shared machines. No-op on Windows.
    if os.name == "posix":
        for d in (REPORTS_DIR, KNOWLEDGE_DIR, Path(SESSIONS_DIR)):
            try:
                os.chmod(d, 0o700)
            except OSError as e:
                _log.warning("Could not chmod 0700 on %s: %s", d, e)
