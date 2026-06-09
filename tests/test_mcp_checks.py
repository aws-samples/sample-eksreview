"""Tests for mcp_checks — domain mapping, collect_check_results, content extraction."""

from __future__ import annotations

import json

import pytest

from eks_review_agent.orchestration.mcp_checks import (
    ALL_DOMAINS,
    DOMAIN_TO_MCP_TOOL,
    _extract_tool_content,
    collect_check_results,
)


class TestDomainMapping:
    def test_all_domains_mapped(self) -> None:
        assert set(ALL_DOMAINS) == set(DOMAIN_TO_MCP_TOOL.keys())

    def test_expected_domains_present(self) -> None:
        for d in ("security", "resiliency", "networking", "karpenter",
                  "cluster-autoscaler", "observability"):
            assert d in DOMAIN_TO_MCP_TOOL


class TestExtractToolContent:
    def test_text_content(self) -> None:
        result = {"content": [{"text": "hello"}, {"text": "world"}]}
        assert _extract_tool_content(result) == "hello\nworld"

    def test_json_content_serialized(self) -> None:
        result = {"content": [{"json": {"a": 1, "b": "two"}}]}
        out = _extract_tool_content(result)
        # Both 'a' and 'b' must appear in some serialization
        assert "1" in out
        assert "two" in out

    def test_structured_content_takes_priority(self) -> None:
        result = {"structuredContent": {"check": "ok"}, "content": [{"text": "tail"}]}
        out = _extract_tool_content(result)
        assert "check" in out
        assert "ok" in out
        assert "tail" in out  # both included

    def test_unknown_shape_falls_back_to_str(self) -> None:
        out = _extract_tool_content("just a string")
        assert "just a string" in out


class TestCollectCheckResults:
    def test_invalid_domain_returns_message(self, fake_mcp_client) -> None:
        out = collect_check_results(
            fake_mcp_client, "eks-demo", domains=["nonexistent-domain"]
        )
        assert "No valid domains" in out["text"]

    def test_calls_each_domain_tool(self, fake_mcp_client) -> None:
        for domain, tool_name in DOMAIN_TO_MCP_TOOL.items():
            fake_mcp_client.set_response(
                tool_name,
                structured={"compliant": [], "non_compliant": [], "domain": domain},
            )

        out = collect_check_results(fake_mcp_client, "eks-demo")
        # All 6 domains called
        called_tools = {call[1] for call in fake_mcp_client.calls}
        assert called_tools == set(DOMAIN_TO_MCP_TOOL.values())

        # Each domain section appears in output
        for domain in ALL_DOMAINS:
            assert domain.upper() in out["text"]

    def test_passes_region_when_provided(self, fake_mcp_client) -> None:
        for tool in DOMAIN_TO_MCP_TOOL.values():
            fake_mcp_client.set_response(tool, text="OK")

        collect_check_results(
            fake_mcp_client, "eks-demo", region="us-east-1", domains=["security"]
        )
        _, _, kwargs = fake_mcp_client.calls[0]
        assert kwargs["region"] == "us-east-1"
        assert kwargs["cluster_name"] == "eks-demo"

    def test_omits_region_when_not_provided(self, fake_mcp_client) -> None:
        for tool in DOMAIN_TO_MCP_TOOL.values():
            fake_mcp_client.set_response(tool, text="OK")

        collect_check_results(fake_mcp_client, "eks-demo", domains=["security"])
        _, _, kwargs = fake_mcp_client.calls[0]
        assert "region" not in kwargs

    def test_failed_tool_marked_fail(self, fake_mcp_client) -> None:
        # security fails, others succeed
        fake_mcp_client.set_failure(
            DOMAIN_TO_MCP_TOOL["security"], RuntimeError("AWS API error")
        )
        for d, t in DOMAIN_TO_MCP_TOOL.items():
            if d != "security":
                fake_mcp_client.set_response(t, text="ok")

        out = collect_check_results(fake_mcp_client, "eks-demo")
        # The failed domain is reported as FAIL
        assert "SECURITY [FAIL]" in out["text"]
        # The error message comes through
        assert "AWS API error" in out["text"]
        # Other domains still ran (call count = 6 total)
        assert len(fake_mcp_client.calls) == 6
        # Error is tracked in the structured result
        assert "security" in out["errors"]
        assert out["error_count"] == 1
        assert out["ok_count"] == 5

    def test_mcp_error_payload_marked_fail(self, fake_mcp_client) -> None:
        """A tool returning isError=True (without raising) is treated as a failure."""
        fake_mcp_client.set_response(
            DOMAIN_TO_MCP_TOOL["security"],
            is_error=True,
            text="Failed to get cluster credentials: Unable to locate credentials",
        )
        out = collect_check_results(
            fake_mcp_client, "eks-demo", region="us-east-1", domains=["security"]
        )
        assert "SECURITY [FAIL]" in out["text"]
        assert out["error_count"] == 1
        assert out["ok_count"] == 0
        assert "credentials" in out["errors"]["security"].lower()

    def test_subset_domains_only_runs_those(self, fake_mcp_client) -> None:
        for tool in DOMAIN_TO_MCP_TOOL.values():
            fake_mcp_client.set_response(tool, text="OK")

        collect_check_results(
            fake_mcp_client, "eks-demo", domains=["security", "networking"]
        )
        called_tools = {call[1] for call in fake_mcp_client.calls}
        assert called_tools == {
            DOMAIN_TO_MCP_TOOL["security"],
            DOMAIN_TO_MCP_TOOL["networking"],
        }


class TestRateLimitInteraction:
    def test_rate_limited_domain_marked_error(
        self, fake_mcp_client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Make the rate limiter refuse immediately.
        from eks_review_agent.core.rate_limiter import MCPRateLimiter

        replacement = MCPRateLimiter(
            soft_limit=0, hard_limit=0, burst_limit=100, burst_window_s=60
        )
        monkeypatch.setattr(
            "eks_review_agent.core.rate_limiter.get_rate_limiter",
            lambda: replacement,
        )

        for tool in DOMAIN_TO_MCP_TOOL.values():
            fake_mcp_client.set_response(tool, text="OK")

        out = collect_check_results(
            fake_mcp_client, "eks-demo", domains=["security"]
        )
        # Should NOT have called the MCP tool — the limiter refused first
        assert len(fake_mcp_client.calls) == 0
        assert "SECURITY [FAIL]" in out["text"]
        assert "limit" in out["text"].lower()
