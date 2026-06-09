"""Strands plugins — Skills for on-demand knowledge, Steering for guardrails."""

import logging

from strands import AgentSkills
from strands.models import BedrockModel
from strands.vended_plugins.steering import LLMSteeringHandler

from eks_review_agent.config import SKILLS_DIR

logger = logging.getLogger("eksreview")

# Tools that are always safe — skip the LLM steering call entirely.
# These are read-only or internal tools that never modify cluster state.
_ALWAYS_SAFE_TOOLS = {
    "think",
    "get_review_history",
    "knowledge_search",
    "report_search",
    "run_full_review",
    "run_upgrade_readiness",
    "save_report",
    "file_read",
    "file_write",
    "http_request",
    "skills",
    # MCP read-only check tools (if they somehow reach the main agent)
    "check_eks_security",
    "check_eks_resiliency",
    "check_eks_networking",
    "check_karpenter_best_practices",
    "check_cluster_autoscaler_best_practices",
    "check_eks_observability",
    "check_eks_upgrade_readiness",
}


STEERING_SYSTEM_PROMPT = """\
<role>Safety guardrail for an EKS operations review agent. Be fast and decisive.</role>

<safe_tools>
save_report, file_write, file_read, skills, think, get_review_history,
knowledge_search, report_search, run_full_review, run_upgrade_readiness,
http_request
</safe_tools>

<rules>
1. Tools in safe_tools → PROCEED immediately.
2. Read-only commands (get, describe, list, check, read, version) → PROCEED.
3. Modification commands (write, update, delete, scale, patch, restart):
   - PROCEED. The shell tool prompts the user to confirm the exact command
     before it executes, so you do not need to gate it here.
4. BLOCKED always (even with confirmation):
   - delete-cluster, delete-nodegroup, delete namespace, delete node
   - terminate-instances, rm -rf, drop database/table
   → GUIDE: "Blocked by safety policy"
5. Off-topic (not EKS/K8s/AWS) → GUIDE back to domain.
6. When in doubt → PROCEED.
</rules>
"""


def create_skills_plugin() -> AgentSkills:
    """Create the AgentSkills plugin that loads skills from the skills directory."""
    logger.info("Loading skills from: %s", SKILLS_DIR)
    return AgentSkills(skills=SKILLS_DIR)


def create_steering_handler() -> LLMSteeringHandler:
    """Create the LLM steering handler with fast-path bypass for safe tools.

    Tools in _ALWAYS_SAFE_TOOLS skip the Haiku LLM call entirely (~6s saved
    per tool call). Only unknown tools (primarily `shell`) go through the
    full LLM evaluation.
    """
    from eks_review_agent.core.model import _create_bedrock_session
    from strands.models.model import CacheConfig

    session = _create_bedrock_session()
    steering_model = BedrockModel(
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        boto_session=session,
        cache_config=CacheConfig(strategy="auto"),
        temperature=0.0,
        max_tokens=8192,
    )

    logger.info("Steering handler using Haiku 4.5 (global), %d tools bypassed", len(_ALWAYS_SAFE_TOOLS))

    handler = LLMSteeringHandler(
        system_prompt=STEERING_SYSTEM_PROMPT,
        model=steering_model,
    )

    # Monkey-patch steer_before_tool to bypass safe tools
    _original_steer = handler.steer_before_tool

    async def _fast_steer(*, agent, tool_use, **kwargs):
        tool_name = tool_use.get("name", "")
        if tool_name in _ALWAYS_SAFE_TOOLS:
            logger.debug("Steering bypass: %s (always safe)", tool_name)
            from strands.vended_plugins.steering import ToolSteeringAction
            return ToolSteeringAction(action="proceed", reason=f"{tool_name} is always safe")
        return await _original_steer(agent=agent, tool_use=tool_use, **kwargs)

    handler.steer_before_tool = _fast_steer
    return handler
