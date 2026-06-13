# Troubleshooting

## "MCP server failed to load" / checks finish in 0 seconds

The bundled MCP server needs `uv`. Install it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

If checks complete instantly and the agent says the review couldn't run, it's usually missing AWS credentials or a missing region. See the next two items.

## All checks fail with "Unable to locate credentials"

Configure AWS credentials (`aws configure`, set `AWS_PROFILE`, or export keys) and retry. Verify with `aws sts get-caller-identity`.

## Checks fail asking for a region

Include the region in your request ("review eks-prod in us-east-1") or set `AWS_REGION`.

## "AccessDeniedException" calling Bedrock InvokeModel

Enable model access in the Bedrock console (Model access) for your region, and confirm your IAM principal has `bedrock:InvokeModel`.

## "No cluster found" / cluster not found

Make sure `AWS_REGION` is set to the region where the cluster lives, and that your credentials point at the right account. Verify with `aws sts get-caller-identity` and list clusters with `aws eks list-clusters --region <region>`. eksreview connects to the Kubernetes API itself using short-lived STS tokens, so you do **not** need to run `aws eks update-kubeconfig` — but your IAM identity must be mapped into the cluster (see [Permissions](permissions.md)).

## Session feels expensive or slow

Type `/context` to see token usage and cost. Switch to a cheaper model mid-session with `/model sonnet`, or start a fresh session.

---

**Related:** [Prerequisites](../getting-started/prerequisites.md) · [Permissions](permissions.md)
