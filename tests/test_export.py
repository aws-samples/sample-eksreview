"""Tests for export.py — assessment + upgrade JIRA CSV export."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from eks_review_agent.reports.export import (
    _detect_report_type,
    _parse_assessment_findings,
    _parse_upgrade_findings,
    _strip_markdown,
    export_report_to_jira_csv,
)


# ── _strip_markdown ──────────────────────────────────────────────────


class TestStripMarkdown:
    @pytest.mark.parametrize(
        "src,expected",
        [
            ("**bold**", "bold"),
            ("*italic*", "italic"),
            ("`code`", "code"),
            ("# Heading", "Heading"),
            ("## Heading", "Heading"),
            ("- bullet", "- bullet"),
            ("* bullet", "- bullet"),
            ("✅ pass", "pass"),
            ("❌ fail", "fail"),
            ("⚠️ warn", "warn"),
        ],
    )
    def test_strips_common_markdown(self, src: str, expected: str) -> None:
        assert _strip_markdown(src) == expected

    def test_strips_code_block_fences(self) -> None:
        src = "```bash\nrun this\n```"
        assert "```" not in _strip_markdown(src)


# ── _detect_report_type ──────────────────────────────────────────────


class TestDetectReportType:
    def test_assessment_default(self) -> None:
        content = "# EKS Operations Review Report\n## Cluster: foo\n"
        assert _detect_report_type(content) == "assessment"

    def test_upgrade_detected(self) -> None:
        content = "# EKS Upgrade Readiness Report\n## Cluster: foo\n"
        assert _detect_report_type(content) == "upgrade"

    def test_upgrade_in_first_500_chars(self) -> None:
        content = "# Some Title\n\nThis is an Upgrade Readiness Report for cluster eks-demo.\n"
        assert _detect_report_type(content) == "upgrade"


# ── Assessment parsing ───────────────────────────────────────────────


ASSESSMENT_SAMPLE = """\
# EKS Operations Review Report
## Cluster: eks-demo (us-west-2)

---

## Critical Issues

### 1. Cluster endpoint is publicly accessible
**Risk Level:** Critical
**Domain:** Security
**Remediation Type:** AWS CLI

The cluster endpoint allows public access from 0.0.0.0/0.

---

### 2. Liveness probes missing
**Risk Level:** High
**Domain:** Resiliency
**Remediation Type:** Manifest change required

70 workloads across 5 namespaces lack liveness probes.

---

## Medium Priority Issues

### 3. Container image scanning not enabled
**Risk Level:** Medium
**Domain:** Security
**Remediation Type:** AWS CLI

ECR repository does not have image scanning configured.
"""


class TestAssessmentParser:
    def test_parses_three_findings(self) -> None:
        findings = _parse_assessment_findings(ASSESSMENT_SAMPLE)
        assert len(findings) == 3

    def test_extracts_summary_and_priority(self) -> None:
        findings = _parse_assessment_findings(ASSESSMENT_SAMPLE)
        assert findings[0]["summary"] == "Cluster endpoint is publicly accessible"
        assert findings[0]["priority"] == "Highest"  # Critical maps to Highest
        assert findings[1]["priority"] == "High"
        assert findings[2]["priority"] == "Medium"

    def test_extracts_domain_and_fix_type(self) -> None:
        findings = _parse_assessment_findings(ASSESSMENT_SAMPLE)
        assert findings[0]["domain"] == "security"
        assert findings[0]["fix_type"] == "AWS CLI"
        assert findings[1]["domain"] == "resiliency"

    def test_description_captured(self) -> None:
        findings = _parse_assessment_findings(ASSESSMENT_SAMPLE)
        assert "publicly accessible" in findings[0]["description"].lower() or \
               "0.0.0.0/0" in findings[0]["description"]


# ── Upgrade parsing ──────────────────────────────────────────────────


UPGRADE_SAMPLE = """\
# EKS Upgrade Readiness Report
## Cluster: eks-demo (us-west-2)

---

## Check Results Summary

| ID | Check | Category | Severity | Status | Timing | Resources |
|----|-------|----------|----------|--------|--------|-----------|
| U1 | Cluster Version | Control | Critical | ✅ PASS | Before | — |
| U7 | Addon Health | Addons | Critical | ❌ BLOCKER | Before | metrics-server |
| U19 | Karpenter Compatibility | Workloads | High | ⚠️ WARNING | Before | karpenter v0.30 |

