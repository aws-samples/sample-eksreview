"""Review orchestrator — runs the full EKS review pipeline.

Owns the entire flow:
  1. collect_check_results() — executes MCP tools, returns raw JSON
  2. Report sub-agent — compiles + saves the markdown report
  3. _extract_summary_from_report() — pulls executive summary from saved report
  4. Returns the extracted summary to the main agent (~200 tokens)

The main agent never sees raw MCP JSON. Only the executive summary
from the saved report enters its context.

Sub-agent lifecycle and Bedrock model construction live in
subagent_pipeline.py — this module just configures the pipeline.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from strands_tools import file_read, file_write

from eks_review_agent.knowledge.knowledge_tool import knowledge_search
from eks_review_agent.orchestration.mcp_checks import ALL_DOMAINS, collect_check_results
from eks_review_agent.reports.report_search import find_latest_report
from eks_review_agent.orchestration.subagent_pipeline import (
    SubAgentPipelineConfig,
    create_subagent_model,
    get_subagent_usage,
    run_subagent_pipeline,
)
from eks_review_agent.tools import save_report

logger = logging.getLogger("eksreview")

__all__ = [
    "run_review",
    "ALL_DOMAINS",
    # Re-exports for backwards compatibility — callers in main.py and
    # observability test fixtures import these names from here.
    "get_subagent_usage",
    "_create_report_model",
    "_accumulate_subagent_usage",
    "_extract_summary_from_report",
]


# ── Backwards-compatible aliases ───────────────────────────────────


# Anyone who imported _create_report_model from this module historically
# now gets the shared subagent_pipeline implementation.
_create_report_model = create_subagent_model


def _accumulate_subagent_usage(agent):
    """Backwards-compat shim for the few external callers (tests)."""
    from eks_review_agent.orchestration.subagent_pipeline import (
        _accumulate_subagent_usage as _impl,
    )
    return _impl(agent)


# ── Summary extraction from saved report ───────────────────────────


def _extract_summary_from_report(report_path: str) -> str | None:
    """Extract the Executive Summary section from a saved report file.

    Reads the report and pulls everything from "## Executive Summary"
    up to the next "---" or "## Check Results" boundary. This gives the
    main agent the domain breakdown, priority classification, and key
    recommendations — without the full check table or detailed findings.

    Returns None if the file can't be read or the section isn't found.
    """
    try:
        content = Path(report_path).read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("Could not read report %s: %s", report_path, e)
        return None

    match = re.search(
        r"(## Executive Summary.*?)(?=\n---|\n## Check Results)",
        content,
        re.DOTALL,
    )
    if not match:
        return None
    return match.group(1).strip()


# ── Sub-agent system prompt ────────────────────────────────────────


def _get_report_system_prompt() -> str:
    """Build the system prompt for the ephemeral report sub-agent."""
    today = date.today().isoformat()

    return f"""\
You are an EKS report compiler. Today's date is {today}.

<context>
You will receive raw MCP check results and review history as your input.
The data is already collected — you do not need to gather any additional information.
</context>

<workflow>
1. Activate the 'eks-report-compiler' skill to load the report format and rules.
2. Call `save_report` with the complete report as the report_content argument.
   Pass the report directly to save_report — do not output it as text first.
3. After saving, respond with only the file path.
</workflow>

<constraints>
- Do not call run_full_review, get_review_history, or any data-gathering tools.
- Do not write the report as text output. Pass it directly to save_report.
- The skill workflow section references tools you do not have — use the skill
  only for its report format template, field mappings, and rules.
