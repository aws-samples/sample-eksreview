"""Startup banner, tip box, and ANSI color constants."""

from __future__ import annotations

import random


# ANSI color helpers — used throughout the CLI for status lines.
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"


BANNER = """
  ####### ##  ## #######
  ##      ## ##  ##
  #####   ####   #######
  ##      ## ##       ##
  ####### ##  ## #######

  ####  ####  #   # #  ####  #   #
  #   # #     #   # #  #     #   #
  ####  ###   #   # #  ###   # # #
  # #   #      # #  #  #     ## ##
  #  #  ####    #   #  ####  #   #
"""

TIPS = (
    "Use /model to switch between Claude Opus and Sonnet at runtime.",
    "Use /knowledge add to index local docs the agent can search.",
    "Use /skill list to see available review skills.",
    "Use /tools to see all available MCP and built-in tools.",
    "Previous review reports are saved in ./reports/ for trend analysis.",
)


def print_banner() -> None:
    """Emit the ASCII banner. Single side-effect call."""
    print(BANNER)


def print_box(title: str, lines: list[str], width: int = 58) -> None:
    """Print a bordered box with a centered title and content lines.

    Used for the startup splash. Wraps long lines at the inner width
    so the box border stays aligned regardless of the input length.
    """
    padding = width - len(title) - 4  # 4 = "─ " + " ─"
    left_pad = padding // 2
    right_pad = padding - left_pad
    print(f"  ┌{'─' * left_pad}─ {title} ─{'─' * right_pad}┐")
    for line in lines:
        while len(line) > width - 1:
            print(f"  │ {line[:width - 1]:<{width - 1}}│")
            line = line[width - 1:]
        print(f"  │ {line:<{width - 1}}│")
    print(f"  └{'─' * width}┘")


def print_startup_tip_box() -> None:
    """Print the post-startup tip-of-the-session box.

    Picks a random tip from TIPS each invocation so the experience
    feels fresh on session resume.
    """
    print_box("eksreview", [
        "Operations Reviewer for Amazon EKS",
        "",
        "Run best-practice reviews against your Amazon EKS",
        "clusters. Checks security, networking, resiliency,",
        "Karpenter, Cluster Autoscaler, and observability.",
        "",
        # Cosmetic tip selection only — not security-sensitive (ruff S311).
        f"Tip: {random.choice(TIPS)}",  # noqa: S311
    ])
