# Example Prompts

Every capability can be driven with natural language, and there is no command syntax to memorize for everyday use. The agent works out which tools to run from how you phrase the request, so you can be as loose or as specific as you like. Use the prompts below as a starting point; the wording is flexible. When you want exact control over a single action, the [slash commands](slash-commands.md) cover the same ground.

**Full cluster review:** runs all checks across six domains and compiles a report.
```text
› review my cluster eks-prod in us-east-1
› run a full best-practice assessment on eks-prod
› audit eks-prod for security and reliability issues
```

**Single-domain or multi-domain review:** scope the review to specific areas.
```text
› check only the security posture of eks-prod in us-east-1
› run networking and resiliency checks on eks-prod
› just review Karpenter configuration for eks-prod
```

**Upgrade readiness:** go or no-go assessment with a recommended upgrade plan.
```text
› is eks-prod ready to upgrade?
› check upgrade readiness for eks-prod in us-east-1
› can I upgrade eks-prod to 1.31?
› /upgrade eks-prod us-east-1 to 1.31
```

**Multi-cluster:** ask the agent to review several clusters in one session and it works through them one at a time. Use conversational phrasing here, since the `/upgrade` command itself takes one cluster per call.
```text
› review eks-prod and eks-staging in us-east-1
› check upgrade readiness for eks-prod, then eks-staging, targeting 1.31
```

**Guided remediation (`/fix`):** fix findings one at a time, with confirmation. Run a review first.
```text
› /fix enable control plane logging
› /fix restrict the cluster endpoint to 10.0.0.0/8
› /fix add a PodDisruptionBudget to the payment-processor deployment
› /fix make the novapay workloads run as non-root
› /fix pin the Karpenter EC2NodeClass AMI to a specific version
```

**Root cause analysis (`/investigate`):** goes past the report with live diagnostics.
```text
› /investigate is our public endpoint actually exploitable?
› /investigate why are pods running as root
› /investigate the subnet IP exhaustion finding
› /investigate why several addons report InsufficientReplicas
```

**Follow-up questions on a report:** search the saved report by keyword.
```text
› what were the high-severity security findings?
› show me the remediation for the storage encryption finding
› which namespaces are missing resource quotas?
› summarize the failed networking checks
```

**Trend analysis:** compare against previous reviews of the same cluster. It picks up the most recent earlier report for that cluster from the `reports/` directory, if one exists, and reports what changed.
```text
› what changed in eks-prod since the last review?
› did our compliance score improve?
› which findings did we resolve and which are new?
```

**JIRA export (`/export`):** produce an importable CSV of findings.
```text
› /export
› /export reports/eks-prod-assessment-20260608_120312.md
```

**Knowledge base:** index your own docs and let the agent cite them.
```text
› /knowledge add runbooks ~/docs/team-runbooks
› /knowledge add eks-pdf ~/Downloads/eks-best-practices.pdf
› /knowledge search pod security standards
› what does the EKS Best Practices Guide say about IRSA vs Pod Identity?
› according to best practices, how should I configure endpoint access?
```

**General EKS / Kubernetes questions:** answered conversationally, grounded in the knowledge base.
```text
› what's the difference between Karpenter and Cluster Autoscaler?
› how do I troubleshoot a pod stuck in Pending?
› explain Pod Security Standards enforcement modes
```

**Model and session control**
```text
› /model                 (show available models)
› /model sonnet          (switch to a faster, cheaper model)
› /context               (show token usage and estimated cost)
› /tools                 (list all loaded tools)
```
