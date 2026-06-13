# Your First Review

When you ask eksreview to review a cluster, it runs best-practice checks across six domains (security, resiliency, networking, Karpenter, Cluster Autoscaler, and observability). The heavy lifting happens inside an ephemeral sub-agent that runs the checks and compiles them into a prioritized report, which is saved to `reports/` in a few minutes.

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

From here, you can keep the conversation going. See [Slash commands](../usage/slash-commands.md) for details on `/investigate` and `/fix`, and [Reports](../usage/reports.md) for what each report contains.
