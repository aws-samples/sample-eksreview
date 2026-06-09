"""Runtime skill management — add/remove/list skills without restart.

Persists custom skill mappings to a JSON file so they survive restarts.
Built-in skills (from ./skills/) are always loaded and can't be removed.
"""

import json
import logging
import os
from pathlib import Path

from strands import AgentSkills, Skill

from eks_review_agent.config import SESSIONS_DIR

logger = logging.getLogger("eksreview")

CUSTOM_SKILLS_FILE = Path(SESSIONS_DIR) / "custom_skills.json"


def _load_custom_skill_paths() -> dict[str, str]:
    """Load persisted custom skill name→path mappings."""
    if CUSTOM_SKILLS_FILE.exists():
        try:
            return json.loads(CUSTOM_SKILLS_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_custom_skill_paths(mappings: dict[str, str]):
    """Persist custom skill name→path mappings."""
    CUSTOM_SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM_SKILLS_FILE.write_text(json.dumps(mappings, indent=2))


def _load_skill_from_path(name: str, path_str: str) -> Skill | None:
    """Load a Skill from a directory or markdown file path."""
    path = Path(path_str).expanduser().resolve()

    if path.is_dir():
        skill_md = path / "SKILL.md"
        if skill_md.exists():
            return Skill.from_file(str(path))
        else:
            logger.warning("No SKILL.md found in %s", path)
            return None

    elif path.is_file() and path.suffix in (".md", ".markdown"):
        content = path.read_text()
        return Skill(
            name=name,
            description=f"Custom skill loaded from {path.name}",
            instructions=content,
        )

    else:
        logger.warning("Invalid skill path: %s (must be a directory with SKILL.md or a .md file)", path)
        return None


def load_persisted_custom_skills() -> list[Skill]:
    """Load all persisted custom skills at startup."""
    mappings = _load_custom_skill_paths()
    skills = []
    for name, path_str in mappings.items():
        skill = _load_skill_from_path(name, path_str)
        if skill:
            skills.append(skill)
            logger.info("Loaded persisted custom skill: %s from %s", name, path_str)
        else:
            logger.warning("Failed to load persisted skill: %s from %s", name, path_str)
    return skills


def get_builtin_skill_names(skills_plugin: AgentSkills) -> set[str]:
    """Get names of built-in skills (loaded from ./skills/)."""
    # Called once at startup before custom skills are added
    return {s.name for s in skills_plugin.get_available_skills()}


def handle_skill_command(cmd_parts: list[str], skills_plugin: AgentSkills, builtin_names: set[str]) -> str:
    """Handle /skill commands. Returns output to print.

    Commands:
        /skill list                  — Show all skills
        /skill add <name> <path>     — Add a custom skill
        /skill remove <name>         — Remove a custom skill
        /skill info <name>           — Show skill details
    """
    if len(cmd_parts) < 2:
        return _usage()

    action = cmd_parts[1].lower()

    if action == "list":
        return _list_skills(skills_plugin, builtin_names)

    elif action == "add":
        if len(cmd_parts) < 4:
            return "  Usage: /skill add <name> <path>\n  Example: /skill add pci-compliance /path/to/skill/"
        name = cmd_parts[2]
        path_str = " ".join(cmd_parts[3:])  # Handle paths with spaces
        return _add_skill(name, path_str, skills_plugin, builtin_names)

    elif action == "remove":
        if len(cmd_parts) < 3:
            return "  Usage: /skill remove <name>"
        name = cmd_parts[2]
        return _remove_skill(name, skills_plugin, builtin_names)

    elif action == "info":
        if len(cmd_parts) < 3:
            return "  Usage: /skill info <name>"
        name = cmd_parts[2]
        return _info_skill(name, skills_plugin)

    else:
        return _usage()


def _usage() -> str:
    return (
        "\n  Skill Commands\n"
        "  ──────────────\n"
        "    /skill list                  Show all loaded skills\n"
        "    /skill add <name> <path>     Add a custom skill from directory or .md file\n"
        "    /skill remove <name>         Remove a custom skill\n"
        "    /skill info <name>           Show skill details\n"
    )


def _list_skills(skills_plugin: AgentSkills, builtin_names: set[str]) -> str:
    skills = skills_plugin.get_available_skills()
    if not skills:
        return "  No skills loaded."

    lines = ["\n  Loaded Skills", "  ─────────────"]
    for s in skills:
        tag = "built-in" if s.name in builtin_names else "custom"
        lines.append(f"    {s.name} [{tag}] — {s.description[:60]}...")
    lines.append(f"\n  Total: {len(skills)} skills")
    return "\n".join(lines)


def _add_skill(name: str, path_str: str, skills_plugin: AgentSkills, builtin_names: set[str]) -> str:
    # Validate name
    if not name or len(name) > 100 or not all(c.isalnum() or c in "-_." for c in name):
        return "  Invalid skill name. Use letters, numbers, dashes, dots only."

    # Check if name conflicts with built-in
    if name in builtin_names:
        return f"  Cannot override built-in skill '{name}'."

    # Check if already exists as custom
    existing = {s.name for s in skills_plugin.get_available_skills()}
    if name in existing:
        return f"  Skill '{name}' already exists. Remove it first with /skill remove {name}"

    # Load the skill
    skill = _load_skill_from_path(name, path_str)
    if not skill:
        return f"  Failed to load skill from: {path_str}\n  Path must be a directory with SKILL.md or a .md file."

    # Add to plugin
    current = skills_plugin.get_available_skills()
    skills_plugin.set_available_skills(current + [skill])

    # Persist
    mappings = _load_custom_skill_paths()
    mappings[name] = path_str
    _save_custom_skill_paths(mappings)

    return f"  Added skill '{name}' from {path_str}"


def _remove_skill(name: str, skills_plugin: AgentSkills, builtin_names: set[str]) -> str:
    if name in builtin_names:
        return f"  Cannot remove built-in skill '{name}'."

    current = skills_plugin.get_available_skills()
    filtered = [s for s in current if s.name != name]

    if len(filtered) == len(current):
        return f"  Skill '{name}' not found."

    skills_plugin.set_available_skills(filtered)

    # Remove from persistence
    mappings = _load_custom_skill_paths()
    mappings.pop(name, None)
    _save_custom_skill_paths(mappings)

    return f"  Removed skill '{name}'"


def _info_skill(name: str, skills_plugin: AgentSkills) -> str:
    for s in skills_plugin.get_available_skills():
        if s.name == name:
            lines = [
                f"\n  Skill: {s.name}",
                f"  Description: {s.description}",
            ]
            if s.instructions:
                preview = s.instructions[:300].replace("\n", "\n    ")
                lines.append(f"  Instructions (preview):\n    {preview}...")
            return "\n".join(lines)
    return f"  Skill '{name}' not found."
