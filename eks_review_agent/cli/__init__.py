"""Command-line interface package — REPL, slash commands, banner.

main.py owns the bootstrap (config validation, MCP connection, agent
construction, signal handling). Everything else that touches the
interactive REPL — slash command parsing, dispatch, output — lives
under this package, one file per command.

Public surface:
    banner: print_banner, print_startup_box, BANNER, TIPS
    readline_setup: setup_readline, SLASH_COMMANDS
    repl: conversation_loop  (the only callable main.py invokes)
    Each *_cmd.py module exposes a single public handler function.
"""
