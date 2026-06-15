# eksreview

**eksreview** is an AI-powered conversational CLI agent that reviews your Amazon EKS clusters against operational best practices in minutes.

Ask for a review in natural language and it runs best-practice checks across six domains (security, resiliency, networking, Karpenter, Cluster Autoscaler, and observability), then produces a prioritized report with ready-to-run remediation for every finding. It also assesses upgrade readiness, returning a clear go or no-go recommendation with a detailed upgrade plan. From there you can investigate a finding with live read-only diagnostics, apply guided fixes step by step, and export results to a JIRA-ready CSV. Answers are grounded in a local knowledge base (the official EKS Best Practices Guide plus any docs you index), and everything runs locally and read-only by default.

📖 **Full documentation: https://aws-samples.github.io/sample-eksreview/**

---

## Quick start

You need Python 3.10+, [`uv`](https://docs.astral.sh/uv/getting-started/installation/), AWS credentials, and Amazon Bedrock model access. See the [Prerequisites](https://aws-samples.github.io/sample-eksreview/getting-started/prerequisites/) for details.

```bash
# 1. Clone and set up (creates a .venv and installs everything)
git clone https://github.com/aws-samples/sample-eksreview.git
cd sample-eksreview
./install.sh

# 2. Set AWS credentials and the region your cluster runs in
export AWS_ACCESS_KEY_ID=<your-access-key-id>
export AWS_SECRET_ACCESS_KEY=<your-secret-access-key>
export AWS_REGION=<your-region>

# 3. Launch the agent (auto-activates the virtual environment)
./eksreview
```

Then, at the prompt, ask for a review in natural language:

```text
review my cluster my-cluster in <your-region>
```

A prioritized report saves to `reports/` in a few minutes. From there, try `/investigate` to dig into a finding or `/fix` to remediate one step by step.

See the [Installation guide](https://aws-samples.github.io/sample-eksreview/getting-started/installation/) for manual setup, Bedrock API keys, and cross-account configuration.

---

## What you can do

- **Full cluster review** across security, resiliency, networking, Karpenter, Cluster Autoscaler, and observability, compiled into one prioritized report.
- **Upgrade readiness** assessment with a clear go or no-go recommendation and a detailed upgrade plan.
- **Guided remediation** (`/fix`): one fix at a time, with confirmation before anything runs.
- **Deep analysis** (`/investigate`): live read-only diagnostics beyond the static report.
- **JIRA export** (`/export`) of findings as an importable CSV.
- **Local knowledge base** the agent searches and cites, plus **custom skills** (`/skill`) you can add for your own playbooks.
- **Runtime model switching** (`/model`) and a full **read-only mode** (`--no-shell`).

---

## Documentation

| Topic | |
|---|---|
| [Getting started](https://aws-samples.github.io/sample-eksreview/getting-started/installation/) | Install, prerequisites, your first review |
| [Usage](https://aws-samples.github.io/sample-eksreview/usage/example-prompts/) | Example prompts, slash commands, reports, knowledge base |
| [Configuration](https://aws-samples.github.io/sample-eksreview/configuration/environment-variables/) | Environment variables, models & regions, credentials, CLI flags |
| [Reference](https://aws-samples.github.io/sample-eksreview/reference/what-gets-checked/) | What gets checked, permissions, cost, performance, safety, troubleshooting |
| [Architecture](https://aws-samples.github.io/sample-eksreview/architecture/) | How eksreview works: components, data flow, and safety |

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) (it includes the project layout and design docs).
