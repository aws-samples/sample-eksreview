"""Shared progress-spinner helper for long-running operations.

Used by mcp_checks, review_orchestrator, and upgrade_orchestrator. The
"thinking..." spinner in callbacks.py is intentionally separate — it
flashes for a model's first response token and has different lifecycle
semantics, so it stays self-contained.

Usage:
    spinner = Spinner("Compiling report")
    spinner.start()
    try:
        do_work()
        spinner.stop("  ✓ Report compiled (12s)")
    except Exception:
        spinner.stop("  ✗ Compilation failed")
        raise
"""

import sys
import threading
import time
from typing import Optional

# Single source of truth for the braille frames used everywhere.
SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class Spinner:
    """Context-managed progress spinner with elapsed time.

    The spinner runs in a daemon thread and writes a single redrawn line
    on stdout. Calling stop() clears the line and writes the final
    `result_msg`. Idempotent — multiple stop() calls are safe.

    Args:
        message: Static text shown next to the rotating frame.
        prefix: Leading whitespace for indentation. Default two spaces
                matches the rest of the CLI's output style.
        show_elapsed: If True, append "(Ns)" to the line. Default True.
        interval_s: Frame rate in seconds.
    """

    def __init__(
        self,
        message: str,
        prefix: str = "  ",
        show_elapsed: bool = True,
        interval_s: float = 0.1,
    ):
        self.message = message
        self.prefix = prefix
        self.show_elapsed = show_elapsed
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started_at: float = 0.0

    def start(self) -> None:
        """Begin spinning. No-op if already started."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._started_at = time.monotonic()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        i = 0
        while not self._stop.is_set():
            frame = SPINNER_FRAMES[i % len(SPINNER_FRAMES)]
            if self.show_elapsed:
                elapsed = time.monotonic() - self._started_at
                line = f"\r{self.prefix}{frame} {self.message} ({elapsed:.0f}s)"
            else:
                line = f"\r{self.prefix}{frame} {self.message}"
            sys.stdout.write(line)
            sys.stdout.flush()
            self._stop.wait(self.interval_s)
            i += 1

    def stop(self, result_msg: Optional[str] = None) -> float:
        """Stop the spinner and optionally write a final line.

        Returns the elapsed time so callers can avoid recomputing it.
        """
        elapsed = time.monotonic() - self._started_at if self._started_at else 0.0
        self._stop.set()
        if self._thread:
            self._thread.join()
            self._thread = None
        # Clear the spinner line and write the result.
        sys.stdout.write("\r\033[K")
        if result_msg is not None:
            sys.stdout.write(result_msg + "\n")
        sys.stdout.flush()
        return elapsed
