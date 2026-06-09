"""/model handler — interactive picker or direct switch."""

from __future__ import annotations

from eks_review_agent.core.model import (
    AVAILABLE_MODELS,
    create_model_by_name,
    get_current_model_name,
)
from eks_review_agent.ui.picker import pick


def handle_model(user_input: str, agent) -> str:
    """Handle /model — interactive selection or direct switch.

    `/model <name>` switches directly. Bare `/model` opens the picker
    pre-positioned on the current model.
    """
    parts = user_input.split(None, 1)

    # /model <name> — direct switch path
    if len(parts) >= 2 and parts[1].strip():
        name = parts[1].strip()
        model, result = create_model_by_name(name)
        if model is None:
            return f"  {result}"
        agent.model = model
        return f"  Switched to {result}"

    # No args — interactive picker
    current = get_current_model_name()
    models = list(AVAILABLE_MODELS.items())

    options: list[str] = []
    current_idx = 0
    for i, (name, info) in enumerate(models):
        tag = " (current)" if name == current else ""
        options.append(f"{name}  {info['description']}{tag}")
        if name == current:
            current_idx = i

    choice = pick(
        options,
        title=f"Select a model (current: {current}):",
        selected=current_idx,
    )

    if choice is None:
        return "  Cancelled."

    chosen_name = models[choice][0]
    if chosen_name == current:
        return f"  Already using {current}."

    model, result = create_model_by_name(chosen_name)
    if model is None:
        return f"  {result}"
    agent.model = model
    return f"  Switched to {result}"
