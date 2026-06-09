"""Local knowledge base with BM25 search, SQLite storage, and sentence-aware chunking.

Improvements over the initial implementation:
1. Chunk text stored in SQLite — no re-reading files at search time
2. Sentence-aware chunking — splits on paragraph/sentence boundaries
3. SQLite storage — faster, smaller, supports concurrent access
"""

import json
import logging
import math
import os
import re
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger("eksreview")

# File extensions we can index as text
SUPPORTED_EXTENSIONS = {
    ".py", ".rs", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".cpp", ".h",
    ".hpp", ".go", ".rb", ".php", ".swift", ".kt", ".cs", ".sh", ".bash",
    ".md", ".markdown", ".mdx", ".html", ".htm", ".xml", ".css", ".scss",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".conf", ".cfg", ".env",
    ".properties",
    ".txt", ".log", ".csv", ".tsv", ".rst", ".tex", ".rtf", ".sql",
    ".pdf",
    ".svg",
}

SUPPORTED_NAMES = {
    "Dockerfile", "Makefile", "LICENSE", "CHANGELOG", "README",
    "Vagrantfile", "Gemfile", "Rakefile", "Procfile",
}

DEFAULT_CHUNK_SIZE = 1024
DEFAULT_CHUNK_OVERLAP = 128
DEFAULT_MAX_FILES = 10000

