"""Tests for the knowledge_search agent tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from eks_review_agent.knowledge import knowledge_tool
from eks_review_agent.knowledge.knowledge_base import KnowledgeBase
from eks_review_agent.knowledge.knowledge_tool import knowledge_search


def _call(tool_obj, *args, **kwargs):
    if hasattr(tool_obj, "_tool_func"):
        return tool_obj._tool_func(*args, **kwargs)
    return tool_obj(*args, **kwargs)


@pytest.fixture
def fresh_kb(tmp_kb_dir: Path, monkeypatch: pytest.MonkeyPatch) -> KnowledgeBase:
    """Construct a fresh KnowledgeBase and have get_knowledge_base() return it.

    This isolates the singleton from other tests.
    """
    kb = KnowledgeBase(tmp_kb_dir)
    monkeypatch.setattr(knowledge_tool, "_kb", kb)
    monkeypatch.setattr(knowledge_tool, "get_knowledge_base", lambda: kb)
    return kb


class TestKnowledgeSearch:
    def test_empty_kb_returns_message(self, fresh_kb: KnowledgeBase) -> None:
        result = _call(knowledge_search, query="anything")
        assert "empty" in result.lower()
        assert "/knowledge add" in result

    def test_no_match_returns_message(
        self, fresh_kb: KnowledgeBase, tmp_path: Path
    ) -> None:
        sample = tmp_path / "doc.md"
        sample.write_text("# EKS Networking\n\nVPC CNI uses ENI for pod IPs.\n")
        fresh_kb.add("net", str(sample))
        result = _call(knowledge_search, query="completely-unrelated-xyzzy")
        assert "No relevant results" in result

    def test_returns_formatted_results(
        self, fresh_kb: KnowledgeBase, tmp_path: Path
    ) -> None:
        sample = tmp_path / "doc.md"
        sample.write_text(
            "# Pod Security\n\n"
            "Pod Security Standards are critical for EKS workloads. "
            "Use the restricted profile in production namespaces.\n"
        )
        fresh_kb.add("docs", str(sample))
        result = _call(knowledge_search, query="pod security standards", top_k=3)
        # Source chunk metadata should be visible
        assert "doc.md" in result
        # The match score should be in the formatted output
        assert "score:" in result
        # Content should appear
        assert "pod" in result.lower() or "security" in result.lower()

    def test_top_k_caps_results(
        self, fresh_kb: KnowledgeBase, tmp_path: Path
    ) -> None:
        for i in range(5):
            sample = tmp_path / f"doc{i}.md"
            sample.write_text(
                f"# Document {i}\n\nThis is about kubernetes networking and pods.\n"
            )
            fresh_kb.add(f"doc{i}", str(sample))
        # Top-1 should produce a single result block
        result = _call(knowledge_search, query="kubernetes networking pods", top_k=1)
        # Count "--- Result" delimiters (the formatter uses them)
        assert result.count("--- Result") == 1
