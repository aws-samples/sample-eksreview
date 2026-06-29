# Installation

There are two ways to use eksreview:

1. **Full agent (recommended)** — a conversational CLI that reviews clusters, generates prioritized reports, and supports follow-up investigation and remediation.
2. **MCP Server only** — plug the review tools into your existing AI agent (Kiro, or any agent that supports MCP) as a local MCP server.

---

## Option 1: Full Agent

Clone, run the installer, set your AWS credentials, and you can start reviewing clusters.

### 60-second start

You need Python 3.10+, [`uv`](https://docs.astral.sh/uv/getting-started/installation/), AWS credentials, and Amazon Bedrock model access (see [Prerequisites](prerequisites.md)).

```bash
# 1. Clone and set up (creates a .venv and installs everything)
git clone https://github.com/aws-samples/sample-eksreview.git
cd sample-eksreview
./install.sh

# 2. Set AWS credentials and the region your cluster runs in.
#    Use `aws configure`, or export keys directly:
export AWS_ACCESS_KEY_ID=<your-access-key-id>
export AWS_SECRET_ACCESS_KEY=<your-secret-access-key>
export AWS_SESSION_TOKEN=<your-session-token>     # only for temporary credentials
export AWS_REGION=<your-region>                   # e.g. the region your cluster runs in

#    Optional: authenticate the Bedrock model with an API key instead of the
#    credentials above (short- or long-term keys both work):
export AWS_BEARER_TOKEN_BEDROCK=<your-bedrock-api-key>

# 3. Launch the agent (auto-activates the virtual environment)
./eksreview
```

!!! note
    By default the credentials above are used for both the cluster calls and the Bedrock model. A [Bedrock API key](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html) (`AWS_BEARER_TOKEN_BEDROCK`) authenticates only the model, and can belong to a different account than the cluster credentials.

Then, at the prompt, ask for a review in natural language:

```text
review my cluster my-cluster in <your-region>
```

A prioritized report saves to `reports/` in a few minutes. From there, try `/investigate` to dig into a finding or `/fix` to remediate one step by step.

### Manual Installation (alternative to install.sh)

If you'd rather not use `./install.sh`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python main.py
```

### Bedrock and EKS in different accounts

If your Bedrock model access lives in one account and your EKS clusters in another, you can point the cluster calls at one account and Bedrock at the other. The MCP sub-process that talks to your cluster only uses the `AWS_*` credentials, while Bedrock calls can use a separate credential source. See [Credentials](../configuration/credentials.md) for step-by-step setup, including using a Bedrock API key and assuming a role in a central account.

---

## Option 2: MCP Server Only (Use with Your Existing AI Agent)

If you already have an AI agent you prefer (Kiro, or any other agent that supports [MCP](https://modelcontextprotocol.io/)), you can add the EKS Review MCP Server as a tool. The MCP server provides the same best-practice checks that power the full agent, exposed as MCP tools your agent can call.

!!! info "What you get with the MCP Server"
    The MCP Server **performs cluster reviews** — it runs the same best-practice checks as the full agent across security, resiliency, networking, Karpenter, Cluster Autoscaler, and upgrade readiness. Your AI agent can call these tools, interpret the structured results, and help you act on findings.

    **What is not supported** with the MCP Server alone:

    - Automated report generation and formatting (your agent composes the output based on your prompt)
    - Review history and trend analysis across runs
    - Built-in `/investigate` and `/fix` slash commands
    - Knowledge base persistence

    For those capabilities, use the [full agent](#option-1-full-agent) instead.

### Setup

You need [`uv`](https://docs.astral.sh/uv/getting-started/installation/) and AWS credentials with [read-only EKS permissions](../reference/permissions.md).

**1. Clone the repository:**

```bash
git clone https://github.com/aws-samples/sample-eksreview.git
```

**2. Add the MCP server to your Kiro configuration:**

Create or edit `.kiro/settings/mcp.json` in your workspace (or `~/.kiro/settings/mcp.json` for global access):

```json
{
  "mcpServers": {
    "awslabs.eks-review-mcp-server": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/sample-eksreview/mcp-server/awslabs/eks_review_mcp_server",
        "run",
        "server.py"
      ],
      "env": {
        "FASTMCP_LOG_LEVEL": "ERROR"
      },
      "disabled": false
    }
  }
}
```

Replace `/absolute/path/to/sample-eksreview/mcp-server/awslabs/eks_review_mcp_server` with the absolute path to the `mcp-server/awslabs/eks_review_mcp_server/` directory in your clone.

Once saved, Kiro auto-detects the config change and connects to the MCP server. 

!!! note "Other MCP-compatible agents"
    This configuration works with any AI agent that supports the [Model Context Protocol](https://modelcontextprotocol.io/) (MCP). The `command` and `args` are the same — adapt the JSON to your agent's MCP configuration format.

### Available Tools

Once connected, your agent gains access to these tools:

| Tool | What it checks |
|------|----------------|
| `check_eks_security` | IAM, RBAC, pod security, encryption, secrets, runtime security |
| `check_eks_resiliency` | Replicas, probes, PDBs, HPA/VPA, control plane, data plane (28 checks) |
| `check_eks_networking` | Endpoint access, multi-AZ distribution, VPC/subnet/SG configuration |
| `check_karpenter_best_practices` | NodePool config, instance selection, spot optimization, disruption |
| `check_cluster_autoscaler_best_practices` | CA deployment, version compatibility, node groups, scaling |
| `check_eks_upgrade_readiness` | Deprecated APIs, addon compatibility, data plane, workload readiness (38 checks) |

All tools accept `cluster_name` (required) and optionally `region` or `namespace`.

### Sample Prompts

Use these prompts with your AI agent to review clusters using the MCP server tools.

#### Quick Review

A simple, natural-language prompt that lets your agent decide which tools to call:

```text
Review my EKS cluster "my-cluster" in us-east-1 for best practices.
Summarize the findings, highlight anything critical, and suggest what to fix first.
```

#### Cluster Review (Assessment Report)

```text
Run a comprehensive EKS best-practices review of my cluster "my-cluster" in us-east-1.
Call all five check tools (security, resiliency, networking, karpenter, cluster-autoscaler).