# Sentence-ending patterns
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+|\n\n+|\n(?=#{1,6}\s)|\n(?=[-*]\s)')


def _is_supported_file(path: Path) -> bool:
    if path.name in SUPPORTED_NAMES:
        return True
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def _read_file_content(path: Path) -> str | None:
    """Read text content from a file, with PDF extraction support."""
    if path.suffix.lower() == ".pdf":
        return _extract_pdf_text(path)
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


# Probe for PDF support at import time so we can log a clear message
# rather than silently failing each time a PDF is indexed. Stays a soft
# warning rather than a hard failure because plenty of users index only
# text files / markdown and don't need pdfminer.
try:
    from pdfminer.high_level import extract_text as _pdfminer_extract  # noqa: F401
    _pdf_support = True
except ImportError:
    _pdf_support = False
    logger.warning(
        "pdfminer.six not installed — PDF indexing disabled. "
        "Install with: pip install 'pdfminer.six>=20221105'"
    )


def _extract_pdf_text(path: Path) -> str | None:
    if not _pdf_support:
        # Single warning at module load already covered the install hint;
        # don't spam per-file here.
        return None
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(str(path))
        return text if text and text.strip() else None
    except Exception as e:
        logger.warning("pdfminer failed for %s: %s", path, e)
        return None


def _chunk_text_semantic(text: str, target_size: int = DEFAULT_CHUNK_SIZE,
                         overlap_sentences: int = 2) -> list[str]:
    """Split text into chunks on sentence/paragraph boundaries.

    Instead of cutting at fixed character positions, this splits on natural
    boundaries (sentence ends, paragraph breaks, markdown headers) and then
    groups sentences into chunks of approximately target_size characters.
    Overlaps by re-including the last N sentences from the previous chunk.
    """
    # Split into sentences/segments
    segments = _SENTENCE_END.split(text)
    segments = [s.strip() for s in segments if s.strip()]

    if not segments:
        return [text] if text.strip() else []

    # If the whole text fits in one chunk, return it
    if len(text) <= target_size:
        return [text]

    chunks = []
    current_segments: list[str] = []
    current_len = 0

    for seg in segments:
        seg_len = len(seg)

        # If adding this segment exceeds target, finalize current chunk
        if current_len + seg_len > target_size and current_segments:
            chunks.append(" ".join(current_segments))
            # Overlap: keep last N sentences for context continuity
            current_segments = current_segments[-overlap_sentences:] if overlap_sentences else []
            current_len = sum(len(s) for s in current_segments)

        current_segments.append(seg)
        current_len += seg_len

    # Don't forget the last chunk
    if current_segments:
        last = " ".join(current_segments)
        # Avoid duplicating if it's identical to the previous chunk
        if not chunks or last != chunks[-1]:
            chunks.append(last)

    return chunks


class KnowledgeBase:
    """Local knowledge base with BM25 search and SQLite storage.

    Storage: single SQLite database at <kb_dir>/knowledge.db
    Tables:
        entries  — metadata per add operation
        chunks   — chunk text + metadata, one row per chunk
        bm25     — term frequencies and doc frequencies for BM25 scoring
    """

    def __init__(self, kb_dir: str | Path, chunk_size: int = DEFAULT_CHUNK_SIZE,
                 max_files: int = DEFAULT_MAX_FILES):
        self.kb_dir = Path(kb_dir)
        self.kb_dir.mkdir(parents=True, exist_ok=True)
        self.chunk_size = chunk_size
        self.max_files = max_files

        self._db_path = self.kb_dir / "knowledge.db"
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

        # BM25 state loaded into memory for fast search
        self._doc_count: int = 0
        self._avg_dl: float = 0.0
        self._doc_lengths: list[int] = []
        self._doc_freqs: dict[str, int] = {}
        self._term_freqs: list[dict[str, int]] = []
        self._chunk_ids: list[int] = []
        # Inverted index: term -> list of (doc_index, term_freq) for O(k) search
        self._inverted_index: dict[str, list[tuple[int, int]]] = {}
        self._load_bm25_state()

    def _init_schema(self):
        c = self._conn
        c.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                name TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                include_patterns TEXT DEFAULT '[]',
                exclude_patterns TEXT DEFAULT '[]',
                file_count INTEGER DEFAULT 0,
                chunk_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                total_chunks INTEGER NOT NULL,
                content TEXT NOT NULL,
                tokens TEXT NOT NULL,
                token_count INTEGER NOT NULL,
                FOREIGN KEY (entry_name) REFERENCES entries(name)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_chunks_entry ON chunks(entry_name)")
        c.commit()

    def _load_bm25_state(self) -> None:
        """Load BM25 scoring state from SQLite into memory with inverted index."""
        self._doc_count = 0
        self._avg_dl = 0.0
        self._doc_lengths = []
        self._doc_freqs = {}
        self._term_freqs = []
        self._chunk_ids = []
        self._inverted_index = {}

        rows = self._conn.execute(
            "SELECT id, tokens, token_count FROM chunks ORDER BY id"
        ).fetchall()

        total_tokens = 0
        for row_id, tokens_json, token_count in rows:
            tf = json.loads(tokens_json)
            doc_idx = self._doc_count

            self._term_freqs.append(tf)
            self._doc_lengths.append(token_count)
            self._chunk_ids.append(row_id)
            self._doc_count += 1
            total_tokens += token_count

            for term, freq in tf.items():
                self._doc_freqs[term] = self._doc_freqs.get(term, 0) + 1
                if term not in self._inverted_index:
                    self._inverted_index[term] = []
                self._inverted_index[term].append((doc_idx, freq))

        self._avg_dl = total_tokens / self._doc_count if self._doc_count else 0


    @property
    def entries(self) -> dict:
        """Return entries as a dict for compatibility with startup display."""
        rows = self._conn.execute("SELECT name, chunk_count FROM entries").fetchall()
        return {name: type("E", (), {"chunk_count": cc})() for name, cc in rows}

    def _tokenize(self, text: str) -> dict[str, int]:
        """Tokenize text and return term frequency dict.

        Uses `\\w+` so punctuation, hyphens, and dots are treated as
        token boundaries. Note: this means version strings like
        "v1.16.0" are split into ["v1", "16", "0"] — a search for
        "v1.16" matches any document containing "v1" and "16". This is
        accepted: BM25 still ranks documents with both tokens highest,
        and the EKS use cases (check names, finding text, remediation
        commands) don't depend on exact version-string matching.
        """
        tokens = re.findall(r"\w+", text.lower())
        tf: dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        return tf

    def add(self, name: str, path: str,
            include_patterns: list[str] | None = None,
            exclude_patterns: list[str] | None = None) -> str:
        """Add files from a path to the knowledge base."""
        # Input validation
        if not name or not name.strip():
            return "  Entry name cannot be empty."
        name = name.strip()
        if len(name) > 100:
            return "  Entry name too long (max 100 characters)."
        if not re.match(r"^[\w\-. ]+$", name):
            return "  Entry name can only contain letters, numbers, dashes, dots, and spaces."

        if not path or not path.strip():
            return "  Path cannot be empty."

        existing = self._conn.execute("SELECT 1 FROM entries WHERE name=?", (name,)).fetchone()
        if existing:
            return f"  Entry '{name}' already exists. Remove it first with /knowledge remove {name}"

        source = Path(path).expanduser().resolve()
        if not source.exists():
            return f"  Path not found: {source}"

        # Capture the user-supplied path BEFORE symlink resolution so we
        # can also block on the literal form (e.g. user passes "/etc"
        # which on macOS resolves to "/private/etc"; the resolved form
        # is fine to compare but we must not over-block "/private/var"
        # because that's where macOS puts pytest tmpdirs etc.).
        literal_path = str(Path(path).expanduser().absolute())

        # Resolve symlinks and verify the real path
        real_source = source.resolve(strict=True)
        if real_source != source:
            logger.info("Resolved symlink: %s -> %s", source, real_source)
            source = real_source

        # Block sensitive system paths and user credential directories.
        # We compare against BOTH the literal user-supplied path and the
        # symlink-resolved path so that:
        #   - User passing /etc on Linux is blocked (literal match).
        #   - User passing /etc on macOS is blocked (literal match before
        #     it resolves to /private/etc).
        #   - User passing /var/folders/... (a tmp dir, resolves to
        #     /private/var/folders/...) is NOT blocked because neither
        #     /var/folders nor /private/var/folders is in the blocklist.
        blocked_system = ["/etc", "/var", "/usr", "/bin", "/sbin", "/proc", "/sys", "/dev"]
        blocked_user_subpaths = [
            ".aws",            # AWS credentials and config
            ".ssh",            # SSH keys
            ".kube",           # kubeconfig and tokens
            ".gnupg",          # GPG keys
            ".config/gcloud",  # GCP credentials
            ".azure",          # Azure credentials
            ".docker",         # Docker registry auth
            ".netrc",          # HTTP basic auth
        ]
        home = Path.home().resolve()
        blocked_user = [str(home / sub) for sub in blocked_user_subpaths]
        blocked = blocked_system + blocked_user

        for path_to_check in (literal_path, str(source)):
            if any(
                path_to_check == b or path_to_check.startswith(b + "/")
                for b in blocked
            ):
                return f"  Cannot index sensitive path: {source}"

        # Collect files
        if source.is_file():
            files = [source]
        else:
            files = []
            for root, _dirs, fnames in os.walk(source):
                for fname in fnames:
                    fp = Path(root) / fname
                    if not _is_supported_file(fp):
                        continue
                    if include_patterns or exclude_patterns:
                        import fnmatch
                        rel = str(fp)
                        if exclude_patterns and any(
                            fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(fp.name, p)
                            for p in exclude_patterns
                        ):
                            continue
                        if include_patterns and not any(
                            fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(fp.name, p)
                            for p in include_patterns
                        ):
                            continue
                    files.append(fp)
                    if len(files) >= self.max_files:
                        break
                if len(files) >= self.max_files:
                    break

        files_indexed = 0
        chunks_indexed = 0

        for fp in files:
            content = _read_file_content(fp)
            if not content:
                continue

            chunks = _chunk_text_semantic(content, self.chunk_size)
            for i, chunk in enumerate(chunks):
                tf = self._tokenize(chunk)
                token_count = sum(tf.values())
                self._conn.execute(
                    "INSERT INTO chunks (entry_name, file_path, chunk_index, total_chunks, content, tokens, token_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (name, str(fp), i, len(chunks), chunk, json.dumps(tf), token_count),
                )
                chunks_indexed += 1
            files_indexed += 1

        self._conn.execute(
            "INSERT INTO entries (name, source_path, include_patterns, exclude_patterns, file_count, chunk_count, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, str(source), json.dumps(include_patterns or []), json.dumps(exclude_patterns or []),
             files_indexed, chunks_indexed, time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        self._conn.commit()
        self._load_bm25_state()

        logger.info("Knowledge add: %s — %d files, %d chunks", name, files_indexed, chunks_indexed)
        return f"  Added '{name}': {files_indexed} files, {chunks_indexed} chunks indexed from {source}"

    def add_synthetic_entry(
        self,
        name: str,
        source: str,
        text: str,
    ) -> int:
        """Index a single synthetic document directly from text.

        Used for entries that don't come from a filesystem path — e.g. a PDF
        downloaded over HTTP and parsed in memory. Replaces any existing
        entry with the same name. Returns the number of chunks indexed.

        This is the supported alternative to writing into _conn directly:
        callers get all the chunking, BM25 reload, and metadata management
        without depending on private internals.
        """
        if not name or not name.strip():
            raise ValueError("Entry name cannot be empty.")
        if not text or not text.strip():
            raise ValueError("Synthetic entry text cannot be empty.")

        # Replace any existing entry with this name (idempotent).
        if self._conn.execute("SELECT 1 FROM entries WHERE name=?", (name,)).fetchone():
            self._conn.execute("DELETE FROM chunks WHERE entry_name=?", (name,))
            self._conn.execute("DELETE FROM entries WHERE name=?", (name,))

        chunks = _chunk_text_semantic(text, self.chunk_size)
        for i, chunk in enumerate(chunks):
            tf = self._tokenize(chunk)
            token_count = sum(tf.values())
            self._conn.execute(
                "INSERT INTO chunks (entry_name, file_path, chunk_index, total_chunks, "
                "content, tokens, token_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, source, i, len(chunks), chunk, json.dumps(tf), token_count),
            )

        self._conn.execute(
            "INSERT INTO entries (name, source_path, include_patterns, exclude_patterns, "
            "file_count, chunk_count, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, source, "[]", "[]", 1, len(chunks),
             time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        self._conn.commit()
        self._load_bm25_state()
        logger.info("Synthetic entry indexed: %s — %d chunks", name, len(chunks))
        return len(chunks)

    def remove(self, name: str) -> str:
        """Remove an entry and its chunks."""
        existing = self._conn.execute("SELECT 1 FROM entries WHERE name=?", (name,)).fetchone()
        if not existing:
            return f"  Entry '{name}' not found."

        self._conn.execute("DELETE FROM chunks WHERE entry_name=?", (name,))
        self._conn.execute("DELETE FROM entries WHERE name=?", (name,))
        self._conn.commit()
        self._load_bm25_state()

        logger.info("Knowledge remove: %s", name)
        return f"  Removed '{name}' from knowledge base."

    def show(self) -> str:
        """Show all entries."""
        rows = self._conn.execute(
            "SELECT name, source_path, file_count, chunk_count, created_at FROM entries"
        ).fetchall()

        if not rows:
            return "  Knowledge base is empty. Use /knowledge add to index files."

        lines = ["\n  Knowledge Base Entries", "  ─────────────────────"]
        total_files = 0
        total_chunks = 0
        for name, source, fc, cc, created in rows:
            lines.append(
                f"    {name}\n"
                f"      Source: {source}\n"
                f"      Files: {fc}  Chunks: {cc}\n"
                f"      Added: {created}"
            )
            total_files += fc
            total_chunks += cc

        lines.append(f"\n  Total: {len(rows)} entries, {total_files} files, {total_chunks} chunks")
        return "\n".join(lines)

    def search(self, query: str, top_k: int = 5) -> list[tuple[dict, float]]:
        """BM25 search using inverted index for O(k) lookup."""
        if self._doc_count == 0:
            return []

        query_tf = self._tokenize(query)
        k1, b = 1.5, 0.75
        scores: dict[int, float] = {}  # Only score docs that match at least one term

        for token in query_tf:
            df = self._doc_freqs.get(token, 0)
            if df == 0:
                continue
            idf = math.log((self._doc_count - df + 0.5) / (df + 0.5) + 1.0)

            # Use inverted index — only iterate matching docs, not all docs
            for doc_idx, tf in self._inverted_index.get(token, []):
                dl = self._doc_lengths[doc_idx]
                num = tf * (k1 + 1)
                den = tf + k1 * (1 - b + b * dl / self._avg_dl)
                scores[doc_idx] = scores.get(doc_idx, 0.0) + idf * num / den

        # Get top-k by score
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

        results = []
        for doc_idx, score in ranked:
            chunk_id = self._chunk_ids[doc_idx]
            row = self._conn.execute(
                "SELECT entry_name, file_path, chunk_index, total_chunks, content FROM chunks WHERE id=?",
                (chunk_id,),
            ).fetchone()
            if row:
                results.append((
                    {
                        "entry_name": row[0],
                        "file": row[1],
                        "chunk_index": row[2],
                        "total_chunks": row[3],
                        "content": row[4],
                    },
                    score,
                ))

        return results

    def search_formatted(self, query: str, top_k: int = 5) -> str:
        """Search and return formatted results for CLI display."""
        results = self.search(query, top_k=top_k)
        if not results:
            return f"  No results found for: {query}"

        lines = [f"\n  Search results for: \"{query}\"", "  ─────────────────────"]
        for i, (meta, score) in enumerate(results, 1):
            fp = meta["file"]
            chunk_info = f"chunk {meta['chunk_index']+1}/{meta['total_chunks']}"
            lines.append(f"    {i}. [{meta['entry_name']}] {Path(fp).name} ({chunk_info}) — score: {score:.2f}")

        return "\n".join(lines)

    def update(self, name: str) -> str:
        """Re-index an existing entry from its source path."""
        row = self._conn.execute(
            "SELECT source_path, include_patterns, exclude_patterns FROM entries WHERE name=?", (name,)
        ).fetchone()
        if not row:
            return f"  Entry '{name}' not found."

        source_path, inc_json, exc_json = row
        include = json.loads(inc_json)
        exclude = json.loads(exc_json)

        self.remove(name)
        return self.add(name, source_path, include_patterns=include or None, exclude_patterns=exclude or None)

    def clear(self) -> str:
        """Remove all entries."""
        count = self._conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        self._conn.execute("DELETE FROM chunks")
        self._conn.execute("DELETE FROM entries")
        self._conn.commit()
        self._load_bm25_state()
        return f"  Cleared {count} entries from knowledge base."
