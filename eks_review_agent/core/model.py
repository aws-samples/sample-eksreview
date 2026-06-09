"""Bedrock model provider setup with runtime model switching.

Supports a registry of available models that can be switched at runtime
via the /model command. Uses global Bedrock endpoints by default.
"""

import logging

import boto3
from botocore.config import Config as BotocoreConfig
from strands.models import BedrockModel, CacheConfig

from eks_review_agent.config import (
    MODEL_ID,
    MODEL_TEMPERATURE,
    MODEL_MAX_TOKENS,
    BEDROCK_AWS_ACCESS_KEY_ID,
    BEDROCK_AWS_SECRET_ACCESS_KEY,
    BEDROCK_AWS_SESSION_TOKEN,
    BEDROCK_AWS_REGION,
)

logger = logging.getLogger("eksreview")


# ── Available models (global endpoints) ─────────
# Pricing is per 1M tokens (USD). Source: Anthropic pricing on Bedrock.
# Keep aligned with AVAILABLE_MODELS — a model entry without a pricing
# entry will fall through to the default below in estimate_cost().
AVAILABLE_MODELS = {
    "claude-opus-4.8": {
        "model_id": "us.anthropic.claude-opus-4-8",
        "description": "Latest and most capable, 1M context (US cross-region)",
        "context_window": 1000000,
        "max_output_tokens": 128000,
        "supports_temperature": False,
        "pricing": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    },
    "claude-opus-4.6": {
        "model_id": "us.anthropic.claude-opus-4-6-v1",
        "description": "Most capable, 1M context (US cross-region)",
        "context_window": 1000000,
        "max_output_tokens": 128000,
        "pricing": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    },
    "claude-opus-4.5": {
        "model_id": "us.anthropic.claude-opus-4-5-20251101-v1:0",
        "description": "Previous gen Opus, 1M context (US cross-region)",
        "context_window": 1000000,
        "max_output_tokens": 64000,
        "pricing": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    },
    "claude-sonnet-4.6": {
        "model_id": "us.anthropic.claude-sonnet-4-6",
        "description": "Fast and capable, 1M context (US cross-region)",
        "context_window": 1000000,
        "max_output_tokens": 128000,
        "pricing": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    },
    "claude-sonnet-4.5": {
        "model_id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "description": "Previous gen Sonnet, 1M context (US cross-region)",
        "context_window": 1000000,
        "max_output_tokens": 64000,
        "pricing": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    },
}

_DEFAULT_PRICING = {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25}

# Short aliases
MODEL_ALIASES = {
    "opus": "claude-opus-4.8",
    "sonnet": "claude-sonnet-4.6",
    "opus-4.8": "claude-opus-4.8",
    "opus-4.6": "claude-opus-4.6",
    "opus-4.5": "claude-opus-4.5",
    "sonnet-4.6": "claude-sonnet-4.6",
    "sonnet-4.5": "claude-sonnet-4.5",
}

# Track current model name for display.
# Backed by the Session singleton — kept here as a thin shim so existing
# module-level helpers (get_current_model_name, list_models_formatted)
# stay backwards-compatible. Direct mutation through the global below
# is no longer the source of truth; Session.set_model_name() is.
from eks_review_agent.session import get_session as _get_session


def _create_bedrock_session() -> boto3.Session:
    """Create a boto3 session for Bedrock.

    If BEDROCK_AWS_ACCESS_KEY_ID is set, uses those credentials (assumed role
    from a different account). Otherwise falls back to default credentials
    (same account as EKS).

    Region is resolved from BEDROCK_AWS_REGION > AWS_REGION > AWS_DEFAULT_REGION >
    boto3 default chain (config file, instance metadata).
    """
    region = BEDROCK_AWS_REGION or None  # None lets boto3 resolve from its own chain

    if BEDROCK_AWS_ACCESS_KEY_ID and BEDROCK_AWS_SECRET_ACCESS_KEY:
        logger.info("Using separate Bedrock credentials (cross-account)")
        return boto3.Session(
            aws_access_key_id=BEDROCK_AWS_ACCESS_KEY_ID,
            aws_secret_access_key=BEDROCK_AWS_SECRET_ACCESS_KEY,
            aws_session_token=BEDROCK_AWS_SESSION_TOKEN,
            region_name=region,
        )

    logger.info("Using default AWS credentials for Bedrock")
    return boto3.Session(region_name=region)


def _resolve_model_name(name: str) -> str | None:
    """Resolve a model name or alias to a canonical name."""
    lower = name.lower().strip()
    if lower in AVAILABLE_MODELS:
        return lower
    if lower in MODEL_ALIASES:
        return MODEL_ALIASES[lower]
    return None


def _model_id_to_name(model_id: str, context_1m: bool = True) -> str:
    """Find the display name for a model_id, preferring the matching context variant."""
    # Prefer the 1M or 200K variant based on context_1m flag
    for name, info in AVAILABLE_MODELS.items():
        if info["model_id"] == model_id:
            is_1m = info.get("context_window", 200000) >= 1000000
            if is_1m == context_1m:
                return name
    # Fallback to any match
    for name, info in AVAILABLE_MODELS.items():
        if info["model_id"] == model_id:
            return name
    return model_id


