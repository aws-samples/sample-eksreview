"""Agent factory — assembles the fully configured EKS review agent."""

import logging
import os
import uuid

from strands import Agent, AgentSkills, tool
from strands.models import BedrockModel
from strands.types.tools import ToolContext
from strands.agent.conversation_manager.sliding_window_conversation_manager import (
    SlidingWindowConversationManager,
)
# --- FALLBACK: Uncomment below and comment above to switch back to SummarizingConversationManager ---
# from strands.agent.conversation_manager.summarizing_conversation_manager import (
#     SummarizingConversationManager,
# )
from strands.session import FileSessionManager
from strands_tools import file_write, file_read, shell, http_request

from eks_review_agent.core.callbacks import streaming_callback
from eks_review_agent.config import (
    CONVERSATION_SUMMARY_RATIO,
    CONVERSATION_PRESERVE_MESSAGES,
    CONVERSATION_WINDOW_SIZE,
    SESSIONS_DIR,
)
from eks_review_agent.core.model import create_model
from eks_review_agent.core.observability import ObservabilityPlugin
from eks_review_agent.core.plugins import create_skills_plugin, create_steering_handler
from eks_review_agent.core.prompts import get_system_prompt
from eks_review_agent.reports.report_search import report_search
from eks_review_agent.orchestration.review_orchestrator import run_review, ALL_DOMAINS
from eks_review_agent.orchestration.upgrade_orchestrator import run_upgrade_check
from eks_review_agent.knowledge.skill_manager import load_persisted_custom_skills, get_builtin_skill_names
from eks_review_agent.tools import save_report, think, get_review_history
from eks_review_agent.knowledge.knowledge_tool import knowledge_search

logger = logging.getLogger("eksreview")


def _on_tool_complete_callback(tool_id: str) -> None:
    """Bridge callback from observability plugin to UI display."""
    from eks_review_agent.core.callbacks import on_tool_complete
    on_tool_complete(tool_id)


def _create_review_tool(mcp_client):
    """Create the run_full_review tool bound to a specific MCP client.

    The entire review pipeline — MCP checks, report compilation, and save —
    runs inside an ephemeral sub-agent. Nothing enters the main agent's
    context except a compact summary string.
    """

    @tool(context=True)
    def run_full_review(cluster_name: str, tool_context: ToolContext,
                        region: str = "", domains: str = "all") -> str:
        """Run an EKS best-practice review and compile a saved report.

        This tool runs MCP review checks, compiles a full markdown assessment
        report, and saves it to the reports directory. It returns a compact summary
        with the report file path and domain-level pass/fail counts.

        Use this for BOTH full reviews and single-domain checks:
        - Full review: run_full_review(cluster_name="eks-demo")
        - Single domain: run_full_review(cluster_name="eks-demo", domains="security")
        - Multiple domains: run_full_review(cluster_name="eks-demo", domains="security,networking")

        After this tool returns, use `report_search` to look up specific findings,
        remediation steps, or check details from the saved report.

        Args:
            cluster_name: The EKS cluster name to review.
            region: Optional AWS region (leave empty for default).
            domains: Comma-separated domains, or "all" for full review.
                     Valid: security, resiliency, networking, karpenter, cluster-autoscaler, observability

        Returns:
            Compact summary with report file path and domain pass/fail counts.
            Use report_search to look up specific details from the saved report.
        """
        if mcp_client is None:
            return "Error: MCP client not initialized."

        if domains.strip().lower() == "all":
            domain_list = None
        else:
            domain_list = [d.strip().lower() for d in domains.split(",") if d.strip()]
            invalid = [d for d in domain_list if d not in ALL_DOMAINS]
            if invalid:
                return (
                    f"Invalid domain(s): {', '.join(invalid)}. "
                    f"Valid domains: {', '.join(ALL_DOMAINS)}"
                )

        try:
            # Entire pipeline runs in the sub-agent's context
            summary = run_review(
                cluster_name=cluster_name,
                mcp_client=mcp_client,
                region=region if region else None,
                domains=domain_list,
            )

            # Set state on the main agent so /fix, /investigate can find the cluster
            tool_context.agent.state.set("last_reviewed_cluster", cluster_name)

            return summary

        except Exception as e:
            logger.exception("Review failed for %s", cluster_name)
            return f"Review failed: {e}. Try calling individual MCP tools directly."

    return run_full_review


