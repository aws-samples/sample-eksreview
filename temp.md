# eksreview

**eksreview** is an AI-powered conversational CLI agent that reviews your Amazon EKS clusters against operational best practices in minutes.

Using natural language, eksreview runs best-practice checks across six domains (security, resiliency, networking, Karpenter, Cluster Autoscaler, and observability), and also evaluates **upgrade readiness** with a go / no-go recommendation. Every review produces a prioritized report with copy-paste remediation. From there you can keep the conversation going: investigate a finding to understand how it affects your cluster, apply guided fixes step by step, and export results to a JIRA-ready CSV.

Its answers are grounded in a **local knowledge base** the official EKS Best Practices Guide, plus any runbooks or docs you index so guidance is cited rather than guessed. Behind the scenes, **skills** give the agent structured playbooks for reviewing, investigating, and compiling reports, keeping its output consistent run to run. Everything runs locally and read-only by default.

---

## 60-second start

Clone, install, and start your first review. You need Python 3.10+, [`uv`](https://docs.astral.sh/uv/getting-started/installation/), AWS credentials, and Amazon Bedrock model access (see [Prerequisites](#prerequisites)).

```bash
# 1. Clone and set up (creates a .venv and installs everything)
git clone https://github.com/aws-samples/eksreview.git
cd eksreview
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

> By default the credentials above are used for both the cluster calls and the Bedrock model. A [Bedrock API key](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html) (`AWS_BEARER_TOKEN_BEDROCK`) authenticates only the model, and can belong to a different account than the cluster credentials. See [Using a Bedrock API key](#using-a-bedrock-api-key).

Then, at the `›` prompt, ask for a review in plain English:

```text
review my cluster my-cluster in <your-region>
```

A prioritized report saves to `reports/` in a few minutes. From there, try `/investigate` to dig into a finding or `/fix` to remediate one step by step.

### Bedrock and EKS in different accounts

If your Bedrock model access lives in one account and your EKS clusters in another, point the cluster calls at one account and Bedrock at the other:

```bash
# Cluster account → EKS / EC2 / IAM / Kubernetes calls
export AWS_ACCESS_KEY_ID=<cluster-account-access-key-id>
export AWS_SECRET_ACCESS_KEY=<cluster-account-secret-access-key>
export AWS_REGION=<cluster-region>                # e.g. the region your cluster runs in

# Central Bedrock account → model calls only
export BEDROCK_AWS_ACCESS_KEY_ID=<bedrock-account-access-key-id>
export BEDROCK_AWS_SECRET_ACCESS_KEY=<bedrock-account-secret-access-key>
export BEDROCK_AWS_SESSION_TOKEN=<bedrock-account-session-token>   # if temporary
export BEDROCK_AWS_REGION=<bedrock-region>        # where Bedrock model access is enabled

./eksreview
```

The MCP sub-process that talks to your cluster only uses the `AWS_*` credentials. If your Bedrock access is from a different account, use `aws sts assume-role` to assume the role and export the resulting temporary credentials into the `BEDROCK_AWS_*` variables. see [Cross-account: Bedrock in one account, EKS in another](#cross-account-bedrock-in-one-account-eks-in-another) for step-by-step examples.

---

## Table of Contents


- [60-second start](#60-second-start)
  - [Bedrock and EKS in different accounts](#bedrock-and-eks-in-different-accounts)
- [Why eksreview](#why-eksreview)
- [How it works](#how-it-works)
- [Features](#features)
- [What gets checked](#what-gets-checked)
- [Cost](#cost)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Usage](#usage)
  - [Conversational reviews](#conversational-reviews)
  - [Slash commands](#slash-commands)
  - [Example prompts by capability](#example-prompts-by-capability)
  - [Example session](#example-session)
- [Reports](#reports)
- [Knowledge base](#knowledge-base)
- [Permissions](#permissions)
- [Configuration](#configuration)
- [Command-line flags](#command-line-flags)
- [Performance and scale](#performance-and-scale)
- [Data and cleanup](#data-and-cleanup)
- [Safety model](#safety-model)
- [Troubleshooting](#troubleshooting)
- [Architecture](#architecture)

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

---

## Features

- **Full cluster review:** best-practice checks across security, resiliency, networking, Karpenter, Cluster Autoscaler, and observability, compiled into one report.
- **Upgrade readiness:** checks covering control plane, addons, deprecated APIs, third-party components, and workload resilience, with a go or no-go verdict and an ordered upgrade plan.
- **Guided remediation (`/fix`):** walks through fixes one at a time, classifies each as patchable, AWS CLI, or manifest change, and confirms before running anything.
- **Root cause analysis (`/investigate`):** correlates findings, runs read-only live diagnostics, and assesses real risk beyond the static report.
- **Multi-cluster (conversational):** ask the agent to review several clusters in one session and it works through them one after another. (The `/upgrade` slash command targets one cluster per invocation.)
- **Trend analysis:** tracks compliance over time and flags regressions and resolved findings.
- **JIRA export (`/export`):** deterministic CSV ready for bulk import, with no LLM involved.
- **Local knowledge base:** index your own runbooks, design docs, and PDFs, and the agent searches and cites them. The EKS Best Practices Guide is auto-synced on startup.
- **Runtime model switching (`/model`):** switch between Claude Opus and Sonnet mid-session.
- **Read-only mode (`--no-shell`):** disable command execution entirely for audit-only use.

---

## What gets checked

A full review evaluates checks across six domains. The exact number that runs depends on what's deployed (for example, Cluster Autoscaler checks that need a CA deployment are skipped on Karpenter-only clusters).

| Domain | Examples |
|---|---|
| **Security** | Endpoint access control, secrets encryption, Pod Security Standards, non-root containers, privilege escalation, IRSA / Pod Identity, anonymous RBAC bindings, private subnets |
| **Resiliency** | Liveness/readiness/startup probes, PodDisruptionBudgets, multi-replica workloads, anti-affinity, HPA/VPA, resource requests/limits, multi-AZ spread, node autoscaling |
| **Networking** | Endpoint access control, multi-AZ node distribution, private subnet placement, VPC CNI configuration, subnet IP availability |
| **Karpenter** | NodePool limits, disruption settings, AMI pinning, instance-type diversity, Spot consolidation |
| **Cluster Autoscaler** | Version match, auto-discovery tags, least-privilege IAM, expander strategy, node group setup |
| **Observability** | API server error/throttling rates, scheduler pending pods, etcd size, admission webhook latency |

Upgrade readiness adds checks covering control plane version and support status, addon compatibility and health, deprecated API usage, third-party component compatibility, data plane readiness, and workload resilience, concluding with a go or no-go verdict and an ordered upgrade plan.

Each finding carries a severity (Critical / High / Medium / Low), the impacted resources, and a specific remediation.

---

## Cost

eksreview is free and open source, but it calls Claude on Amazon Bedrock, so **you incur Amazon Bedrock charges depending on which model you use** and how much work each session does. (The EKS, EC2, and IAM API calls it makes are read-only and effectively free.)

A few ways to keep an eye on it:

- **`/context`** shows an approximate running session cost and token usage.
- **`/model sonnet`** switches to a cheaper, faster model mid-session.

For current per-token rates, see the [Amazon Bedrock pricing page](https://aws.amazon.com/bedrock/pricing/). The `/context` figure is a rough estimate; your AWS bill is the source of truth.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.10+** | 3.11 or 3.12 recommended |
| **`uv`** | Required. The bundled MCP server runs via `uv`. [Install guide](https://docs.astral.sh/uv/getting-started/installation/) |
| **AWS credentials** | With permissions for EKS, EC2, IAM, STS, and Bedrock (see [Permissions](#permissions)) |
| **Amazon Bedrock model access** | Claude Opus and/or Sonnet enabled in your Bedrock region. By default the agent uses **global** cross-region inference profiles, which work from commercial regions worldwide. To pin a region, set `MODEL_ID` to a regional profile (see [Configuration](#configuration)). |
| **Cluster access for your IAM identity** | Your IAM principal mapped into each cluster you review, via an EKS access entry (recommended) or the `aws-auth` ConfigMap (see [Permissions](#permissions)) |

**Supported clusters:** any Amazon EKS cluster, on any supported Kubernetes version, in standard mode or **EKS Auto Mode**. On Auto Mode clusters the Karpenter and Cluster Autoscaler checks that don't apply are automatically reported as "not applicable" rather than failing. The upgrade-readiness checker pulls its API-deprecation data live, so it stays current with new Kubernetes releases.

---

## Quick Start

The [60-second start](#60-second-start) at the top is the fast path: `git clone` → `./install.sh` → set credentials and `AWS_REGION` → `./eksreview`. This section covers two things it doesn't: the region requirement and a manual setup without `install.sh`.

> **Tip:** always include the AWS region in your request (e.g. "review eks-prod in us-east-1") or set `AWS_REGION`. The checks require a region; without one they fail fast and no report is generated.

### Manual setup (alternative to install.sh)

If you'd rather not use `./install.sh`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python main.py
```

---

## Usage

### Conversational reviews

You talk to eksreview in plain English; it decides which tools to run.

```
› review my cluster eks-prod in us-east-1
› run an upgrade readiness check on eks-staging targeting 1.31
› what changed in eks-prod since the last review?
› what does the EKS Best Practices Guide say about endpoint access control?
```

### Slash commands

| Command | Purpose |
|---|---|
| `/help` | Show all commands |
| `/upgrade <cluster> [region] [to <version>]` | Run upgrade readiness assessment |
| `/fix <description>` | Guided remediation for a finding (requires a review first) |
| `/investigate <description>` | Root cause analysis (loads the latest report if none in session) |
| `/export [path]` | Export findings to a JIRA-importable CSV |
| `/model [name]` | Show models, or switch (e.g. `/model sonnet`) |
| `/context` | Show context-window usage and approximate session cost |
| `/tools` | List all loaded tools and their status |
| `/knowledge show` | List knowledge base entries |
| `/knowledge add <name> <path>` | Index local files or a PDF |
| `/knowledge search <query>` | Search the knowledge base directly |
| `/knowledge remove <name>` | Remove an entry |
| `/knowledge update <name>` | Re-index an entry from its source |
| `/skill list` | List loaded review skills |
| `/exit` | End the session |

Notes on specific commands:

- **`/fix`** needs a review to have run in the current session. It pulls findings from the saved report rather than re-scanning, handles one fix at a time, and confirms before executing.
- **`/investigate`** works even without a review in the session: it loads the most recent assessment report from `reports/` and investigates against it.
- **`/upgrade`** accepts the cluster name plus an optional region and target version in any order, e.g. `/upgrade eks-prod us-east-1 to 1.31`. If you omit the version it auto-detects the next minor version.
- **`/export`** uses the last report from the session, or a path you give it; with neither, it lists recent reports.

### Example prompts by capability

Every capability can be driven with natural language. Use these as a starting point; phrasing is flexible.

**Full cluster review:** runs all checks across six domains and compiles a report.
```
› review my cluster eks-prod in us-east-1
› run a full best-practice assessment on eks-prod
› audit eks-prod for security and reliability issues
```

**Single-domain or multi-domain review:** scope the review to specific areas.
```
› check only the security posture of eks-prod in us-east-1
› run networking and resiliency checks on eks-prod
› just review Karpenter configuration for eks-prod
```

**Upgrade readiness:** go or no-go assessment with an ordered upgrade plan.
```
› is eks-prod ready to upgrade?
› check upgrade readiness for eks-prod in us-east-1
› can I upgrade eks-prod to 1.31?
› /upgrade eks-prod us-east-1 to 1.31
```

**Multi-cluster:** ask the agent to review several clusters in one session and it works through them one at a time. Use conversational phrasing for this (the `/upgrade` command itself takes one cluster per call).
```
› review eks-prod and eks-staging in us-east-1
› check upgrade readiness for eks-prod, then eks-staging, targeting 1.31
```

**Guided remediation (`/fix`):** fix findings one at a time, with confirmation. Run a review first.
```
› /fix enable control plane logging
› /fix restrict the cluster endpoint to 10.0.0.0/8
› /fix add a PodDisruptionBudget to the payment-processor deployment
› /fix make the novapay workloads run as non-root
› /fix pin the Karpenter EC2NodeClass AMI to a specific version
```

**Root cause analysis (`/investigate`):** goes beyond the report with live diagnostics.
```
› /investigate is our public endpoint actually exploitable?
› /investigate why are pods running as root
› /investigate the subnet IP exhaustion finding
› /investigate why several addons report InsufficientReplicas
```

**Follow-up questions on a report:** search the saved report by keyword.
```
› what were the high-severity security findings?
› show me the remediation for the storage encryption finding
› which namespaces are missing resource quotas?
› summarize the failed networking checks
```

**Trend analysis:** compare against previous reviews of the same cluster.
```
› what changed in eks-prod since the last review?
› did our compliance score improve?
› which findings did we resolve and which are new?
```

**JIRA export (`/export`):** produce an importable CSV of findings.
```
› /export
› /export reports/eks-prod-assessment-20260608_120312.md
```

**Knowledge base:** index your own docs and let the agent cite them.
```
› /knowledge add runbooks ~/docs/team-runbooks
› /knowledge add eks-pdf ~/Downloads/eks-best-practices.pdf
› /knowledge search pod security standards
› what does the EKS Best Practices Guide say about IRSA vs Pod Identity?
› according to best practices, how should I configure endpoint access?
```

**General EKS / Kubernetes questions:** answered conversationally, grounded in the knowledge base.
```
› what's the difference between Karpenter and Cluster Autoscaler?
› how do I troubleshoot a pod stuck in Pending?
› explain Pod Security Standards enforcement modes
```

**Model and session control**
```
› /model                 (show available models)
› /model sonnet          (switch to a faster, cheaper model)
› /context               (show token usage and estimated cost)
› /tools                 (list all loaded tools)
```

### Example session

```
› review my cluster eks-prod in us-east-1

  Checking review history...
  Reviewing eks-prod (6 checks)...
  ✓ security (12s)   ✓ resiliency (8s)   ✓ networking (15s)
  ✓ karpenter (3s)   ✓ cluster-autoscaler (6s)   ✓ observability (3s)
  ✓ Report compiled (45s)
  > Report saved: reports/eks-prod-assessment-20260608_120312.md
  > 31 passed, 45 failed. Use /investigate or /fix for any finding.

› /investigate is our public endpoint actually exploitable?

  ─ report_search: 'endpoint' + 'anonymous'
  ✓ Endpoint is public with no CIDR restriction
  ─ shell: kubectl auth can-i --list --as=system:anonymous
  ✓ Anonymous can list pods, nodes, services across all namespaces

  Risk: CRITICAL. The cluster can be enumerated from the internet without credentials.

› /fix restrict the endpoint to our office CIDR
  ...
```

---

## Reports

Each review writes a Markdown report to `reports/`:

```
reports/
├── eks-prod-assessment-20260608_120312.md
├── eks-prod-upgrade-readiness-20260608_131045.md
└── eks-staging-assessment-20260607_154420.md
```

Every report includes:

- An **executive summary** with the compliance score and a per-category breakdown
- **Quick wins:** fixes achievable in under 30 minutes
- A **findings table** with every check, its severity, status, and impacted resources
- **Per-finding detail:** impact, current state, and exact remediation commands
- **Trend analysis** against previous reviews of the same cluster

Reports are written to `reports/` as plain Markdown, so they are easy to share with your team or attach to tickets. They do contain cluster configuration details and IAM ARNs, so review them before sharing externally (see [Handling sensitive information](#handling-sensitive-information)).

### See a sample before you install

Want to judge the output quality first? Two real (sanitized) reports are checked into the repo:

- [`examples/sample-assessment-report.md`](examples/sample-assessment-report.md): a full best-practice review with executive summary, findings table, per-finding remediation, and trend analysis
- [`examples/sample-upgrade-readiness-report.md`](examples/sample-upgrade-readiness-report.md): a multi-hop (1.30 to 1.33) upgrade readiness assessment with a go or no-go verdict and ordered upgrade plan

---

## Knowledge base

eksreview keeps a local, SQLite-backed knowledge base with BM25 keyword search. On first launch it auto-syncs the official **EKS Best Practices Guide** PDF (about 1,400 searchable chunks), and re-checks for updates on later launches.

You can index your own content:

```
/knowledge add my-runbooks ~/docs/runbooks
/knowledge add eks-pdf ~/Downloads/eks-best-practices.pdf
/knowledge search pod security standards
```

The agent searches the knowledge base automatically when you ask best-practice or "how/why" questions, and cites it in the answer. Supported file types include Markdown, text, YAML, source files, and PDFs (PDF text extraction uses `pdfminer.six`).

To verify the knowledge base is working, run `/knowledge show` (lists entries and chunk counts) and `/knowledge search <query>` (returns ranked matches with scores).

---

## Permissions

eksreview talks to three things: the **AWS APIs** (EKS, EC2, IAM, STS), the **Kubernetes API** of each cluster, and **Amazon Bedrock** for the model. It runs read-only by default, and every change goes through a confirmation prompt, so most users only need the read-only IAM policy below.

### Read-only IAM policy (default, recommended)

This is all you need for reviews, upgrade-readiness checks, and `/investigate`. Attach it to the IAM role or user eksreview runs as.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EKSReviewReadOnly",
      "Effect": "Allow",
      "Action": [
        "eks:DescribeCluster",
        "eks:ListClusters",
        "eks:DescribeAddon",
        "eks:ListAddons",
        "eks:DescribeAddonVersions",
        "eks:DescribeUpdate",
        "eks:DescribeNodegroup",
        "eks:ListNodegroups",
        "eks:ListInsights",
        "eks:DescribeInsight",
        "ec2:DescribeInstances",
        "ec2:DescribeSubnets",
        "ec2:DescribeVpcs",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeLaunchTemplates",
        "ec2:DescribeLaunchTemplateVersions",
        "ec2:DescribeFlowLogs",
        "iam:GetRole",
        "iam:ListRolePolicies",
        "iam:ListAttachedRolePolicies",
        "iam:GetPolicy",
        "iam:GetPolicyVersion",
        "sts:GetCallerIdentity"
      ],
      "Resource": "*"
    },
    {
      "Sid": "Bedrock",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": "arn:aws:bedrock:*::foundation-model/anthropic.claude-*"
    }
  ]
}
```

### Write permissions (only for `/fix`)

eksreview never writes to AWS or your cluster unless you run `/fix` and confirm a command. The read-only setup above is enough for everything else.

If you intend to apply remediations, the principal needs **elevated permissions** scoped to what you actually fix — extra IAM actions for AWS-side changes (e.g. `eks:UpdateClusterConfig`) and/or edit-level Kubernetes RBAC for manifest changes. Grant these narrowly, prefer a **separate role**, and use the read-only policy day-to-day, switching only when remediating.

### Cluster access (Kubernetes RBAC)

IAM gets eksreview to the AWS APIs, but reading pods, deployments, RBAC, and other in-cluster objects requires your IAM principal to be **mapped inside the cluster**. Without this, the AWS calls succeed but the Kubernetes checks fail.

If you review **multiple clusters**, the same IAM principal must be granted this read access (an access entry with `AmazonEKSAdminViewPolicy`, or the equivalent `aws-auth` mapping) in **each** cluster you want to review. The mapping is per-cluster, so a principal mapped only in cluster A can describe resources in A but its Kubernetes checks will fail against cluster B until it's mapped there too. Repeat the steps below for every cluster.

Map your principal using an **EKS access entry** (recommended) or the legacy `aws-auth` ConfigMap:

```bash
# EKS access entry (recommended): grant the IAM role cluster read access
aws eks create-access-entry \
  --cluster-name my-cluster \
  --region us-east-1 \
  --principal-arn arn:aws:iam::111122223333:role/eksreview-readonly

