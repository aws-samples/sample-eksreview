"""/upgrade handler — parse args, build prompt, run agent turn."""

from __future__ import annotations

import logging

from eks_review_agent.cli._turn import run_agent_turn
from eks_review_agent.core.prompts import build_upgrade_prompt, detect_prompt_injection

logger = logging.getLogger("eksreview")


_AWS_REGION_PREFIXES = ("us-", "eu-", "ap-", "sa-", "ca-", "me-", "af-")


def handle_upgrade(user_input: str, agent, obs_plugin) -> None:
    """Parse `/upgrade <cluster> [region] [to <version>]` and dispatch.

    Recognized shapes (all case-insensitive on the keyword `to`):
        /upgrade eks-demo
        /upgrade eks-demo us-east-1
        /upgrade eks-demo to 1.33
        /upgrade eks-demo us-east-1 to 1.33
        /upgrade eks-demo 1.33                  (bare version)
    """
    upgrade_args = user_input[len("/upgrade"):].strip()
    parts = upgrade_args.split()
    if not parts:
        _print_usage()
        return

    if detect_prompt_injection(upgrade_args):
        logger.warning(
            "Blocked potential prompt injection in /upgrade: %s",
            upgrade_args[:100],
        )
        print("  Input rejected — contains disallowed patterns.")
        return

    cluster = parts[0]
    target_version = ""
    region = ""

    # Walk the remaining tokens. Order doesn't matter — we recognize each
    # token by its shape (region prefix, version number, "to <version>").
    remaining = parts[1:]
    for i, arg in enumerate(remaining):
        if arg.lower() == "to" and i + 1 < len(remaining):
            candidate = remaining[i + 1]
            if candidate and candidate[0].isdigit():
                target_version = candidate
        elif arg.startswith(_AWS_REGION_PREFIXES):
            region = arg
        elif arg and arg[0].isdigit() and "." in arg and not target_version:
            target_version = arg

    prompt = build_upgrade_prompt(cluster, target_version, region)
    run_agent_turn(agent, obs_plugin, prompt, label="upgrade")

    # Remember the cluster for follow-up /fix and /investigate calls.
    agent.state.set("last_reviewed_cluster", cluster)


def _print_usage() -> None:
    print("  Usage: /upgrade <cluster-name> [region] [to <version>]")
    print("  Example: /upgrade eks-demo")
    print("  Example: /upgrade eks-demo us-east-1")
    print("  Example: /upgrade eks-demo to 1.33")
    print("  Example: /upgrade eks-demo us-east-1 to 1.33")
