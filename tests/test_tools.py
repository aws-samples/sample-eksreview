"""Tests for the custom @tool functions: save_report, think, get_review_history."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from eks_review_agent.tools import (
    get_review_history,
    save_report,
    think,
)


# Strands @tool wraps functions; reach the underlying callable for unit tests.
def _call(tool_obj, *args, **kwargs):
    """Call a Strands-wrapped @tool function directly."""
    if hasattr(tool_obj, "_tool_func"):
        return tool_obj._tool_func(*args, **kwargs)
    if hasattr(tool_obj, "func"):
        return tool_obj.func(*args, **kwargs)
    return tool_obj(*args, **kwargs)


# ── save_report ─────────────────────────────────────────────────────


class TestSaveReport:
    def test_writes_file_to_reports_dir(self, tmp_reports_dir: Path) -> None:
        result = _call(
            save_report,
            "# Hello\n\nSome content",
            "eks-test",
            report_type="assessment",
        )
        assert "Report saved to:" in result
        files = list(tmp_reports_dir.glob("*.md"))
        assert len(files) == 1
        assert files[0].read_text() == "# Hello\n\nSome content"
        assert "eks-test" in files[0].name
        assert "assessment" in files[0].name

    def test_default_report_type_is_assessment(self, tmp_reports_dir: Path) -> None:
        _call(save_report, "content", "eks-test")
        files = list(tmp_reports_dir.glob("*.md"))
        assert len(files) == 1
        assert "assessment" in files[0].name

    def test_upgrade_report_type(self, tmp_reports_dir: Path) -> None:
        _call(save_report, "content", "eks-test", report_type="upgrade-readiness")
        files = list(tmp_reports_dir.glob("*.md"))
        assert "upgrade-readiness" in files[0].name

    def test_cluster_name_sanitized(self, tmp_reports_dir: Path) -> None:
        _call(save_report, "content", "EKS Demo/v2")
        files = list(tmp_reports_dir.glob("*.md"))
        assert "/" not in files[0].name
        assert " " not in files[0].name
        # Lowercased and sanitized
        assert "eks-demo-v2" in files[0].name


# ── think (no-op smoke) ─────────────────────────────────────────────


def test_think_returns_acknowledgment() -> None:
    result = _call(think, "I should check the security domain first")
    assert "Thought recorded" in result


# ── get_review_history ──────────────────────────────────────────────


def _write_report_with_meta(
    reports_dir: Path,
    cluster: str,
    timestamp: str,
    domains: dict[str, dict],
    failed_checks: list[str],
    total_compliance: float,
) -> Path:
    """Helper: write a markdown report + .meta.json sidecar."""
    safe = cluster.replace("/", "-").replace(" ", "-").lower()
    md_path = reports_dir / f"{safe}-assessment-{timestamp}.md"
    md_path.write_text(f"# Report for {cluster}\n\n## Executive Summary\nStub\n")

    meta = md_path.with_suffix(".meta.json")
    meta.write_text(
        json.dumps({
            "domains": domains,
            "failed_checks": failed_checks,
            "total_compliance": total_compliance,
        })
    )
    return md_path


class TestGetReviewHistory:
    def test_no_reports_returns_first_review_message(self, tmp_reports_dir: Path) -> None:
        result = _call(get_review_history, cluster_name="eks-demo")
        assert "first review" in result.lower()

    def test_single_report_shows_compliance_trend(self, tmp_reports_dir: Path) -> None:
        _write_report_with_meta(
            tmp_reports_dir,
            "eks-demo",
            "20260101_120000",
            domains={"security": {"passed": 8, "failed": 2, "total": 10, "compliance": 80.0}},
            failed_checks=["Use IRSA", "Enable audit logs"],
            total_compliance=80.0,
        )
        result = _call(get_review_history, cluster_name="eks-demo")
        assert "Review History for 'eks-demo'" in result
        assert "Compliance Trend" in result
        assert "80.0%" in result
        assert "2026-01-01" in result

    def test_two_reports_shows_domain_diff(self, tmp_reports_dir: Path) -> None:
        # Older report: weaker
        _write_report_with_meta(
            tmp_reports_dir,
            "eks-demo",
            "20260101_120000",
            domains={"security": {"passed": 5, "failed": 5, "total": 10, "compliance": 50.0}},
            failed_checks=["Issue A", "Issue B", "Issue C"],
            total_compliance=50.0,
        )
        # Newer report: stronger
        _write_report_with_meta(
            tmp_reports_dir,
            "eks-demo",
            "20260201_120000",
            domains={"security": {"passed": 8, "failed": 2, "total": 10, "compliance": 80.0}},
            failed_checks=["Issue B"],
            total_compliance=80.0,
        )
        result = _call(get_review_history, cluster_name="eks-demo")
        # Domain diff section
        assert "Domain Changes" in result
        assert "improved" in result
        # Resolved issues from old report
        assert "Resolved" in result
        assert "Issue A" in result
        assert "Issue C" in result
        # Persistent issue should appear
        assert "Persistent" in result
        assert "Issue B" in result

    def test_three_reports_detects_stale_findings(self, tmp_reports_dir: Path) -> None:
        for ts in ["20260101_120000", "20260201_120000", "20260301_120000"]:
            _write_report_with_meta(
                tmp_reports_dir,
                "eks-demo",
                ts,
                domains={"security": {"passed": 5, "failed": 5, "total": 10, "compliance": 50.0}},
                failed_checks=["Stubborn Issue", "Other Issue"],
                total_compliance=50.0,
            )
        result = _call(get_review_history, cluster_name="eks-demo")
        assert "Stale Findings" in result
        assert "Stubborn Issue" in result

    def test_falls_back_to_regex_when_meta_missing(self, tmp_reports_dir: Path) -> None:
        # Write a markdown-only report (no .meta.json sidecar)
        ts = "20260101_120000"
        md = tmp_reports_dir / f"eks-demo-assessment-{ts}.md"
        md.write_text(
            "# Report\n\n"
            "## Summary\n"
            "| Domain | Passed | Failed | Total | Compliance |\n"
            "|--------|--------|--------|-------|------------|\n"
            "| **Security** | 7 | 3 | 10 | 70.0% |\n"
            "| **Total** | 7 | 3 | 10 | 70.0% |\n\n"
            "## High Priority Issues\n\n"
            "### 1. First Failed Check\n"
            "Body\n\n"
            "### 2. Second Failed Check\n"
            "Body\n"
        )
        result = _call(get_review_history, cluster_name="eks-demo")
        assert "70.0%" in result

    def test_cluster_name_sanitization(self, tmp_reports_dir: Path) -> None:
        # File path uses sanitized name; lookup must match
        _write_report_with_meta(
            tmp_reports_dir,
            "EKS/Demo",
            "20260101_120000",
            domains={},
            failed_checks=[],
            total_compliance=80.0,
        )
        result = _call(get_review_history, cluster_name="EKS/Demo")
        assert "first review" not in result.lower()
        assert "Compliance Trend" in result
