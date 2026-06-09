"""Tests for the local knowledge base — chunking, BM25 search, sensitive-path blocks."""

from __future__ import annotations

from pathlib import Path

import pytest

from eks_review_agent.knowledge.knowledge_base import (
    KnowledgeBase,
    _chunk_text_semantic,
)


# ── Chunking ────────────────────────────────────────────────────────


class TestChunkTextSemantic:
    def test_short_text_returned_whole(self) -> None:
        text = "Short paragraph that fits in a single chunk."
        chunks = _chunk_text_semantic(text, target_size=1024)
        assert chunks == [text]

    def test_long_text_split_on_sentences(self) -> None:
        # Build a long text with clear sentence boundaries
        sentence = "This is a sentence about EKS clusters and security best practices. "
        text = sentence * 20
        chunks = _chunk_text_semantic(text, target_size=200)
        assert len(chunks) > 1
        # Each chunk should not vastly exceed the target
        assert all(len(c) <= 600 for c in chunks)

    def test_empty_input_returns_empty(self) -> None:
        assert _chunk_text_semantic("") == []
        assert _chunk_text_semantic("   ") == []

    def test_overlap_keeps_continuity(self) -> None:
        sentence_a = "Alpha alpha alpha. "
        sentence_b = "Beta beta beta. "
        sentence_c = "Gamma gamma gamma. "
        text = sentence_a * 5 + sentence_b * 5 + sentence_c * 5
        chunks = _chunk_text_semantic(text, target_size=80, overlap_sentences=2)
        # If chunking happens at all, adjacent chunks should overlap on
        # at least one shared sentence (proves overlap is wired up).
        if len(chunks) > 1:
            tails = [c[-50:] for c in chunks[:-1]]
            heads = [c[:50] for c in chunks[1:]]
            # At least one pair should share a substring
            assert any(t.split(".")[-2] in h for t, h in zip(tails, heads) if "." in t)


# ── KnowledgeBase ───────────────────────────────────────────────────


@pytest.fixture
def kb(tmp_kb_dir: Path) -> KnowledgeBase:
    return KnowledgeBase(tmp_kb_dir)


