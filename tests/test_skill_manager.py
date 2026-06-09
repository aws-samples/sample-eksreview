"""Tests for the skill manager — CRUD on custom skills + persistence."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from eks_review_agent.knowledge import skill_manager
from eks_review_agent.knowledge.skill_manager import (
    _add_skill,
    _info_skill,
    _list_skills,
    _load_custom_skill_paths,
    _load_skill_from_path,
    _remove_skill,
    _save_custom_skill_paths,
    get_builtin_skill_names,
    handle_skill_command,
    load_persisted_custom_skills,
)


@pytest.fixture
def tmp_skills_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the persisted custom-skills file to a tmp path."""
    f = tmp_path / "custom_skills.json"
    monkeypatch.setattr(skill_manager, "CUSTOM_SKILLS_FILE", f)
    return f


def _make_skill(name: str, instructions: str = "do stuff") -> MagicMock:
    """Build a Skill-like mock that the plugin will accept."""
    s = MagicMock()
    s.name = name
    s.description = "test skill"
    s.instructions = instructions
    return s


def _make_skills_plugin(skills: list | None = None) -> MagicMock:
    """Build an AgentSkills-like mock with get/set helpers."""
    plugin = MagicMock()
    state = {"skills": list(skills or [])}
    plugin.get_available_skills = lambda: list(state["skills"])
    plugin.set_available_skills = lambda new: state.update(skills=list(new))
    return plugin


# ── Persistence helpers ─────────────────────────────────────────────


class TestPersistence:
    def test_load_returns_empty_when_missing(self, tmp_skills_file: Path) -> None:
        assert _load_custom_skill_paths() == {}

    def test_save_then_load_roundtrip(self, tmp_skills_file: Path) -> None:
        _save_custom_skill_paths({"alpha": "/path/a", "beta": "/path/b"})
        assert _load_custom_skill_paths() == {"alpha": "/path/a", "beta": "/path/b"}

    def test_load_handles_corrupt_json(
        self, tmp_skills_file: Path
    ) -> None:
        tmp_skills_file.write_text("{ broken json")
        assert _load_custom_skill_paths() == {}


# ── Loading from path ───────────────────────────────────────────────