aws eks associate-access-policy \
  --cluster-name my-cluster \
  --region us-east-1 \
  --principal-arn arn:aws:iam::111122223333:role/eksreview-readonly \
  --access-scope type=cluster \
  --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSAdminViewPolicy
```

`AmazonEKSViewPolicy` grants read access to most resources. The review reads pods, deployments, statefulsets, daemonsets, namespaces, RBAC (roles/bindings), network policies, storage classes, PDBs, and HPAs. For `/fix` operations that apply manifests, use a role mapped with edit/admin scope instead.

Verify your mapping with:

```bash
kubectl auth can-i list pods --all-namespaces
```

### Cross-account: Bedrock in one account, EKS in another

A common enterprise setup is to centralize Bedrock model access in one account while the EKS clusters live in others. eksreview supports this by using **two separate credential sources**:

- **EKS / EC2 / IAM calls** use your default AWS credential chain (`AWS_PROFILE`, env keys, SSO, instance role) and `AWS_REGION`.
- **Bedrock calls** use, in order: a Bedrock API key in `AWS_BEARER_TOKEN_BEDROCK` if set; otherwise the dedicated `BEDROCK_AWS_*` access keys if set; otherwise the same default credentials.

To run the model from a central Bedrock account while reviewing a cluster in another account:

```bash
# Default credentials → the account/region where the EKS cluster lives
export AWS_PROFILE=eks-cluster-account
export AWS_REGION=us-east-1

