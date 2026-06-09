"""Callback handler for real-time streaming output.

Follows q-cli display pattern:
  [spinner while model is thinking]
  > agent text streams here...
  ⋮
  ● Using tool: tool_name
  ⋮
  ● Completed in X.Xs
  > more agent text...

Spinner only shows while waiting for the model's first response token.
Tool execution uses static status lines, no spinner.
"""

import os
import sys
import threading
import time

# Enable ANSI on Windows
if os.name == "nt":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass

_seen_tool_ids: set[str] = set()
_response_started = False
_tool_start_times: dict[str, float] = {}
_thinking_spinner: threading.Thread | None = None
_thinking_stop = threading.Event()
_waiting_for_response = False
_lock = threading.Lock()  # Protects mutable state from race conditions

# ANSI
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

FRIENDLY_NAMES = {
    "think": "Thinking",
    "skills": "Loading skill",
    "get_review_history": "Checking review history",
    "run_full_review": "Running cluster review",
    "save_report": "Saving report",
    "file_write": "Writing file",
    "file_read": "Reading file",
    "shell": "Running command",
    "knowledge_search": "Searching knowledge base",
    "check_eks_security": "Checking security",
    "check_eks_resiliency": "Checking resiliency",
    "check_eks_networking": "Checking networking",
    "check_karpenter_best_practices": "Checking Karpenter",
    "check_cluster_autoscaler_best_practices": "Checking Cluster Autoscaler",
}


def _spin_thinking():
    """Background thread — spinner while waiting for model response."""
    i = 0
    while not _thinking_stop.is_set():
        frame = SPINNER_FRAMES[i % len(SPINNER_FRAMES)]
        sys.stdout.write(f"\r\033[K  {frame} Thinking...")
        sys.stdout.flush()
        _thinking_stop.wait(0.08)
        i += 1


def _start_thinking_spinner():
    """Start the thinking spinner (waiting for model)."""
    global _thinking_spinner
    _stop_thinking_spinner()
    _thinking_stop.clear()
    _thinking_spinner = threading.Thread(target=_spin_thinking, daemon=True)
    _thinking_spinner.start()


def _stop_thinking_spinner():
    """Stop the thinking spinner and clear the line."""
    global _thinking_spinner
    if _thinking_spinner and _thinking_spinner.is_alive():
        _thinking_stop.set()
        _thinking_spinner.join()
        _thinking_spinner = None
        # Only clear the line if a spinner was actually running
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()


def streaming_callback(**kwargs):
    """Stream agent output with q-cli style display."""
    global _response_started, _waiting_for_response

    if "data" in kwargs:
        _stop_thinking_spinner()

        data = kwargs["data"]

        with _lock:
            if not _response_started:
                _response_started = True
                _waiting_for_response = False
                data = data.lstrip("\n")
                if not data:
                    return
                sys.stdout.write(f"\n> {data}")
            else:
                sys.stdout.write(data)

        sys.stdout.flush()

    elif "current_tool_use" in kwargs:
        _stop_thinking_spinner()

        tool_info = kwargs["current_tool_use"]
        tool_id = tool_info.get("toolUseId", "")
        tool_name = tool_info.get("name", "")

        with _lock:
            if tool_name and tool_id not in _seen_tool_ids:
                _seen_tool_ids.add(tool_id)
                _tool_start_times[tool_id] = time.monotonic()

                label = FRIENDLY_NAMES.get(tool_name, tool_name)
                sys.stdout.write(f"\n {DIM}⋮{RESET}\n {GREEN}●{RESET} {label}")
                sys.stdout.write("\n")
                sys.stdout.flush()


def on_tool_complete(tool_id: str):
    """Called when a tool finishes to show completion time."""
    with _lock:
        elapsed = 0.0
        if tool_id in _tool_start_times:
            elapsed = time.monotonic() - _tool_start_times.pop(tool_id)
    sys.stdout.write(f" {DIM}⋮{RESET}\n {GREEN}●{RESET} Completed in {elapsed:.1f}s\n")
    sys.stdout.flush()


def start_thinking():
    """Start the thinking spinner — call after user submits a prompt."""
    global _waiting_for_response
    with _lock:
        _waiting_for_response = True
    _start_thinking_spinner()


def reset_seen_tools():
    """Clear tracked tool IDs and reset response prefix.

    Acquires the same lock as streaming_callback so a callback racing in
    from the previous turn can't observe a half-cleared state. Spinner
    stop is intentionally outside the lock — _stop_thinking_spinner has
    its own join() and joining a thread while holding _lock could
    deadlock if the spinner ever calls back into us.
    """
    global _response_started, _waiting_for_response
    _stop_thinking_spinner()
    with _lock:
        _seen_tool_ids.clear()
        _tool_start_times.clear()
        _response_started = False
        _waiting_for_response = False