def create_model(model_id: str | None = None, context_1m: bool = True) -> BedrockModel:
    """Create a Bedrock model instance.

    Args:
        model_id: Bedrock model ID. Defaults to MODEL_ID from config.
        context_1m: Whether to enable the 1M context window beta. Default True.

    Returns:
        Configured BedrockModel.
    """
    mid = model_id or MODEL_ID
    session = _create_bedrock_session()
    _get_session().set_model_name(_model_id_to_name(mid, context_1m))
    logger.info("Primary model: %s in %s (1M=%s)", mid, BEDROCK_AWS_REGION, context_1m)

    extra_fields = {}
    if context_1m:
        extra_fields["anthropic_beta"] = ["context-1m-2025-08-07"]

    # Use per-model max_output_tokens, capped by MODEL_MAX_TOKENS from config
    current_name = _get_session().get_model_name()
    model_info = AVAILABLE_MODELS.get(current_name, {})
    model_max = model_info.get("max_output_tokens", 128000)
    effective_max_tokens = min(MODEL_MAX_TOKENS, model_max)

    # Some newer models (e.g. Opus 4.8) deprecated the `temperature` parameter
    # and reject requests that include it. Only pass it when supported.
    kwargs = dict(
        model_id=mid,
        boto_session=session,
        boto_client_config=BotocoreConfig(read_timeout=1200),
        additional_request_fields=extra_fields or None,
        cache_config=CacheConfig(strategy="auto"),
        max_tokens=effective_max_tokens,
    )
    if model_info.get("supports_temperature", True):
        kwargs["temperature"] = MODEL_TEMPERATURE

    return BedrockModel(**kwargs)


def create_model_by_name(name: str) -> tuple[BedrockModel, str] | tuple[None, str]:
    """Create a model by its display name or alias.

    Args:
        name: Model name like "claude-opus-4.6", "opus", "sonnet", etc.

    Returns:
        (model, display_name) on success, or (None, error_message) on failure.
    """
    resolved = _resolve_model_name(name)
    if resolved is None:
        valid = ", ".join(list(AVAILABLE_MODELS.keys()) + list(MODEL_ALIASES.keys()))
        return None, f"Unknown model: {name}. Available: {valid}"

    info = AVAILABLE_MODELS[resolved]
    is_1m = info.get("context_window", 200000) >= 1000000
    model = create_model(model_id=info["model_id"], context_1m=is_1m)
    return model, resolved


def get_current_model_name() -> str:
    """Get the display name of the currently active model."""
    return _get_session().get_model_name()


def get_current_context_window() -> int:
    """Get the context window size for the currently active model."""
    name = _get_session().get_model_name()
    if name in AVAILABLE_MODELS:
        return AVAILABLE_MODELS[name].get("context_window", 200000)
    return 200000


def get_pricing(model_name: str | None = None) -> dict:
    """Return per-1M-token pricing for the given (or active) model.

    Falls back to the default Opus pricing if the model name is unknown so
    callers always get a usable dict. Keys: input, output, cache_read,
    cache_write.
    """
    if model_name is None:
        model_name = _get_session().get_model_name()
    info = AVAILABLE_MODELS.get(model_name, {})
    return info.get("pricing", _DEFAULT_PRICING)


def estimate_cost(usage: dict, model_name: str | None = None) -> float:
    """Estimate USD cost from a Bedrock usage dict.

    `usage` keys expected: inputTokens, outputTokens, cacheReadInputTokens,
    cacheWriteInputTokens. Missing keys are treated as zero. Pricing is
    looked up from AVAILABLE_MODELS so adding a new model in one place
    keeps cost reporting accurate.
    """
    if not usage:
        return 0.0
    p = get_pricing(model_name)
    return (
        (usage.get("inputTokens", 0) / 1_000_000) * p["input"]
        + (usage.get("outputTokens", 0) / 1_000_000) * p["output"]
        + (usage.get("cacheReadInputTokens", 0) / 1_000_000) * p["cache_read"]
        + (usage.get("cacheWriteInputTokens", 0) / 1_000_000) * p["cache_write"]
    )


def list_models_formatted() -> str:
    """Return a formatted list of available models for display."""
    current = _get_session().get_model_name()
    lines = ["\n  Available Models", "  ────────────────"]
    for name, info in AVAILABLE_MODELS.items():
        marker = " *" if name == current else "  "
        lines.append(f"  {marker} {name:<20} {info['description']}")

    lines.append(f"\n  Aliases: {', '.join(f'{a} → {t}' for a, t in MODEL_ALIASES.items())}")
    lines.append(f"  Region: {BEDROCK_AWS_REGION}")
    lines.append(f"\n  Current: {current}")
    lines.append("  Usage: /model <name>  (e.g. /model sonnet)")
    return "\n".join(lines)