def _create_upgrade_tool(mcp_client):
    """Create the run_upgrade_readiness tool bound to a specific MCP client.

    The entire upgrade pipeline — MCP check, analysis, http_request verification,
    and report save — runs inside an ephemeral sub-agent with thinking enabled.
    Only a compact summary enters the main agent's context.
    """

    @tool(context=True)
    def run_upgrade_readiness(cluster_name: str, tool_context: ToolContext,
                               region: str = "", target_version: str = "") -> str:
        """Run an EKS upgrade readiness assessment and compile a saved report.

        This tool runs 38 upgrade readiness checks via MCP, then an analysis agent
        verifies component compatibility against official sources, correlates findings,
        and saves a full upgrade readiness report with go/no-go verdict.

        Args:
            cluster_name: The EKS cluster name to check.
            region: AWS region where the cluster runs (required).
            target_version: Target K8s version (e.g., "1.32"). If empty, auto-detects next minor.

        Returns:
            Compact summary with verdict, blocker count, and report file path.
            Use report_search to look up specific findings from the saved report.
        """
        if mcp_client is None:
            return "Error: MCP client not initialized."

        try:
            summary = run_upgrade_check(
                cluster_name=cluster_name,
                mcp_client=mcp_client,
                region=region if region else None,
                target_version=target_version if target_version else None,
            )

            tool_context.agent.state.set("last_reviewed_cluster", cluster_name)

            return summary

        except Exception as e:
            logger.exception("Upgrade check failed for %s", cluster_name)
            return f"Upgrade check failed: {e}. Verify cluster connectivity and IAM permissions."

    return run_upgrade_readiness


