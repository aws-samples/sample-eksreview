"""Observability plugin using Strands-native hooks for tool timing and diagnostics."""

import logging
import re
import time

from strands.plugins import Plugin, hook
from strands.hooks import BeforeToolCallEvent, AfterToolCallEvent

from eks_review_agent.core.rate_limiter import (
    get_rate_limiter,
    RateLimitExceeded,
)

logger = logging.getLogger("eksreview")

# Tool names that hit the MCP server (and therefore EKS/EC2/STS/K8s APIs)
# from the main agent's tool loop. The orchestrator entrypoints
# (run_full_review, run_upgrade_readiness) call MCP internally and are
# already counted at the call site, so we don't double-count them here.
_MCP_TOOL_PREFIXES = ("check_eks_", "check_karpenter_", "check_cluster_autoscaler_")


# ── Destructive shell command guard (#12) ───────────────────────────
# These patterns match the *actual* shell command the agent is about to
# execute. This is the real guard at the execution boundary — it sees the
# literal command string and cannot be bypassed by how the agent phrases
# its intent. The shell tool's own interactive confirmation prompts the
# user before any command runs; this guard hard-blocks the destructive
# ones regardless of that confirmation.
#
# Each entry is a tuple of consecutive tokens that must appear in order
# in the tokenized command. Matching by token sequence avoids false
# positives like "kubectl get pod my-deletion-controller" because we
# look for "delete" + "pod" / "delete" + "namespace" / etc. as adjacent
# tokens, not free substrings.
_DESTRUCTIVE_SHELL_PATTERNS: tuple[tuple[str, ...], ...] = (
    # AWS EKS destructive operations
    ("aws", "eks", "delete-cluster"),
    ("aws", "eks", "delete-nodegroup"),
    ("aws", "eks", "delete-fargate-profile"),
    ("aws", "eks", "delete-addon"),
    ("eksctl", "delete", "cluster"),
    ("eksctl", "delete", "nodegroup"),
    ("eksctl", "delete", "fargateprofile"),
    # AWS EC2 destructive operations
    ("aws", "ec2", "terminate-instances"),
    ("aws", "ec2", "delete-vpc"),
    ("aws", "ec2", "delete-subnet"),
    ("aws", "ec2", "delete-security-group"),
    # Kubernetes destructive operations (sequenced delete + resource type)
    ("kubectl", "delete", "namespace"),
    ("kubectl", "delete", "ns"),
    ("kubectl", "delete", "node"),
    ("kubectl", "delete", "nodes"),
    ("kubectl", "delete", "cluster"),
    ("kubectl", "delete", "all"),
    ("kubectl", "drain"),  # node drain is destructive enough to gate
    # Helm uninstall (full release removal)
    ("helm", "uninstall"),
    ("helm", "delete"),  # legacy helm v2 syntax, still works
    # Filesystem / DB classics
    ("rm", "-rf", "/"),
    ("rm", "-rf", "/*"),
    ("rmdir", "/"),
    ("drop", "database"),
    ("drop", "table"),
    ("truncate", "table"),
)

# Tokens that should never appear at the very start of a piped/chained
# command segment regardless of context. These catch common shapes that
# don't fit the "tool subcommand resource" template above.
_DESTRUCTIVE_BARE_TOKENS = frozenset({
    # nothing yet — keep this knob available for future patterns
})


def _tokenize_command(cmd: str) -> list[str]:
    """Split a shell command into a flat token list for pattern matching.

    Aggressive but safe: lowercases, splits on whitespace and common shell
    operators (|, &, ;, $(, `). Keeps quoted segments together. Used only
    for the destructive-command guard — not for executing anything.
    """
    if not cmd:
        return []
    # Replace shell separators with spaces so they don't merge tokens.
    normalized = re.sub(r"[|&;()`]+", " ", cmd.lower())
    # Collapse multi-whitespace and strip empties.
    return [tok for tok in normalized.split() if tok]


def _match_destructive(cmd: str) -> str | None:
    """Return the matched pattern (joined) if the command is destructive, else None."""
    tokens = _tokenize_command(cmd)
    if not tokens:
        return None

    # Sequential token-window match for each pattern.
    for pattern in _DESTRUCTIVE_SHELL_PATTERNS:
        if len(pattern) > len(tokens):
            continue
        plen = len(pattern)
        for start in range(len(tokens) - plen + 1):
            window = tokens[start:start + plen]
            if all(_token_matches(window[i], pattern[i]) for i in range(plen)):
                return " ".join(pattern)

    if tokens and tokens[0] in _DESTRUCTIVE_BARE_TOKENS:
        return tokens[0]

    return None


def _token_matches(token: str, pattern_token: str) -> bool:
    """Match a single token against a pattern token.

    Currently exact match. Kept as a function so future extensions
    (prefix wildcards, regex tokens) plug in cleanly.
    """
    return token == pattern_token


