"""/fix handler — guided remediation flow."""

from __future__ import annotations

import logging

from eks_review_agent.cli._turn import run_agent_turn
from eks_review_agent.core.prompts import build_fix_prompt, detect_prompt_injection

logger = logging.getLogger("eksreview")


def handle_fix(user_input: str, agent, obs_plugin) -> None:
    """Run a guided fix flow against the most recent reviewed cluster.

    Requires that a review has run in this session — without an
    in-memory cluster name, we don't have a target for the fix. Tells
    the user to run a review first.
    """
    last_cluster = agent.state.get("last_reviewed_cluster")
    if not last_cluster:
        print("  No review has been run in this session.")
        print("  Run a cluster review first, then use /fix to remediate findings.")
        return

    fix_desc = user_input[len("/fix"):].strip()
    if not fix_desc:
        _print_usage()
        return

    if detect_prompt_injection(fix_desc):
        logger.warning(
            "Blocked potential prompt injection in /fix: %s", fix_desc[:100]
        )
        print("  Input rejected — contains disallowed patterns.")
        return

    prompt = build_fix_prompt(last_cluster, fix_desc)
    run_agent_turn(agent, obs_plugin, prompt, label="fix")


def _print_usage() -> None:
    print("  Usage: /fix <description of the issue to fix>")
    print("  Example: /fix resolve the cluster endpoint access issue")
    print("  Example: /fix add health probes to nginx deployment")