Then compile the results into a single assessment report with:
- An executive summary with pass/fail counts and overall compliance assessment
- A check results summary table (S.No, Check Name, Category, Severity, Status, Impacted Resources)
- Separate sections for Critical, High, Medium, and Low findings — each with impact,
  impacted resources, current state, remediation (commands/YAML), and considerations
- A "Quick Wins" table for items fixable in under 30 minutes
- A "Requires Planning" section for items needing a maintenance window

Save the report as a markdown file.
```

#### Upgrade Readiness

```text
Check if my cluster "my-cluster" in us-east-1 is ready to upgrade to Kubernetes 1.32.
Use the check_eks_upgrade_readiness tool.

Then compile the results into an upgrade readiness report with:
- A summary table (current version, target version, region, date, readiness verdict,
  upgrade path, checks run, passed, blockers, warnings)
- A Go/No-Go decision paragraph explaining any blockers
- A check results summary table (ID, Check, Category, Severity, Status, Timing, Impacted Resources)
- Detailed sections for each blocker and warning with remediation steps
- A recommended upgrade plan with ordered steps

Save the report as a markdown file.
```

!!! note "Prompt quality matters"
    The MCP server returns raw check data. The report quality depends entirely on how well you prompt your agent. The prompts above are tuned to produce output similar to the full agent's reports — adjust them to your team's format as needed.

---

## Updating

### Full Agent

eksreview is run from a clone, so update with `git pull` and re-run the installer to pick up any new updates:

```bash
cd sample-eksreview
git pull
./install.sh
```

### MCP Server

Pull the latest changes — Kiro will pick up the updated server automatically on next reconnect:

```bash
cd sample-eksreview
git pull
```

## Uninstalling

Remove the virtual environment and (optionally) the local data directories, then delete the clone:

```bash
cd sample-eksreview
rm -rf .venv                      # the installed environment
rm -rf reports/ .knowledge/ .sessions/   # optional: generated data (see Data & Cleanup)
cd .. && rm -rf sample-eksreview  # the clone itself
```

Nothing is installed outside the project directory, so removing the clone leaves no residue in the project. One exception: `uv` may keep a package cache under `~/.cache/uv` (shared with other uv projects; safe to leave or clear with `uv cache clean`).
