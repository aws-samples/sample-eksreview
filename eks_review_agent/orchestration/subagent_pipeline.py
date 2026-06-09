"""Shared sub-agent pipeline for orchestrators.

Both the review and upgrade orchestrators run an ephemeral Strands Agent
to compile a saved markdown report from raw MCP results, with the same
shape:

    1. Build a Bedrock model (same model as the main agent).
    2. Construct an Agent with a tight tool list, the eks-* skill plugin,
       a NullConversationManager (one-shot, no history), and
       callback_handler=None (no streaming).
    3. Run it under a Spinner with a known status message.
    4. Detect that a fresh report file was written (mtime > start).
    5. Accumulate the sub-agent's token usage into the session totals.
    6. Return the report path or None on failure.

This module owns all of that. Each orchestrator defines its own:

    - System prompt for the sub-agent.
    - Tool list (review: save_report + knowledge_search + file_*; upgrade
      adds shell, http_request, think conditional on
      --no-shell).
    - User prompt that frames the task.
    - Spinner message and the function that locates the saved report.
    - Bedrock pricing key for cost rollup.

The pipeline glues those together. New pipelines (e.g. cost analysis,
security deep-dive) plug in by providing the same six pieces.

Token-usage accumulation is delegated to session.Session — the
module-level get_subagent_usage / reset_subagent_usage / accumulate
helpers below are thin shims over the singleton Session for
backwards-compatibility.
"""

from __future__ import annotations

import logging
import sys
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from strands import Agent, AgentSkills
from strands.agent.conversation_manager import NullConversationManager

from eks_review_agent.config import SKILLS_DIR
from eks_review_agent.session import get_session
from eks_review_agent.core.spinner import Spinner

logger = logging.getLogger("eksreview")


# ── Session-wide sub-agent token usage ──────────────────────────────


def get_subagent_usage() -> dict[str, int]:
    """Get accumulated sub-agent token usage for the session."""
    return get_session().get_subagent_usage()


def reset_subagent_usage() -> None:
    """Reset sub-agent usage totals — primarily for tests."""
    get_session().reset_subagent_usage()


def _accumulate_subagent_usage(agent: Agent) -> None:
    """Add the sub-agent's usage to the session accumulator."""
    usage = getattr(agent.event_loop_metrics, "accumulated_usage", None)
    get_session().accumulate_usage(usage)


# ── Pipeline configuration ──────────────────────────────────────────


@dataclass
class SubAgentPipelineConfig:
    """Configuration for one orchestrator pipeline.

    Attributes:
        name: Short label for logs (e.g. "review", "upgrade").
        spinner_message: Text shown next to the rotating spinner during
            sub-agent execution. Example: "Compiling report".
        system_prompt: Sub-agent's system prompt — frames the task and
            references the skill it should activate.
        tools: List of @tool functions the sub-agent can call.
        user_prompt: The single-shot prompt the sub-agent receives.
        find_report: Callable that returns the most recent report Path
            for the given cluster name (or None). Used to detect that
            the sub-agent actually wrote a file, by comparing stat.mtime
            against the recorded `before_ts`.
        success_message: Template for the success line. Receives
            elapsed_seconds (float). Default shows "Done".
        failure_message: Template for the failure line. Receives
            elapsed_seconds (float). Default shows "Failed".
    """

    name: str
    spinner_message: str
    system_prompt: str
    tools: list[Any]
    user_prompt: str
    find_report: Callable[[str], Path | None]
    success_message: str = "✓ {label} ({elapsed:.0f}s)"
    failure_message: str = "✗ {label} ({elapsed:.0f}s)"
    extra_log_context: dict[str, Any] = field(default_factory=dict)


# ── Bedrock model factory (kept here so both pipelines share one impl) ──


