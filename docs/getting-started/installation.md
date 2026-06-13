# Installation

## 60-second start

Clone, install, and start your first review. You need Python 3.10+, [`uv`](https://docs.astral.sh/uv/getting-started/installation/), AWS credentials, and Amazon Bedrock model access (see [Prerequisites](prerequisites.md)).

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

Then, at the prompt, ask for a review in plain English:

```text
review my cluster my-cluster in <your-region>
```

A prioritized report saves to `reports/` in a few minutes. From there, try `/investigate` to dig into a finding or `/fix` to remediate one step by step.

### Manual setup (alternative to install.sh)

If you'd rather not use `./install.sh`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python main.py
```

### Bedrock and EKS in different accounts

If your Bedrock model access lives in one account and your EKS clusters in another, you can point the cluster calls at one account and Bedrock at the other. The MCP sub-process that talks to your cluster only uses the `AWS_*` credentials, while Bedrock calls can use a separate credential source. See [Credentials](../configuration/credentials.md) for step-by-step setup, including using a Bedrock API key and assuming a role in a central account.

## Updating

eksreview is run from a clone, so update with `git pull` and re-run the installer to pick up any new dependencies:

```bash
cd eksreview
git pull
./install.sh
```

## Uninstalling

Remove the virtual environment and (optionally) the local data directories, then delete the clone:

```bash
cd eksreview
rm -rf .venv                      # the installed environment
rm -rf reports/ .knowledge/ .sessions/   # optional: generated data (see Data & Cleanup)
cd .. && rm -rf eksreview         # the clone itself
```

Nothing is installed outside the project directory, so removing the clone leaves no residue in the project — though `uv` may keep a package cache under `~/.cache/uv` (shared with other uv projects; safe to leave or clear with `uv cache clean`).

---

**Next:** [Your First Review](first-review.md) · [Prerequisites](prerequisites.md) · [Configuration](../configuration/environment-variables.md)