class TestLoadSkillFromPath:
    def test_loads_md_file(self, tmp_path: Path) -> None:
        md = tmp_path / "skill.md"
        md.write_text("# A custom skill\n\nDo this thing.")
        skill = _load_skill_from_path("custom", str(md))
        assert skill is not None
        assert skill.name == "custom"
        assert "custom skill" in skill.instructions

    def test_directory_without_skill_md_returns_none(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        d = tmp_path / "skill-dir"
        d.mkdir()
        skill = _load_skill_from_path("custom", str(d))
        assert skill is None

    def test_invalid_path_returns_none(self, tmp_path: Path) -> None:
        skill = _load_skill_from_path("custom", str(tmp_path / "nonexistent"))
        assert skill is None


# ── handle_skill_command dispatch ──────────────────────────────────


class TestHandleSkillCommand:
    def test_no_args_returns_usage(self) -> None:
        plugin = _make_skills_plugin()
        out = handle_skill_command(["/skill"], plugin, set())
        assert "Skill Commands" in out

    def test_unknown_action_returns_usage(self) -> None:
        plugin = _make_skills_plugin()
        out = handle_skill_command(["/skill", "wat"], plugin, set())
        assert "Skill Commands" in out

    def test_list_with_no_skills(self) -> None:
        plugin = _make_skills_plugin()
        out = handle_skill_command(["/skill", "list"], plugin, set())
        assert "No skills" in out

    def test_list_shows_loaded_skills(self) -> None:
        plugin = _make_skills_plugin([_make_skill("eks-knowledge"), _make_skill("custom-pci")])
        builtin = {"eks-knowledge"}
        out = handle_skill_command(["/skill", "list"], plugin, builtin)
        assert "eks-knowledge" in out
        assert "custom-pci" in out
        assert "built-in" in out
        assert "custom" in out

    def test_add_missing_args(self) -> None:
        plugin = _make_skills_plugin()
        out = handle_skill_command(["/skill", "add"], plugin, set())
        assert "Usage" in out

    def test_add_invalid_name_rejected(
        self, tmp_path: Path, tmp_skills_file: Path
    ) -> None:
        plugin = _make_skills_plugin()
        out = handle_skill_command(
            ["/skill", "add", "bad name with spaces!", str(tmp_path)],
            plugin, set(),
        )
        assert "Invalid skill name" in out

    def test_add_conflicts_with_builtin(
        self, tmp_path: Path, tmp_skills_file: Path
    ) -> None:
        plugin = _make_skills_plugin()
        builtin = {"eks-knowledge"}
        md = tmp_path / "skill.md"
        md.write_text("hi")
        out = handle_skill_command(
            ["/skill", "add", "eks-knowledge", str(md)],
            plugin, builtin,
        )
        assert "Cannot override" in out

    def test_add_succeeds_and_persists(
        self, tmp_path: Path, tmp_skills_file: Path
    ) -> None:
        plugin = _make_skills_plugin()
        md = tmp_path / "skill.md"
        md.write_text("# Custom\n\nInstructions.")
        out = handle_skill_command(
            ["/skill", "add", "my-custom", str(md)],
            plugin, set(),
        )
        assert "Added skill" in out
        # Should now be in the plugin
        assert any(s.name == "my-custom" for s in plugin.get_available_skills())
        # And persisted
        assert "my-custom" in _load_custom_skill_paths()

    def test_remove_missing_arg(self) -> None:
        plugin = _make_skills_plugin()
        out = handle_skill_command(["/skill", "remove"], plugin, set())
        assert "Usage" in out

    def test_remove_builtin_blocked(self) -> None:
        plugin = _make_skills_plugin([_make_skill("eks-knowledge")])
        builtin = {"eks-knowledge"}
        out = handle_skill_command(
            ["/skill", "remove", "eks-knowledge"], plugin, builtin
        )
        assert "Cannot remove built-in" in out

    def test_remove_unknown_returns_message(
        self, tmp_skills_file: Path
    ) -> None:
        plugin = _make_skills_plugin()
        out = handle_skill_command(
            ["/skill", "remove", "nonexistent"], plugin, set()
        )
        assert "not found" in out

    def test_remove_custom_skill(
        self, tmp_path: Path, tmp_skills_file: Path
    ) -> None:
        plugin = _make_skills_plugin([_make_skill("custom")])
        # Pre-populate the persistence file
        _save_custom_skill_paths({"custom": str(tmp_path / "skill.md")})
        out = handle_skill_command(["/skill", "remove", "custom"], plugin, set())
        assert "Removed skill" in out
        assert plugin.get_available_skills() == []
        assert "custom" not in _load_custom_skill_paths()

    def test_info_missing_arg(self) -> None:
        plugin = _make_skills_plugin()
        out = handle_skill_command(["/skill", "info"], plugin, set())
        assert "Usage" in out

    def test_info_unknown(self) -> None:
        plugin = _make_skills_plugin()
        out = handle_skill_command(
            ["/skill", "info", "nonexistent"], plugin, set()
        )
        assert "not found" in out

    def test_info_shows_details(self) -> None:
        skill = _make_skill("custom", instructions="A long instruction string here")
        plugin = _make_skills_plugin([skill])
        out = handle_skill_command(["/skill", "info", "custom"], plugin, set())
        assert "custom" in out
        assert "test skill" in out
        assert "instruction" in out.lower()


# ── load_persisted_custom_skills ───────────────────────────────────


class TestLoadPersisted:
    def test_loads_persisted_skills(
        self, tmp_path: Path, tmp_skills_file: Path
    ) -> None:
        md = tmp_path / "p.md"
        md.write_text("# Persisted\n\nDo persisted stuff.")
        _save_custom_skill_paths({"persisted": str(md)})
        skills = load_persisted_custom_skills()
        assert len(skills) == 1
        assert skills[0].name == "persisted"

    def test_empty_when_no_state(self, tmp_skills_file: Path) -> None:
        assert load_persisted_custom_skills() == []

    def test_skips_invalid_paths(
        self, tmp_path: Path, tmp_skills_file: Path
    ) -> None:
        _save_custom_skill_paths({
            "broken": str(tmp_path / "does-not-exist.md"),
        })
        # Should return empty list, not crash
        assert load_persisted_custom_skills() == []


def test_get_builtin_skill_names() -> None:
    plugin = _make_skills_plugin([_make_skill("a"), _make_skill("b")])
    names = get_builtin_skill_names(plugin)
    assert names == {"a", "b"}
