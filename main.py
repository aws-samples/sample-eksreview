#!/usr/bin/env python3
"""
eksreview — Operations Reviewer for Amazon EKS

A conversational Strands agent for EKS/Kubernetes operations review.

Usage:
    export EKS_MCP_SERVER_DIR=/path/to/your/mcp/server
    source .venv/bin/activate
    python main.py
    python main.py --session <session-id>   # Resume a previous session
    python main.py --no-shell               # Disable the shell tool (read-only mode)

Environment knobs:
    EKS_REVIEW_OFFLINE=1         Skip the EKS Best Practices PDF sync at startup.
    EKS_REVIEW_NO_SHELL=1        Same as --no-shell.
    MCP_RATE_LIMIT_SOFT=N        Warn after N MCP calls in a session (default 200).
    MCP_RATE_LIMIT_HARD=N        Refuse after N MCP calls in a session (default 500).
    MCP_RATE_LIMIT_BURST=N       Refuse if N calls in a sliding window (default 60).
    MCP_RATE_LIMIT_BURST_WINDOW  Burst window in seconds (default 60).

main.py owns startup and session lifecycle only:
  - Logging + config validation
  - CLI flag parsing
  - MCP client construction (with __enter__/__exit__ unwound by `with`)
  - Agent factory call
  - Banner + startup status lines
  - Hand-off to the REPL in eks_review_agent/cli/repl.py

Slash commands and the conversation loop live under eks_review_agent/cli/.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time as _time

# Enable ANSI escape codes on Windows
if os.name == "nt":
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass

from eks_review_agent.agent import create_agent
from eks_review_agent.cli.banner import (
    GREEN,
    RED,
    RESET,
    print_banner,
    print_startup_tip_box,
)
from eks_review_agent.cli.repl import conversation_loop
from eks_review_agent.config import BEDROCK_AWS_REGION, EKS_MCP_SERVER_DIR, MODEL_ID
from eks_review_agent.knowledge.knowledge_tool import get_knowledge_base
from eks_review_agent.ui.logging_config import setup_logging
from eks_review_agent.orchestration.mcp import create_mcp_client
from eks_review_agent.core.model import get_current_model_name


logger: logging.Logger = None  # type: ignore[assignment]


# ── CLI flag parsing ───────────────────────────────────────────────


def _parse_cli_flags() -> str | None:
    """Parse simple CLI flags. Returns the resumed session id or None."""
    session_id: str | None = None
    if "--session" in sys.argv:
        idx = sys.argv.index("--session")
        if idx + 1 < len(sys.argv):
            session_id = sys.argv[idx + 1]

    # Mode flags translate to env vars so child code paths see them
    # consistently without threading bool flags through every layer.
    if "--no-shell" in sys.argv:
        os.environ["EKS_REVIEW_NO_SHELL"] = "1"

    return session_id


# ── Startup status lines ───────────────────────────────────────────


def _print_status(agent, mcp_tools, skills_plugin, session_id: str | None) -> None:
    """Print the model/region/tools/skills/knowledge status block."""
    friendly_model = get_current_model_name() or MODEL_ID
    print(f"  Model:          {friendly_model}")
    print(f"  Model Region:   {BEDROCK_AWS_REGION}")
    print(
        f"  Tools:          {len(mcp_tools)} MCP + "
        f"{len(agent.tool_names) - len(mcp_tools)} built-in"
    )
    if os.environ.get("EKS_REVIEW_NO_SHELL", "").strip().lower() in (
        "1", "true", "yes",
    ):
        print("  Mode:           --no-shell (read-only, shell tool disabled)")

    skill_names = [s.name for s in skills_plugin.get_available_skills()]
    if skill_names:
        print(f"  Skills:         {', '.join(skill_names)}")
    else:
        print("  Skills:         none")

    # Auto-sync EKS Best Practices into the knowledge base
    kb = get_knowledge_base()
    try:
        from eks_review_agent.knowledge.builtin_knowledge import sync_eks_best_practices

        bp_status = sync_eks_best_practices(kb)
        print(f"  {GREEN}✓{RESET} {bp_status}")
    except Exception as e:
        logger.warning("EKS best practices sync failed: %s", e)
        print(f"  {RED}✗{RESET} eks-best-practices sync failed: {e}")

    kb_count = len(kb.entries)
    if kb_count > 0:
        total_chunks = sum(e.chunk_count for e in kb.entries.values())
        print(f"  Knowledge:      {kb_count} entries, {total_chunks} chunks")
    else:
        print("  Knowledge:      empty (/knowledge add to index files)")

    if session_id:
        print(f"  Session:        {session_id}")

    print()
    print_startup_tip_box()
    print()
    print("  Type your question or /help for commands.\n")


# ── Session bootstrap ──────────────────────────────────────────────


def _run_session(eks_mcp_client, mcp_start: float, session_id: str | None) -> None:
    """Run the full agent session after the MCP client is connected."""
    try:
        mcp_tools = eks_mcp_client.list_tools_sync()
        mcp_elapsed = _time.monotonic() - mcp_start
        logger.info("Discovered %d MCP tools", len(mcp_tools))
        print(f"  {GREEN}✓{RESET} eks-review-mcp-server loaded in {mcp_elapsed:.2f}s")
    except Exception as e:
        logger.error("Failed to discover MCP tools: %s", e)
        print(f"  {RED}✗{RESET} eks-review-mcp-server tool discovery failed")
        print(f"    {e}")
        return

    print()

    try:
        agent, skills_plugin, obs_plugin, builtin_names = create_agent(
            mcp_tools, eks_mcp_client, session_id,
        )
    except Exception as e:
        logger.error("Failed to create agent: %s", e)
        print(f"  error: Agent creation failed: {e}")
        return

    _print_status(agent, mcp_tools, skills_plugin, session_id)
    conversation_loop(agent, obs_plugin, skills_plugin, builtin_names)


# ── Entrypoint ─────────────────────────────────────────────────────


def main() -> None:
    global logger
    logger = setup_logging()

    from eks_review_agent.config import validate_config
    validate_config()

    session_id = _parse_cli_flags()

    print_banner()

    mcp_start = _time.monotonic()
    try:
        eks_mcp_client = create_mcp_client()
    except FileNotFoundError as e:
        print(f"  {RED}✗{RESET} MCP server directory not found\n")
        print(f"    {e}\n")
        print("    Set the environment variable before starting:")
        print("    export EKS_MCP_SERVER_DIR=/path/to/mcp-server")
        sys.exit(1)

    # MCPClient as a context manager — __enter__ + __exit__ stay paired,
    # subprocess always torn down even if the session crashes.
    try:
        with eks_mcp_client as mcp:
            _run_session(mcp, mcp_start, session_id)
    except FileNotFoundError:
        raise
    except Exception as e:
        logger.error("MCP server connection error: %s", e)
        print(f"  {RED}✗{RESET} eks-review-mcp-server failed to load\n")
        print(f"    Error: {e}\n")
        print(f"    Current EKS_MCP_SERVER_DIR: {EKS_MCP_SERVER_DIR}")
        print("    Ensure the path is correct and 'uv' is installed.")
        print("    Example: export EKS_MCP_SERVER_DIR=/path/to/mcp-server")
        sys.exit(1)
    finally:
        logger.info("MCP client disconnected")


def handle_sigint(sig, frame):
    """SIGINT handler — kept thin so default KeyboardInterrupt-raising
    behavior unwinds cleanly through main()'s try/finally.
    """
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_sigint)
    main()
