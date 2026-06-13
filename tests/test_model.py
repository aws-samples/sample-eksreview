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
        # Strips the geo prefix and matches the base id, so both global and
        # regional profile IDs resolve to the same display name.
        assert _model_id_to_name("global.anthropic.claude-opus-4-8") == "claude-opus-4.8"
        assert _model_id_to_name("us.anthropic.claude-opus-4-8") == "claude-opus-4.8"
        assert _model_id_to_name("us.anthropic.claude-opus-4-6-v1") == "claude-opus-4.6"
        assert _model_id_to_name("global.anthropic.claude-sonnet-4-6") == "claude-sonnet-4.6"

    def test_unknown_returns_id(self) -> None:
        assert _model_id_to_name("unknown-id") == "unknown-id"


class TestModelIdForName:
    def test_default_prefix_is_global(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import eks_review_agent.core.model as model

        monkeypatch.setattr(model, "MODEL_ID", None)
        assert model._active_geo_prefix() == "global."
        assert (
            model.model_id_for_name("claude-opus-4.8")
            == "global.anthropic.claude-opus-4-8"
        )
        assert (
            model.model_id_for_name("claude-sonnet-4.6")
            == "global.anthropic.claude-sonnet-4-6"
        )

    def test_regional_override_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # An explicit regional MODEL_ID makes /model switches stay regional.
        import eks_review_agent.core.model as model

        monkeypatch.setattr(model, "MODEL_ID", "us.anthropic.claude-sonnet-4-6")
        assert (
            model.model_id_for_name("claude-opus-4.8")
            == "us.anthropic.claude-opus-4-8"
        )
        assert (
            model.model_id_for_name("claude-sonnet-4.6")
            == "us.anthropic.claude-sonnet-4-6"
        )

    def test_eu_override_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import eks_review_agent.core.model as model

        monkeypatch.setattr(model, "MODEL_ID", "eu.anthropic.claude-sonnet-4-6")
        assert model._active_geo_prefix() == "eu."
        assert (
            model.model_id_for_name("claude-opus-4.6")
            == "eu.anthropic.claude-opus-4-6-v1"
        )

    def test_unknown_name_returned_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import eks_review_agent.core.model as model

        monkeypatch.setattr(model, "MODEL_ID", None)
        # A full override id that isn't a bundled model passes through as-is.
        assert (
            model.model_id_for_name("apac.anthropic.claude-nova-1")
            == "apac.anthropic.claude-nova-1"
        )


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


class TestCreateBedrockSession:
    """Credential precedence in _create_bedrock_session.

    Order: explicit BEDROCK_AWS_* access keys → AWS_BEARER_TOKEN_BEDROCK
    (Bedrock API key) → default AWS credential chain.
    """

    def _patch(self, monkeypatch: pytest.MonkeyPatch, **values) -> dict:
        """Patch model-module config constants and capture Session kwargs."""
        import eks_review_agent.core.model as model

        defaults = {
            "BEDROCK_AWS_ACCESS_KEY_ID": None,
            "BEDROCK_AWS_SECRET_ACCESS_KEY": None,
            "BEDROCK_AWS_SESSION_TOKEN": None,
            "BEDROCK_AWS_REGION": "us-east-1",
            "BEDROCK_API_KEY": None,
        }
        defaults.update(values)
        for name, val in defaults.items():
            monkeypatch.setattr(model, name, val)

        captured: dict = {}

        def fake_session(**kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr(model.boto3, "Session", fake_session)
        return captured

    def test_bearer_token_takes_precedence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # botocore applies AWS_BEARER_TOKEN_BEDROCK to the bedrock-runtime
        # client regardless of session credentials, so when both the API key
        # and explicit BEDROCK_AWS_* keys are set, the API key wins. We honor
        # that order: the session is built without explicit access keys.
        import eks_review_agent.core.model as model

        captured = self._patch(
            monkeypatch,
            BEDROCK_AWS_ACCESS_KEY_ID="AKIA",
            BEDROCK_AWS_SECRET_ACCESS_KEY="secret",
            BEDROCK_AWS_SESSION_TOKEN="token",
            BEDROCK_API_KEY="bedrock-api-key",
        )
        model._create_bedrock_session()
        assert "aws_access_key_id" not in captured
        assert captured == {"region_name": "us-east-1"}

    def test_explicit_access_keys_used_when_no_bearer_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import eks_review_agent.core.model as model

        captured = self._patch(
            monkeypatch,
            BEDROCK_AWS_ACCESS_KEY_ID="AKIA",
            BEDROCK_AWS_SECRET_ACCESS_KEY="secret",
            BEDROCK_AWS_SESSION_TOKEN="token",
        )
        model._create_bedrock_session()
        assert captured["aws_access_key_id"] == "AKIA"
        assert captured["aws_secret_access_key"] == "secret"
        assert captured["aws_session_token"] == "token"

    def test_bearer_token_used_when_no_access_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import eks_review_agent.core.model as model

        captured = self._patch(
            monkeypatch,
            BEDROCK_API_KEY="bedrock-api-key",
        )
        model._create_bedrock_session()
        # Bearer auth is applied by botocore from the env; the session is just
        # region-scoped with no explicit credentials passed.
        assert "aws_access_key_id" not in captured
        assert captured == {"region_name": "us-east-1"}

    def test_default_chain_when_nothing_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import eks_review_agent.core.model as model

        captured = self._patch(monkeypatch)
        model._create_bedrock_session()
        assert "aws_access_key_id" not in captured
        assert captured == {"region_name": "us-east-1"}
