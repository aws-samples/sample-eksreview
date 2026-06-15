# Cost

eksreview is free and open source, but it calls Claude on Amazon Bedrock, so **you incur Amazon Bedrock charges depending on which model you use** and how much work each session does. (The EKS, EC2, and IAM API calls it makes are read-only and effectively free.)

A few ways to keep an eye on it:

- **`/context`** shows an approximate running session cost and token usage.
- **`/model sonnet`** switches to a cheaper, faster model mid-session.

For current per-token rates, see the [Amazon Bedrock pricing page](https://aws.amazon.com/bedrock/pricing/). The `/context` figure is a rough estimate; your AWS bill is the source of truth.
