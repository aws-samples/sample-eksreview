# What Gets Checked

A full review evaluates checks across six domains. The exact number that runs depends on what's deployed (for example, Cluster Autoscaler checks that need a CA deployment are skipped on Karpenter-only clusters).

| Domain | Examples |
|---|---|
| **Security** | Endpoint access control, secrets encryption, Pod Security Standards, non-root containers, privilege escalation, IRSA / Pod Identity, anonymous RBAC bindings, private subnets |
| **Resiliency** | Liveness/readiness/startup probes, PodDisruptionBudgets, multi-replica workloads, anti-affinity, HPA/VPA, resource requests/limits, multi-AZ spread, node autoscaling |
| **Networking** | Endpoint access control, multi-AZ node distribution, private subnet placement, VPC CNI configuration, subnet IP availability |
| **Karpenter** | NodePool limits, disruption settings, AMI pinning, instance-type diversity, Spot consolidation |
| **Cluster Autoscaler** | Version match, auto-discovery tags, least-privilege IAM, expander strategy, node group setup |
| **Observability** | API server error/throttling rates, scheduler pending pods, etcd size, admission webhook latency |

Upgrade readiness adds checks covering control plane version and support status, addon compatibility and health, deprecated API usage, third-party component compatibility, data plane readiness, and workload resilience. It ends with a go or no-go verdict and an ordered upgrade plan.

Each finding carries a severity (Critical / High / Medium / Low), the impacted resources, and a specific remediation.

---

**Related:** [Example Prompts](../usage/example-prompts.md) · [Reports](../usage/reports.md)
