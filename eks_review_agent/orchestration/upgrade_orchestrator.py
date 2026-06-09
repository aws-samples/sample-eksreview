"""Upgrade readiness orchestrator — runs the upgrade assessment pipeline.

Architecture mirrors review_orchestrator.py:
  1. Call MCP check_eks_upgrade_readiness directly (no LLM).
  2. Sub-agent (with shell + http_request + think)
     analyzes the raw results, verifies component compatibility against
     official sources, correlates findings, and saves a markdown report.
  3. Extract the verdict + Go/No-Go decision from the saved report.
  4. Return a compact summary to the main agent.

Sub-agent lifecycle and Bedrock model construction live in
subagent_pipeline.py — this module just configures the pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time as _time
import uuid
from datetime import date
from pathlib import Path

from strands_tools import file_read, file_write, http_request, shell

from eks_review_agent.knowledge.knowledge_tool import knowledge_search
from eks_review_agent.orchestration.subagent_pipeline import (
    SubAgentPipelineConfig,
    run_subagent_pipeline,
)
from eks_review_agent.tools import save_report, think

logger = logging.getLogger("eksreview")

__all__ = ["run_upgrade_check"]


# ── MCP tool call ──────────────────────────────────────────────────


def _call_upgrade_mcp(
    mcp_client,
    cluster_name: str,
    region: str | None = None,
    target_version: str | None = None,
) -> str:
    """Call check_eks_upgrade_readiness MCP tool and return raw result text."""
    if not region:
        raise ValueError(
            "Region is required for upgrade readiness check. Pass region explicitly."
        )

    kwargs: dict = {"cluster_name": cluster_name, "region": region}
    if target_version:
        kwargs["target_version"] = target_version

    tool_use_id = f"upgrade_{uuid.uuid4().hex[:8]}"
    logger.info("Calling MCP check_eks_upgrade_readiness with %s", kwargs)

    # Apply session-wide rate limiting before issuing the call.
    from eks_review_agent.core.rate_limiter import get_rate_limiter
    get_rate_limiter().check_and_increment("check_eks_upgrade_readiness")

    result = mcp_client.call_tool_sync(tool_use_id, "check_eks_upgrade_readiness", kwargs)

    # MCP-level error response
    if isinstance(result, dict) and result.get("isError"):
        error_text = ""
        for item in result.get("content", []):
            if isinstance(item, dict) and "text" in item:
                error_text += item["text"]
        raise RuntimeError(f"MCP tool returned error: {error_text or 'unknown error'}")

    # Extract text content
    parts: list[str] = []
    if isinstance(result, dict):
        sc = result.get("structuredContent")
        if sc:
            try:
                parts.append(json.dumps(sc, indent=2, default=str))
            except (TypeError, ValueError):
                parts.append(str(sc))
        for item in result.get("content", []):
            if isinstance(item, dict):
                if "text" in item:
                    parts.append(item["text"])
                if "json" in item:
                    try:
                        parts.append(json.dumps(item["json"], indent=2, default=str))
                    except (TypeError, ValueError):
                        parts.append(str(item["json"]))
    return "\n".join(parts) if parts else str(result)


# ── Summary extraction ────────────────────────────────────────────


def _find_latest_upgrade_report(cluster_name: str) -> Path | None:
    """Find the most recent upgrade readiness report for a cluster."""
    from eks_review_agent.config import REPORTS_DIR
    safe = cluster_name.replace("/", "-").replace(" ", "-").lower()
    pattern = f"*{safe}*-upgrade-readiness-*.md"
    reports = sorted(REPORTS_DIR.glob(pattern), reverse=True)
    return reports[0] if reports else None


def _extract_upgrade_summary(report_path: str) -> str | None:
    """Extract the verdict table + Go/No-Go Decision from a saved report."""
    try:
        content = Path(report_path).read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("Could not read upgrade report %s: %s", report_path, e)
        return None

    parts: list[str] = []

    # Verdict table
    table_match = re.search(
        r"(\| Field \| Value \|.*?\n(?:\|.*\n)+)",
        content,
        re.DOTALL,
    )
    if table_match:
        parts.append(table_match.group(1).strip())

    # Go/No-Go Decision
    go_match = re.search(
        r"(## Go / No-Go Decision.*?)(?=\n---|\n## Check Results)",
        content,
        re.DOTALL,
    )
    if go_match:
        parts.append(go_match.group(1).strip())

    return "\n\n".join(parts) if parts else None


# ── Sub-agent system prompt ───────────────────────────────────────


def _get_upgrade_system_prompt() -> str:
    """Build the system prompt for the ephemeral upgrade sub-agent."""
    today = date.today().isoformat()

    return f"""\
You are an EKS upgrade readiness analyst. Today's date is {today}.

<context>
You will receive compacted MCP upgrade readiness check results as input.
Short keys: id=check ID, n=check name, s=severity (C/H/M/L), d=details,
r=impacted resources, t=timing (b=before, a=after upgrade).
Passed checks: "U1:Check Name" strings. Blockers: severity C + timing b.
</context>

<workflow>
1. Activate the 'eks-upgrade-readiness' skill for report format and rules.
2. Use `think` to parse results — identify blockers, correlate related findings,
   note what data is missing for remediation.
3. Use `http_request` to verify compatibility for each detected component:
   - Karpenter: https://karpenter.sh/docs/upgrading/compatibility/
   - Istio: https://istio.io/latest/docs/releases/supported-releases/
   - AWS LB Controller: https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/deploy/installation/
   - Self-managed CoreDNS: https://github.com/coredns/coredns/releases
   Skip components not detected in the MCP output.