def create_subagent_model() -> Any:
    """Create a Bedrock model configured for sub-agent use.

    Same model as the main agent (set via /model). Created directly
    rather than going through model.create_model() so that the global
    _current_model_name isn't overwritten when a sub-agent spins up.
    """
    from botocore.config import Config as BotocoreConfig
    from strands.models import BedrockModel as BM, CacheConfig

    from eks_review_agent.config import MODEL_MAX_TOKENS
    from eks_review_agent.core.model import (
        AVAILABLE_MODELS,
        _create_bedrock_session,
    )

    current_name = get_session().get_model_name()
    model_info = AVAILABLE_MODELS.get(current_name, {})
    model_id = model_info.get("model_id", "us.anthropic.claude-opus-4-8")
    context_window = model_info.get("context_window", 200000)
    is_1m = context_window >= 1000000

    extra_fields: dict[str, Any] = {}
    if is_1m:
        extra_fields["anthropic_beta"] = ["context-1m-2025-08-07"]

    model_max = model_info.get("max_output_tokens", 128000)
    effective_max_tokens = min(MODEL_MAX_TOKENS, model_max)

    session = _create_bedrock_session()
    logger.info(
        "Sub-agent model: %s, region=%s, max_tokens=%d, 1m=%s",
        model_id, session.region_name, effective_max_tokens, is_1m,
    )
    # Some newer models (e.g. Opus 4.8) reject the deprecated `temperature`
    # parameter. Only include it when the model supports it.
    bm_kwargs: dict[str, Any] = dict(
        model_id=model_id,
        boto_session=session,
        boto_client_config=BotocoreConfig(read_timeout=1200),
        additional_request_fields=extra_fields or None,
        cache_config=CacheConfig(strategy="auto"),
        max_tokens=effective_max_tokens,
    )
    if model_info.get("supports_temperature", True):
        bm_kwargs["temperature"] = 0.0

    return BM(**bm_kwargs)


# ── Pipeline runner ─────────────────────────────────────────────────


def run_subagent_pipeline(
    cluster_name: str,
    config: SubAgentPipelineConfig,
) -> str | None:
    """Run a sub-agent pipeline end-to-end.

    Returns the absolute path to the saved report, or None if the
    sub-agent failed or no report was written.
    """
    model = create_subagent_model()
    skills_plugin = AgentSkills(skills=SKILLS_DIR)

    sub_agent = Agent(
        model=model,
        tools=config.tools,
        system_prompt=config.system_prompt,
        callback_handler=None,
        conversation_manager=NullConversationManager(),
        plugins=[skills_plugin],
    )

    tool_names = [
        getattr(t, "__name__", None) or getattr(t, "tool_name", None) or str(t)
        for t in config.tools
    ]
    logger.info(
        "Sub-agent created: pipeline=%s, tools=%s, skills=%s, system_prompt_chars=%d",
        config.name,
        tool_names,
        [s.name for s in skills_plugin.get_available_skills()],
        len(sub_agent.system_prompt) if sub_agent.system_prompt else 0,
    )
    logger.info(
        "Sub-agent prompt size: pipeline=%s, chars=%d (~%d tokens)",
        config.name, len(config.user_prompt), len(config.user_prompt) // 4,
    )

    before_ts = _time.time()
    spinner = Spinner(config.spinner_message)
    spinner.start()

    try:
        logger.info("Invoking sub-agent for %s...", config.name)
        sub_agent(config.user_prompt)
        logger.info("Sub-agent completed: %s", config.name)
    except Exception as e:
        elapsed = spinner.stop()
        sys.stdout.write(
            "  " + config.failure_message.format(
                label=config.spinner_message, elapsed=elapsed,
            ) + "\n"
        )
        sys.stdout.flush()
        logger.exception("Sub-agent failed: %s, cluster=%s, error=%s",
                         config.name, cluster_name, e)
        _accumulate_subagent_usage(sub_agent)
        return None

    elapsed = spinner.stop()
    _accumulate_subagent_usage(sub_agent)

    sub_usage = getattr(sub_agent.event_loop_metrics, "accumulated_usage", None)
    sub_cycles = getattr(sub_agent.event_loop_metrics, "cycle_count", 0)
    logger.info(
        "Sub-agent finished: pipeline=%s, cycles=%s, usage=%s, elapsed=%.1fs",
        config.name, sub_cycles, sub_usage, elapsed,
    )

    result = config.find_report(cluster_name)
    if result and result.stat().st_mtime >= before_ts:
        sys.stdout.write(
            "  " + config.success_message.format(
                label=config.spinner_message, elapsed=elapsed,
            ) + "\n"
        )
        sys.stdout.flush()
        logger.info("Report created: %s -> %s", config.name, result)
        return str(result.absolute())

    sys.stdout.write(
        "  " + config.failure_message.format(
            label=f"No report file created ({config.name})", elapsed=elapsed,
        ) + "\n"
    )
    sys.stdout.flush()
    logger.warning(
        "Sub-agent finished but no fresh report found: pipeline=%s, cluster=%s",
        config.name, cluster_name,
    )
    return None
