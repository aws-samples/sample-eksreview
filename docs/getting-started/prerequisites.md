# Prerequisites

Before you install eksreview, make sure your machine and AWS access are set up as described below. If you already work with EKS, you probably have most of this in place.

| Requirement | Notes |
|---|---|
| **Operating system** | macOS or Linux. On Windows, run it under [WSL](https://learn.microsoft.com/windows/wsl/install), since the `install.sh` and `eksreview` launchers are bash scripts. |
| **Python 3.10+** | 3.11 or 3.12 recommended |
| **`uv`** | Required for the bundled MCP server. `install.sh` installs it for you if it's missing; you can also install it yourself with the [install guide](https://docs.astral.sh/uv/getting-started/installation/). |
| **AWS access** | Credentials with permissions for EKS, EC2, IAM, STS, and Bedrock |
| **Amazon Bedrock model access** | Claude Opus and/or Sonnet enabled in your Bedrock region. By default the agent uses global cross-region inference profiles, which work from commercial regions worldwide. To pin a region, set `MODEL_ID` to a regional inference profile. |
| **Cluster access for your IAM identity** | Your IAM principal mapped into each cluster you review, via an EKS access entry (recommended) or the `aws-auth` ConfigMap |

**Supported clusters:** any Amazon EKS cluster, on any supported Kubernetes version, in standard mode or EKS Auto Mode. On Auto Mode clusters the Karpenter and Cluster Autoscaler checks that don't apply are automatically reported as "not applicable" rather than failing. The upgrade-readiness checker pulls its API-deprecation data live, so it stays current with new Kubernetes releases.
