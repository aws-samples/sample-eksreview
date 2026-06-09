"""Tests for the Session container and singleton helpers."""

from __future__ import annotations

import pytest

from eks_review_agent.session import Session, get_session, reset_session


@pytest.fixture(autouse=True)
def _reset():
    reset_session()
    yield
    reset_session()


class TestModelName:
    def test_default_empty(self) -> None:
        assert get_session().get_model_name() == ""

    def test_set_and_get(self) -> None:
        get_session().set_model_name("claude-opus-4.6")
        assert get_session().get_model_name() == "claude-opus-4.6"

    def test_overwrite(self) -> None:
        get_session().set_model_name("opus")
        get_session().set_model_name("sonnet")
        assert get_session().get_model_name() == "sonnet"


class TestSubagentUsage:
    def test_zero_default(self) -> None:
        u = get_session().get_subagent_usage()
        assert all(v == 0 for v in u.values())
        assert set(u.keys()) == {
            "inputTokens", "outputTokens", "totalTokens",
            "cacheReadInputTokens", "cacheWriteInputTokens",
        }

    def test_accumulate(self) -> None:
        s = get_session()
        s.accumulate_usage({"inputTokens": 100, "outputTokens": 50, "totalTokens": 150})
        u = s.get_subagent_usage()
        assert u["inputTokens"] == 100
        assert u["outputTokens"] == 50
        assert u["totalTokens"] == 150

    def test_accumulate_is_additive(self) -> None:
        s = get_session()
        for _ in range(3):
            s.accumulate_usage({"inputTokens": 100})
        assert s.get_subagent_usage()["inputTokens"] == 300

    def test_accumulate_handles_none(self) -> None:
        s = get_session()
        s.accumulate_usage(None)
        assert s.get_subagent_usage()["totalTokens"] == 0

    def test_accumulate_handles_empty_dict(self) -> None:
        s = get_session()
        s.accumulate_usage({})
        assert s.get_subagent_usage()["totalTokens"] == 0

    def test_accumulate_handles_unknown_keys(self) -> None:
        # Extra keys in the usage dict should be ignored
        s = get_session()
        s.accumulate_usage({"inputTokens": 50, "unknownField": 999})
        assert s.get_subagent_usage()["inputTokens"] == 50

    def test_get_returns_copy(self) -> None:
        s = get_session()
        s.accumulate_usage({"inputTokens": 100})
        u = s.get_subagent_usage()
        u["inputTokens"] = 999
        # Mutation in the returned dict should not affect internal state
        assert s.get_subagent_usage()["inputTokens"] == 100

    def test_reset(self) -> None:
        s = get_session()
        s.accumulate_usage({"inputTokens": 100, "totalTokens": 100})
        s.reset_subagent_usage()
        assert all(v == 0 for v in s.get_subagent_usage().values())


class TestSingleton:
    def test_get_session_is_idempotent(self) -> None:
        a = get_session()
        b = get_session()
        assert a is b

    def test_reset_replaces_instance(self) -> None:
        a = get_session()
        a.set_model_name("opus")
        reset_session()
        b = get_session()
        # Different instance after reset
        assert a is not b
        assert b.get_model_name() == ""

    def test_session_reset_method(self) -> None:
        s = get_session()
        s.set_model_name("opus")
        s.accumulate_usage({"inputTokens": 100})
        s.reset()
        assert s.get_model_name() == ""
        assert s.get_subagent_usage()["inputTokens"] == 0


class TestModelHelpersIntegration:
    """Verify the existing model.py helpers route through Session."""

    def test_model_module_writes_through_session(self) -> None:
        from eks_review_agent.core.model import get_current_model_name

        get_session().set_model_name("claude-sonnet-4.6")
        assert get_current_model_name() == "claude-sonnet-4.6"


class TestPipelineHelpersIntegration:
    """Verify subagent_pipeline.get_subagent_usage routes through Session."""

    def test_pipeline_module_reads_session(self) -> None:
        from eks_review_agent.orchestration.subagent_pipeline import get_subagent_usage

        get_session().accumulate_usage({"inputTokens": 42})
        assert get_subagent_usage()["inputTokens"] == 42
