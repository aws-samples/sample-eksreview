# How eksreview Works

This page gives a high-level picture of what eksreview is made of and
how a review flows through it. You don't need any of this to use the
tool, but it helps to know what's running on your machine and how your
cluster and account are accessed.

## Overview

eksreview runs entirely on your machine as a conversational CLI. When
you ask it to review a cluster, three things work together:

- **The agent** is the conversational brain. It reads your prompt,
  decides what to do, and talks to you in natural language. It connects to the
  model on Amazon Bedrock (Claude, by default a global
  cross-region inference profile).
- **The review checks** run as a bundled service (the agent's skills and an MCP server) that talks to
  the AWS APIs (EKS, EC2) and your cluster's Kubernetes API. Every
  check is read-only: it inspects configuration and reports findings,
  and never changes your cluster.
- **The knowledge base** is a local search index built from the EKS
  Best Practices guide and anything else you add. The agent searches
  it to ground its findings in documented guidance.

```
   You  ──prompt──►  Agent  ──►  Amazon Bedrock (the model)
                       │
                       ▼
                  Review checks (MCP)  ──►  AWS APIs (EKS, EC2)
                       │
                       ▼
                  Your cluster's Kubernetes API  (read-only)
```

Results are written to local markdown reports, and the agent keeps a
short summary in the conversation so you can ask follow-up questions
without re-running anything.

## What runs where

Everything stays local to where you run the CLI. eksreview does not
host a service, open a network port, or send your cluster data
anywhere except to Amazon Bedrock for the model to reason over it.
Your AWS credentials are used the same way the AWS CLI uses them.

The review checks run in a separate local process from the agent (the bundled MCP server). That
separation is deliberate: the checks get only the AWS credentials they
need to read your cluster, and never see the credentials used to call
Bedrock. This matters when your model and your cluster live in
different accounts (see [Credentials](configuration/credentials.md)).

## Performance

A full review can produce a lot of data. eksreview keeps the
conversation responsive by collecting all the raw check results first,
then having a short-lived helper compile them into a report.
Only a brief executive summary comes back into the chat. When you ask
about a specific finding, the agent searches the saved report for the
detail instead of holding the entire thing in memory.

## Safety

eksreview is built to review, not to change things. A few layers keep
it that way:

- **Read-only by design.** Every best-practice check only reads
  configuration. Nothing in the review path can modify your cluster or
  account.
- **You approve every command.** The only way eksreview can run a
  command (for example, when you ask `/fix` to apply a remediation) is
  through its shell tool, and it always shows you the exact command and
  waits for your confirmation before running it.
- **Destructive commands are blocked.** Even if a command is approved,
  a hard block refuses known-destructive operations (cluster/node
  deletes, `kubectl delete`, `helm uninstall`, `rm -rf /`, and
  similar). This runs on the literal command, so it can't be talked
  around.
- **A full read-only mode.** Start with `--no-shell` (or set
  `EKS_REVIEW_NO_SHELL=1`) and the shell tool is removed entirely.
  Reviews, upgrade-readiness, investigations, and Q&A all still work;
  `/fix` switches to showing you the command to run yourself. Use this
  for audits, CI, regulated environments, or any cluster you don't own.

For a deeper look at the safety model and the trade-offs the project
accepts, see the [Safety Model](reference/safety.md) page.

## Your data

eksreview stores everything under the directory you run it from:

- `reports/` holds the markdown reports from each review, plus a small
  metadata file used for trend comparisons.
- `.knowledge/` holds the local search index.
- `.sessions/` holds your conversation history, so you can resume.

On macOS and Linux these directories are created owner-readable only,
because reports can contain cluster security posture and IAM details.
You can change where they live with `REPORTS_DIR`, `KNOWLEDGE_DIR`, and
`SESSIONS_DIR`. See [Local Data](reference/data-and-cleanup.md) for
how to clear them.

## Built on

- [Strands Agents SDK](https://github.com/strands-agents/sdk-python)
  for the agent loop and session handling.
- [Amazon Bedrock](https://aws.amazon.com/bedrock/) for the model.
- [Model Context Protocol](https://modelcontextprotocol.io/) for the
  cluster review checks.
