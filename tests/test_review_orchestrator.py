"""Tests for the review orchestrator's early-abort logic.

Focus areas:
  - _is_cluster_not_found: the cluster-not-found signal.
  - run_review: aborts (no report) when every check fails OR when any
    check reports the cluster does not exist, even if another check
    happened to succeed.
  - run_review: proceeds to report compilation on a normal mixed result.
"""

from __future__ import annotations

import pytest

from eks_review_agent.orchestration import review_orchestrator as ro


# Real cluster-not-found text the MCP handlers surface (matches the
# DescribeCluster ResourceNotFoundException path).
_NOT_FOUND = (
    "Failed to connect to cluster eks-review: Failed to get cluster "
    "credentials: An error occurred (ResourceNotFoundException) when calling "
    "the DescribeCluster operation: No cluster found for name: eks-review."
)


class TestIsClusterNotFound:
    @pytest.mark.parametrize(
        "text",
        [
            _NOT_FOUND,
            "ResourceNotFoundException: boom",
            "No cluster found for name: foo",
            "NO CLUSTER FOUND",  # case-insensitive
        ],
    )
    def test_matches_not_found(self, text: str) -> None:
        assert ro._is_cluster_not_found(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "AccessDeniedException: not authorized",
            "Unable to locate credentials",
            "throttled: rate exceeded",
            "",
        ],
    )
    def test_ignores_other_errors(self, text: str) -> None:
        assert ro._is_cluster_not_found(text) is False


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, calls: list) -> None:
    """Stub the sub-agent pipeline so no LLM/report compilation runs.

    Records invocation so tests can assert whether compilation was reached.
    """

    def _fake_pipeline(cluster_name, config):
        calls.append(cluster_name)
        return f"reports/{cluster_name}-assessment.md"

    monkeypatch.setattr(ro, "run_subagent_pipeline", _fake_pipeline)
    # Keep history lookup cheap and deterministic.
    monkeypatch.setattr(
        ro, "_extract_summary_from_report", lambda _p: "## Executive Summary\nok"
    )


class TestRunReviewAbort:
    def test_aborts_when_all_checks_fail(
        self, fake_mcp_client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        compiled: list = []
        _patch_pipeline(monkeypatch, compiled)

        # Every domain returns an MCP error payload.
        for tool in ro.ALL_DOMAINS:
            from eks_review_agent.orchestration.mcp_checks import DOMAIN_TO_MCP_TOOL

            fake_mcp_client.set_response(
                DOMAIN_TO_MCP_TOOL[tool], text=_NOT_FOUND, is_error=True
            )

        result = ro.run_review("eks-review", fake_mcp_client, region="us-west-2")

        assert "could not run" in result
        assert "No report was generated" in result
        assert compiled == []  # never reached report compilation

    def test_aborts_on_cluster_not_found_even_if_one_check_passes(
        self, fake_mcp_client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The real bug: networking 'passed' while the cluster was missing."""
        compiled: list = []
        _patch_pipeline(monkeypatch, compiled)

        from eks_review_agent.orchestration.mcp_checks import DOMAIN_TO_MCP_TOOL

        for domain in ro.ALL_DOMAINS:
            tool = DOMAIN_TO_MCP_TOOL[domain]
            if domain == "networking":
                # Networking returns a non-error result (the false pass).
                fake_mcp_client.set_response(tool, text="all good", is_error=False)
            else:
                fake_mcp_client.set_response(tool, text=_NOT_FOUND, is_error=True)

        result = ro.run_review("eks-review", fake_mcp_client, region="us-west-2")

        assert "could not run" in result
        assert "eks-review" in result
        # Cluster-not-found cause should be surfaced, not the networking pass.
        assert "ResourceNotFoundException" in result or "No cluster found" in result
        assert compiled == []  # aborted before compiling a misleading report

    def test_proceeds_on_normal_mixed_results(
        self, fake_mcp_client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A real cluster with some failing checks should still compile."""
        compiled: list = []
        _patch_pipeline(monkeypatch, compiled)

        from eks_review_agent.orchestration.mcp_checks import DOMAIN_TO_MCP_TOOL

        for domain in ro.ALL_DOMAINS:
            tool = DOMAIN_TO_MCP_TOOL[domain]
            if domain == "security":
                # A legitimate finding-style failure (not cluster-not-found).
                fake_mcp_client.set_response(
                    tool, text="AccessDeniedException", is_error=True
                )
            else:
                fake_mcp_client.set_response(tool, text="compliant", is_error=False)

        result = ro.run_review("real-cluster", fake_mcp_client, region="us-west-2")

        assert compiled == ["real-cluster"]  # compilation was reached
        assert "Report saved to:" in result