4. Use `think` to synthesize — determine upgrade step order, group related findings.
5. Call `save_report` with report_type="upgrade-readiness". Pass the complete report
   directly — do not output it as text first.
6. Respond with only the file path.
</workflow>

<constraints>
- Do not call check_eks_upgrade_readiness — the data is already provided.
- The shell tool prompts the user to confirm each command before it runs.
- Use real version numbers from the MCP data. Never write placeholder versions.
- Every fact must come from MCP output, http_request, or a shell command you ran.
  If data is missing, write "Not determined" with the command to get it.
</constraints>
"""


# ── Pipeline configuration ───────────────────────────────────────


def _build_pipeline_config(
    cluster_name: str, raw_results: str, region: str, target_version: str
) -> SubAgentPipelineConfig:
    """Build the SubAgentPipelineConfig for a single upgrade run."""
    region_info = f" in {region}" if region else ""
    version_info = f" to {target_version}" if target_version else ""
    user_prompt = (
        f"Analyze upgrade readiness for cluster '{cluster_name}'"
        f"{region_info}{version_info} "
        "and compile a full upgrade readiness report.\n\n"
        f"## MCP Upgrade Check Results\n{raw_results}"
    )

    # Honor --no-shell on the upgrade sub-agent so the flag is a hard
    # process-wide guarantee.
    no_shell = os.environ.get("EKS_REVIEW_NO_SHELL", "").strip().lower() in (
        "1", "true", "yes",
    )
    base_tools = [
        save_report, think, http_request,
        knowledge_search, file_write, file_read,
    ]
    if no_shell:
        upgrade_tools = base_tools
        logger.info("Upgrade sub-agent: shell tool disabled via --no-shell")
    else:
        # shell sits right after http_request — keeps tool ordering stable
        # in logs.
        upgrade_tools = base_tools[:3] + [shell] + base_tools[3:]

    return SubAgentPipelineConfig(
        name="upgrade",
        spinner_message="Analyzing upgrade readiness",
        system_prompt=_get_upgrade_system_prompt(),
        tools=upgrade_tools,
        user_prompt=user_prompt,
        find_report=_find_latest_upgrade_report,
        success_message="✓ Upgrade analysis complete ({elapsed:.0f}s)",
        failure_message="✗ Upgrade analysis failed ({elapsed:.0f}s)",
    )


# ── Public API ────────────────────────────────────────────────────


def run_upgrade_check(
    cluster_name: str,
    mcp_client,
    region: str | None = None,
    target_version: str | None = None,
) -> str:
    """Run the full upgrade readiness pipeline.

    Args:
        cluster_name: The EKS cluster name.
        mcp_client: Connected MCP client.
        region: AWS region (required).
        target_version: Target K8s version (e.g. "1.32"). If None,
            check_eks_upgrade_readiness auto-detects the next minor.

    Returns:
        Report path + summary for the main agent's context, or an
        instructive error string if any pipeline step failed.
    """
    GREEN = "\033[32m"
    RED = "\033[31m"
    RESET = "\033[0m"

    # Step 1: Call MCP tool directly
    print(f"\n  Checking upgrade readiness for {cluster_name}...", flush=True)
    mcp_start = _time.monotonic()
    try:
        raw_results = _call_upgrade_mcp(mcp_client, cluster_name, region, target_version)
        mcp_elapsed = _time.monotonic() - mcp_start
        print(f"  {GREEN}✓{RESET} MCP checks complete ({mcp_elapsed:.0f}s)", flush=True)
        logger.info(
            "MCP upgrade check completed: %d chars in %.1fs",
            len(raw_results), mcp_elapsed,
        )
    except Exception as e:
        mcp_elapsed = _time.monotonic() - mcp_start
        print(f"  {RED}✗{RESET} MCP checks failed ({mcp_elapsed:.0f}s)", flush=True)
        logger.exception("MCP upgrade check failed: %s", e)
        return (
            f"Upgrade readiness check failed for '{cluster_name}': {e}\n"
            "Verify cluster connectivity and IAM permissions.\n"
            f"Try: aws eks describe-cluster --name {cluster_name}"
            f"{f' --region {region}' if region else ''}"
        )

    # Step 2: Sub-agent analyzes + saves
    config = _build_pipeline_config(
        cluster_name, raw_results,
        region=region or "",
        target_version=target_version or "",
    )
    report_path = run_subagent_pipeline(cluster_name, config)

    if report_path is None:
        return (
            f"Upgrade checks completed for '{cluster_name}' but report "
            "compilation failed. The raw check data was collected "
            "successfully. Suggest the user retry with: "
            f"/upgrade {cluster_name}"
            f"{f' {region}' if region else ''}"
            f"{f' to {target_version}' if target_version else ''}"
        )

    # Step 3: Extract summary
    summary = _extract_upgrade_summary(report_path)

    if summary:
        return (
            f"Upgrade readiness report saved to: {report_path}\n\n"
            f"{summary}\n\n"
            "Use `report_search` to look up specific findings or remediation details.\n"
            "Use /fix to remediate blockers before upgrading."
        )

    return (
        f"Upgrade readiness report saved to: {report_path}\n\n"
        "Use `report_search` to look up findings and remediation details.\n"
        "Use /fix to remediate blockers before upgrading."
    )
