# eksreview Architecture

This document is the single source of truth for how the eksreview agent
is structured and how it stays safe. It's aimed at contributors,
reviewers, and security auditors.

When the architecture or safety model changes, update this doc in the
same PR. Code is still the canonical truth — but if the doc and code
disagree, that's a bug worth raising.

## Table of contents

1. [System overview](#system-overview)
2. [Trust boundaries](#trust-boundaries)
3. [Two-tier orchestration](#two-tier-orchestration)
4. [The safety stack](#the-safety-stack)
5. [Environment variable policy](#environment-variable-policy)
6. [Read-only mode (`--no-shell`)](#read-only-mode---no-shell)
7. [Rate limiting](#rate-limiting)
8. [Session and state management](#session-and-state-management)
9. [Knowledge base](#knowledge-base)
10. [Reports and history](#reports-and-history)
11. [Known limitations / accepted residual risk](#known-limitations--accepted-residual-risk)
12. [What this document isn't](#what-this-document-isnt)

---

## System overview

eksreview is a conversational CLI that runs best-practice reviews
against Amazon EKS clusters. It is built on:

- **Strands Agents SDK** for the LLM agent loop, tools, hooks, and
  session persistence.
- **AWS Bedrock** for the model — Claude Opus or Sonnet, selectable
  at runtime.
- **MCP (Model Context Protocol)** for cluster review checks. The MCP
  server is bundled in `mcp-server/` and runs as a stdio subprocess.
- **Local SQLite + BM25** for the knowledge base.

```
┌──────────┐    prompt   ┌──────────┐  Bedrock   ┌──────────┐
│   User   │ ──────────► │  Agent   │ ─────────► │ Bedrock  │
└──────────┘             └─────┬────┘            └──────────┘
                               │
                          stdio │       AWS APIs (EKS, EC2, STS)
                               ▼
                         ┌──────────┐         ┌──────────┐
                         │   MCP    │ ──────► │   AWS    │
                         │subprocess│         │  APIs    │
                         └────┬─────┘         └──────────┘
                              │
                              ▼
                         ┌──────────┐
                         │   K8s    │  (STS-presigned tokens, 14m TTL)
                         │   API    │
                         └──────────┘
```

## Trust boundaries

The system has six trust boundaries. Each one has an explicit
treatment in code.

| # | Boundary | Treatment |
|---|----------|-----------|
| 1 | User ↔ Agent (CLI input) | Slash-command parser is anchored on a fixed allowlist. Free-text prompts go through the LLM, which is itself layered (see safety stack). |
| 2 | Agent ↔ Bedrock | Trusted. AWS-managed, encrypted in transit. Auth via boto3's credential chain. |
| 3 | Agent ↔ MCP subprocess | Trusted but isolated: subprocess receives a filtered env (no Bedrock secrets, no third-party tokens). Communication is stdio in the same process tree. |
| 4 | MCP ↔ AWS APIs (EKS / EC2 / STS) | IAM-authenticated. The MCP code never modifies cluster state — every check is read-only. |
| 5 | MCP ↔ K8s API | STS-presigned URL tokens with 14-minute TTL. Cluster CA temp file is 0o600 in a dedicated dir, with `atexit` + startup-sweep cleanup. |
| 6 | Agent ↔ external URLs (`http_request`, EKS BP PDF, Pluto deprecation YAML) | Untrusted. Fetched data is validated structurally before use. Bundled fallback exists for the deprecation DB. |

## Two-tier orchestration

Reviews and upgrade-readiness checks both follow the same pattern:

1. **Pure-Python data collection.** `mcp_checks.py` calls each MCP
   review tool directly — no LLM involved. Same for the upgrade
   handler. The agent's main context never sees raw MCP JSON.
2. **Ephemeral sub-agent for synthesis.** A short-lived Strands
   `Agent` (`subagent_pipeline.run_subagent_pipeline`) compiles the
   raw results into a markdown report. Its `NullConversationManager`
   means it doesn't accumulate history; its tool list is
   intentionally small (`save_report`, `knowledge_search`, plus
   `shell` + `http_request` for the upgrade pipeline).
3. **Summary extraction.** The orchestrator regex-parses the saved
   report and returns just the executive summary (~200 tokens) to
   the main agent. Detailed findings stay on disk; the agent
   retrieves them on demand via `report_search`.

This split is the central performance and safety design choice:

- The main agent's context stays small. Reviews don't blow the
  conversation window.
- Each pipeline runs in its own model call with a tight prompt and
  tool surface, so the sub-agent's behavior is easier to reason
  about.
- Adding a new pipeline (e.g. cost analysis, security deep-dive) is
  a `SubAgentPipelineConfig` and a few prompt strings — no new
  orchestration boilerplate.

## The safety stack

There are four independent layers gating destructive actions. Each is
designed to catch a different class of failure:

```
                       User input
                            │
                            ▼
                ┌───────────────────────┐
                │ 1. Injection tripwire │  unicode-normalized keyword
                │   (prompts.py)        │  check on /fix /investigate /upgrade
                └──────────┬────────────┘
                           ▼
                ┌───────────────────────┐
                │ 2. LLM steering       │  Haiku gates the shell tool;
                │   (plugins.py)        │  bypasses _ALWAYS_SAFE_TOOLS
                └──────────┬────────────┘
                           ▼
                ┌───────────────────────┐
                │ 3. Shell tool consent │  interactive [y/*] prompt showing
                │   (strands_tools)     │  the verbatim command before exec
                └──────────┬────────────┘
                           ▼
                ┌───────────────────────┐
                │ 4. Shell-cmd blocklist│  token-based match on the literal
                │   (observability.py)  │  shell command — final hard gate
                └──────────┬────────────┘
                           ▼
                       subprocess
```

`shell` is the only tool that can execute commands against the cluster
or AWS account, so the gates concentrate on it. (`file_write` writes
local files only; every other tool is read-only or internal.)

### Layer 1 — Injection tripwire

`prompts.detect_prompt_injection` runs NFKC-normalized + casefolded
substring matching against a small list of known attack phrases
("ignore all previous", "system prompt", etc.). Strips zero-width
characters before matching. Applied to user-supplied free text in
`/fix`, `/investigate`, `/upgrade`.

This is **best-effort**, not a security boundary. Trivially bypassable
by encoding tricks or paraphrasing. Its job is to log and reject
obvious attacks before they enter the system.

### Layer 2 — LLM steering handler

`plugins.create_steering_handler` configures Strands' built-in
`LLMSteeringHandler` with Claude Haiku as the gate model. Before each
tool call, Haiku judges whether to PROCEED, GUIDE, or BLOCK based on
a fixed system prompt that lists safe tools, allowed read-only command
patterns, and unconditionally blocked operations.

The plugin bypasses Haiku entirely for tools in `_ALWAYS_SAFE_TOOLS`
(read-only tools: `think`, `report_search`, `knowledge_search`, the
MCP check tools, etc.) — saves ~6 seconds per call. `shell` does not
have a bypass and always goes through Haiku.

This is the only layer that uses an LLM as part of the safety
decision. It catches commands the static rules miss but is bound by
the gate model's quality. Modifying commands are allowed to PROCEED
here — the interactive confirmation (Layer 3) and the hard blocklist
(Layer 4) are what actually gate execution.

### Layer 3 — Shell tool consent prompt

The `shell` tool (from `strands_tools`) prompts the user interactively
before it executes any command: it prints the **verbatim command** and
asks `Do you want to proceed with execution? [y/*]`. Anything other
than `y` cancels the command. This is the primary interactive gate the
user sees — the agent is instructed (in the system prompt and skills)
not to add a second confirmation of its own.

Because the confirmation operates on the **literal command** (not the
agent's description of it), the user always approves exactly what will
run. This replaced the earlier `confirm_action` tool, which prompted on
the agent's natural-language description and could be bypassed by
paraphrasing.

Caveat: the upstream tool suppresses this prompt if `BYPASS_TOOL_CONSENT=true`
or `STRANDS_NON_INTERACTIVE=true` is set in the environment. eksreview
does not set either; they are documented escape hatches for trusted
automation and should not be set in normal interactive use. An
always-on confirmation that ignores these env vars is on the roadmap
(see residual risk #2).

### Layer 4 — Shell-command blocklist

`observability.on_before_tool` intercepts `shell` invocations and
inspects the literal command string. The matcher (`_match_destructive`)
tokenizes the command on whitespace and shell separators (`|`, `&`,
`;`, etc.), then looks for any pattern from `_DESTRUCTIVE_SHELL_PATTERNS`
appearing as a contiguous token sequence.

When matched, the hook sets `event.cancel_tool` (Strands' framework
API for refusing a tool call from a hook). The LLM sees a structured
tool error and explains the refusal to the user. This runs regardless
of the Layer 3 consent prompt — a destructive command is blocked even
if the user (or an env-var escape hatch) would have approved it.

Patterns covered: AWS EKS / EC2 deletes, eksctl deletes, kubectl
delete + drain, helm uninstall, `rm -rf /`, SQL `drop`/`truncate`.

Token-sequence matching avoids false positives like
`kubectl get pod my-deletion-controller`.

This is the **real hard gate** at the execution boundary. It cannot be
bypassed by polite description or by disabling the consent prompt,
because it sees the actual command.

## Environment variable policy

The MCP subprocess receives a *filtered* environment, not the parent
shell's full env. See `mcp.py::_filter_env_for_mcp` for the exact
implementation.

**Allowed:**

- All `AWS_*` vars (covers boto3's full credential chain: static keys,
  profiles, SSO, IRSA, container creds, `AWS_CA_BUNDLE`, etc.).
- A small explicit list: `HOME`, `PATH`, `USER`, `SHELL`, `TZ`,
  locale vars, proxy vars (`HTTPS_PROXY`, `NO_PROXY`, etc.), TLS
  bundle vars (`REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`, etc.), and
  Python/`uv` runtime vars.

**Stripped:**

- `BEDROCK_AWS_*` — the agent uses these to authenticate to Bedrock.
  They have no purpose in the MCP subprocess and stripping them
  limits credential exposure.
- Agent-only config (`MODEL_ID`, `LOG_LEVEL`, `EKS_MCP_SERVER_DIR`,
  knowledge / conversation tunables).
- Everything else (third-party API tokens, custom env, etc.).

This filter is the C2 fix from the original security review. The
subprocess can authenticate to AWS (for EKS / EC2 / STS / K8s API
calls) but cannot exfiltrate Bedrock credentials or other unrelated
secrets.

## Read-only mode (`--no-shell`)

Pass `--no-shell` (or set `EKS_REVIEW_NO_SHELL=1`) to disable the
`shell` tool process-wide.

**What still works:** all read paths — cluster reviews, upgrade
readiness, `/investigate`, `/export`, knowledge base, free-text Q&A,
report search.

**What changes:** `/fix` switches from "execute the kubectl/aws CLI
command" to "show the command and let the user run it". The agent
can still produce remediation guidance; it just can't apply it.

The flag is a **hard process-wide guarantee**: it filters `shell`
out of both the main agent's tool list (`agent.py`) and the upgrade
sub-agent's tool list (`upgrade_orchestrator.py`). The review
sub-agent never had `shell` to begin with.

Use this mode for read-only audits, CI pipelines, regulated
environments, or any cluster the user doesn't own.

## Rate limiting

`rate_limiter.py` enforces three thresholds on MCP tool calls per
process:

| Limit | Default | Behavior |
|-------|---------|----------|
| Soft | 200 calls | Logs a one-shot warning |
| Hard | 500 calls | Refuses further MCP calls; tells user to start a new session |
| Burst | 60 calls in 60s (sliding window) | Refuses; for runaway-loop detection |

Limits are applied at three call sites:

1. `mcp_checks.collect_check_results` — review pipeline calls.
2. `upgrade_orchestrator._call_upgrade_mcp` — upgrade pipeline calls.
3. `observability.on_before_tool` — direct agent-side MCP tool calls
   (any tool name starting with `check_eks_`, `check_karpenter_`, or
   `check_cluster_autoscaler_`). Refusals go through Strands'
   `event.cancel_tool` API rather than raising, so the LLM sees a
   structured tool error and can explain to the user.

Defaults are loose enough that normal interactive sessions never trip
them. The thresholds exist as tripwires for misbehavior, not as a
quota.

Override via `MCP_RATE_LIMIT_SOFT` / `_HARD` / `_BURST` /
`_BURST_WINDOW`.

## Session and state management

The agent's per-process state is split across three layers:

1. **Strands `Agent.state`** — per-conversation state (e.g.
   `last_reviewed_cluster`, `tool_metrics`). Survives across REPL
   turns. Backed by `FileSessionManager` so `/exit` and resume work.
2. **`session.Session` singleton** — process-global state that
   doesn't belong to any one conversation: active model name,
   accumulated sub-agent token usage. Each field has its own lock.
   `reset_session()` rotates the singleton (used in tests).
3. **Module-level globals** — kept where the lifecycle is
   intrinsically module-scoped: `report_search._section_cache`
   (LRU-bounded), `callbacks` per-turn streaming state. These are
   self-contained and lock-protected where mutation matters.

`get_session()` is the path forward — when state needs to move out of
module globals, it goes here. Existing helpers (`get_current_model_name`,
`get_subagent_usage`) delegate to the singleton.

## Knowledge base

`knowledge_base.py` is a local SQLite-backed BM25 index for indexed
content (PDFs, markdown, code, configs).

- Storage: `.knowledge/knowledge.db` plus a state file for the
  EKS Best Practices PDF auto-sync (etag/last-modified).
- Chunking: sentence-aware (splits on `.!?`, paragraph breaks,
  markdown headers). Chunks are stored directly in SQLite — no
  re-reading source files at search time.
- Tokenization: lowercase `\w+`. Version strings split into parts
  (`v1.16.0` → `["v1", "16", "0"]`); accepted tradeoff for BM25
  use.
- Search: BM25 with an in-memory inverted index for O(k) lookup.
- Sensitive paths: blocked from indexing — system paths
  (`/etc`, `/var`, etc.) and user credential dirs (`~/.aws`,
  `~/.ssh`, `~/.kube`, `~/.gnupg`, `~/.config/gcloud`, `~/.azure`,
  `~/.docker`, `~/.netrc`). Both literal and symlink-resolved
  forms are checked so `/etc` → `/private/etc` on macOS still gets
  blocked.

The `EKS_REVIEW_OFFLINE=1` env var skips the startup PDF sync
entirely — for air-gapped or restricted networks.

## Reports and history

Reports are saved as markdown under `reports/` with the filename
shape `<cluster>-<assessment|upgrade-readiness>-YYYYMMDD_HHMMSS.md`,
optionally accompanied by a `.meta.json` sidecar holding structured
domain counts and failed-check names.

`get_review_history` parses the sidecar (preferred) or falls back to
regex on the markdown to compute compliance trends, resolved /
persistent / new failures across the most recent 5 reports per
cluster.

`report_search` uses an LRU-bounded section parser (cap 32 entries)
to support keyword search into saved reports without re-reading them
on every query.

`export.py` parses saved reports deterministically (no LLM) into JIRA
CSV. Auto-detects assessment vs upgrade-readiness based on the
report's first 500 chars and uses different column shapes for each.

The `reports/`, `.sessions/`, and `.knowledge/` directories are
created with mode `0o700` on POSIX (the L2 fix from the security
review). Sensitive cluster data stays owner-readable only.

## Known limitations / accepted residual risk

The following are documented residual risks the project accepts:

1. **Layer 1 (injection tripwire) is bypassable.** Unicode
   homoglyphs, base64, multilingual rephrasing, splitting tokens
   across turns. Real defense is layers 2–4.
2. **Layer 3 (shell consent) can be disabled by env vars.** Setting
   `BYPASS_TOOL_CONSENT=true` or `STRANDS_NON_INTERACTIVE=true`
   suppresses the upstream shell tool's confirmation prompt. eksreview
   does not set them, but a user could. An always-on confirmation
   enforced in `observability.on_before_tool` (independent of these
   env vars) is on the roadmap. Layer 4 still hard-blocks destructive
   commands regardless.
3. **Layer 4 tokenizer is quote-unaware.** `echo 'rm -rf /'` would
   match the destructive pattern even though the actual exec is
   harmless. False positive rather than false negative.
4. **Pluto deprecation DB is fetched live from `master`.** Schema
   validation, size cap, and shrink detection are in place; we accept
   the residual risk of trusting FairwindsOps' upstream branch
   without pinning. Pinning would freeze the deprecation list, which
   would make the upgrade-readiness check go stale faster than it
   stays accurate.
5. **`shell` runs in the host process.** No sandbox (firejail /
   bwrap). `--no-shell` is the recommended mitigation for high-risk
   environments.
6. **The Strands framework is evolving.** Hook semantics, tool
   discovery, and session APIs may shift. Our tests pin the contract
   we depend on; CI catches drift on dependency upgrades.
7. **Two-venv install pattern.** Agent has its own `.venv` (managed
   via `pip install -e .`); the bundled MCP server uses `uv` to
   manage its own. Documented in the README.

## What this document isn't

- **Not a substitute for code review.** Code is the source of truth.
- **Not a complete API reference.** Public modules have docstrings;
  read those.
- **Not auto-generated.** Update it in the same PR that changes the
  architecture or safety model.
- **Not a security certification.** It documents the model so
  reviewers can evaluate it; passing the doc doesn't mean passing
  an audit.
