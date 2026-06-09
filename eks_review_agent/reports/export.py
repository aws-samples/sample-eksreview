"""Export review reports to JIRA-importable CSV.

Parses saved markdown reports deterministically — no LLM involved.
Supports both assessment reports and upgrade readiness reports.
"""

import csv
import io
import logging
import re
from pathlib import Path

from eks_review_agent.config import REPORTS_DIR

logger = logging.getLogger("eksreview")

# ── Assessment report patterns ──────────────────
_FINDING_HEADER = re.compile(r"^###\s+\d+\.\s+(.+)$")
_RISK_LEVEL = re.compile(r"^\*\*Risk Level:\*\*\s*(.+)$", re.IGNORECASE)
_REMEDIATION_TYPE = re.compile(r"^\*\*Remediation Type:\*\*\s*(.+)$", re.IGNORECASE)
_DOMAIN = re.compile(r"^\*\*Domain:\*\*\s*(.+)$", re.IGNORECASE)

# ── Upgrade report patterns ─────────────────────
# Matches the check results summary table row:
# | U7 | Addon Health Issues | Addons | Critical | ❌ BLOCKER | Before | metrics-server |
_UPGRADE_TABLE_ROW = re.compile(
    r"^\|\s*(U\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$"
)

# Map report risk levels to JIRA priorities
_PRIORITY_MAP = {
    "critical": "Highest",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
}


def _strip_markdown(text: str) -> str:
    """Strip markdown formatting to produce clean plain text for JIRA."""
    text = re.sub(r"```\w*\n?", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]\s+", "- ", text, flags=re.MULTILINE)
    # Strip emoji status markers
    text = text.replace("✅", "").replace("❌", "").replace("⚠️", "")
    return text.strip()


def _detect_report_type(content: str) -> str:
    """Detect whether a report is an assessment or upgrade readiness report."""
    if "Upgrade Readiness Report" in content[:500]:
        return "upgrade"
    return "assessment"


# ── Assessment report parser ────────────────────

def _parse_assessment_findings(content: str) -> list[dict]:
    """Parse findings from an assessment report."""
    lines = content.splitlines()
    findings = []
    current: dict | None = None
    desc_lines: list[str] = []

    for line in lines:
        header_match = _FINDING_HEADER.match(line)
        if header_match:
            if current:
                current["description"] = "\n".join(desc_lines).strip()
                findings.append(current)
            current = {
                "summary": header_match.group(1).strip(),
                "priority": "Medium",
                "domain": "general",
                "fix_type": "",
                "description": "",
            }
            desc_lines = []
            continue

        if current is None:
            continue

        risk_match = _RISK_LEVEL.match(line)
        if risk_match:
            level = risk_match.group(1).strip().lower()
            current["priority"] = _PRIORITY_MAP.get(level, "Medium")
            continue

        rem_match = _REMEDIATION_TYPE.match(line)
        if rem_match:
            current["fix_type"] = rem_match.group(1).strip()
            continue

        domain_match = _DOMAIN.match(line)
        if domain_match:
            current["domain"] = domain_match.group(1).strip().lower()
            continue

        if line.strip() == "---" and current and desc_lines:
            current["description"] = "\n".join(desc_lines).strip()
            findings.append(current)
            current = None
            desc_lines = []
            continue

        desc_lines.append(line)

    if current:
        current["description"] = "\n".join(desc_lines).strip()
        findings.append(current)

    return findings


# ── Upgrade report parser ───────────────────────

