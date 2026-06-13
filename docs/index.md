<div class="eksr-hero" markdown>

![eksreview logo](assets/logo.png){ .eksr-logo }

# eksreview { .eksr-title }

An AI-powered conversational CLI agent that reviews your Amazon EKS clusters against operational best practices — in minutes.
{ .eksr-tagline }

[:material-rocket-launch: Get started](getting-started/installation.md){ .md-button .md-button--primary }
[:material-github: View on GitHub](https://github.com/aws-samples/sample-eksreview){ .md-button }

</div>

Using natural language, eksreview runs best-practice checks across six domains (security, resiliency, networking, Karpenter, Cluster Autoscaler, and observability), and also evaluates **upgrade readiness** with a clear go / no-go recommendation. Every review produces a prioritized report with copy-paste remediation. From there you can keep the conversation going: investigate a finding to understand how it affects your cluster, apply guided fixes step by step, and export results to a JIRA-ready CSV.

Its answers are grounded in a **local knowledge base** — the official EKS Best Practices Guide, plus any runbooks or docs you index — so guidance is cited rather than guessed. Behind the scenes, **skills** give the agent structured playbooks for reviewing, investigating, and compiling reports, keeping its output consistent run to run. Everything runs locally and read-only by default.

<div class="grid cards" markdown>

- :material-map-marker-path: **[Your first review](getting-started/first-review.md)**

    Walk through running a review and reading the report.

- :material-checkbox-marked-circle-outline: **[What gets checked](reference/what-gets-checked.md)**

    See the domains and best practices a review covers.

- :material-console: **[Usage](usage/conversational-reviews.md)**

    Talk to the agent, run reviews, fix findings, export results.

- :material-shield-key: **[Permissions](reference/permissions.md)**

    The read-only IAM policy and cluster access setup.

</div>

---

## Why eksreview

AWS publishes an excellent [EKS Best Practices Guide](https://docs.aws.amazon.com/eks/latest/best-practices/introduction.html) covering security, reliability, scalability, networking, and cost. The guidance is thorough, but applying it is the hard part. You have to read through hundreds of recommendations, work out which ones apply to your cluster, then dig through the EKS console, IAM, and `kubectl` to check whether your setup actually follows them. That takes deep Kubernetes expertise and hours of manual effort per cluster, and the results vary depending on who does the review.

eksreview does that work for you. You ask in plain language, and it inspects your live cluster against the best practices that matter, flags what is wrong with severity and impact, and gives you the exact commands to fix each issue. It also assesses upgrade readiness with a clear go or no-go verdict before you move to a new Kubernetes version. Run it again later and it tracks what changed since your last review.

It turns a slow, expertise-heavy review into something you can run in minutes, as often as you like.

---

## How it works

eksreview is a conversational agent built on the [Strands Agents SDK](https://github.com/strands-agents/sdk-python) and Anthropic Claude models on Amazon Bedrock. The actual cluster checks are executed by a bundled MCP (Model Context Protocol) server that talks to the EKS, EC2, IAM, and Kubernetes APIs.

When you ask for a review, the heavy lifting (running checks, compiling the report) happens inside an ephemeral **sub-agent**, so raw check data never floods the main conversation. Only a compact summary returns to your session, which keeps long sessions fast and cheap.

You do not need to install or run the MCP server yourself. It ships in the `mcp-server/` directory and is launched automatically via `uv`.

For the trust boundaries, the layered safety stack, and the two-tier sub-agent orchestration, see the [Architecture](architecture.md) overview.

---

## Features

- **Full cluster review:** best-practice checks across security, resiliency, networking, Karpenter, Cluster Autoscaler, and observability, compiled into one report.
- **Upgrade readiness:** checks covering control plane, addons, deprecated APIs, third-party components, and workload resilience, with a go or no-go verdict and an ordered upgrade plan.
- **Guided remediation (`/fix`):** walks through fixes one at a time, classifies each as patchable, AWS CLI, or manifest change, and confirms before running anything.
- **Root cause analysis (`/investigate`):** correlates findings, runs read-only live diagnostics, and assesses real risk beyond the static report.
- **Multi-cluster (conversational):** ask the agent to review several clusters in one session and it works through them one after another.
- **Trend analysis:** tracks compliance over time and flags regressions and resolved findings.
- **JIRA export (`/export`):** deterministic CSV ready for bulk import, with no LLM involved.
- **Local knowledge base:** index your own runbooks, design docs, and PDFs, and the agent searches and cites them. The EKS Best Practices Guide is auto-synced on startup.
- **Runtime model switching (`/model`):** switch between Claude Opus and Sonnet mid-session.
- **Read-only mode (`--no-shell`):** disable command execution entirely for audit-only use.

Ready to try it? Head to [Installation](getting-started/installation.md).
