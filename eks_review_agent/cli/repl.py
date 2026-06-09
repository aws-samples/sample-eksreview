"""Interactive REPL — slash command dispatch + free-text agent turns.

main.py wires up the agent and MCP client, then hands control to
conversation_loop(). Every slash command lives in its own module under
this package; this file is just the dispatcher.
"""

from __future__ import annotations

import logging
import time as _time

from eks_review_agent.core.callbacks import reset_seen_tools, start_thinking
from eks_review_agent.cli.context_cmd import handle_context
from eks_review_agent.cli.export_cmd import handle_export
from eks_review_agent.cli.fix_cmd import handle_fix
from eks_review_agent.cli.investigate_cmd import handle_investigate
from eks_review_agent.cli.knowledge_cmd import handle_knowledge
from eks_review_agent.cli.model_cmd import handle_model
from eks_review_agent.cli.readline_setup import setup_readline
from eks_review_agent.cli.tools_cmd import handle_tools
from eks_review_agent.cli.upgrade_cmd import handle_upgrade
from eks_review_agent.knowledge.knowledge_tool import get_knowledge_base
from eks_review_agent.core.prompts import HELP_TEXT
from eks_review_agent.knowledge.skill_manager import handle_skill_command

logger = logging.getLogger("eksreview")


def _is_cmd(cmd: str, name: str) -> bool:
    """True if `cmd` is exactly `name` or starts with `name + " "`.

    Prevents over-broad prefix matches like /upgradeoo, /fixxer.
    """
    return cmd == name or cmd.startswith(name + " ")


def _print_session_stats(agent) -> None:
    """Print the per-session tool metrics block at /exit."""
    metrics = agent.state.get("tool_metrics") or {}
    if metrics.get("total_calls", 0) > 0:
        print(
            f"\n  Session stats: {metrics['total_calls']} tool calls, "
            f"{metrics.get('total_errors', 0)} errors, "
            f"{metrics.get('total_time_s', 0):.1f}s tool time"
        )
    print("  Thanks for using eksreview. Happy clustering!")


def conversation_loop(agent, obs_plugin, skills_plugin, builtin_names) -> None:
    """Run the interactive conversation REPL.

    All commands use the / prefix (q-cli style). Plain text goes to
    the agent. The dispatch order below is significant: the more
    specific commands (/skill, /knowledge) must come before the more
    general default agent-turn handler.
    """
    setup_readline()
    kb = get_knowledge_base()

    while True:
        try:
            user_input = input("› ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n  Thanks for using eksreview. Happy clustering!")
            break

        if not user_input:
            continue

        cmd = user_input.lower()
        log_cmd = cmd.split()[0] if cmd.startswith("/") else "prompt"
        logger.info("command=%s | input_len=%d", log_cmd, len(user_input))

        # ── Slash commands ────────────────────────────────────────

        if cmd in ("/exit", "/quit", "exit", "quit"):
            _print_session_stats(agent)
            break

        if cmd == "/help":
            print(HELP_TEXT)
            continue

        if cmd == "/context":
            print(handle_context(agent))
            continue

        if cmd == "/tools":
            print(handle_tools(agent))
            continue

        if _is_cmd(cmd, "/skill"):
            parts = user_input.split()
            print(handle_skill_command(parts, skills_plugin, builtin_names))
            continue

        if _is_cmd(cmd, "/knowledge"):
            print(handle_knowledge(user_input, kb))
            continue

        if _is_cmd(cmd, "/model"):
            print(handle_model(user_input, agent))
            continue

        if _is_cmd(cmd, "/upgrade"):
            handle_upgrade(user_input, agent, obs_plugin)
            continue

        if _is_cmd(cmd, "/investigate"):
            handle_investigate(user_input, agent, obs_plugin)
            continue

        if _is_cmd(cmd, "/fix"):
            handle_fix(user_input, agent, obs_plugin)
            continue

        if _is_cmd(cmd, "/export"):
            handle_export(user_input, agent)
            continue

        # ── Free-text agent turn (default) ────────────────────────

        reset_seen_tools()
        obs_plugin.reset()
        start_thinking()
        turn_start = _time.monotonic()

        try:
            result = agent(user_input)
            turn_elapsed = _time.monotonic() - turn_start
            logger.info(
                "turn_completed | elapsed=%.1fs | input_len=%d | stop_reason=%s",
                turn_elapsed, len(user_input),
                getattr(result, "stop_reason", "unknown"),
            )
            print("\n")
            if hasattr(result, "metrics") and result.metrics:
                logger.debug("Turn metrics: %s", result.metrics)

        except KeyboardInterrupt:
            logger.info(
                "turn_interrupted | elapsed=%.1fs",
                _time.monotonic() - turn_start,
            )
            print("\n  Interrupted. You can continue or type /exit.")
        except TypeError as e:
            # Strands occasionally surfaces an interrupt-state TypeError when
            # a tool confirmation was cancelled. Reset its internal flag and
            # let the user continue rather than crashing the REPL.
            if "interruptResponse" in str(e):
                logger.warning("Clearing Strands interrupt state after cancelled confirmation")
                if hasattr(agent, "_interrupt_state"):
                    agent._interrupt_state._activated = False
                print("\n  Previous command was cancelled. You can continue normally.")
            else:
                logger.exception(
                    "turn_failed | elapsed=%.1fs | error=%s",
                    _time.monotonic() - turn_start, e,
                )
                print(f"\n  error: {e}")
        except Exception as e:
            logger.exception(
                "turn_failed | elapsed=%.1fs | error=%s",
                _time.monotonic() - turn_start, e,
            )
            print(f"\n  error: {e}")
            print("  Try again or rephrase your question.")
