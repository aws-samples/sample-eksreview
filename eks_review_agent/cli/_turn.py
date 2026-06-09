"""Shared helper for running a single agent turn from a slash command.

Each agent-invoking command (/upgrade, /fix, /investigate, plus the
default free-text path) does the same setup/teardown around the actual
agent(prompt) call:

    reset_seen_tools() / obs_plugin.reset() / start_thinking()
    try: agent(prompt) / print blank line
    except KeyboardInterrupt: 'Interrupted'
    except Exception: log + 'error: ...'

Putting that here keeps the per-command modules focused on their own
prompt construction and makes the error-handling consistent.
"""

from __future__ import annotations

import logging

from eks_review_agent.core.callbacks import reset_seen_tools, start_thinking

logger = logging.getLogger("eksreview")


def run_agent_turn(agent, obs_plugin, prompt: str, *, label: str) -> None:
    """Run one agent turn with the standard setup/teardown.

    `label` is used as a log prefix so failed turns are easy to grep.
    All exceptions except KeyboardInterrupt are logged at exception
    level and surfaced as a one-line error to stdout. Returns nothing —
    the agent's streaming callbacks handle output.
    """
    reset_seen_tools()
    obs_plugin.reset()
    start_thinking()

    try:
        agent(prompt)
        print("\n")
    except KeyboardInterrupt:
        print("\n  Interrupted.")
    except Exception as e:
        logger.exception("%s error: %s", label, e)
        print(f"\n  error: {e}")
