"""Report search tool — targeted search into saved review reports.

Instead of reading the entire 40-50K report into context, this tool
searches for relevant sections and returns only the matching findings.
"""

import logging
import re
from pathlib import Path

from strands import tool

from eks_review_agent.config import REPORTS_DIR

logger = logging.getLogger("eksreview")

_MAX_RESULT_CHARS = 8000

# LRU bound on the parsed-section cache. A long session against many
# clusters can grow this unbounded otherwise. 32 reports is plenty for
# typical investigations and bounds memory at a few MB.
_SECTION_CACHE_MAX = 32

# Cache: report_path → (mtime, parsed_sections). Insertion-ordered dict
# used as an LRU — least-recently-touched key is evicted when the cache
# grows past _SECTION_CACHE_MAX. Move-to-end on hit keeps hot entries.
_section_cache: dict[str, tuple[float, list[dict]]] = {}


def find_latest_report(cluster_name: str | None = None) -> Path | None:
    """Find the most recent report (assessment or upgrade), optionally filtered by cluster.

    Public so review_orchestrator and upgrade_orchestrator can reuse it.
    """
    if cluster_name:
        safe = cluster_name.replace("/", "-").replace(" ", "-").lower()
        patterns = [f"*{safe}*-assessment-*.md", f"*{safe}*-upgrade-readiness-*.md"]
    else:
        patterns = ["*-assessment-*.md", "*-upgrade-readiness-*.md"]

    all_reports = []
    for pattern in patterns:
        all_reports.extend(REPORTS_DIR.glob(pattern))

    all_reports.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return all_reports[0] if all_reports else None


# Section titles that are containers (parents) — not useful as search results
# because they contain all child findings and are very large.
_CONTAINER_SECTIONS = {
    "critical issues",
    "high priority issues",
    "medium priority issues",
    "low priority issues",
    "check results summary",
}


def _extract_sections(content: str) -> list[dict]:
    """Parse a report into leaf sections based on markdown headings.

    Each section captures the content under a heading up to the next
    heading of the same or higher level. Container sections (like
    "Critical Issues" which just wraps individual findings) are tagged
    so they can be deprioritized in search results.
    """
    sections = []
    heading_re = re.compile(r"^(#{2,4})\s+(.+?)$", re.MULTILINE)
    matches = list(heading_re.finditer(content))

    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        start = m.start()

        # Body extends to the next heading of same or higher level, or EOF
        end = len(content)
        for j in range(i + 1, len(matches)):
            next_level = len(matches[j].group(1))
            if next_level <= level:
                end = matches[j].start()
                break

        # For leaf sections (### findings), body is just their own content
        # For container sections (## Critical Issues), body includes children
        # We want the leaf body only — trim at the first child heading
        body_start = m.end()
        body_end = end
        if level < 4:  # L2 or L3 might have children
            for j in range(i + 1, len(matches)):
                if matches[j].start() < end:
                    child_level = len(matches[j].group(1))
                    if child_level > level:
                        # Trim body to just the content before the first child
                        body_end = matches[j].start()
                        break

        body = content[body_start:body_end].strip()
        is_container = title.lower().rstrip(":").strip() in _CONTAINER_SECTIONS

        sections.append({
            "level": level,
            "title": title,
            "body": body,
            "start": start,
            "is_container": is_container,
        })

    return sections


def _get_sections(report_path: Path) -> list[dict]:
    """Get parsed sections for a report, with caching.

    Caches by file path + mtime so re-searches on the same report
    don't re-read and re-parse the file. Bounded by an LRU eviction
    policy at _SECTION_CACHE_MAX entries.
    """
    path_str = str(report_path)
    mtime = report_path.stat().st_mtime

    cached = _section_cache.get(path_str)
    if cached and cached[0] == mtime:
        # LRU touch: move to the end so it's not the next eviction target.
        _section_cache[path_str] = _section_cache.pop(path_str)
        return cached[1]

    content = report_path.read_text(encoding="utf-8")
    sections = _extract_sections(content)
    _section_cache[path_str] = (mtime, sections)

    # Evict oldest entries past the cap.
    while len(_section_cache) > _SECTION_CACHE_MAX:
        _section_cache.pop(next(iter(_section_cache)))

    return sections


