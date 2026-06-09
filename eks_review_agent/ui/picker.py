"""Interactive picker for terminal menus.

Uses arrow keys on macOS/Linux (tty/termios), falls back to
numbered input on Windows.
"""

import os
import sys

IS_WINDOWS = os.name == "nt"

# ANSI helpers — Windows Terminal and PowerShell support these,
# but we enable VT processing to be safe
CYAN = "\033[36m"
RESET = "\033[0m"


def _enable_windows_ansi():
    """Enable ANSI escape codes on Windows."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # STD_OUTPUT_HANDLE = -11
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def pick(options: list[str], title: str = "Select:", selected: int = 0) -> int | None:
    """Show an interactive picker.

    On macOS/Linux: arrow keys + Enter.
    On Windows: numbered input.

    Returns selected index, or None if cancelled.
    """
    _enable_windows_ansi()

    if not options:
        return None

    if IS_WINDOWS:
        return _pick_numbered(options, title, selected)
    else:
        return _pick_arrow(options, title, selected)


def _pick_numbered(options: list[str], title: str, selected: int) -> int | None:
    """Windows fallback — numbered selection."""
    print(f"\n  {title}\n")
    for i, opt in enumerate(options, 1):
        marker = f"{CYAN}>{RESET}" if i - 1 == selected else " "
        print(f"  {marker} {i}. {opt}")
    print(f"\n  Enter number (1-{len(options)}) or press Enter to cancel.")

    try:
        choice = input("  > ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if not choice:
        return None

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(options):
            return idx
    except ValueError:
        pass

    return None


def _pick_arrow(options: list[str], title: str, selected: int) -> int | None:
    """macOS/Linux — arrow key selection with tty/termios."""
    import tty
    import termios

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    n = len(options)
    HIDE = "\033[?25l"
    SHOW = "\033[?25h"

    def _restore():
        """Always-safe terminal restore. Idempotent."""
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except Exception:
            pass
        try:
            sys.stdout.write(SHOW)
            sys.stdout.flush()
        except Exception:
            pass

    def _read_key():
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            s1 = sys.stdin.read(1)
            if s1 == "[":
                s2 = sys.stdin.read(1)
                if s2 == "A":
                    return "up"
                elif s2 == "B":
                    return "down"
            return "esc"
        if ch == "\x03":
            return "esc"
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "k":
            return "up"
        if ch == "j":
            return "down"
        return ch

    def _draw(first_time=False):
        if not first_time:
            sys.stdout.write(f"\033[{n + 1}A")
        sys.stdout.write(f"\r\033[K  {title}\n")
        for i, opt in enumerate(options):
            sys.stdout.write("\r\033[K")
            if i == selected:
                sys.stdout.write(f"  {CYAN}> {opt}{RESET}\n")
            else:
                sys.stdout.write(f"    {opt}\n")
        sys.stdout.flush()

    # try/finally guarantees terminal restore even on KeyboardInterrupt,
    # SystemExit, or any other BaseException raised during the loop. Without
    # this, Ctrl+C mid-pick leaves the terminal in raw mode with cursor hidden.
    try:
        sys.stdout.write(HIDE)
        sys.stdout.flush()
        _draw(first_time=True)
        tty.setraw(fd)

        while True:
            key = _read_key()
            if key == "up":
                selected = (selected - 1) % n
            elif key == "down":
                selected = (selected + 1) % n
            elif key == "enter":
                return selected
            elif key == "esc":
                return None
            else:
                continue

            # Switch back to cooked mode briefly to redraw, then back to raw.
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            _draw()
            tty.setraw(fd)
    finally:
        _restore()
