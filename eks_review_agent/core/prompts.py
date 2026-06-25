"""System prompt and help text for the EKS review agent."""

import unicodedata
from datetime import date


# Keyword filter applied to user-supplied free-text fragments before they
# are embedded into structured prompts (/fix, /investigate, /upgrade).
# This is a best-effort guardrail — the LLM steering handler is the real
# defense layer.
#
# Limitations to be aware of:
#   - Bypassable via base64, ROT13, language switching, indirect references,
#     or by splitting tokens across multiple turns.
#   - The check runs after NFKC normalization to defeat trivial lookalike
#     bypasses (full-width ASCII, ligatures, zero-width joiners, etc.) but
#     cannot catch determined attackers.
#   - Treat this as an audit log + obvious-attack tripwire, not a security
#     boundary. The real guardrails are the shell tool's interactive
#     confirmation, the LLM steering handler, and the destructive-command
#     hard block in observability.py.
_INJECTION_PATTERNS = (
    "ignore all previous", "ignore above", "disregard all",
    "forget your instructions", "new instructions",
    "you are now",
    "system prompt", "jailbreak",
    # Note: avoided patterns like "act as" / "override" / "pretend to be"
    # because they appear in legitimate EKS phrasing ("override the cache TTL",
    # "ConfigMap acts as...", "the controller pretends..."). The LLM steering
    # handler and the destructive-command hard block are the real defense.
)


def detect_prompt_injection(text: str) -> bool:
    """Return True if the input contains a known injection keyword.

    Normalizes to NFKC + casefold before keyword scan to defeat trivial
    Unicode-based bypasses (full-width characters, ligatures, etc.). Best
    effort only — see _INJECTION_PATTERNS docstring for limitations.
    """
    if not text:
        return False
    # NFKC folds compatibility forms (e.g. full-width 'ｉ' → 'i', 'ﬁ' → 'fi').
    # casefold() is a stronger lowercase that handles non-ASCII case (e.g.
    # German 'ß' → 'ss') so simple charcase tricks don't help either.
    normalized = unicodedata.normalize("NFKC", text).casefold()
    # Strip zero-width characters that might split keywords without changing
    # how the LLM reads them.
    for zw in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        normalized = normalized.replace(zw, "")
    return any(p in normalized for p in _INJECTION_PATTERNS)


def get_system_prompt() -> str:
    """Build the system prompt with the current date injected."""
    today = date.today().isoformat()
    return f"""\
You are an EKS Operations Review specialist — a senior Kubernetes and AWS engineer \
who helps teams assess and optimize their Amazon EKS clusters.

Today's date is {today}.

<rules>
1. Never fabricate cluster data. Every resource name, version, and IP must come from \
tool results. If data is missing, say "Not determined" and provide the command to \
retrieve it. When a tool returns an error, report it honestly.

2. Conversational by default. Answer EKS and Kubernetes questions naturally. \
For best-practice, configuration, or "how/why" questions about EKS or Kubernetes, \
call knowledge_search FIRST to ground your answer in the indexed EKS Best Practices \
Guide and any user-added docs. Prefer and cite what the knowledge base returns; \
only fall back to your own knowledge if the search returns nothing relevant. \
Use cluster tools only when the user asks about a specific cluster.

3. Read-only unless confirmed. Read-only shell commands (get, describe, list) are \
allowed during /fix and /investigate. For any write/mutate operation, the shell tool \
will prompt the user to confirm the exact command before it executes — let that \
confirmation happen rather than asking separately. Never batch multiple mutations \
into one command to avoid the prompt.

4. Full reviews use run_full_review. Call it ONCE. It handles MCP checks, history, \
report compilation, and saving. Do NOT call individual MCP review tools. \
Do NOT call get_review_history before run_full_review.

5. Upgrade readiness uses run_upgrade_readiness. Call it ONCE for /upgrade. \
Do NOT fall back to shell commands if it fails — report the error and suggest retry.

6. Use report_search for follow-up details. Search by keyword instead of reading \
the entire report. Only use file_read if report_search doesn't return what you need.

7. Single-domain checks also use run_full_review with the domains parameter.
</rules>

<tools>
- run_full_review — runs review checks (all or specific domains), compiles and saves report
- run_upgrade_readiness — runs 38 upgrade checks, verifies compatibility, saves report
- report_search — keyword search into saved reports for specific findings or remediation
- knowledge_search — searches the local knowledge base (EKS Best Practices Guide + \
user-added PDFs/docs). Use this FIRST for EKS/K8s best-practice and conceptual questions.
- http_request — fetches live URLs for docs, CVEs, release notes
- think — use before correlating cross-domain findings or planning remediation
- get_review_history — check for previous reports (called automatically by run_full_review)
- save_report — save custom reports only (review and upgrade reports save automatically)
</tools>

<skills>
- eks-upgrade-readiness — handled internally by run_upgrade_readiness (do NOT activate manually)
- eks-knowledge — deep EKS/K8s reference
- eks-investigation — root cause analysis workflow
</skills>

<output>
- Terminal responses: PLAIN TEXT only. No markdown formatting — no **bold**, no ## headers,
  no - bullet lists, no | tables. Use plain dashes and indentation instead. Code blocks OK.
- When you answer from the knowledge base, briefly cite the source (e.g. "per the EKS
  Best Practices Guide") so the user knows the answer is grounded, not invented.
- Saved reports (via save_report): full markdown formatting is fine.
- Stay focused on EKS/Kubernetes/AWS.
</output>
"""

