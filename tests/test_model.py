"""Tests for the model registry — pricing and cost estimation.

We test the pure helpers (resolve, pricing, cost). Model construction
hits Bedrock and is excluded.
"""

from __future__ import annotations

import pytest

from eks_review_agent.core.model import (
    AVAILABLE_MODELS,
    MODEL_ALIASES,
    _DEFAULT_PRICING,
    _model_id_to_name,
    _resolve_model_name,
    estimate_cost,
    get_pricing,
    list_models_formatted,
)


class TestResolveModelName:
    def test_canonical_name(self) -> None:
        assert _resolve_model_name("claude-opus-4.6") == "claude-opus-4.6"

    def test_alias(self) -> None:
        assert _resolve_model_name("opus") == "claude-opus-4.8"
        assert _resolve_model_name("sonnet") == "claude-sonnet-4.6"

    def test_case_insensitive(self) -> None:
        assert _resolve_model_name("OPUS") == "claude-opus-4.8"

    def test_unknown_returns_none(self) -> None:
        assert _resolve_model_name("nonexistent-model") is None


class TestModelIdToName:
    def test_returns_model_name(self) -> None:
        # All models are 1M context; each model_id maps to a single name.
        assert _model_id_to_name("us.anthropic.claude-opus-4-8") == "claude-opus-4.8"
        assert _model_id_to_name("us.anthropic.claude-opus-4-6-v1") == "claude-opus-4.6"
        assert _model_id_to_name("us.anthropic.claude-sonnet-4-6") == "claude-sonnet-4.6"

    def test_unknown_returns_id(self) -> None:
        assert _model_id_to_name("unknown-id") == "unknown-id"


class TestPricing:
    def test_known_model(self) -> None:
        p = get_pricing("claude-opus-4.6")
        assert p["input"] == 5.00
        assert p["output"] == 25.00
        assert p["cache_read"] == 0.50
        assert p["cache_write"] == 6.25

    def test_sonnet_cheaper_than_opus(self) -> None:
        opus = get_pricing("claude-opus-4.6")
        sonnet = get_pricing("claude-sonnet-4.6")
        assert sonnet["input"] < opus["input"]

    def test_unknown_falls_back_to_default(self) -> None:
        p = get_pricing("nonexistent")
        assert p == _DEFAULT_PRICING

    def test_all_models_have_pricing(self) -> None:
        for name, info in AVAILABLE_MODELS.items():
            assert "pricing" in info, f"{name} missing pricing"
            for key in ("input", "output", "cache_read", "cache_write"):
                assert key in info["pricing"], f"{name} pricing missing {key}"


class TestEstimateCost:
    def test_zero_usage_zero_cost(self) -> None:
        assert estimate_cost({}, "claude-opus-4.6") == 0.0
        assert estimate_cost(None, "claude-opus-4.6") == 0.0  # type: ignore[arg-type]

    def test_input_only(self) -> None:
        cost = estimate_cost({"inputTokens": 1_000_000}, "claude-opus-4.6")
        assert cost == pytest.approx(5.0)

    def test_output_only(self) -> None:
        cost = estimate_cost({"outputTokens": 1_000_000}, "claude-opus-4.6")
        assert cost == pytest.approx(25.0)

    def test_combined(self) -> None:
        cost = estimate_cost(
            {
                "inputTokens": 100_000,
                "outputTokens": 50_000,
                "cacheReadInputTokens": 200_000,
                "cacheWriteInputTokens": 50_000,
            },
            "claude-sonnet-4.6",
        )
        # 0.1*3.0 + 0.05*15 + 0.2*0.3 + 0.05*3.75 = 0.3 + 0.75 + 0.06 + 0.1875 = 1.2975
        assert cost == pytest.approx(1.2975, abs=0.001)

    def test_unknown_model_uses_default_pricing(self) -> None:
        cost1 = estimate_cost({"inputTokens": 1_000_000}, "nonexistent")
        cost2 = estimate_cost({"inputTokens": 1_000_000}, "claude-opus-4.6")
        assert cost1 == cost2  # Default is Opus pricing


class TestListModelsFormatted:
    def test_returns_string_with_models(self) -> None:
        out = list_models_formatted()
        assert "Available Models" in out
        # Must include canonical names
        for name in AVAILABLE_MODELS:
            assert name in out
        # Must include aliases section
        assert "Aliases" in out
        for alias in MODEL_ALIASES:
            assert alias in out
