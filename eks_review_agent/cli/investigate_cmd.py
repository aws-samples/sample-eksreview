"""/investigate handler — load latest report if missing, then run agent turn."""

from __future__ import annotations

import logging

from eks_review_agent.cli._turn import run_agent_turn
from eks_review_agent.core.prompts import (
    build_investigate_prompt,
    detect_prompt_injection,
)

logger = logging.getLogger("eksreview")


def handle_investigate(user_input: str, agent, obs_plugin) -> None:
    """Investigate a finding from the most recent review.

    If no review has run in this session, loads the latest assessment
    report from disk and seeds the prompt with its contents so the
    investigation can proceed without re-running the review.
    """
    last_cluster = agent.state.get("last_reviewed_cluster")

    report_context = ""
    if not last_cluster:
        from eks_review_agent.config import REPORTS_DIR
        reports = sorted(REPORTS_DIR.glob("*-assessment-*.md"), reverse=True)
        if not reports:
            print("  No reports found. Run a cluster review first, then use /investigate.")
            return

        latest = reports[0]
        # Filename shape: <cluster>-assessment-YYYYMMDD_HHMMSS.md
        parts_name = latest.stem.rsplit("-assessment-", 1)
        last_cluster = parts_name[0] if parts_name else "unknown"
        report_context = latest.read_text(encoding="utf-8")
        agent.state.set("last_reviewed_cluster", last_cluster)
        print(f"  Loaded latest report: {latest.name}")

    finding_desc = user_input[len("/investigate"):].strip()
    if not finding_desc:
        _print_usage()
        return

    if detect_prompt_injection(finding_desc):
        logger.warning(
            "Blocked potential prompt injection in /investigate: %s",
            finding_desc[:100],
        )
        print("  Input rejected — contains disallowed patterns.")
        return

    prompt = build_investigate_prompt(last_cluster, finding_desc)

    # If we loaded a report from disk, prepend it as conversational
    # context so the agent doesn't need to call file_read.
    if report_context:
        prompt = (
            f"Here is the latest review report for cluster '{last_cluster}':\n\n"
            f"{report_context}\n\n---\n\n{prompt}"
        )

    run_agent_turn(agent, obs_plugin, prompt, label="investigate")


def _print_usage() -> None:
    print("  Usage: /investigate <description of the finding to investigate>")
    print("  Example: /investigate subnet IP exhaustion")
    print("  Example: /investigate why are pods running as root")
    print("  Example: /investigate cluster autoscaler version mismatch")
