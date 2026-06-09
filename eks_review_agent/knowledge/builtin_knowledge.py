"""Auto-sync EKS Best Practices Guide into the knowledge base.

Downloads the official AWS EKS Best Practices PDF and indexes it
on startup. Uses HTTP ETag/Last-Modified headers to detect updates
and skip re-indexing when the content hasn't changed.

No git dependency — single PDF download over HTTPS.
"""

import io
import json
import logging
import sys
from pathlib import Path

import requests

from eks_review_agent.config import KNOWLEDGE_DIR

logger = logging.getLogger("eksreview")

ENTRY_NAME = "eks-best-practices"
PDF_URL = "https://docs.aws.amazon.com/pdfs/eks/latest/best-practices/eks-bpg.pdf"
STATE_FILE = KNOWLEDGE_DIR / "eks-bp-sync-state.json"


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


def _check_for_update() -> tuple[bool, dict]:
    """Check if the PDF has been updated using HTTP HEAD request.

    Returns (needs_update, new_state) where new_state contains
    etag and last_modified for saving after successful indexing.
    """
    state = _load_state()
    try:
        resp = requests.head(PDF_URL, timeout=10, allow_redirects=True)
        if resp.status_code != 200:
            return (not state.get("etag"), state)
    except Exception as e:
        logger.warning("Failed to check EKS best practices PDF: %s", e)
        return (not state.get("etag"), state)

    etag = resp.headers.get("ETag", "")
    last_modified = resp.headers.get("Last-Modified", "")

    new_state = {"etag": etag, "last_modified": last_modified}

    # Compare with saved state
    if etag and etag == state.get("etag"):
        return (False, new_state)
    if last_modified and last_modified == state.get("last_modified"):
        return (False, new_state)

    # No match or first run — needs update
    return (True, new_state)


def _download_pdf() -> bytes | None:
    """Download the PDF with progress indicator."""
    try:
        resp = requests.get(PDF_URL, timeout=60, stream=True)
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to download EKS best practices PDF: %s", e)
        return None

    total = int(resp.headers.get("content-length", 0))
    chunks = []
    downloaded = 0

    for chunk in resp.iter_content(chunk_size=32768):
        chunks.append(chunk)
        downloaded += len(chunk)
        if total > 0:
            pct = downloaded * 100 // total
            mb = downloaded / (1024 * 1024)
            sys.stdout.write(f"\r    Downloading PDF... {mb:.1f}MB ({pct}%)")
            sys.stdout.flush()

    if total > 0:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    return b"".join(chunks)


def _extract_pdf_text(pdf_bytes: bytes) -> str | None:
    """Extract text from PDF bytes using pdfminer.six.

    Returns None if pdfminer is not installed or the PDF can't be parsed.
    The startup-time log in knowledge_base.py already announces missing
    pdfminer; we don't repeat the install hint here on every call.
    """
    try:
        from pdfminer.high_level import extract_text
    except ImportError:
        logger.warning(
            "Cannot sync EKS Best Practices PDF — pdfminer.six is not installed."
        )
        return None

    try:
        text = extract_text(io.BytesIO(pdf_bytes))
        return text if text and text.strip() else None
    except Exception as e:
        logger.error("PDF extraction failed: %s", e)
        return None


def sync_eks_best_practices(kb) -> str:
    """Sync the EKS Best Practices Guide PDF into the knowledge base.

    Checks for updates via HTTP headers and only re-indexes when changed.
    Set EKS_REVIEW_OFFLINE=1 to skip the network call entirely (useful for
    air-gapped environments or fast restarts).

    Args:
        kb: KnowledgeBase instance.

    Returns:
        Status message for display.
    """
    import os

    if os.environ.get("EKS_REVIEW_OFFLINE", "").strip().lower() in ("1", "true", "yes"):
        if _load_state().get("etag"):
            return "eks-best-practices (offline mode, using cached index)"
        return "eks-best-practices (offline mode, no cached index)"

    needs_update, new_state = _check_for_update()

    if not needs_update:
        return "eks-best-practices (up to date)"

    # Download PDF
    pdf_bytes = _download_pdf()
    if not pdf_bytes:
        if _load_state().get("etag"):
            return "eks-best-practices (download failed, using cached index)"
        return "eks-best-practices (download failed)"

    # Extract text
    text = _extract_pdf_text(pdf_bytes)
    if not text:
        return "eks-best-practices (PDF extraction failed — install pdfminer.six)"

    # Index via the public KnowledgeBase API. add_synthetic_entry handles
    # idempotent replacement, chunking, BM25 reload, and metadata bookkeeping.
    chunk_count = kb.add_synthetic_entry(name=ENTRY_NAME, source=PDF_URL, text=text)

    _save_state(new_state)

    return f"eks-best-practices synced (1 PDF, {chunk_count} chunks)"
