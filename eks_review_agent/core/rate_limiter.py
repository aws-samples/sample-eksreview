"""Process-wide rate limiter for MCP tool calls.

Defends against runaway LLM loops or malicious prompts that could
drive the agent to call EKS/EC2/STS APIs in tight loops, hitting AWS
throttle limits or generating unexpected Bedrock costs.

Two thresholds:
  - SOFT — warn the user but allow the call.
  - HARD — refuse further calls for this session and tell the user how
    to reset (start a new session).

The limiter is a process-global singleton. Counters reset on process
exit, not per turn — that's intentional, since the threat is unbounded
calls within a single conversation.

Sub-agents (review and upgrade orchestrators) share this counter
because they live in the same Python process. If the codebase ever
moves to multiprocess execution (e.g. spawning sub-agents in worker
processes for parallelism), the rate limiter would need to be backed
by something cross-process — shared memory, a tiny socket, or a
short-lived file lock. Today we don't need that.
"""

import logging
import os
import threading
import time
from collections import deque
from typing import Optional

logger = logging.getLogger("eksreview")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid value for %s: %r, using default %d", name, raw, default)
        return default


# Defaults tuned for normal review/upgrade flows:
#   • One full review = 6 MCP calls (one per domain).
#   • One upgrade = 1 MCP call.
#   • Heavy interactive sessions: ~30 reviews = 180 calls.
# The limits below sit well above realistic interactive use; they exist
# as a tripwire for runaway agent loops, not as an everyday throttle.
SOFT_LIMIT_TOTAL: int = _env_int("MCP_RATE_LIMIT_SOFT", 200)
HARD_LIMIT_TOTAL: int = _env_int("MCP_RATE_LIMIT_HARD", 500)
# Sliding-window burst guard: at most N calls in the last WINDOW seconds.
# 60 calls/60s = 10 full reviews in a minute, then a brief cool-off.
BURST_LIMIT: int = _env_int("MCP_RATE_LIMIT_BURST", 60)
BURST_WINDOW_S: int = _env_int("MCP_RATE_LIMIT_BURST_WINDOW", 60)


class RateLimitExceeded(Exception):
    """Raised when an MCP call is refused by the rate limiter."""


class MCPRateLimiter:
    """Process-wide MCP call counter with soft + hard + burst limits.

    Thread-safe. Singleton — call get_rate_limiter() rather than instantiating.
    """

    def __init__(
        self,
        soft_limit: int = SOFT_LIMIT_TOTAL,
        hard_limit: int = HARD_LIMIT_TOTAL,
        burst_limit: int = BURST_LIMIT,
        burst_window_s: int = BURST_WINDOW_S,
    ):
        self.soft_limit = soft_limit
        self.hard_limit = hard_limit
        self.burst_limit = burst_limit
        self.burst_window_s = burst_window_s

        self._lock = threading.Lock()
        self._total_calls = 0
        self._timestamps: deque[float] = deque()  # for burst sliding window
        self._soft_warned = False

    def check_and_increment(self, tool_name: str) -> None:
        """Record a call, raising RateLimitExceeded if hard/burst limit hit.

        Logs a warning the first time the soft limit is crossed. Subsequent
        calls under the hard limit proceed silently.
        """
        now = time.monotonic()
        with self._lock:
            # Trim old burst entries
            cutoff = now - self.burst_window_s
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()

            # Burst guard
            if len(self._timestamps) >= self.burst_limit:
                logger.warning(
                    "MCP burst limit hit: %d calls in %ds (tool=%s). Refusing.",
                    len(self._timestamps), self.burst_window_s, tool_name,
                )
                raise RateLimitExceeded(
                    f"Burst limit reached ({self.burst_limit} MCP calls in "
                    f"{self.burst_window_s}s). Slow down or start a new session."
                )

            # Hard cap
            if self._total_calls >= self.hard_limit:
                logger.warning(
                    "MCP hard limit reached: %d total calls (tool=%s). Refusing.",
                    self._total_calls, tool_name,
                )
                raise RateLimitExceeded(
                    f"Session reached the MCP call limit ({self.hard_limit}). "
                    f"Start a new session to continue. "
                    f"Set MCP_RATE_LIMIT_HARD to override."
                )

            # Soft warning (once)
            if (
                not self._soft_warned
                and self._total_calls >= self.soft_limit
            ):
                self._soft_warned = True
                logger.warning(
                    "MCP soft limit crossed: %d calls in this session "
                    "(soft=%d, hard=%d). Continued use is allowed but unusual.",
                    self._total_calls, self.soft_limit, self.hard_limit,
                )

            self._total_calls += 1
            self._timestamps.append(now)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "total_calls": self._total_calls,
                "soft_limit": self.soft_limit,
                "hard_limit": self.hard_limit,
                "burst_limit": self.burst_limit,
                "burst_window_s": self.burst_window_s,
                "calls_in_burst_window": len(self._timestamps),
            }

    def reset(self) -> None:
        """Reset all counters. Intended for tests, not for runtime use."""
        with self._lock:
            self._total_calls = 0
            self._timestamps.clear()
            self._soft_warned = False


_singleton: Optional[MCPRateLimiter] = None
_singleton_lock = threading.Lock()


def get_rate_limiter() -> MCPRateLimiter:
    """Return the process-global MCP rate limiter."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = MCPRateLimiter()
    return _singleton
