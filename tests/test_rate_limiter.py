"""Tests for the MCP rate limiter — soft, hard, burst, sliding window."""

from __future__ import annotations

import time

import pytest

from eks_review_agent.core.rate_limiter import (
    MCPRateLimiter,
    RateLimitExceeded,
    get_rate_limiter,
)


class TestSoftLimit:
    def test_allows_calls_below_threshold(self) -> None:
        rl = MCPRateLimiter(soft_limit=5, hard_limit=10, burst_limit=100, burst_window_s=60)
        for _ in range(4):
            rl.check_and_increment("check_eks_security")
        assert rl.stats()["total_calls"] == 4

    def test_warns_once_at_threshold(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        rl = MCPRateLimiter(soft_limit=2, hard_limit=10, burst_limit=100, burst_window_s=60)
        with caplog.at_level(logging.WARNING, logger="eksreview"):
            for _ in range(5):
                rl.check_and_increment("check_eks_security")
        warnings = [r for r in caplog.records if "soft limit" in r.message.lower()]
        # Expect a single soft-limit warning, not one per call past the threshold
        assert len(warnings) == 1


class TestHardLimit:
    def test_refuses_at_threshold(self) -> None:
        rl = MCPRateLimiter(soft_limit=5, hard_limit=3, burst_limit=100, burst_window_s=60)
        for _ in range(3):
            rl.check_and_increment("check_eks_security")
        with pytest.raises(RateLimitExceeded, match="MCP call limit"):
            rl.check_and_increment("check_eks_security")

    def test_total_calls_unchanged_on_refusal(self) -> None:
        rl = MCPRateLimiter(soft_limit=5, hard_limit=3, burst_limit=100, burst_window_s=60)
        for _ in range(3):
            rl.check_and_increment("x")
        with pytest.raises(RateLimitExceeded):
            rl.check_and_increment("x")
        # The refused call should NOT count toward the total
        assert rl.stats()["total_calls"] == 3


class TestBurstLimit:
    def test_refuses_when_window_full(self) -> None:
        rl = MCPRateLimiter(soft_limit=100, hard_limit=100, burst_limit=2, burst_window_s=60)
        rl.check_and_increment("a")
        rl.check_and_increment("b")
        with pytest.raises(RateLimitExceeded, match="Burst limit"):
            rl.check_and_increment("c")

    def test_window_expiry_releases_capacity(self) -> None:
        # Tight window so we can wait it out in the test.
        rl = MCPRateLimiter(soft_limit=100, hard_limit=100, burst_limit=2, burst_window_s=1)
        rl.check_and_increment("a")
        rl.check_and_increment("b")
        with pytest.raises(RateLimitExceeded):
            rl.check_and_increment("c")
        time.sleep(1.05)
        # After window expiry, capacity should be available again
        rl.check_and_increment("d")
        assert rl.stats()["total_calls"] == 3


class TestStatsAndReset:
    def test_stats_shape(self) -> None:
        rl = MCPRateLimiter(soft_limit=10, hard_limit=20, burst_limit=5, burst_window_s=30)
        s = rl.stats()
        assert set(s.keys()) >= {
            "total_calls", "soft_limit", "hard_limit",
            "burst_limit", "burst_window_s", "calls_in_burst_window",
        }

    def test_reset_clears_state(self) -> None:
        rl = MCPRateLimiter(soft_limit=2, hard_limit=10, burst_limit=10, burst_window_s=60)
        for _ in range(3):
            rl.check_and_increment("x")
        rl.reset()
        s = rl.stats()
        assert s["total_calls"] == 0
        assert s["calls_in_burst_window"] == 0


class TestSingleton:
    def test_get_rate_limiter_is_idempotent(self) -> None:
        rl1 = get_rate_limiter()
        rl2 = get_rate_limiter()
        assert rl1 is rl2


class TestEnvDefaults:
    def test_env_overrides_picked_up_on_module_import(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The module reads env at import — reload to verify the env path.
        monkeypatch.setenv("MCP_RATE_LIMIT_SOFT", "7")
        monkeypatch.setenv("MCP_RATE_LIMIT_HARD", "11")
        monkeypatch.setenv("MCP_RATE_LIMIT_BURST", "3")
        monkeypatch.setenv("MCP_RATE_LIMIT_BURST_WINDOW", "5")

        import importlib
        import eks_review_agent.core.rate_limiter as rl_mod
        importlib.reload(rl_mod)
        try:
            assert rl_mod.SOFT_LIMIT_TOTAL == 7
            assert rl_mod.HARD_LIMIT_TOTAL == 11
            assert rl_mod.BURST_LIMIT == 3
            assert rl_mod.BURST_WINDOW_S == 5
        finally:
            # Restore defaults so other tests aren't poisoned
            monkeypatch.delenv("MCP_RATE_LIMIT_SOFT", raising=False)
            monkeypatch.delenv("MCP_RATE_LIMIT_HARD", raising=False)
            monkeypatch.delenv("MCP_RATE_LIMIT_BURST", raising=False)
            monkeypatch.delenv("MCP_RATE_LIMIT_BURST_WINDOW", raising=False)
            importlib.reload(rl_mod)

    def test_invalid_env_falls_back(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        monkeypatch.setenv("MCP_RATE_LIMIT_SOFT", "not-a-number")
        import importlib
        import eks_review_agent.core.rate_limiter as rl_mod
        with caplog.at_level(logging.WARNING, logger="eksreview"):
            importlib.reload(rl_mod)
        try:
            # Should fall back to default 200
            assert rl_mod.SOFT_LIMIT_TOTAL == 200
        finally:
            monkeypatch.delenv("MCP_RATE_LIMIT_SOFT", raising=False)
            importlib.reload(rl_mod)