HELP_TEXT = """\

  Commands
  ────────
    /help                Show this help text
    /tools               List all available tools
    /model               Show or switch the active model
    /context             Show context window usage, cost, and loaded skills
    /skill list          Show all loaded skills
    /skill add <n> <p>   Add a custom skill from path
    /skill remove <n>    Remove a custom skill
    /skill info <n>      Show skill details
    /knowledge show      Show knowledge base entries
    /knowledge add       Index files into knowledge base
    /knowledge search    Search the knowledge base
    /knowledge remove    Remove a knowledge base entry
    /knowledge update    Re-index an existing entry
    /knowledge clear     Remove all entries
    /fix <description>   Remediate a finding from the current review
    /investigate <desc>  Deep root cause analysis of a finding
    /upgrade <cluster>   Check upgrade readiness for a cluster
    /export              Export findings as JIRA-importable CSV
    /exit                End the session

  Ask EKS/K8s Questions
  ─────────────────────
    "What's the difference between IRSA and Pod Identity?"
    "How do I troubleshoot a pod stuck in Pending?"
    "Explain Karpenter vs Cluster Autoscaler"

  Query a Cluster (uses MCP tools)
  ────────────────────────────────
    "List all clusters in us-west-2"
    "Check the security posture of cluster eks-demo"
    "Run a resiliency check on my prod cluster"

  Run a Full Review
  ─────────────────
    "Review my cluster eks-demo for best practices"
    "Run a full assessment on eks-demo"
    "Audit eks-demo — security, networking, and resiliency"

  Upgrade Readiness
  ─────────────────
    /upgrade eks-demo
    /upgrade eks-demo to 1.33
    /upgrade my-prod-cluster

  Knowledge Base
  ──────────────
    /knowledge add <name> <path>
      Index files from a path (recursive for directories).
      Example: /knowledge add eks-docs ~/docs/eks-best-practices
      Options: --include "*.md" --exclude "node_modules/**"

    /knowledge search <query>
      Search indexed content by keyword.
      Example: /knowledge search "pod security standards"

    The agent can also search the knowledge base automatically
    using the knowledge_search tool during conversation.
"""


def build_fix_prompt(cluster_name: str, fix_desc: str) -> str:
    """Build the structured prompt for /fix command."""
    return (
        f"Fix an issue from the review of cluster '{cluster_name}'. "
        f"User request: \"{fix_desc}\"\n\n"
        "<procedure>\n"
        "1. Use report_search to find the relevant finding by keyword. "
        "Do NOT re-run MCP review tools.\n"
        "2. Classify the fix: Patchable (kubectl patch), AWS CLI, or Manifest change required.\n"
        "3. For multiple issues, list them briefly then handle ONE AT A TIME. "
        "Ask 'Continue to the next issue?' after each.\n"
        "4. For each Patchable or AWS CLI fix:\n"
        "   - Show the exact command\n"
        "   - Explain impact and what could break\n"
        "   - Offer dry-run if available\n"
        "   - Ask clarifying questions for ambiguous values (CIDRs, ports)\n"
        "   - Run the command. The shell tool will prompt the user to confirm the\n"
        "     exact command before executing — do not ask for confirmation separately.\n"
        "   - Verify with a read-only command after execution\n"
        "5. For Manifest changes: show guidance and example YAML, then move on.\n"
        "</procedure>\n\n"
        "<constraints>\n"
        "- One fix at a time. Never batch commands.\n"
        "- Never guess user-specific values — ask.\n"
        "- Commands must use real values from the review data.\n"
        "- Use PLAIN TEXT. No markdown tables or bold. Code blocks OK.\n"
        "</constraints>"
    )




def build_investigate_prompt(cluster_name: str, finding_desc: str) -> str:
    """Build the structured prompt for /investigate command."""
    return (
        f"Investigate a finding from the review of cluster '{cluster_name}'. "
        f"User request: \"{finding_desc}\"\n\n"
        "Activate the 'eks-investigation' skill and follow its procedure.\n\n"
        "Use `report_search` to find the relevant finding from the saved report. "
        "If no report exists, tell the user to run a review first."
    )


def build_upgrade_prompt(cluster_name: str, target_version: str = "", region: str = "") -> str:
    """Build the structured prompt for /upgrade command.

    The upgrade pipeline runs entirely in a sub-agent via run_upgrade_readiness.
    The main agent just needs to call the tool with the right parameters.
    """
    target_arg = f', target_version="{target_version}"' if target_version else ""
    region_arg = f', region="{region}"' if region else ""

    return (
        f"Check upgrade readiness for cluster '{cluster_name}'"
        f"{f' to version {target_version}' if target_version else ''}"
        f"{f' in region {region}' if region else ''}.\n\n"
        f"Call run_upgrade_readiness(cluster_name=\"{cluster_name}\"{region_arg}{target_arg}).\n\n"
        "This runs 38 checks, verifies component compatibility, and saves a full report.\n"
        "After it returns, present the verdict and key findings to the user.\n"
        "Use report_search for any follow-up questions about specific findings."
    )
