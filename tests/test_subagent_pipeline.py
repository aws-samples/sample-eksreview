"""Tests for the shared sub-agent pipeline scaffolding.

Sub-agent execution itself hits Bedrock and is integration territory;
here we cover the pure helpers — token-usage accumulation, config
shape, and the deterministic surface around the pipeline runner.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from eks_review_agent.orchestration.subagent_pipeline import (
    SubAgentPipelineConfig,
    _accumulate_subagent_usage,
    get_subagent_usage,
    reset_subagent_usage,
)


@pytest.fixture(autouse=True)
def _reset_usage():
    reset_subagent_usage()
    yield
    reset_subagent_usage()


class TestUsageAccumulator:
    def test_get_returns_zero_when_no_runs(self) -> None:
        s = get_subagent_usage()
        assert s == {
            "inputTokens": 0,
            "outputTokens": 0,
            "totalTokens": 0,
            "cacheReadInputTokens": 0,
            "cacheWriteInputTokens": 0,
        }

    def test_get_returns_a_copy(self) -> None:
        s = get_subagent_usage()
        s["inputTokens"] = 999
        # Mutating the returned dict should not affect the global state
        assert get_subagent_usage()["inputTokens"] == 0

    def test_accumulate_adds_to_totals(self) -> None:
        agent = MagicMock()
        agent.event_loop_metrics.accumulated_usage = {
            "inputTokens": 100,
            "outputTokens": 50,
            "totalTokens": 150,
            "cacheReadInputTokens": 10,
            "cacheWriteInputTokens": 5,
        }
        _accumulate_subagent_usage(agent)
        s = get_subagent_usage()
        assert s["inputTokens"] == 100
        assert s["outputTokens"] == 50
        assert s["totalTokens"] == 150

    def test_accumulate_is_additive(self) -> None:
        agent = MagicMock()
        agent.event_loop_metrics.accumulated_usage = {
            "inputTokens": 100,
            "outputTokens": 50,
            "totalTokens": 150,
        }
        _accumulate_subagent_usage(agent)
        _accumulate_subagent_usage(agent)
        s = get_subagent_usage()
        assert s["inputTokens"] == 200
        assert s["totalTokens"] == 300

    def test_accumulate_handles_no_usage(self) -> None:
        agent = MagicMock()
        agent.event_loop_metrics.accumulated_usage = None
        _accumulate_subagent_usage(agent)
        # Should not raise, totals still zero
        assert get_subagent_usage()["totalTokens"] == 0

    def test_accumulate_handles_missing_attr(self) -> None:
        # In practice Strands Agents always have event_loop_metrics with an
        # accumulated_usage attribute. Confirm: an agent whose metrics dict
        # is missing keys still doesn't crash and leaves totals untouched.
        agent = MagicMock()
        agent.event_loop_metrics.accumulated_usage = {}  # empty dict
        _accumulate_subagent_usage(agent)
        assert get_subagent_usage()["totalTokens"] == 0


class TestPipelineConfig:
    def test_required_fields(self, tmp_path: Path) -> None:
        config = SubAgentPipelineConfig(
            name="test",
            spinner_message="Testing",
            system_prompt="You are a test.",
            tools=[],
            user_prompt="Do the test.",
            find_report=lambda c: None,
        )
        assert config.name == "test"
        assert config.spinner_message == "Testing"
        # Defaults filled in
        assert "✓" in config.success_message
        assert "✗" in config.failure_message
        # The default templates accept {label} and {elapsed}
        assert "{label}" in config.success_message
        assert "{elapsed:.0f}s" in config.success_message

    def test_custom_messages(self) -> None:
        config = SubAgentPipelineConfig(
            name="x",
            spinner_message="working",
            system_prompt="",
            tools=[],
            user_prompt="",
            find_report=lambda c: None,
            success_message="DONE in {elapsed:.0f}s",
            failure_message="FAIL in {elapsed:.0f}s",
        )
        # Templates should still render correctly
        assert config.success_message.format(label="x", elapsed=12.0) == "DONE in 12s"


class TestReExports:
    def test_review_orchestrator_re_exports(self) -> None:
        # Backwards-compat: importing from review_orchestrator should
        # still work for callers that depend on the old module layout.
        from eks_review_agent.orchestration.review_orchestrator import (
            _accumulate_subagent_usage as ro_accumulate,
            _create_report_model as ro_model,
            _extract_summary_from_report as ro_extract,
            get_subagent_usage as ro_usage,
        )
        assert callable(ro_accumulate)
        assert callable(ro_model)
        assert callable(ro_extract)
        assert callable(ro_usage)
