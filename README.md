# eksreview

**eksreview** is an AI-powered conversational CLI agent that reviews your Amazon EKS clusters against operational best practices in minutes.

Using natural language, it runs best-practice checks across six domains (security, resiliency, networking, Karpenter, Cluster Autoscaler, and observability) and evaluates **upgrade readiness** with a clear go / no-go recommendation. Every review produces a prioritized report with copy-paste remediation. From there you can investigate a finding, apply guided fixes step by step, and export results to a JIRA-ready CSV. Answers are grounded in a local knowledge base (the official EKS Best Practices Guide plus any docs you index), and everything runs locally and read-only by default.

📖 **Full documentation: https://aws-samples.github.io/eksreview/**

---

## Quick start

You need Python 3.10+, [`uv`](https://docs.astral.sh/uv/getting-started/installation/), AWS credentials, and Amazon Bedrock model access. See the [Prerequisites](https://aws-samples.github.io/eksreview/getting-started/prerequisites/) for details.

```bash
# 1. Clone and set up (creates a .venv and installs everything)
git clone https://github.com/aws-samples/eksreview.git
cd eksreview
./install.sh

# 2. Set AWS credentials and the region your cluster runs in
export AWS_ACCESS_KEY_ID=<your-access-key-id>
export AWS_SECRET_ACCESS_KEY=<your-secret-access-key>
export AWS_REGION=<your-region>

# 3. Launch the agent (auto-activates the virtual environment)
./eksreview
```

Then, at the prompt, ask for a review in plain English:

```text
review my cluster my-cluster in <your-region>
```

A prioritized report saves to `reports/` in a few minutes. From there, try `/investigate` to dig into a finding or `/fix` to remediate one step by step.

See the [Installation guide](https://aws-samples.github.io/eksreview/getting-started/installation/) for manual setup, Bedrock API keys, and cross-account configuration.

---

## What you can do

- **Full cluster review** across security, resiliency, networking, Karpenter, Cluster Autoscaler, and observability — compiled into one prioritized report.
- **Upgrade readiness** assessment with a go / no-go verdict and an ordered upgrade plan.
- **Guided remediation** (`/fix`) — one fix at a time, with confirmation before anything runs.
- **Root cause analysis** (`/investigate`) — live read-only diagnostics beyond the static report.
- **Trend analysis** across previous reviews, and **JIRA export** (`/export`) of findings.
- **Local knowledge base** the agent searches and cites, plus runtime model switching (`/model`).

---

## Documentation

| Topic | |
|---|---|
| [Getting started](https://aws-samples.github.io/eksreview/getting-started/installation/) | Install, prerequisites, your first review |
| [Usage](https://aws-samples.github.io/eksreview/usage/conversational-reviews/) | Conversational reviews, slash commands, example prompts, reports, knowledge base |
| [Configuration](https://aws-samples.github.io/eksreview/configuration/environment-variables/) | Environment variables, models & regions, credentials, CLI flags |
| [Reference](https://aws-samples.github.io/eksreview/reference/what-gets-checked/) | What gets checked, permissions, cost, performance, safety, troubleshooting |
| [Architecture](https://aws-samples.github.io/eksreview/architecture/) | Design, trust boundaries, and decision records |

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) (it includes the project layout and design docs).

---
