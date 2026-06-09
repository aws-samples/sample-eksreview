"""Tests for the shared Spinner helper."""

from __future__ import annotations

import io
import time
from contextlib import redirect_stdout

import pytest

from eks_review_agent.core.spinner import SPINNER_FRAMES, Spinner


def test_spinner_writes_to_stdout() -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        s = Spinner("Working", interval_s=0.01)
        s.start()
        time.sleep(0.05)
        elapsed = s.stop("Done")
    out = buf.getvalue()
    # Some braille frame must appear
    assert any(frame in out for frame in SPINNER_FRAMES)
    # Final line written
    assert "Done" in out
    assert elapsed >= 0


def test_spinner_idempotent_start() -> None:
    s = Spinner("Working", interval_s=0.01)
    buf = io.StringIO()
    with redirect_stdout(buf):
        s.start()
        s.start()  # second call should be a no-op
        time.sleep(0.02)
        s.stop()


def test_spinner_idempotent_stop() -> None:
    """Calling stop() twice must not crash. Returns elapsed-since-start
    even after a previous stop, which is acceptable behavior — the
    spinner thread is gone, so subsequent stops are best-effort.
    """
    s = Spinner("Working", interval_s=0.01)
    buf = io.StringIO()
    with redirect_stdout(buf):
        s.start()
        time.sleep(0.02)
        first = s.stop()
        # Second call must be safe
        second = s.stop()
    assert first >= 0
    assert second >= 0


def test_spinner_no_elapsed_mode() -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        s = Spinner("Working", show_elapsed=False, interval_s=0.01)
        s.start()
        time.sleep(0.02)
        s.stop()
    out = buf.getvalue()
    # Output should NOT contain a "(Ns)" pattern
    import re
    assert not re.search(r"\(\d+s\)", out)


def test_spinner_returns_elapsed() -> None:
    s = Spinner("Working", interval_s=0.01)
    with redirect_stdout(io.StringIO()):
        s.start()
        time.sleep(0.05)
        elapsed = s.stop()
    assert elapsed >= 0.04  # roughly 0.05s
