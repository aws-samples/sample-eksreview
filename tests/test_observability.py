"""Tests for the observability plugin — destructive-command guard + rate-limit hook."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from eks_review_agent.core.observability import (
    ObservabilityPlugin,
    _DESTRUCTIVE_SHELL_PATTERNS,
    _match_destructive,
    _tokenize_command,
)
from eks_review_agent.core.rate_limiter import MCPRateLimiter, get_rate_limiter


# ── Tokenizer ────────────────────────────────────────────────────────


class TestTokenizer:
    def test_empty(self) -> None:
        assert _tokenize_command("") == []
        assert _tokenize_command("   ") == []

    def test_simple(self) -> None:
        assert _tokenize_command("kubectl get pods") == ["kubectl", "get", "pods"]

    def test_lowercases(self) -> None:
        assert _tokenize_command("KUBECTL Delete Namespace") == ["kubectl", "delete", "namespace"]

    @pytest.mark.parametrize("sep", ["|", "&&", "||", ";", "(", ")", "`"])
    def test_shell_separators_split_tokens(self, sep: str) -> None:
        cmd = f"kubectl get {sep} kubectl delete namespace foo"
        toks = _tokenize_command(cmd)
        assert "delete" in toks
        assert "namespace" in toks


# ── Destructive matcher ──────────────────────────────────────────────


class TestMatchDestructive:
    @pytest.mark.parametrize(
        "cmd,expected",
        [
            ("aws eks delete-cluster --name prod", "aws eks delete-cluster"),
            ("aws eks delete-nodegroup --name workers", "aws eks delete-nodegroup"),
            ("aws eks delete-fargate-profile --name fp1", "aws eks delete-fargate-profile"),
            ("aws eks delete-addon --addon-name vpc-cni", "aws eks delete-addon"),
            ("eksctl delete cluster --name eks-demo", "eksctl delete cluster"),
            ("eksctl delete nodegroup workers", "eksctl delete nodegroup"),
            ("aws ec2 terminate-instances --instance-ids i-abc", "aws ec2 terminate-instances"),
            ("aws ec2 delete-vpc --vpc-id vpc-1", "aws ec2 delete-vpc"),
            ("kubectl delete namespace metrics-server", "kubectl delete namespace"),
            ("kubectl delete ns metrics-server", "kubectl delete ns"),
            ("kubectl delete node ip-10-0-1-23", "kubectl delete node"),
            ("kubectl delete nodes --all", "kubectl delete nodes"),
            ("kubectl delete cluster --selector=foo", "kubectl delete cluster"),
            ("kubectl delete all --all -n stuck", "kubectl delete all"),
            ("kubectl drain node-1 --ignore-daemonsets", "kubectl drain"),
            ("helm uninstall my-release -n prod", "helm uninstall"),
            ("helm delete legacy-release", "helm delete"),
            ("rm -rf /", "rm -rf /"),
            ("rm -rf /*", "rm -rf /*"),
            ("DROP DATABASE production", "drop database"),
            ("drop table users", "drop table"),
            ("TRUNCATE TABLE logs", "truncate table"),
        ],
    )
    def test_blocks_destructive(self, cmd: str, expected: str) -> None:
        matched = _match_destructive(cmd)
        assert matched == expected, f"Expected {expected!r}, got {matched!r} for {cmd!r}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "",
            "   ",
            # Resource names containing "delete" / "deletion" (the original FP class)
            "kubectl get pod my-app-deletion-controller",
            "kubectl describe deployment delete-handler",
            "kubectl logs -l app=delete-job",
            # Read-only AWS / kubectl
            "aws eks list-clusters",
            "aws eks describe-cluster --name eks-demo",
            "kubectl get pdb -A -o wide",
            # Patches that aren't full deletes
            'kubectl patch deployment foo --type=json -p=\'[{"op":"remove"}]\'',
            # Allowed update commands
            "aws eks update-cluster-config --name foo",
        ],
    )
    def test_allows_benign(self, cmd: str) -> None:
        assert _match_destructive(cmd) is None

    def test_chained_command_caught(self) -> None:
        # Common chained shape — separator must split tokens
        cmd = "kubectl get pods | grep stuck && kubectl delete namespace stuck"
        assert _match_destructive(cmd) == "kubectl delete namespace"

    def test_destructive_in_subshell(self) -> None:
        cmd = "echo hello && (aws eks delete-cluster --name prod)"
        assert _match_destructive(cmd) == "aws eks delete-cluster"

    def test_pattern_list_consistency(self) -> None:
        # Every pattern is a tuple of lowercase strings, no empty entries.
        for pattern in _DESTRUCTIVE_SHELL_PATTERNS:
            assert isinstance(pattern, tuple)
            assert all(isinstance(t, str) and t for t in pattern)
            assert all(t == t.lower() for t in pattern)


# ── Hook integration ─────────────────────────────────────────────────


class TestBeforeToolHookGuard:
    def test_shell_destructive_sets_cancel_tool(self, make_before_tool_event) -> None:
        plugin = ObservabilityPlugin()
        event = make_before_tool_event(
            "shell", {"command": "kubectl delete namespace prod"}
        )
        plugin.on_before_tool(event)
        assert event.cancel_tool is not False
        assert "kubectl delete namespace" in str(event.cancel_tool)
        assert "blocked by safety policy" in str(event.cancel_tool)

    def test_shell_benign_passes_through(self, make_before_tool_event) -> None:
        plugin = ObservabilityPlugin()
        event = make_before_tool_event(
            "shell", {"command": "kubectl get pods -n monitoring"}
        )
        plugin.on_before_tool(event)
        assert event.cancel_tool is False

    def test_non_shell_tool_not_checked(self, make_before_tool_event) -> None:
        plugin = ObservabilityPlugin()
        # Even if the tool input *contains* a destructive substring, only
        # the shell tool's command goes through the guard.
        event = make_before_tool_event(
            "knowledge_search", {"query": "aws eks delete-cluster"}
        )
        plugin.on_before_tool(event)
        assert event.cancel_tool is False

    def test_mcp_rate_limit_uses_cancel_tool(
        self, make_before_tool_event, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force the limiter to refuse the next call.
        rl = get_rate_limiter()
        rl.reset()
        # Patch the singleton with a tighter limit just for this test.
        replacement = MCPRateLimiter(soft_limit=0, hard_limit=0, burst_limit=100, burst_window_s=60)
        monkeypatch.setattr(
            "eks_review_agent.core.observability.get_rate_limiter",
            lambda: replacement,
        )

        plugin = ObservabilityPlugin()
        event = make_before_tool_event("check_eks_security", {})
        # Should NOT raise — should set cancel_tool instead
        plugin.on_before_tool(event)
        assert event.cancel_tool is not False
        assert "rate limiter" in str(event.cancel_tool).lower()


class TestAfterToolHook:
    def test_records_metrics(self, make_before_tool_event) -> None:
        plugin = ObservabilityPlugin()
        # Build a stub agent that exposes a real-ish state interface.
        state = {"tool_metrics": None}

        class _State:
            def get(self, key, default=None):
                return state.get(key, default)

            def set(self, key, value):
                state[key] = value

        agent = MagicMock()
        agent.state = _State()
        plugin.init_agent(agent)
        before = make_before_tool_event("knowledge_search")
        plugin.on_before_tool(before)

        after = MagicMock()
        after.tool_use = before.tool_use
        after.exception = None
        after.agent = agent
        plugin.on_after_tool(after)

        metrics = state["tool_metrics"]
        assert metrics["total_calls"] == 1
        assert metrics["total_errors"] == 0
        assert metrics["total_time_s"] >= 0


class TestPluginReset:
    def test_reset_clears_per_turn_state(self, make_before_tool_event) -> None:
        plugin = ObservabilityPlugin()
        plugin.on_before_tool(make_before_tool_event("knowledge_search"))
        assert plugin._tool_call_count == 1
        plugin.reset()
        assert plugin._tool_call_count == 0
        assert plugin._tool_start_times == {}
