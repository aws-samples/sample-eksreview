# FAQ

Common questions before and during adoption. Most link to a fuller answer elsewhere in the docs.

## Does eksreview modify my cluster?

No, not unless you ask it to. It runs **read-only by default** — only `get`/`describe`/`list`-style operations. Any change happens only through `/fix` and requires an explicit confirmation before it runs. You can remove command execution entirely with `--no-shell`. See the [Safety Model](reference/safety.md).

## Does my cluster data leave my machine?

The only data sent off your machine is what goes to **Amazon Bedrock** for the model (your prompts and the check results the agent reasons over). Reports, the knowledge base, and session state are stored locally. Nothing is uploaded anywhere else. See [Data & Cleanup](reference/data-and-cleanup.md) and [Handling sensitive information](reference/safety.md).

## What does it cost?

The tool is free and open source, but it calls Claude on Amazon Bedrock, so you incur Bedrock token charges depending on the model and how much work each session does. The EKS/EC2/IAM calls are read-only and effectively free. Use `/context` to track approximate session cost and `/model sonnet` to lower it. See [Cost](reference/cost.md).

## Which models does it use?

Claude Opus (default) and Sonnet on Amazon Bedrock, via **global** cross-region inference profiles by default. You can switch with `/model` or pin a region with `MODEL_ID`. See [Models & Regions](configuration/models.md).

## Does it run on Windows?

macOS and Linux are supported natively. On Windows, run it under [WSL](https://learn.microsoft.com/windows/wsl/install) — the `install.sh` and `eksreview` launchers are bash scripts. See [Prerequisites](getting-started/prerequisites.md).

## Do I need to run `kubectl` or set up a kubeconfig?

No. eksreview connects to the Kubernetes API itself using short-lived STS tokens. You do need your IAM identity **mapped into each cluster** you review (an EKS access entry or `aws-auth`). See [Permissions](reference/permissions.md).

## Do I need write/admin permissions?

No for reviews, upgrade-readiness checks, and `/investigate` — the [read-only IAM policy](reference/permissions.md) is enough. You only need elevated permissions if you intend to apply remediations with `/fix`.

## Can Bedrock and my EKS cluster be in different AWS accounts?

Yes. The model credentials are independent of the cluster credentials. Use a Bedrock API key (`AWS_BEARER_TOKEN_BEDROCK`) or the `BEDROCK_AWS_*` variables for the model, and your default chain for the cluster. See [Credentials & Cross-Account](configuration/credentials.md).

## Can it review more than one cluster?

Yes — ask it conversationally to review several clusters in one session and it works through them one at a time. The same IAM identity must be mapped into each cluster. See [Example Prompts](usage/example-prompts.md).

## A review didn't run / all checks failed. What now?

Almost always a missing region, missing/wrong credentials, or a cluster the credentials can't see. See [Troubleshooting](reference/troubleshooting.md).

## Is this an official AWS service?

No. It's a sample provided for illustrative purposes, with no warranty or production support guarantee.

---

**Related:** [Installation](getting-started/installation.md) · [Troubleshooting](reference/troubleshooting.md)