class ObservabilityPlugin(Plugin):
    """Track tool execution metrics using Strands-native hooks."""

    name = "eks-observability"

    def __init__(self, on_complete=None):
        super().__init__()
        self._tool_start_times: dict[str, float] = {}
        self._tool_call_count: int = 0
        self._on_complete = on_complete  # Callback for tool completion display

    def init_agent(self, agent) -> None:
        """Initialize metrics in agent state."""
        if agent.state.get("tool_metrics") is None:
            agent.state.set("tool_metrics", {
                "total_calls": 0,
                "total_errors": 0,
                "total_time_s": 0.0,
            })

    @hook
    def on_before_tool(self, event: BeforeToolCallEvent) -> None:
        """Record tool call start time and display shell commands."""
        tool_id = event.tool_use.get("toolUseId", "")
        tool_name = event.tool_use.get("name", "unknown")
        self._tool_call_count += 1
        self._tool_start_times[tool_id] = time.monotonic()

        # Apply MCP rate limiting for direct agent-side MCP tool calls.
        # Orchestrator paths (run_full_review, run_upgrade_readiness) count
        # at their own call sites, so we only count the prefixed MCP tools
        # the agent might invoke directly.
        #
        # Use Strands' event.cancel_tool API rather than raising — Strands
        # lets hook exceptions propagate (which would kill the agent loop),
        # whereas cancel_tool turns into a structured tool error the LLM
        # can read and explain to the user.
        if any(tool_name.startswith(p) for p in _MCP_TOOL_PREFIXES):
            try:
                get_rate_limiter().check_and_increment(tool_name)
            except RateLimitExceeded as rle:
                self._tool_start_times.pop(tool_id, None)
                self._tool_call_count -= 1
                logger.warning("MCP rate-limit blocked %s: %s", tool_name, rle)
                event.cancel_tool = (
                    f"Tool '{tool_name}' refused by rate limiter: {rle} "
                    "Inform the user the session has hit its MCP call limit."
                )
                return

        # Display the shell command before execution (input is fully parsed here).
        # Also apply the destructive-command guard at this exact point — this
        # is the only boundary where we have the literal command string the
        # agent is about to run, which is what should actually be matched
        # against the block list (#12).
        if tool_name == "shell":
            import sys
            tool_input = event.tool_use.get("input", {})
            cmd = tool_input.get("command", "") if isinstance(tool_input, dict) else ""

            matched = _match_destructive(cmd) if cmd else None
            if matched:
                self._tool_start_times.pop(tool_id, None)
                self._tool_call_count -= 1
                logger.warning(
                    "BLOCKED destructive shell command (matched %r): %s",
                    matched, cmd[:200],
                )
                event.cancel_tool = (
                    f"Refused — '{matched}' is blocked by safety policy. "
                    "The agent cannot execute destructive cluster, node, or "
                    "data-deletion commands. Inform the user this command is "
                    "not permitted; suggest a safer alternative or have them "
                    "run it manually outside the agent."
                )
                return

            if cmd:
                CYAN = "\033[36m"
                RESET = "\033[0m"
                display_cmd = cmd if len(cmd) <= 120 else cmd[:117] + "..."
                sys.stdout.write(f"  $ {CYAN}{display_cmd}{RESET}\n")
                sys.stdout.flush()

        logger.info(
            "tool_start | tool=#%d name=%s id=%s",
            self._tool_call_count, tool_name, tool_id[:12],
        )

    @hook
    def on_after_tool(self, event: AfterToolCallEvent) -> None:
        """Record tool call completion, duration, and show completion display."""
        tool_id = event.tool_use.get("toolUseId", "")
        tool_name = event.tool_use.get("name", "unknown")

        has_error = event.exception is not None

        elapsed = 0.0
        if tool_id in self._tool_start_times:
            elapsed = time.monotonic() - self._tool_start_times.pop(tool_id)

        # Show q-cli style completion line
        if has_error:
            RED = "\033[31m"
            DIM = "\033[2m"
            RESET = "\033[0m"
            import sys
            sys.stdout.write(f" {DIM}⋮{RESET}\n {RED}●{RESET} {tool_name} failed ({elapsed:.1f}s)\n")
            sys.stdout.flush()
            logger.warning("Tool %s failed (%.1fs): %s", tool_name, elapsed, event.exception)
        else:
            if self._on_complete:
                self._on_complete(tool_id)
            logger.info("tool_complete | name=%s elapsed=%.1fs", tool_name, elapsed)

        # Update agent state metrics
        metrics = event.agent.state.get("tool_metrics") or {}
        metrics["total_calls"] = metrics.get("total_calls", 0) + 1
        metrics["total_time_s"] = metrics.get("total_time_s", 0.0) + elapsed
        if has_error:
            metrics["total_errors"] = metrics.get("total_errors", 0) + 1
        event.agent.state.set("tool_metrics", metrics)

    def reset(self):
        """Reset per-turn counters."""
        self._tool_start_times.clear()
        self._tool_call_count = 0
