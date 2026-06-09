"""/tools handler — list all tools with load status."""

from __future__ import annotations

from eks_review_agent.cli.banner import GREEN, RED, RESET


def handle_tools(agent) -> str:
    """List every tool registered with the agent and whether it loaded."""
    validated = set(agent.tool_registry.get_all_tools_config().keys())
    registered = set(agent.tool_registry.registry.keys())
    all_names = sorted(registered | set(agent.tool_names))
    ok = len([n for n in all_names if n in validated])
    fail = len(all_names) - ok

    lines = [f"\n  Tools ({ok} loaded{f', {fail} failed' if fail else ''}):\n"]
    for name in all_names:
        marker = f"{GREEN}✓{RESET}" if name in validated else f"{RED}✗{RESET}"
        lines.append(f"    {marker} {name}")
    lines.append("")
    return "\n".join(lines)
