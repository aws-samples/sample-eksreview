# Credentials & Cross-Account

## Cross-account: Bedrock in one account, EKS in another

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

### Using a Bedrock API key

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

### Assuming a Bedrock role in the central account

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

**Related:** [Models & Regions](models.md) · [Environment Variables](environment-variables.md) · [Permissions](../reference/permissions.md)