def _parse_upgrade_findings(content: str) -> list[dict]:
    """Parse findings from an upgrade readiness report.

    Extracts from two sources:
    1. The check results summary table (all 38 checks)
    2. The detailed blocker/warning sections (for description text)
    """
    lines = content.splitlines()

    # Phase 1: Parse the summary table for all non-passing checks
    table_findings: dict[str, dict] = {}
    in_table = False

    for line in lines:
        # Detect table start (header row)
        if "| ID |" in line and "Check |" in line and "Status |" in line:
            in_table = True
            continue
        # Skip separator row
        if in_table and line.strip().startswith("|---"):
            continue
        # End of table
        if in_table and not line.strip().startswith("|"):
            in_table = False
            continue

        if not in_table:
            continue

        row_match = _UPGRADE_TABLE_ROW.match(line)
        if not row_match:
            continue

        check_id = row_match.group(1).strip()
        check_name = row_match.group(2).strip()
        category = row_match.group(3).strip()
        severity = row_match.group(4).strip().lower()
        status = row_match.group(5).strip()
        timing = row_match.group(6).strip()
        resources = row_match.group(7).strip()

        # Only export non-passing checks
        status_clean = _strip_markdown(status).strip().upper()
        if "PASS" in status_clean:
            continue

        is_blocker = "BLOCKER" in status_clean
        priority = _PRIORITY_MAP.get(severity, "Medium")

        table_findings[check_id] = {
            "summary": f"{check_name} ({check_id})",
            "priority": priority,
            "domain": category.lower(),
            "fix_type": "Before upgrade" if timing.lower() == "before" else timing,
            "description": "",
            "status": "BLOCKER" if is_blocker else "WARNING",
            "resources": resources if resources != "—" else "",
        }

    # Phase 2: Parse detailed sections for description text
    # Match: ### N. Title (Check ID)  or  ### N. Check Name (UNN)
    detail_header = re.compile(r"^###\s+\d+\.\s+(.+?)\s*\((U\d+)\)\s*$")
    current_id: str | None = None
    desc_lines: list[str] = []

    for line in lines:
        header_match = detail_header.match(line)
        if header_match:
            # Save previous
            if current_id and current_id in table_findings:
                table_findings[current_id]["description"] = "\n".join(desc_lines).strip()
            current_id = header_match.group(2).strip()
            desc_lines = []
            continue

        if current_id is None:
            continue

        # Stop at section boundaries
        if line.startswith("## ") or (line.strip() == "---" and desc_lines):
            if current_id in table_findings:
                table_findings[current_id]["description"] = "\n".join(desc_lines).strip()
            current_id = None
            desc_lines = []
            continue

        desc_lines.append(line)

    # Save last
    if current_id and current_id in table_findings:
        table_findings[current_id]["description"] = "\n".join(desc_lines).strip()

    # Build final list, sorted by check ID
    findings = []
    for check_id in sorted(table_findings.keys(), key=lambda x: int(x[1:])):
        f = table_findings[check_id]
        # If no detailed description was found, use resources as description
        if not f["description"] and f["resources"]:
            f["description"] = f"Impacted resources: {f['resources']}"
        findings.append(f)

    return findings


# ── Unified export function ─────────────────────

def export_report_to_jira_csv(report_path: str, cluster_name: str) -> str:
    """Parse a saved report and export findings as JIRA CSV.

    Automatically detects report type (assessment or upgrade readiness)
    and uses the appropriate parser.

    Args:
        report_path: Path to the saved markdown report.
        cluster_name: Cluster name for the output filename.

    Returns:
        Status message with file path and count.
    """
    try:
        content = Path(report_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"Report file not found: {report_path}"
    except Exception as e:
        logger.exception("Failed to read report: %s", e)
        return f"Failed to read report: {e}"

    # Detect report type and parse accordingly
    report_type = _detect_report_type(content)

    try:
        if report_type == "upgrade":
            findings = _parse_upgrade_findings(content)
        else:
            findings = _parse_assessment_findings(content)
    except Exception as e:
        logger.exception("Failed to parse report: %s", e)
        return f"Failed to parse report: {e}"

    if not findings:
        return "No findings found in the report. Check the report format."

    # Write CSV
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)

    if report_type == "upgrade":
        writer.writerow(["Summary", "Issue Type", "Priority", "Labels", "Status", "Description"])
        for f in findings:
            summary = f"EKS Upgrade {cluster_name}: {_strip_markdown(f['summary'])}"
            domain = f["domain"].split(",")[0].strip()
            labels = f"eks-upgrade {domain}"
            status = f.get("status", "WARNING")
            description = _strip_markdown(f["description"])
            if f["fix_type"]:
                description = f"Timing: {f['fix_type']}\n\n{description}"
            writer.writerow([summary, "Task", f["priority"], labels, status, description])
    else:
        writer.writerow(["Summary", "Issue Type", "Priority", "Labels", "Description"])
        for f in findings:
            summary = f"EKS {cluster_name}: {_strip_markdown(f['summary'])}"
            domain = f["domain"].split(",")[0].strip()
            labels = f"eks-review {domain}"
            description = _strip_markdown(f["description"])
            if f["fix_type"]:
                description = f"Fix Type: {f['fix_type']}\n\n{description}"
            writer.writerow([summary, "Task", f["priority"], labels, description])

    # Output filename
    safe_name = cluster_name.replace("/", "-").replace(" ", "-").lower()
    suffix = "upgrade-jira-export" if report_type == "upgrade" else "jira-export"
    csv_path = REPORTS_DIR / f"{safe_name}-{suffix}.csv"
    csv_path.write_text(output.getvalue(), encoding="utf-8")

    logger.info("JIRA CSV exported: %s (%d findings, type=%s)", csv_path, len(findings), report_type)
    return (
        f"Exported {len(findings)} {report_type} findings to: {csv_path.absolute()}\n\n"
        "  To import into JIRA:\n"
        "  1. Go to JIRA > Settings > System > External System Import > CSV\n"
        "  2. Upload the CSV file\n"
        "  3. Map columns: Summary, Issue Type, Priority, Labels, "
        f"{'Status, ' if report_type == 'upgrade' else ''}Description\n"
        "  4. Select the target project\n"
        "  5. Validate and import"
    )
