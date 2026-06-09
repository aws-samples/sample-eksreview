"""Tests for report_search — section parsing, scoring, LRU cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from eks_review_agent.reports.report_search import (
    _SECTION_CACHE_MAX,
    _extract_sections,
    _get_sections,
    _search_sections,
    _section_cache,
    find_latest_report,
    report_search,
)


def _call(tool_obj, *args, **kwargs):
    if hasattr(tool_obj, "_tool_func"):
        return tool_obj._tool_func(*args, **kwargs)
    return tool_obj(*args, **kwargs)


SAMPLE_REPORT = """\
# EKS Operations Review Report
## Cluster: eks-demo (us-west-2)

---

## Executive Summary

Security and resiliency need work.

### Issue Breakdown
- Security: 5 failed
- Resiliency: 3 failed

---

## Check Results Summary

| 1 | Use IRSA | Security | High | FAIL |

---

## Critical Issues

### 1. Cluster endpoint is publicly accessible

**Risk Level:** Critical
**Category:** Security

The cluster endpoint allows public access from 0.0.0.0/0.

**Remediation:**
```bash
aws eks update-cluster-config --name eks-demo --resources-vpc-config endpointPublicAccess=false
```

### 2. Liveness probes missing

**Risk Level:** High
**Category:** Resiliency

70 workloads across 5 namespaces lack liveness probes.

---

## High Priority Issues

### 3. Pod Security Standards not enforced

