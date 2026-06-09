"""Readline setup — slash command tab completion and prompt protection.

Single source of truth for the slash command list. cli.repl uses this
list for dispatch validation; the completer uses it for tab completion.
Keeping both consumers anchored to the same module prevents the kind
of drift where a new command works but isn't autocompleted.
"""

from __future__ import annotations


SLASH_COMMANDS: tuple[str, ...] = (
    "/help", "/tools", "/model", "/fix", "/investigate", "/upgrade",
    "/export", "/context", "/exit", "/quit",
    "/skill list", "/skill add", "/skill remove", "/skill info",
    "/knowledge show", "/knowledge add", "/knowledge remove",
    "/knowledge search", "/knowledge update", "/knowledge clear",
)


def setup_readline() -> None:
    """Configure readline for prompt protection and tab completion.

    No-op on systems where readline isn't available (Windows without
    pyreadline3). Safe to call multiple times.
    """
    try:
        import readline
    except ImportError:
        return

    def completer(text, state):
        line = readline.get_line_buffer()
        if line.startswith("/"):
            matches = [c + " " for c in SLASH_COMMANDS if c.startswith(line)]
        else:
            matches = []
        return matches[state] if state < len(matches) else None

    readline.set_completer(completer)
    readline.set_completer_delims("")

    # macOS uses libedit, Linux/Linux-on-Windows uses GNU readline.
    doc = getattr(readline, "__doc__", "") or ""
    if "libedit" in doc:
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")
