"""Custom tools for the EKS review agent.

All tools use the Strands @tool decorator and follow the native tool protocol.
Tools that need shared state access use ToolContext.
"""

import logging
from datetime import datetime

from strands import tool

from eks_review_agent.config import REPORTS_DIR

logger = logging.getLogger("eksreview")


@tool
def save_report(report_content: str, cluster_name: str,
                report_type: str = "assessment") -> str:
    """Save an EKS review report to the reports directory.

    Args:
        report_content: The full markdown report content.
        cluster_name: Name of the cluster being reviewed.
        report_type: Type of report. Use "assessment" for best-practice reviews,
                     "upgrade-readiness" for upgrade checks. Defaults to "assessment".

    Returns:
        Path to the saved report file.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = cluster_name.replace("/", "-").replace(" ", "-").lower()
    filename = REPORTS_DIR / f"{safe_name}-{report_type}-{timestamp}.md"
    filename.write_text(report_content, encoding="utf-8")

    logger.info("Report saved: %s", filename.absolute())
    return f"Report saved to: {filename.absolute()}"


@tool
def think(thought: str) -> str:
    """Use this tool to reason through complex decisions before taking action.

    Call this when you need to:
    - Correlate findings across multiple review domains
    - Decide which severity to assign a finding
    - Plan the next steps in a multi-phase review
    - Analyze trade-offs before making a recommendation

    This tool does NOT perform any action — it simply lets you think out loud.

    Args:
        thought: Your reasoning, analysis, or plan.

    Returns:
        Acknowledgment that the thought was recorded.
    """
    logger.debug("Agent thinking: %s", thought[:200])
    return "Thought recorded. Continue with your analysis."


@tool
def get_review_history(cluster_name: str) -> str:
    """Retrieve previous review reports and generate a trend/diff analysis.

    Parses the executive summary table and failed check headings from past
    reports to produce:
    - Compliance score trend over time
    - Per-domain pass/fail trends
    - List of persistent (stale) failures across reviews
    - Date of each past review

    Use this BEFORE running a new review to understand the baseline, or
    AFTER a review to compare current vs previous findings.

    Args:
        cluster_name: Name of the cluster to look up history for.

    Returns:
        Structured trend analysis, or a message if no history exists.
    """
    import re

    safe_name = cluster_name.replace("/", "-").replace(" ", "-").lower()
    pattern = f"{safe_name}-assessment-*.md"
    reports = sorted(REPORTS_DIR.glob(pattern), reverse=True)

    if not reports:
        return f"No previous review reports found for cluster '{cluster_name}'. This will be the first review."

    def _parse_report(path):
        """Extract structured data from a report.

        Prefers the .meta.json sidecar file (reliable, format-independent).
        Falls back to regex parsing of the markdown if no metadata file exists.
        """
        import json as _json

        result = {"file": path.name, "date": "unknown", "domains": {}, "failed_checks": [], "total_compliance": None}

        # Extract date from filename (YYYYMMDD_HHMMSS)
        stem = path.stem
        ts_match = re.search(r"(\d{8})_(\d{6})$", stem)
        if ts_match:
            d = ts_match.group(1)
            result["date"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}"

        # Try loading structured metadata first (format-independent)
        meta_path = path.with_suffix(".meta.json")
        if meta_path.exists():
            try:
                meta = _json.loads(meta_path.read_text(encoding="utf-8"))
                result["total_compliance"] = meta.get("total_compliance")
                result["failed_checks"] = meta.get("failed_checks", [])
                for domain, info in meta.get("domains", {}).items():
                    result["domains"][domain] = {
                        "passed": info.get("passed", 0),
                        "failed": info.get("failed", 0),
                        "total": info.get("total", 0),
                        "compliance": info.get("compliance", 0),
                    }
                return result
            except Exception as e:
                logger.warning("Failed to load metadata %s: %s, falling back to regex", meta_path.name, e)

        # Fallback: parse the markdown report with regex
        content = path.read_text(encoding="utf-8")

        # Parse executive summary — supports both table and bullet-point formats
        # Table format: | **Security** | 7 | 16 | 23 | 30.4% |
        table_pattern = re.compile(
            r"\|\s*\*?\*?(\w[\w\s]*?)\*?\*?\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([\d.]+)%\s*\|"
        )
        for m in table_pattern.finditer(content):
            domain = m.group(1).strip().lower()
            if domain == "total":
                result["total_compliance"] = float(m.group(5))
            else:
                result["domains"][domain] = {
                    "passed": int(m.group(2)),
                    "failed": int(m.group(3)),
                    "total": int(m.group(4)),
                    "compliance": float(m.group(5)),
                }

        # Bullet-point format: - **Security:** 4 passed, 5 failed
        if not result["domains"]:
            bullet_pattern = re.compile(
                r"-\s*\*\*(.+?)\*\*[:\s]+(\d+)\s*passed,\s*(\d+)\s*failed"
            )
            total_passed = 0
            total_failed = 0
            for m in bullet_pattern.finditer(content):
                domain = m.group(1).strip().lower()
                passed = int(m.group(2))
                failed = int(m.group(3))
                total = passed + failed
                compliance = (passed / total * 100) if total > 0 else 0
                result["domains"][domain] = {
                    "passed": passed,
                    "failed": failed,
                    "total": total,
                    "compliance": compliance,
                }
                total_passed += passed
                total_failed += failed
            if total_passed + total_failed > 0:
                result["total_compliance"] = total_passed / (total_passed + total_failed) * 100

        # Extract failed check headings from detailed findings sections
        # Matches both old format (#### Check Name) and new format (### N. Check Name)
        in_failed_section = False
        for line in content.splitlines():
            lower = line.strip().lower()
            if any(x in lower for x in ["high priority", "critical issues", "medium priority", "low priority",
                                         "failed check", "key failed", "areas of failure"]):
                in_failed_section = True
            elif line.startswith("---") or "next steps" in lower or "passed" in lower:
                in_failed_section = False
            elif in_failed_section and (line.startswith("### ") or line.startswith("#### ")):
                check_name = re.sub(r"^#{3,4}\s*\d+\.?\s*", "", line).strip()
                if check_name and "additional" not in check_name.lower():
                    result["failed_checks"].append(check_name)

        return result

    # Parse all reports (most recent first)
    parsed = []
    for rp in reports[:5]:
        try:
            parsed.append(_parse_report(rp))
        except Exception as e:
            logger.warning("Failed to parse report %s: %s", rp.name, e)

    if not parsed:
        return f"Found {len(reports)} report(s) but could not parse them."

    latest = parsed[0]
    parts = [f"## Review History for '{cluster_name}' ({len(reports)} report(s))\n"]

    # Compliance trend
    parts.append("### Compliance Trend\n")
    for p in reversed(parsed):
        score = f"{p['total_compliance']:.1f}%" if p["total_compliance"] is not None else "N/A"
        parts.append(f"  {p['date']}  {score}")
    parts.append("")

    # Per-domain trends (latest vs previous)
    if len(parsed) >= 2:
        prev = parsed[1]
        parts.append("### Domain Changes (current vs previous)\n")
        all_domains = set(list(latest["domains"].keys()) + list(prev["domains"].keys()))
        for domain in sorted(all_domains):
            curr_d = latest["domains"].get(domain)
            prev_d = prev["domains"].get(domain)
            if curr_d and prev_d:
                delta = curr_d["compliance"] - prev_d["compliance"]
                arrow = "improved" if delta > 0 else "regressed" if delta < 0 else "unchanged"
                parts.append(
                    f"  {domain:<25} {prev_d['compliance']:5.1f}% -> {curr_d['compliance']:5.1f}%  ({arrow})"
                )
            elif curr_d:
                parts.append(f"  {domain:<25} new domain: {curr_d['compliance']:.1f}%")
        parts.append("")

        # Failed check diff
        curr_fails = set(latest["failed_checks"])
        prev_fails = set(prev["failed_checks"])
        resolved = prev_fails - curr_fails
        new_fails = curr_fails - prev_fails
        persistent = curr_fails & prev_fails

        if resolved:
            parts.append("### Resolved (were failing, now fixed)\n")
            for c in sorted(resolved):
                parts.append(f"  + {c}")
            parts.append("")

        if new_fails:
            parts.append("### New Failures (not in previous report)\n")
            for c in sorted(new_fails):
                parts.append(f"  ! {c}")
            parts.append("")

        if persistent:
            parts.append(f"### Persistent Failures ({len(persistent)} unchanged)\n")
            for c in sorted(persistent):
                parts.append(f"  - {c}")
            parts.append("")

    # Stale findings (appear in 3+ consecutive reports)
    if len(parsed) >= 3:
        all_fail_sets = [set(p["failed_checks"]) for p in parsed[:3]]
        stale = all_fail_sets[0] & all_fail_sets[1] & all_fail_sets[2]
        if stale:
            parts.append(f"### Stale Findings (unresolved across 3+ reviews)\n")
            for c in sorted(stale):
                parts.append(f"  !! {c}")
            parts.append("")

    logger.info("Parsed %d reports for %s", len(parsed), cluster_name)
    return "\n".join(parts)


