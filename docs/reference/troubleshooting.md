# Troubleshooting

## "MCP server failed to load" / checks finish in 0 seconds

The bundled MCP server needs `uv`. Install it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

If checks complete instantly and the agent reports that the review couldn't run, the cause is usually missing AWS credentials or an unset region. See the items below.

## All checks fail with "Unable to locate credentials"

Configure AWS credentials (run `aws configure`, set `AWS_PROFILE`, or export keys) and retry. Verify with `aws sts get-caller-identity`.

## Checks fail asking for a region

Include the region in your request (for example, "review eks-prod in us-east-1") or set `AWS_REGION`.

## "AccessDeniedException" calling Bedrock InvokeModel

Enable model access in the Bedrock console (under Model access) for your region, then confirm your IAM principal has `bedrock:InvokeModel`.

## "No cluster found" / cluster not found

Make sure `AWS_REGION` is set to the region where the cluster lives, and that your credentials point at the right account. Verify with `aws sts get-caller-identity` and list clusters with `aws eks list-clusters --region <region>`. eksreview connects to the Kubernetes API itself using short-lived STS tokens, so you do **not** need to run `aws eks update-kubeconfig`. Your IAM identity must still be mapped into the cluster (see [Permissions](permissions.md)).

## Session feels expensive or slow

Type `/context` to see token usage and cost. Switch to a cheaper model mid-session with `/model sonnet`, or start a fresh session.
