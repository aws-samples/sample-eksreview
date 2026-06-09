"""Knowledge base search tool for the agent.

Exposes the local knowledge base as a Strands @tool so the agent
can search indexed files/docs during conversation.
"""

import logging
from pathlib import Path

from strands import tool

from eks_review_agent.config import KNOWLEDGE_DIR
from eks_review_agent.knowledge.knowledge_base import KnowledgeBase

logger = logging.getLogger("eksreview")

# Singleton — initialized once, reused across calls
_kb: KnowledgeBase | None = None


def get_knowledge_base() -> KnowledgeBase:
    """Get or create the singleton KnowledgeBase instance."""
    global _kb
    if _kb is None:
        _kb = KnowledgeBase(KNOWLEDGE_DIR)
    return _kb


@tool
def knowledge_search(query: str, top_k: int = 3) -> str:
    """Search the local knowledge base for relevant information.

    The knowledge base contains files and documents that the user has
    indexed with /knowledge add. Use this tool when:
    - The user asks about content they've previously indexed
    - You need reference material from local docs, configs, or code
    - You want to find relevant context from the user's project files

    Args:
        query: Natural language search query.
        top_k: Maximum number of results to return (default 3).

    Returns:
        Matching text chunks with source file information, or a message
        if the knowledge base is empty or no results found.
    """
    kb = get_knowledge_base()

    if kb._doc_count == 0:
        return "Knowledge base is empty. The user can add files with /knowledge add."

    results = kb.search(query, top_k=top_k)
    if not results:
        return f"No relevant results found for: {query}"

    parts = []
    for i, (meta, score) in enumerate(results, 1):
        filepath = meta["file"]
        chunk_idx = meta["chunk_index"]
        total = meta["total_chunks"]
        entry_name = meta["entry_name"]
        # Chunk text is stored directly in the index — no file re-reading needed
        chunk_text = meta["content"]

        parts.append(
            f"--- Result {i} [{entry_name}] {Path(filepath).name} "
            f"(chunk {chunk_idx+1}/{total}, score: {score:.2f}) ---\n"
            f"{chunk_text}"
        )

    return "\n\n".join(parts)