def _search_sections(sections: list[dict], query: str) -> list[dict]:
    """Search sections by keyword matching, prioritizing leaf findings.

    Scoring:
      - Title exact phrase match: 50 points
      - Title keyword match: 10 points per keyword
      - Body keyword match: 1 point per keyword occurrence (capped at 5)
      - Container sections: score halved (deprioritized)
    """
    keywords = [kw.lower().strip() for kw in query.split() if len(kw.strip()) > 2]
    if not keywords:
        keywords = [query.lower().strip()]

    query_lower = query.lower().strip()
    scored = []

    for section in sections:
        title_lower = section["title"].lower()
        body_lower = section["body"].lower()
        score = 0

        # Exact phrase match in title — strongest signal
        if query_lower in title_lower:
            score += 50

        # Keyword matches in title
        for kw in keywords:
            if kw in title_lower:
                score += 10

        # Keyword matches in body — capped to avoid large sections dominating
        for kw in keywords:
            count = body_lower.count(kw)
            score += min(count, 5)

        # Deprioritize container sections
        if section["is_container"] and score > 0:
            score = score // 2

        if score > 0:
            scored.append((score, section))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Deduplicate: if a parent and its child both match, drop the parent
    seen_titles = set()
    deduped = []
    for score, section in scored:
        # Skip containers if we already have a more specific child match
        if section["is_container"] and any(
            not s["is_container"] for _, s in scored[:5]
        ):
            continue
        if section["title"] not in seen_titles:
            seen_titles.add(section["title"])
            deduped.append(section)

    return deduped


@tool
def report_search(query: str, cluster_name: str = "", report_path: str = "") -> str:
    """Search the saved review report for specific findings, checks, or remediation steps.

    This tool searches reports in the ./reports/ directory by keyword instead of
    loading the entire file. Use it when you need details about a specific finding,
    check, or remediation from a previous review or upgrade assessment.

    Examples:
      - report_search("endpoint private") → finds the endpoint access finding
      - report_search("liveness probes") → finds the probes check details
      - report_search("critical issues") → finds all critical severity findings
      - report_search("remediation karpenter") → finds Karpenter fix commands
      - report_search("executive summary") → finds the summary section

    Args:
        query: Search keywords (e.g., "endpoint private", "pod security",
               "critical", "remediation autoscaler").
        cluster_name: Optional cluster name to find the right report.
                      If empty, searches the most recent report.
        report_path: Optional explicit path to a report file.
                     Takes precedence over cluster_name.

    Returns:
        Matching report sections with findings, remediation, and context.
        Returns up to 3 most relevant sections.
    """
    # Resolve the report file — priority: explicit path > cluster name > latest
    report_file = None
    if report_path:
        p = Path(report_path)
        if p.exists():
            report_file = p
        else:
            return f"Report file not found: {report_path}"
    elif cluster_name:
        report_file = find_latest_report(cluster_name)
    else:
        report_file = find_latest_report()

    if not report_file:
        target = f"cluster '{cluster_name}'" if cluster_name else "any cluster"
        return f"No report found for {target}. Run a review or upgrade check first."

    try:
        sections = _get_sections(report_file)
    except Exception as e:
        return f"Could not read report: {report_file.name} ({e})"

    if not sections:
        return f"Could not parse report: {report_file.name}"

    matches = _search_sections(sections, query)

    if not matches:
        return (
            f"No matches for '{query}' in {report_file.name}. "
            f"Try broader keywords like 'security', 'critical', 'remediation', "
            f"or a specific check name."
        )

    # Return top 3 matches, respecting the character budget
    results = []
    total_chars = 0
    for section in matches[:3]:
        header = "#" * section["level"] + " " + section["title"]
        body = section["body"]

        # Truncate very long sections
        if len(body) > 3000:
            body = body[:3000] + "\n\n[... truncated — use file_read for full section]"

        entry = f"{header}\n{body}"
        if total_chars + len(entry) > _MAX_RESULT_CHARS:
            break
        results.append(entry)
        total_chars += len(entry)

    source_line = f"Source: {report_file.name}"
    return f"{source_line}\n\n" + "\n\n---\n\n".join(results)