def create_agent(
    mcp_tools: list,
    mcp_client,
    session_id: str | None = None,
) -> tuple[Agent, AgentSkills, ObservabilityPlugin, set[str]]:
    """Create the fully configured conversational EKS review agent.

    Args:
        mcp_tools: List of tools discovered from the MCP server.
        mcp_client: The connected MCPClient instance for direct tool calls.
        session_id: Optional session ID for resuming a previous session.

    Returns:
        Tuple of (Agent, AgentSkills, ObservabilityPlugin, builtin_skill_names).
    """
    model = create_model()
    run_full_review = _create_review_tool(mcp_client)
    run_upgrade_readiness = _create_upgrade_tool(mcp_client)

    # Filter out MCP review check tools from the main agent — they're handled
    # by the sub-agent via mcp_client.call_tool_sync() in mcp_checks.py.
    # Also filter out check_eks_upgrade_readiness — handled by upgrade sub-agent.
    from eks_review_agent.orchestration.mcp_checks import DOMAIN_TO_MCP_TOOL
    review_tool_names = set(DOMAIN_TO_MCP_TOOL.values())
    review_tool_names.add("check_eks_upgrade_readiness")
    filtered_mcp_tools = [
        t for t in mcp_tools
        if getattr(t, "tool_name", getattr(t, "name", "")) not in review_tool_names
    ]

    all_tools = filtered_mcp_tools + [
        file_write,
        file_read,
        shell,
        http_request,
        save_report,
        think,
        get_review_history,
        knowledge_search,
        report_search,
        run_full_review,
        run_upgrade_readiness,
    ]

    # Optional --no-shell mode: drop the shell tool entirely. /fix and
    # /investigate still work for read-only and Patchable findings (kubectl
    # patch / aws CLI suggestions) but the agent can't execute commands.
    if os.environ.get("EKS_REVIEW_NO_SHELL", "").strip().lower() in ("1", "true", "yes"):
        all_tools = [t for t in all_tools if t is not shell]
        logger.info("Shell tool disabled via --no-shell / EKS_REVIEW_NO_SHELL")

    logger.info(
        "Agent tools: %d MCP (of %d, %d review tools delegated to sub-agent) + %d built-in = %d total",
        len(filtered_mcp_tools),
        len(mcp_tools),
        len(mcp_tools) - len(filtered_mcp_tools),
        len(all_tools) - len(filtered_mcp_tools),
        len(all_tools),
    )

    # --- SlidingWindowConversationManager ---
    # Proactively manages context by truncating old tool results and trimming messages.
    # Old tool results are compressed to first/last 200 chars. The agent can recover
    # full details by using file_read on the saved report file.
    conversation_manager = SlidingWindowConversationManager(
        window_size=CONVERSATION_WINDOW_SIZE,
        should_truncate_results=True,
        per_turn=True,
    )

    # --- FALLBACK: Uncomment below and comment above to switch back to SummarizingConversationManager ---
    # from eks_review_agent.core.model import _create_bedrock_session
    # summarizer_model = BedrockModel(
    #     model_id="global.anthropic.claude-sonnet-4-6",
    #     boto_session=_create_bedrock_session(),
    #     additional_request_fields={"anthropic_beta": ["context-1m-2025-08-07"]},
    #     temperature=0.0,
    #     max_tokens=64000,
    # )
    # summarizer_agent = Agent(
    #     model=summarizer_model,
    #     system_prompt="Summarize EKS review conversation for context continuity. Rules:\n"
    #                   "- For each failed check: preserve check name, severity (C/H/M/L), resource count, "
    #                   "and namespace breakdown (e.g. 'scale-test: 41, app-ns: 8'). Do NOT list individual resource names.\n"
    #                   "- For passed checks: list names only, no details.\n"
    #                   "- Preserve the cluster name, region, and overall pass/fail counts per pillar.\n"
    #                   "- Preserve the saved report file path. If specific resource names are needed later, "
    #                   "use file_read on the saved report.\n"
    #                   "- Discard raw JSON, tool call metadata, and verbose outputs.\n"
    #                   "- Keep any remediation steps, user decisions, and action items from the conversation.",
    # )
    # conversation_manager = SummarizingConversationManager(
    #     summary_ratio=CONVERSATION_SUMMARY_RATIO,
    #     preserve_recent_messages=CONVERSATION_PRESERVE_MESSAGES,
    #     summarization_agent=summarizer_agent,
    # )

    sid = session_id or f"eks-review-{uuid.uuid4().hex[:8]}"
    session_manager = FileSessionManager(
        session_id=sid,
        storage_dir=SESSIONS_DIR,
    )
    logger.info("Session ID: %s (storage: %s)", sid, SESSIONS_DIR)

    skills_plugin = create_skills_plugin()

    # Track built-in skill names before adding custom ones
    builtin_names = get_builtin_skill_names(skills_plugin)

    # Load persisted custom skills from previous sessions
    custom_skills = load_persisted_custom_skills()
    if custom_skills:
        all_skills = skills_plugin.get_available_skills() + custom_skills
        skills_plugin.set_available_skills(all_skills)
        logger.info("Loaded %d persisted custom skills", len(custom_skills))

    steering_handler = create_steering_handler()
    observability_plugin = ObservabilityPlugin(on_complete=_on_tool_complete_callback)

    agent = Agent(
        model=model,
        tools=all_tools,
        system_prompt=get_system_prompt(),
        conversation_manager=conversation_manager,
        session_manager=session_manager,
        callback_handler=streaming_callback,
        record_direct_tool_call=False,
        plugins=[skills_plugin, steering_handler, observability_plugin],
    )

    return agent, skills_plugin, observability_plugin, builtin_names
