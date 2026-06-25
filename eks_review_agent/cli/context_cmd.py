"""/context handler — show context window usage + cost."""

from __future__ import annotations

from eks_review_agent.cli.banner import RESET
from eks_review_agent.core.model import (
    estimate_cost,
    get_current_context_window,
    get_current_model_name,
)


def estimate_session_cost(agent) -> float:
    """Estimate session cost from accumulated Bedrock usage metrics.

    Sums the main agent's usage and the sub-agent rollup. Both share the
    active model (set via /model), so we look up pricing once.
    """
    from eks_review_agent.orchestration.subagent_pipeline import get_subagent_usage

    main_usage = getattr(agent.event_loop_metrics, "accumulated_usage", None) or {}
    sub_usage = get_subagent_usage()
    model_name = get_current_model_name()
    return estimate_cost(main_usage, model_name) + estimate_cost(sub_usage, model_name)


def handle_context(agent, skills_plugin=None) -> str:
    """Build the multi-line /context output."""
    from eks_review_agent.orchestration.subagent_pipeline import get_subagent_usage

    model_name = get_current_model_name()
    max_tokens = get_current_context_window()

    msgs = agent.messages
    user_msgs = sum(1 for m in msgs if m.get("role") == "user")

    usage = getattr(agent.event_loop_metrics, "accumulated_usage", None)
    main_in = usage.get("inputTokens", 0) if usage else 0
    main_out = usage.get("outputTokens", 0) if usage else 0
    main_total = usage.get("totalTokens", 0) if usage else 0
    cache_read = usage.get("cacheReadInputTokens", 0) if usage else 0

    sub_usage = get_subagent_usage()
    sub_total = sub_usage.get("totalTokens", 0)

    # Estimate next request size based on the average input per turn
    num_turns = max(user_msgs, 1)
    if main_in > 0 or cache_read > 0:
        cache_write = usage.get("cacheWriteInputTokens", 0) if usage else 0
        total_input_per_turn = (main_in + cache_read + cache_write) // num_turns
        est = total_input_per_turn
    else:
        est = 0

    pct = min(100, (est / max_tokens) * 100) if max_tokens else 0
    bar_width = 30
    filled = int(bar_width * pct / 100)
    bar = "█" * filled + "░" * (bar_width - filled)

    color = "\033[32m" if pct < 60 else "\033[33m" if pct < 80 else "\033[31m"

    combined = main_total + sub_total
    cost = estimate_session_cost(agent)

    lines = [
        f"\n  Context: {color}{bar}{RESET} {pct:.0f}%  (~{est:,} / {max_tokens:,} tokens)",
        f"  Model:   {model_name}  |  {len(msgs)} messages ({user_msgs} turns)",
    ]
    if main_total > 0:
        token_parts = [f"{main_in:,} in", f"{main_out:,} out"]
        if cache_read:
            token_parts.append(f"{cache_read:,} cache-read")
        cache_write = usage.get("cacheWriteInputTokens", 0) if usage else 0
        if cache_write:
            token_parts.append(f"{cache_write:,} cache-write")
        lines.append(f"  Tokens:  {', '.join(token_parts)}")
    if sub_total > 0:
        lines.append(f"  Sub-agents: {sub_total:,} tokens  |  Combined: {combined:,}")
    lines.append(f"  Cost:    ~${cost:.4f}")

    if skills_plugin is not None:
        skill_list = skills_plugin.get_available_skills(agent)
        if skill_list:
            names = ", ".join(s.name for s in skill_list)
            lines.append(f"  Skills:  {names}")
        else:
            lines.append("  Skills:  none loaded")

    if pct > 80:
        lines.append(f"  {color}Context getting full — consider starting a new session.{RESET}")
    lines.append("")
    return "\n".join(lines)