# Bedrock credentials → the central model account/region
export BEDROCK_AWS_ACCESS_KEY_ID=AKIA...
export BEDROCK_AWS_SECRET_ACCESS_KEY=...
export BEDROCK_AWS_SESSION_TOKEN=...        # if using temporary creds
export BEDROCK_AWS_REGION=us-west-2
```

Notes:
- The bundled MCP subprocess (which makes the EKS and Kubernetes calls) is given only the `AWS_*` credentials. The `BEDROCK_AWS_*` values and `AWS_BEARER_TOKEN_BEDROCK` are stripped from its environment, so Bedrock credentials never reach the cluster-facing process.
- If `BEDROCK_AWS_REGION` is unset, Bedrock uses `AWS_REGION`.
- You can also assume a Bedrock role and export its temporary credentials into the `BEDROCK_AWS_*` variables.

#### Using a Bedrock API key

Instead of access keys, you can authenticate to Bedrock with a [Bedrock API key](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html) — generate one in the Bedrock console and export it as `AWS_BEARER_TOKEN_BEDROCK`:

```bash
# Cluster account → EKS / EC2 / IAM / Kubernetes calls
export AWS_PROFILE=eks-cluster-account
export AWS_REGION=us-east-1

# Bedrock API key → model calls (short- or long-term keys both work)
export AWS_BEARER_TOKEN_BEDROCK=<your-bedrock-api-key>
export BEDROCK_AWS_REGION=us-west-2     # the region the key was generated for
```

Both **short-term** keys (expire with your session, up to 12 hours) and **long-term** keys (a fixed expiry you set) use the same variable. When `AWS_BEARER_TOKEN_BEDROCK` is set it takes precedence over `BEDROCK_AWS_*` access keys for Bedrock calls. Short-term keys are recommended; long-term keys are best kept to local exploration.

#### Assuming a Bedrock role in the central account

eksreview does not take a role name or ARN directly — it consumes
already-resolved credentials. To use a role in the central Bedrock
account, assume it yourself and export the temporary credentials into
the `BEDROCK_AWS_*` variables. There are two common ways to do this.

**Option A — assume the role with the AWS CLI:**

```bash
# Default credentials still point at the EKS cluster account
export AWS_PROFILE=eks-cluster-account
export AWS_REGION=us-east-1