</constraints>
"""


# ── Pipeline configuration ─────────────────────────────────────────


def _build_pipeline_config(
    cluster_name: str, raw_results: str, review_history: str
) -> SubAgentPipelineConfig:
    """Build the SubAgentPipelineConfig for a single review run."""
    user_prompt = (
        f"Compile a full assessment report for cluster '{cluster_name}' "
        f"from these review results:\n\n"
        f"## Review History\n{review_history}\n\n"
        f"## Current Review Results\n{raw_results}"
    )
    return SubAgentPipelineConfig(
        name="review",
        spinner_message="Compiling report",
        system_prompt=_get_report_system_prompt(),
        tools=[save_report, knowledge_search, file_write, file_read],
        user_prompt=user_prompt,
        find_report=find_latest_report,
    )


# ── Error guidance ─────────────────────────────────────────────────


def _error_hint(error_text: str, cluster_name: str, region: str | None) -> str:
    """Map a check error to actionable guidance for the user."""
    lower = error_text.lower()
    if "credential" in lower or "unable to locate credentials" in lower:
        return (
            "This is an AWS credentials problem. Ask the user to configure "
            "credentials (e.g. `aws configure`, set AWS_PROFILE, or export "
            "AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY) and retry."
        )
    if "region" in lower:
        return (
            "The AWS region is missing or incorrect. Ask the user which region "
            f"'{cluster_name}' runs in and retry with that region "
            "(e.g. us-east-1, us-west-2)."
        )
    if "resourcenotfound" in lower or "no cluster found" in lower or "not found" in lower:
        reg = region or "the specified region"
        return (
            f"Cluster '{cluster_name}' was not found in {reg}. Verify the cluster "
            "name and region. List clusters with: "
            f"aws eks list-clusters --region {region or '<region>'}"
        )
    if "accessdenied" in lower or "not authorized" in lower or "forbidden" in lower:
        return (
            "The credentials lack permission to read the cluster. Ensure the IAM "
            "principal has EKS/EC2 describe permissions and is mapped in the "
            "cluster's access entries (aws-auth)."
        )
    return (
        "Verify cluster connectivity: confirm the cluster name and region are "
        "correct, AWS credentials are valid, and kubectl access is configured "
        f"(aws eks update-kubeconfig --name {cluster_name}"
        f"{f' --region {region}' if region else ''})."
    )


# ── Public API ─────────────────────────────────────────────────────


def run_review(
    cluster_name: str,
    mcp_client,
    region: str | None = None,
    domains: list[str] | None = None,
) -> str:
    """Run the full EKS review pipeline.

    Steps:
      1. Look up the review history (pure Python, no LLM).
      2. Run MCP checks via collect_check_results (no LLM).
      3. Spin up the report sub-agent to compile and save the markdown.
      4. Extract the executive summary from the saved report.
      5. Return the report path + summary to the main agent (~200 tokens).

    Args:
        cluster_name: The EKS cluster name.
        mcp_client: Connected MCP client for running checks.
        region: Optional AWS region.
        domains: Optional list of domain names. None = all domains.

    Returns:
        Report path + executive summary for the main agent's context, or
        an instructive error string if any pipeline step failed.
    """
    # Step 1: Review history (best-effort — never fail the pipeline on this)
    from eks_review_agent.tools import get_review_history as _get_history
    logger.info("Checking review history for cluster: %s", cluster_name)
    print("  Checking review history...", flush=True)
    try:
        history = _get_history._tool_func(cluster_name=cluster_name)
    except Exception:
        history = "No previous reports found."
    logger.info("Review history preview: %s", history[:100])

    # Step 2: MCP checks
    logger.info("Collecting check results for cluster: %s", cluster_name)
    check_data = collect_check_results(
        mcp_client,
        cluster_name,
        region=region,
        domains=domains,
    )
    raw_results = check_data["text"]

    # Abort early if every domain check errored out. This avoids handing
    # the report sub-agent a payload of error blobs (which it would then
    # either flag as UNKNOWN or, worse, mislabel as PASS). Surface the
    # real cause to the user instead of compiling a meaningless report.
    if check_data["total"] > 0 and check_data["ok_count"] == 0:
        sample_error = next(iter(check_data["errors"].values()), "unknown error")
        logger.error(
            "All %d checks failed for %s. Sample error: %s",
            check_data["total"], cluster_name, sample_error,
        )
        hint = _error_hint(sample_error, cluster_name, region)
        return (
            f"The review could not run — all {check_data['total']} domain checks "
            f"failed for cluster '{cluster_name}'. No report was generated.\n\n"
            f"Cause: {sample_error}\n\n"
            f"{hint}\n\n"
            "Tell the user the review did not run and why. Do not claim any "
            "checks passed and do not fabricate findings."
        )

    # Step 3: Sub-agent compiles + saves
    config = _build_pipeline_config(cluster_name, raw_results, history)
    report_path = run_subagent_pipeline(cluster_name, config)

    if report_path is None:
        return (
            f"Review checks completed for '{cluster_name}' but report "
            "compilation failed. The raw check data was collected "
            "successfully across all domains. Tell the user the review "
            "checks passed but the report could not be generated. "
            f"Suggest they retry with: run_full_review(cluster_name=\"{cluster_name}\")\n"
            "Do NOT search the filesystem or call file_read to look for results."
        )

    # Step 4: Pull the executive summary
    exec_summary = _extract_summary_from_report(report_path)

    if exec_summary:
        return (
            f"Report saved to: {report_path}\n\n"
            f"{exec_summary}\n\n"
            "Use `report_search` to look up specific findings or remediation details.\n"
            "Use /fix, /investigate, or /export for follow-up actions."
        )

    # Fallback if summary extraction failed (shouldn't happen on a valid report)
    return (
        f"Report saved to: {report_path}\n\n"
        "Use `report_search` to look up findings and remediation details.\n"
        "Use /fix, /investigate, or /export for follow-up actions."
    )
