"""Shared pytest fixtures for the eksreview test suite.

Fixtures live here so any test module can use them without importing.
Keep them small and composable.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# Ensure the repo root is importable when pytest is run from anywhere.
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))


# ── Filesystem isolation ─────────────────────────────────────────────


@pytest.fixture
def tmp_reports_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect REPORTS_DIR to a tmp path for the duration of one test.

    Many modules read eks_review_agent.config.REPORTS_DIR at import time
    and resolve it lazily, so we patch in two places: the config module
    and any modules that imported the symbol directly.
    """
    reports = tmp_path / "reports"
    reports.mkdir()
    import eks_review_agent.config as cfg
    monkeypatch.setattr(cfg, "REPORTS_DIR", reports)

    # Modules that did `from eks_review_agent.config import REPORTS_DIR`
    # captured the original Path. Patch each of them too.
    for mod_name in (
        "eks_review_agent.tools",
        "eks_review_agent.reports.report_search",
        "eks_review_agent.reports.export",
        "eks_review_agent.orchestration.review_orchestrator",
        "eks_review_agent.orchestration.upgrade_orchestrator",
    ):
        if mod_name in sys.modules:
            monkeypatch.setattr(sys.modules[mod_name], "REPORTS_DIR", reports, raising=False)

    return reports


@pytest.fixture
def tmp_kb_dir(tmp_path: Path) -> Path:
    """Standalone knowledge-base dir for KnowledgeBase tests."""
    kb = tmp_path / ".knowledge"
    kb.mkdir()
    return kb


@pytest.fixture(autouse=True)
def isolate_logging():
    """Prevent test logs from polluting stderr or competing for handlers."""
    logger = logging.getLogger("eksreview")
    original_level = logger.level
    original_handlers = list(logger.handlers)
    logger.handlers = [logging.NullHandler()]
    logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        logger.handlers = original_handlers
        logger.setLevel(original_level)


# ── Mock MCP client ──────────────────────────────────────────────────


class FakeMCPClient:
    """In-memory MCP client stand-in.

    Records every call_tool_sync invocation and returns canned responses
    keyed by tool name. Tests can configure responses ahead of time and
    inspect the call log afterward.

    Response shape mirrors the real MCP TypedDict that mcp_checks expects:
        {"content": [{"text": "..."}], "structuredContent": {...}}

    For success: provide either the structuredContent dict or a text string.
    For failure: configure the tool name to raise.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._responses: dict[str, dict[str, Any]] = {}
        self._failures: dict[str, Exception] = {}

    def set_response(
        self,
        tool_name: str,
        *,
        text: str | None = None,
        structured: dict[str, Any] | None = None,
        is_error: bool = False,
    ) -> None:
        result: dict[str, Any] = {}
        if structured is not None:
            result["structuredContent"] = structured
        if text is not None:
            result["content"] = [{"text": text}]
        if is_error:
            result["isError"] = True
        self._responses[tool_name] = result

    def set_failure(self, tool_name: str, exc: Exception) -> None:
        self._failures[tool_name] = exc

    def call_tool_sync(
        self, tool_use_id: str, tool_name: str, kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((tool_use_id, tool_name, kwargs))
        if tool_name in self._failures:
            raise self._failures[tool_name]
        return self._responses.get(tool_name, {"content": [{"text": "OK"}]})


@pytest.fixture
def fake_mcp_client() -> FakeMCPClient:
    return FakeMCPClient()


# ── Rate limiter reset ───────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Ensure each test starts with a fresh rate limiter."""
    from eks_review_agent.core.rate_limiter import get_rate_limiter

    rl = get_rate_limiter()
    rl.reset()
    yield
    rl.reset()


@pytest.fixture(autouse=True)
def reset_session_state():
    """Ensure each test starts with a fresh Session singleton."""
    from eks_review_agent.session import reset_session

    reset_session()
    yield
    reset_session()


# ── Strands hook event helpers ───────────────────────────────────────


@pytest.fixture
def make_before_tool_event():
    """Factory for BeforeToolCallEvent-shaped objects without spinning up Strands.

    The observability plugin only reads `tool_use` and writes `cancel_tool`,
    so a thin attribute holder is enough for unit-level testing.
    """

    class _Event:
        def __init__(self, name: str, tool_input: dict[str, Any] | None = None,
                     tool_use_id: str = "test-tool-id"):
            self.tool_use = {
                "name": name,
                "toolUseId": tool_use_id,
                "input": tool_input or {},
            }
            self.cancel_tool: bool | str = False
            self.agent = MagicMock()

    return _Event


# ── Misc ─────────────────────────────────────────────────────────────


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    """Helper: env(KEY=val, OTHER=val2) sets env vars for one test."""

    def _set(**kwargs: str) -> None:
        for k, v in kwargs.items():
            monkeypatch.setenv(k, v)

    return _set