# Assume the Bedrock role in the central account
creds=$(aws sts assume-role \
  --role-arn arn:aws:iam::111122223333:role/bedrock-invoke \
  --role-session-name eksreview \
  --query 'Credentials' --output json)

export BEDROCK_AWS_ACCESS_KEY_ID=$(echo "$creds" | jq -r .AccessKeyId)
export BEDROCK_AWS_SECRET_ACCESS_KEY=$(echo "$creds" | jq -r .SecretAccessKey)
export BEDROCK_AWS_SESSION_TOKEN=$(echo "$creds" | jq -r .SessionToken)
export BEDROCK_AWS_REGION=us-west-2

./eksreview
```

These credentials are temporary — when they expire you'll need to
re-assume the role and re-export them.

**Option B — let a named profile assume the role.** Define the role in
`~/.aws/config` so the SDK handles assumption (and refresh) for you:

```ini
# ~/.aws/config
[profile bedrock-central]
role_arn = arn:aws:iam::111122223333:role/bedrock-invoke
source_profile = default
region = us-west-2
```

Then resolve that profile into the `BEDROCK_AWS_*` variables before launching:

```bash
export AWS_PROFILE=eks-cluster-account   # EKS/EC2/IAM calls
export AWS_REGION=us-east-1

# Resolve the Bedrock profile to concrete credentials
eval "$(aws configure export-credentials --profile bedrock-central --format env)" 2>/dev/null
export BEDROCK_AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID
export BEDROCK_AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY
export BEDROCK_AWS_SESSION_TOKEN=$AWS_SESSION_TOKEN
export BEDROCK_AWS_REGION=us-west-2