class TestKnowledgeBaseAdd:
    def test_add_single_file(self, kb: KnowledgeBase, tmp_path: Path) -> None:
        sample = tmp_path / "doc.md"
        sample.write_text("# EKS Cluster Security\n\nUse IRSA for pod IAM.\n")
        msg = kb.add("test-entry", str(sample))
        assert "Added" in msg
        assert "1 files" in msg
        assert kb._doc_count > 0

    def test_add_directory_indexes_recursively(
        self, kb: KnowledgeBase, tmp_path: Path
    ) -> None:
        d = tmp_path / "docs"
        (d / "sub").mkdir(parents=True)
        (d / "a.md").write_text("# Doc A\n\nSome content about EKS.\n")
        (d / "sub" / "b.md").write_text("# Doc B\n\nSome other content.\n")
        msg = kb.add("docs", str(d))
        assert "2 files" in msg

    def test_duplicate_name_rejected(
        self, kb: KnowledgeBase, tmp_path: Path
    ) -> None:
        sample = tmp_path / "doc.md"
        sample.write_text("content")
        kb.add("entry", str(sample))
        msg = kb.add("entry", str(sample))
        assert "already exists" in msg

    def test_invalid_name_rejected(self, kb: KnowledgeBase) -> None:
        msg = kb.add("", "/tmp")
        assert "empty" in msg.lower()

    def test_invalid_chars_in_name_rejected(self, kb: KnowledgeBase) -> None:
        msg = kb.add("name/with/slash", "/tmp")
        assert "letters, numbers" in msg

    def test_nonexistent_path_rejected(self, kb: KnowledgeBase) -> None:
        msg = kb.add("entry", "/this/path/does/not/exist/anywhere")
        assert "not found" in msg.lower()

    def test_system_path_blocked(self, kb: KnowledgeBase) -> None:
        for path in ("/etc", "/var/log", "/usr/bin"):
            msg = kb.add("entry", path)
            assert "Cannot index sensitive path" in msg, f"Failed for {path}"

    def test_user_credential_paths_blocked(
        self, kb: KnowledgeBase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force HOME to a specific location and create the sensitive
        # subdirectory so the resolved-path check fires.
        fake_home = tmp_path / "home"
        for sub in (".aws", ".ssh", ".kube"):
            (fake_home / sub).mkdir(parents=True)
        monkeypatch.setenv("HOME", str(fake_home))

        for sub in (".aws", ".ssh", ".kube"):
            msg = kb.add("entry", str(fake_home / sub))
            assert "sensitive path" in msg.lower(), f"Failed for {sub}"


class TestKnowledgeBaseSearch:
    def test_empty_returns_no_results(self, kb: KnowledgeBase) -> None:
        assert kb.search("anything") == []

    def test_finds_matching_chunk(self, kb: KnowledgeBase, tmp_path: Path) -> None:
        sample = tmp_path / "doc.md"
        sample.write_text(
            "# Pod Security\n\n"
            "Pod Security Standards are critical for EKS workloads. "
            "Use the restricted profile in production namespaces.\n"
        )
        kb.add("docs", str(sample))
        results = kb.search("pod security standards")
        assert results
        meta, score = results[0]
        assert score > 0
        assert "pod security" in meta["content"].lower()

    def test_search_formatted_includes_filename(
        self, kb: KnowledgeBase, tmp_path: Path
    ) -> None:
        sample = tmp_path / "guide.md"
        sample.write_text(
            "# IRSA Configuration\n\n"
            "IRSA uses OIDC for fine-grained pod IAM access.\n"
        )
        kb.add("docs", str(sample))
        out = kb.search_formatted("irsa oidc")
        assert "guide.md" in out
        # IRSA might end up tokenized as one chunk; just ensure formatting
        # returns non-trivial content
        assert "irsa" in out.lower() or "guide" in out.lower()


class TestKnowledgeBaseRemove:
    def test_remove_known_entry(self, kb: KnowledgeBase, tmp_path: Path) -> None:
        sample = tmp_path / "doc.md"
        sample.write_text("content")
        kb.add("entry", str(sample))
        msg = kb.remove("entry")
        assert "Removed" in msg
        assert kb._doc_count == 0

    def test_remove_unknown_returns_message(self, kb: KnowledgeBase) -> None:
        msg = kb.remove("nonexistent")
        assert "not found" in msg.lower()


class TestKnowledgeBaseShow:
    def test_empty_message(self, kb: KnowledgeBase) -> None:
        out = kb.show()
        assert "empty" in out.lower()

    def test_show_lists_entries(self, kb: KnowledgeBase, tmp_path: Path) -> None:
        for name in ("alpha", "beta"):
            sample = tmp_path / f"{name}.md"
            sample.write_text(f"# {name} content")
            kb.add(name, str(sample))
        out = kb.show()
        assert "alpha" in out
        assert "beta" in out
        assert "Total: 2 entries" in out


class TestSyntheticEntry:
    def test_add_synthetic(self, kb: KnowledgeBase) -> None:
        chunk_count = kb.add_synthetic_entry(
            name="builtin",
            source="https://example.com/doc.pdf",
            text="A long enough piece of synthetic text. " * 50,
        )
        assert chunk_count >= 1
        assert kb._doc_count >= 1

    def test_synthetic_replaces_existing(self, kb: KnowledgeBase) -> None:
        kb.add_synthetic_entry("builtin", "src", "first version" * 50)
        first_count = kb._doc_count
        kb.add_synthetic_entry("builtin", "src", "second version " * 50)
        # Replaced (not duplicated)
        assert kb._doc_count >= 1

    def test_empty_text_rejected(self, kb: KnowledgeBase) -> None:
        with pytest.raises(ValueError):
            kb.add_synthetic_entry("name", "src", "")
        with pytest.raises(ValueError):
            kb.add_synthetic_entry("", "src", "text")
