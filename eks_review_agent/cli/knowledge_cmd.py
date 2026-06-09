"""/knowledge handler — show, add, remove, search, update, clear.

Operates against the singleton KnowledgeBase. All branches return a
string the REPL prints; only `clear` interacts directly with stdin
to confirm a destructive operation.
"""

from __future__ import annotations


def handle_knowledge(user_input: str, kb) -> str:
    """Dispatch /knowledge subcommands. Returns the line(s) to print."""
    parts = user_input.split(None, 1)
    if len(parts) < 2:
        return _knowledge_usage()

    rest = parts[1].strip()
    sub_parts = rest.split(None, 1)
    action = sub_parts[0].lower() if sub_parts else ""

    if action == "show":
        return kb.show()

    if action == "add":
        return _knowledge_add(rest, kb)

    if action == "remove":
        if len(sub_parts) < 2:
            return "  Usage: /knowledge remove <name>"
        return kb.remove(sub_parts[1].strip())

    if action == "search":
        if len(sub_parts) < 2:
            return "  Usage: /knowledge search <query>"
        return kb.search_formatted(sub_parts[1].strip())

    if action == "update":
        if len(sub_parts) < 2:
            return "  Usage: /knowledge update <name>"
        return kb.update(sub_parts[1].strip())

    if action == "clear":
        confirm = input("  Remove ALL knowledge base entries? [y/*] ").strip().lower()
        if confirm == "y":
            return kb.clear()
        return "  Cancelled."

    return _knowledge_usage()


def _knowledge_add(args_str: str, kb) -> str:
    """Parse `/knowledge add <name> <path> [--include …] [--exclude …]`."""
    rest = args_str.split(None, 1)
    if len(rest) < 2:
        return _add_usage()

    tokens = rest[1].strip().split()
    if len(tokens) < 2:
        return _add_usage()

    name = tokens[0]
    include_patterns: list[str] = []
    exclude_patterns: list[str] = []
    path_parts: list[str] = []

    i = 1
    while i < len(tokens):
        if tokens[i] == "--include" and i + 1 < len(tokens):
            include_patterns.append(tokens[i + 1].strip("\"'"))
            i += 2
        elif tokens[i] == "--exclude" and i + 1 < len(tokens):
            exclude_patterns.append(tokens[i + 1].strip("\"'"))
            i += 2
        else:
            path_parts.append(tokens[i])
            i += 1

    if not path_parts:
        return "  Usage: /knowledge add <name> <path>"

    path = " ".join(path_parts)
    return kb.add(
        name,
        path,
        include_patterns=include_patterns or None,
        exclude_patterns=exclude_patterns or None,
    )


def _add_usage() -> str:
    return (
        "  Usage: /knowledge add <name> <path> [--include pattern] [--exclude pattern]\n"
        "  Example: /knowledge add eks-docs ~/docs/eks-best-practices"
    )


def _knowledge_usage() -> str:
    return (
        "\n  Knowledge Base Commands\n"
        "  ──────────────────────\n"
        "    /knowledge show                          Show all entries\n"
        "    /knowledge add <name> <path> [options]   Index files from path\n"
        "    /knowledge remove <name>                 Remove an entry\n"
        "    /knowledge search <query>                Search indexed content\n"
        "    /knowledge update <name>                 Re-index from source\n"
        "    /knowledge clear                         Remove all entries\n"
        "\n"
        "  Options for add:\n"
        "    --include <pattern>   Glob pattern for files to include\n"
        "    --exclude <pattern>   Glob pattern for files to exclude\n"
        "\n"
        "  Examples:\n"
        "    /knowledge add eks-docs ~/docs/eks-best-practices\n"
        '    /knowledge add my-configs ./configs --include "*.yaml" --exclude "test/**"\n'
        "    /knowledge search pod security standards\n"
    )