./eksreview
```

The role in the central account needs `bedrock:InvokeModel` (and
`bedrock:InvokeModelWithResponseStream`) on the models you use, and its
trust policy must allow your cluster-account principal to assume it.

---

## Configuration

eksreview reads configuration from environment variables. Defaults are sensible; most users only need AWS credentials and a region.

| Variable | Default | Description |
|---|---|---|
| `AWS_REGION` / `AWS_DEFAULT_REGION` | `us-east-1` | Region for EKS/EC2 API calls |
| `MODEL_ID` | global Claude Opus profile | Bedrock model override. By default the agent uses **global** cross-region inference profiles (`global.` prefix). Set this to a regional system inference profile (e.g. `us.anthropic.claude-sonnet-4-6`) to pin a geography; `/model` switches then stay in that same region |
| `MODEL_TEMPERATURE` | `0.1` | Sampling temperature (ignored by models that don't support it) |
| `MODEL_MAX_TOKENS` | `128000` | Max output tokens (capped per model) |
| `BEDROCK_AWS_REGION` | Same as `AWS_REGION` | Region for Bedrock, if different from the cluster's |
| `BEDROCK_AWS_ACCESS_KEY_ID` | — | Cross-account credentials for Bedrock |
| `BEDROCK_AWS_SECRET_ACCESS_KEY` | — | Cross-account credentials for Bedrock |
| `BEDROCK_AWS_SESSION_TOKEN` | — | Cross-account session token for Bedrock |
| `AWS_BEARER_TOKEN_BEDROCK` | — | Bedrock API key (short- or long-term); takes precedence over `BEDROCK_AWS_*` for Bedrock calls |
| `EKS_MCP_SERVER_DIR` | bundled `./mcp-server` | Override the MCP server path (dev only) |
| `EKS_REVIEW_NO_SHELL` | — | Set to `1` to disable command execution (same as `--no-shell`) |
| `EKS_REVIEW_OFFLINE` | — | Set to `1` to skip the EKS Best Practices PDF sync at startup |
| `LOG_LEVEL` | `WARNING` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `REPORTS_DIR` | `reports` | Where reports are written |
| `KNOWLEDGE_DIR` | `.knowledge` | Knowledge base storage |
| `SESSIONS_DIR` | `.sessions` | Conversation session storage |
| `MCP_RATE_LIMIT_SOFT` | `200` | Warn after this many MCP calls in a session |
| `MCP_RATE_LIMIT_HARD` | `500` | Refuse further MCP calls after this many in a session |

On POSIX systems the `reports/`, `.knowledge/`, and `.sessions/` directories are created with owner-only (`0700`) permissions because they can contain cluster security posture and IAM details.

### Corporate networks (TLS-inspecting proxies)

If you sit behind an HTTPS-inspecting proxy that re-signs traffic with an internal CA:

```bash
export HTTPS_PROXY=http://corp-proxy.internal:3128
export NO_PROXY=169.254.169.254,localhost
export AWS_CA_BUNDLE=/etc/ssl/certs/corp-ca.pem
export REQUESTS_CA_BUNDLE=/etc/ssl/certs/corp-ca.pem
export SSL_CERT_FILE=/etc/ssl/certs/corp-ca.pem
```

Both the agent and the MCP subprocess honor these.

---

## Command-line flags

Pass flags through the launcher (`./eksreview <flags>`) or directly (`python main.py <flags>`):

| Flag | Description |
|---|---|
| `--session <id>` | Resume a previous session by ID (sessions are stored in `.sessions/`) |
| `--no-shell` | Read-only mode: removes the shell tool so the agent can never execute commands. `/fix` and `/investigate` still suggest commands and example manifests |

---

## Performance and scale

A full review typically completes in a few minutes. The six domain checks run sequentially, then a sub-agent compiles the report. Most of the wall-clock time is the report compilation, not the checks themselves.

Review time scales with **how much is deployed**, not cluster node count alone. Domains like security and resiliency inspect every workload, so a cluster with thousands of pods takes longer and produces a larger report than a small one. For very large or busy clusters, two safety valves apply automatically:

- `MCP_RATE_LIMIT_SOFT` (default 200) warns when a session crosses that many MCP calls.
- `MCP_RATE_LIMIT_HARD` (default 500) stops further MCP calls in a session.

If you hit these on a large cluster, run one domain at a time or raise the limits via environment variables.

---

## Data and cleanup

eksreview writes only to three local directories (owner-only on POSIX):

| Path | Contents |
|---|---|
| `reports/` | Generated Markdown reports |
| `.knowledge/` | The SQLite knowledge base (EKS Best Practices Guide + anything you indexed) |
| `.sessions/` | Conversation session state for `--session` resume |

Nothing is uploaded anywhere. To remove all local state:

```bash
rm -rf reports/ .knowledge/ .sessions/
```

---

## Safety model

eksreview is built to be safe to point at production clusters:

- **Read-only by default.** The agent only runs read-only commands (`get`, `describe`, `list`) unless you confirm a change.
- **Confirmation before any mutation.** Write or mutate operations require an explicit `y` at a confirmation prompt.
- **Hard block on destructive commands.** A guard at the execution boundary blocks high-blast-radius commands (cluster/nodegroup deletion, `terminate-instances`, `kubectl delete namespace/node`, `rm -rf /`, `drop database`, etc.) even if confirmed.
- **`--no-shell` for audit-only use.** Removes command execution entirely.
- **Prompt-injection tripwire** on free-text `/fix`, `/investigate`, and `/upgrade` input.

These are layered defenses, not a substitute for least-privilege IAM and Kubernetes RBAC.

### Handling sensitive information

- **Reports contain security posture.** Generated reports include IAM ARNs, cluster configuration, and the list of failing security controls. They're written to `reports/` with owner-only permissions on POSIX systems. Treat them as sensitive and review before sharing or attaching to tickets.
- **Don't paste secrets into prompts.** Anything you type goes to the model. Don't paste credentials, tokens, or Kubernetes Secret values into your requests.
- **The agent does not read Kubernetes Secret values** as part of a review. Diagnostics use read-only metadata commands; avoid asking it to dump secret contents.
- **Use AWS Secrets Manager / Parameter Store or IRSA / EKS Pod Identity** for application credentials rather than storing them in manifests the agent might surface.
- **The knowledge base is local.** Anything you index with `/knowledge add` is stored in `.knowledge/` on your machine; nothing is uploaded.

---

## Troubleshooting

**"MCP server failed to load" / checks finish in 0 seconds**
The bundled MCP server needs `uv`. Install it:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
If checks complete instantly and the agent says the review couldn't run, it's usually missing AWS credentials or a missing region. See the next two items.

**All checks fail with "Unable to locate credentials"**
Configure AWS credentials (`aws configure`, set `AWS_PROFILE`, or export keys) and retry. Verify with `aws sts get-caller-identity`.

**Checks fail asking for a region**
Include the region in your request ("review eks-prod in us-east-1") or set `AWS_REGION`.

**"AccessDeniedException" calling Bedrock InvokeModel**
Enable model access in the Bedrock console (Model access) for your region, and confirm your IAM principal has `bedrock:InvokeModel`.

**"No cluster found" / cluster not found**
Make sure `AWS_REGION` is set to the region where the cluster lives, and that your credentials point at the right account. Verify with `aws sts get-caller-identity` and list clusters with `aws eks list-clusters --region <region>`. eksreview connects to the Kubernetes API itself using short-lived STS tokens, so you do **not** need to run `aws eks update-kubeconfig` — but your IAM identity must be mapped into the cluster (see [Permissions](#permissions)).

**Session feels expensive or slow**
Type `/context` to see token usage and cost. Switch to a cheaper model mid-session with `/model sonnet`, or start a fresh session.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                        eksreview                         │
│                                                          │
│  ./eksreview ──► main.py ──► Strands Agent (Claude)      │
│                       │                                  │
│                       ├─ Conversation + slash commands   │
│                       ├─ Sub-agents (review, upgrade)    │
│                       ├─ Knowledge base (SQLite + BM25)  │
│                       ├─ Skills (report templates)       │
│                       └─ Report generation + JIRA export │
│                       │                                  │
│            ┌──────────┴───────────┐                      │
│            ▼                      ▼                      │
│   Bedrock (Claude)        MCP server (bundled)           │
│                                  │                       │
│                                  ▼                       │
│                     EKS / EC2 / IAM / K8s API            │
└──────────────────────────────────────────────────────────┘
```

For trust boundaries, the layered safety stack, and the two-tier sub-agent orchestration, see [`docs/architecture.md`](docs/architecture.md). Design decisions are recorded as [Architecture Decision Records](docs/adr/README.md).

---