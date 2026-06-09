"""Golden-file tests for report formats.

These tests pin the expected shape of saved reports. If the report format
ever drifts (because a skill prompt changed, or the LLM produces something
slightly different), the parsers should still extract the same fields —
otherwise downstream tooling (`report_search`, `export.py`, history-trend
analysis) breaks silently.

Updating fixtures: when the report format intentionally changes, regenerate
the file under tests/fixtures/ and update the assertions below.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from eks_review_agent.reports.export import (
    _detect_report_type,
    _parse_assessment_findings,
    _parse_upgrade_findings,
    export_report_to_jira_csv,
)
from eks_review_agent.reports.report_search import _extract_sections, _search_sections
from eks_review_agent.orchestration.review_orchestrator import _extract_summary_from_report
from eks_review_agent.orchestration.upgrade_orchestrator import _extract_upgrade_summary


_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def assessment_report(tmp_reports_dir: Path) -> Path:
    """Copy the assessment golden into REPORTS_DIR with a real filename."""
    src = _FIXTURES / "sample_assessment_report.md"
    dst = tmp_reports_dir / "eks-prod-assessment-20260523_120000.md"
    dst.write_text(src.read_text())
    return dst


@pytest.fixture
def upgrade_report(tmp_reports_dir: Path) -> Path:
    src = _FIXTURES / "sample_upgrade_report.md"
    dst = tmp_reports_dir / "eks-prod-upgrade-readiness-20260523_120000.md"
    dst.write_text(src.read_text())
    return dst


# ── Assessment golden ───────────────────────────────────────────────


class TestAssessmentGolden:
    def test_detected_as_assessment(self, assessment_report: Path) -> None:
        assert _detect_report_type(assessment_report.read_text()) == "assessment"

    def test_executive_summary_extractable(self, assessment_report: Path) -> None:
        summary = _extract_summary_from_report(str(assessment_report))
        assert summary is not None
        # Pin the summary anchors that the main agent relies on.
        assert "Executive Summary" in summary
        assert "Issue Breakdown" in summary
        assert "Priority Classification" in summary
        assert "Key Recommendations" in summary

    def test_summary_does_not_include_check_table(
        self, assessment_report: Path
    ) -> None:
        # The whole point of summary extraction is to keep the giant
        # check-results table OUT of the main agent's context.
        summary = _extract_summary_from_report(str(assessment_report))
        assert "Check Results Summary" not in summary

    def test_findings_parsed(self, assessment_report: Path) -> None:
        findings = _parse_assessment_findings(assessment_report.read_text())
        # Pin the count + priority distribution.
        assert len(findings) == 3
        priorities = [f["priority"] for f in findings]
        assert priorities.count("Highest") == 2  # 2 Criticals
        assert priorities.count("High") == 1

    def test_findings_have_remediation_type(
        self, assessment_report: Path
    ) -> None:
        findings = _parse_assessment_findings(assessment_report.read_text())
        fix_types = [f["fix_type"] for f in findings]
        assert "AWS CLI" in fix_types
        assert "Manifest change required" in fix_types

    def test_jira_export_roundtrip(
        self, assessment_report: Path, tmp_reports_dir: Path
    ) -> None:
        result = export_report_to_jira_csv(str(assessment_report), "eks-prod")
        assert "Exported 3" in result
        csv_path = tmp_reports_dir / "eks-prod-jira-export.csv"
        rows = list(csv.reader(io.StringIO(csv_path.read_text())))
        # header + 3 findings
        assert len(rows) == 4
        # Pin column headers — downstream tooling depends on this.
        assert rows[0] == [
            "Summary", "Issue Type", "Priority", "Labels", "Description"
        ]

    def test_report_search_finds_specific_finding(
        self, assessment_report: Path
    ) -> None:
        sections = _extract_sections(assessment_report.read_text())
        results = _search_sections(sections, "secrets encryption")
        assert results
        # Top result should be the secrets-encryption finding
        assert "encryption" in results[0]["title"].lower()

    def test_search_finds_resource_count_in_body(
        self, assessment_report: Path
    ) -> None:
        sections = _extract_sections(assessment_report.read_text())
        # Search for a string that's only in the body, not the title
        results = _search_sections(sections, "70 workloads")
        assert results


# ── Upgrade golden ──────────────────────────────────────────────────


class TestUpgradeGolden:
    def test_detected_as_upgrade(self, upgrade_report: Path) -> None:
        assert _detect_report_type(upgrade_report.read_text()) == "upgrade"

    def test_summary_extracts_verdict_and_decision(
        self, upgrade_report: Path
    ) -> None:
        summary = _extract_upgrade_summary(str(upgrade_report))
        assert summary is not None
        assert "NO-GO" in summary
        assert "Go / No-Go Decision" in summary

    def test_findings_count_and_status(self, upgrade_report: Path) -> None:
        findings = _parse_upgrade_findings(upgrade_report.read_text())
        # Pin: 5 rows in the table, 1 PASS (U1, U20), 2 BLOCKER, 1 WARNING.
        # PASS rows are excluded.
        assert len(findings) == 3
        statuses = [f["status"] for f in findings]
        assert statuses.count("BLOCKER") == 2
        assert statuses.count("WARNING") == 1

    def test_ids_extracted_in_order(self, upgrade_report: Path) -> None:
        findings = _parse_upgrade_findings(upgrade_report.read_text())
        # Sort key should be by U-id integer
        ids = [f["summary"].split("(")[-1].rstrip(")") for f in findings]
        assert ids == sorted(ids, key=lambda x: int(x[1:]))

    def test_jira_export_includes_status_column(
        self, upgrade_report: Path, tmp_reports_dir: Path
    ) -> None:
        result = export_report_to_jira_csv(str(upgrade_report), "eks-prod")
        assert "Exported" in result
        csv_path = tmp_reports_dir / "eks-prod-upgrade-jira-export.csv"
        rows = list(csv.reader(io.StringIO(csv_path.read_text())))
        # Pin upgrade column shape (different from assessment).
        assert rows[0] == [
            "Summary", "Issue Type", "Priority", "Labels", "Status", "Description"
        ]


# ── Round-trip stability ────────────────────────────────────────────


class TestRoundTripStability:
    """Re-parsing the same content should yield identical results."""

    def test_assessment_idempotent(self, assessment_report: Path) -> None:
        content = assessment_report.read_text()
        a = _parse_assessment_findings(content)
        b = _parse_assessment_findings(content)
        assert a == b

    def test_upgrade_idempotent(self, upgrade_report: Path) -> None:
        content = upgrade_report.read_text()
        a = _parse_upgrade_findings(content)
        b = _parse_upgrade_findings(content)
        assert a == b

    def test_section_extraction_stable(self, assessment_report: Path) -> None:
        content = assessment_report.read_text()
        a = _extract_sections(content)
        b = _extract_sections(content)
        assert len(a) == len(b)
        assert [s["title"] for s in a] == [s["title"] for s in b]