---

## Detailed Findings

### 1. Addon Health (U7)
**Severity:** Critical
**Category:** Addons

The metrics-server addon is in a degraded state and must be repaired before upgrade.

### 2. Karpenter Compatibility (U19)
**Severity:** High
**Category:** Workloads

Karpenter v0.30 is not compatible with K8s 1.32. Upgrade to v0.32 first.
"""


class TestUpgradeParser:
    def test_excludes_passing_checks(self) -> None:
        findings = _parse_upgrade_findings(UPGRADE_SAMPLE)
        # Only the non-PASS rows should be present.
        # U7 (BLOCKER) and U19 (WARNING) — but not U1 (PASS).
        check_ids = []
        for f in findings:
            # Summary format: "Check Name (U7)" — extract the ID
            paren = f["summary"].split("(")[-1].rstrip(")")
            check_ids.append(paren)
        assert "U1" not in check_ids
        assert "U7" in check_ids
        assert "U19" in check_ids
        assert len(check_ids) == 2

    def test_includes_blockers_and_warnings(self) -> None:
        findings = _parse_upgrade_findings(UPGRADE_SAMPLE)
        statuses = [f["status"] for f in findings]
        assert "BLOCKER" in statuses
        assert "WARNING" in statuses

    def test_priority_mapped_correctly(self) -> None:
        findings = _parse_upgrade_findings(UPGRADE_SAMPLE)
        by_id = {f["summary"].split("(")[-1].rstrip(")"): f for f in findings}
        assert by_id["U7"]["priority"] == "Highest"
        assert by_id["U19"]["priority"] == "High"

    def test_resources_captured(self) -> None:
        findings = _parse_upgrade_findings(UPGRADE_SAMPLE)
        by_id = {f["summary"].split("(")[-1].rstrip(")"): f for f in findings}
        assert "metrics-server" in by_id["U7"]["resources"]


# ── End-to-end CSV export ────────────────────────────────────────────


class TestExportCSV:
    def test_assessment_export_creates_csv(self, tmp_reports_dir: Path) -> None:
        report_path = tmp_reports_dir / "eks-demo-assessment-20260101_120000.md"
        report_path.write_text(ASSESSMENT_SAMPLE)
        result = export_report_to_jira_csv(str(report_path), "eks-demo")

        assert "Exported 3" in result
        csv_path = tmp_reports_dir / "eks-demo-jira-export.csv"
        assert csv_path.exists()

        # Read back as CSV — header + 3 rows
        rows = list(csv.reader(io.StringIO(csv_path.read_text())))
        assert rows[0] == ["Summary", "Issue Type", "Priority", "Labels", "Description"]
        assert len(rows) == 4  # header + 3 findings
        # First data row should have priority Highest (Critical maps to Highest)
        assert rows[1][2] == "Highest"

    def test_upgrade_export_includes_status_column(
        self, tmp_reports_dir: Path
    ) -> None:
        report_path = tmp_reports_dir / "eks-demo-upgrade-readiness-20260101_120000.md"
        report_path.write_text(UPGRADE_SAMPLE)
        result = export_report_to_jira_csv(str(report_path), "eks-demo")

        assert "Exported" in result
        csv_path = tmp_reports_dir / "eks-demo-upgrade-jira-export.csv"
        assert csv_path.exists()

        rows = list(csv.reader(io.StringIO(csv_path.read_text())))
        # Upgrade CSV has 6 columns including Status
        assert "Status" in rows[0]

    def test_missing_file_returns_error(self, tmp_reports_dir: Path) -> None:
        result = export_report_to_jira_csv(
            str(tmp_reports_dir / "nope.md"), "eks-demo"
        )
        assert "not found" in result.lower()

    def test_empty_findings_returns_message(self, tmp_reports_dir: Path) -> None:
        report_path = tmp_reports_dir / "eks-demo-assessment-20260101_120000.md"
        report_path.write_text("# Empty Report\n\nNo findings here.\n")
        result = export_report_to_jira_csv(str(report_path), "eks-demo")
        assert "No findings" in result
