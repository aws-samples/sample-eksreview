"""Per-process Session state container.

Holds state that's currently scattered as module-level globals:

  - The active model name (read on every cost-display refresh).
  - Sub-agent token usage accumulated across the lifetime of one
    interactive session.

The existing module-level functions (model.get_current_model_name,
subagent_pipeline.get_subagent_usage, etc.) still work and keep the
public surface stable — they delegate to the singleton Session below.
The Session class is the path forward when we eventually thread state
through the agent factory instead of relying on globals; today it
reduces the conceptual surface and gives tests a single reset point.

Thread-safety: each field manages its own lock. The Session itself
isn't a coordinator; it's a typed home for related state.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


_USAGE_KEYS: tuple[str, ...] = (
    "inputTokens",
    "outputTokens",
    "totalTokens",
    "cacheReadInputTokens",
    "cacheWriteInputTokens",
)


@dataclass
class Session:
    """Container for per-process agent session state.

    Acquired via get_session(). Most callers should NOT instantiate
    this directly — the singleton is wired into the existing
    module-level helpers.
    """

    # Currently active model display name (e.g. "claude-opus-4.6"). Set
    # by model.create_model() and read by /context, /model, the cost
    # estimator, and the sub-agent factory.
    model_name: str = ""

    # Accumulated sub-agent token usage across the session.
    subagent_usage: dict[str, int] = field(
        default_factory=lambda: {k: 0 for k in _USAGE_KEYS}
    )

    # Locks live on the instance so resetting the singleton (in tests)
    # also rotates the lock.
    _model_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False,
    )
    _usage_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False,
    )

    # ── Model name ──────────────────────────────────────────────

    def set_model_name(self, name: str) -> None:
        with self._model_lock:
            self.model_name = name

    def get_model_name(self) -> str:
        with self._model_lock:
            return self.model_name

    # ── Sub-agent usage ─────────────────────────────────────────

    def get_subagent_usage(self) -> dict[str, int]:
        with self._usage_lock:
            return dict(self.subagent_usage)

    def reset_subagent_usage(self) -> None:
        with self._usage_lock:
            for k in self.subagent_usage:
                self.subagent_usage[k] = 0

    def accumulate_usage(self, usage: dict[str, Any] | None) -> None:
        """Add a Bedrock usage dict into the running totals.

        Missing keys are treated as zero. None or empty dict is a no-op.
        """
        if not usage:
            return
        with self._usage_lock:
            for k in self.subagent_usage:
                self.subagent_usage[k] += usage.get(k, 0)

    # ── Reset (tests only) ──────────────────────────────────────

    def reset(self) -> None:
        """Reset all state. Intended for tests, not for runtime use."""
        self.set_model_name("")
        self.reset_subagent_usage()


# ── Module-level singleton ─────────────────────────────────────────


_singleton: Session | None = None
_singleton_lock = threading.Lock()


def get_session() -> Session:
    """Return the process-global Session instance, creating it on first use."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = Session()
    return _singleton


def reset_session() -> None:
    """Reset the singleton's state. Tests use this for isolation.

    Replaces the underlying Session entirely so any locks held by old
    state are released.
    """
    global _singleton
    with _singleton_lock:
        _singleton = Session()