Some content here.
"""


@pytest.fixture
def sample_report(tmp_reports_dir: Path) -> Path:
    p = tmp_reports_dir / "eks-demo-assessment-20260101_120000.md"
    p.write_text(SAMPLE_REPORT)
    # Ensure a fresh cache for each test
    _section_cache.clear()
    return p


# ── Section extraction ──────────────────────────────────────────────


class TestExtractSections:
    def test_returns_sections_for_each_heading(self) -> None:
        sections = _extract_sections(SAMPLE_REPORT)
        titles = [s["title"] for s in sections]
        assert "Cluster: eks-demo (us-west-2)" in titles
        assert "Executive Summary" in titles
        assert "Critical Issues" in titles
        # Findings are numbered in the source so the title includes the number
        assert "1. Cluster endpoint is publicly accessible" in titles

    def test_levels_correct(self) -> None:
        sections = _extract_sections(SAMPLE_REPORT)
        by_title = {s["title"]: s for s in sections}
        assert by_title["Executive Summary"]["level"] == 2
        assert by_title["1. Cluster endpoint is publicly accessible"]["level"] == 3
        assert by_title["Issue Breakdown"]["level"] == 3

    def test_container_sections_flagged(self) -> None:
        sections = _extract_sections(SAMPLE_REPORT)
        crit = next(s for s in sections if s["title"] == "Critical Issues")
        assert crit["is_container"] is True
        # Leaf finding should NOT be flagged
        finding = next(
            s for s in sections if s["title"] == "1. Cluster endpoint is publicly accessible"
        )
        assert finding["is_container"] is False

    def test_empty_report_returns_no_sections(self) -> None:
        assert _extract_sections("") == []

    def test_no_headings_returns_no_sections(self) -> None:
        assert _extract_sections("Just some text without any markdown headings.") == []


# ── Search scoring ──────────────────────────────────────────────────


class TestSearchSections:
    def test_title_phrase_match_scores_highest(self) -> None:
        sections = _extract_sections(SAMPLE_REPORT)
        results = _search_sections(sections, "endpoint publicly accessible")
        assert results
        assert "endpoint" in results[0]["title"].lower()

    def test_keyword_only_in_body_returns_lower_priority_match(self) -> None:
        sections = _extract_sections(SAMPLE_REPORT)
        results = _search_sections(sections, "liveness probes")
        # Should still find the resiliency section
        assert any("Liveness probes missing" in r["title"] for r in results)

    def test_no_match_returns_empty(self) -> None:
        sections = _extract_sections(SAMPLE_REPORT)
        results = _search_sections(sections, "completely-unrelated-term-xyzzy")
        assert results == []


# ── LRU cache ───────────────────────────────────────────────────────


class TestSectionCache:
    def test_repeat_get_uses_cache(self, sample_report: Path) -> None:
        sections1 = _get_sections(sample_report)
        sections2 = _get_sections(sample_report)
        # Same object returned (cache hit, not re-parsed)
        assert sections1 is sections2

    def test_cache_invalidated_on_mtime_change(self, sample_report: Path) -> None:
        first = _get_sections(sample_report)
        # Bump the mtime artificially by re-writing
        import time
        time.sleep(0.01)
        sample_report.write_text(SAMPLE_REPORT + "\n## New Section\nhello\n")
        second = _get_sections(sample_report)
        assert second is not first
        assert any(s["title"] == "New Section" for s in second)

    def test_cache_capped_at_max(self, tmp_reports_dir: Path) -> None:
        _section_cache.clear()
        # Create more reports than cache cap and read them all
        paths = []
        for i in range(_SECTION_CACHE_MAX + 5):
            p = tmp_reports_dir / f"eks-demo-assessment-{i:020d}.md"
            p.write_text(f"# Report {i}\n## Body\ncontent\n")
            paths.append(p)
            _get_sections(p)
        # Cache size capped
        assert len(_section_cache) == _SECTION_CACHE_MAX
        # Earliest entries evicted
        assert str(paths[0]) not in _section_cache
        # Most recent retained
        assert str(paths[-1]) in _section_cache


# ── find_latest_report ──────────────────────────────────────────────


class TestFindLatest:
    def test_finds_most_recent_assessment(self, tmp_reports_dir: Path) -> None:
        old = tmp_reports_dir / "eks-demo-assessment-20260101_120000.md"
        new = tmp_reports_dir / "eks-demo-assessment-20260201_120000.md"
        old.write_text("old")
        new.write_text("new")
        # Set mtimes explicitly so the test is deterministic
        import os
        import time
        old_t = time.time() - 3600
        os.utime(old, (old_t, old_t))
        result = find_latest_report("eks-demo")
        assert result == new

    def test_finds_upgrade_report(self, tmp_reports_dir: Path) -> None:
        path = tmp_reports_dir / "eks-demo-upgrade-readiness-20260101_120000.md"
        path.write_text("upgrade")
        assert find_latest_report("eks-demo") == path

    def test_no_match_returns_none(self, tmp_reports_dir: Path) -> None:
        assert find_latest_report("nonexistent") is None

    def test_no_cluster_filter_returns_global_latest(
        self, tmp_reports_dir: Path
    ) -> None:
        a = tmp_reports_dir / "eks-a-assessment-20260101_120000.md"
        b = tmp_reports_dir / "eks-b-assessment-20260201_120000.md"
        a.write_text("a")
        b.write_text("b")
        import os
        import time
        a_t = time.time() - 3600
        os.utime(a, (a_t, a_t))
        result = find_latest_report()
        assert result == b


# ── report_search tool ──────────────────────────────────────────────


class TestReportSearchTool:
    def test_finds_relevant_section(self, sample_report: Path) -> None:
        result = _call(report_search, query="endpoint publicly accessible")
        assert "Cluster endpoint is publicly accessible" in result
        assert sample_report.name in result

    def test_explicit_path_takes_precedence(self, sample_report: Path) -> None:
        result = _call(report_search, query="liveness", report_path=str(sample_report))
        assert "Liveness probes" in result

    def test_path_not_found_returns_error(self, tmp_reports_dir: Path) -> None:
        result = _call(report_search, query="x", report_path="/nonexistent.md")
        assert "not found" in result.lower()

    def test_no_reports_returns_message(self, tmp_reports_dir: Path) -> None:
        result = _call(report_search, query="anything")
        assert "No report found" in result

    def test_no_match_in_report(self, sample_report: Path) -> None:
        result = _call(report_search, query="completely-unrelated-term-xyzzy")
        assert "No matches" in result
