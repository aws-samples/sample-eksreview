---
name: eks-knowledge
description: Deep reference knowledge for Amazon EKS and Kubernetes operations, covering architecture, best practices, troubleshooting patterns, and AWS integrations. Activate when answering EKS/K8s questions that need detailed technical guidance.
---

# EKS & Kubernetes Knowledge Base

## EKS Architecture

### Control Plane
- Managed by AWS across multiple AZs
- API server, etcd, controller manager, scheduler
- Accessed via public and/or private endpoints
- Control plane logging: api, audit, authenticator, controllerManager, scheduler

### Data Plane Options
- **Managed Node Groups**: EC2 instances managed by EKS, auto-patching support
- **Self-Managed Nodes**: Full control over EC2 instances, custom AMIs
- **Fargate**: Serverless pods, no node management, per-pod isolation
- **EKS Hybrid Nodes**: On-premises nodes registered to EKS control plane
- **EKS Auto Mode**: Fully automated node management with Karpenter built-in

### Networking (VPC CNI)
- Each pod gets a real VPC IP address
- Custom networking mode for IP conservation
- Prefix delegation for high pod density (up to 110 pods per node on Nitro)
- Security Groups for Pods
- Network Policy support (native VPC CNI or Calico)
- IPv6 support for large-scale clusters

### Identity & Access
- **IRSA** (IAM Roles for Service Accounts): OIDC-based, per-pod IAM
- **EKS Pod Identity**: Simplified alternative to IRSA, no OIDC provider needed
- **Access Entries**: Cluster access management without aws-auth ConfigMap
- **aws-auth ConfigMap**: Legacy IAM-to-K8s RBAC mapping

## AWS Best Practices Reference

Source: https://aws.github.io/aws-eks-best-practices/

### Security Best Practices
1. Use IRSA or Pod Identity — never attach broad IAM policies to node roles
2. Enable envelope encryption for Secrets using KMS
3. Enable all control plane log types, especially audit logs
4. Enforce Pod Security Standards at namespace level (restricted preferred)
5. Implement NetworkPolicies for east-west traffic control
6. Use private cluster endpoints or restrict public access via CIDR
7. Rotate credentials and certificates regularly
8. Use Security Groups for Pods where applicable
9. Run containers as non-root with read-only root filesystem
10. Scan images for vulnerabilities, use ECR image scanning

### Networking Best Practices
1. Plan subnet sizing: /19 or larger for pod subnets
2. Use custom networking to separate pod and node IP ranges
3. Enable prefix delegation for high-density workloads
4. Use AWS Load Balancer Controller for ALB/NLB ingress
5. Implement CoreDNS autoscaling for large clusters
6. Use topology-aware routing to reduce cross-AZ traffic

### Reliability Best Practices
1. Spread nodes across 3+ AZs
2. Set PodDisruptionBudgets for all production workloads
3. Configure liveness, readiness, and startup probes
4. Set resource requests AND limits on all containers
5. Use topology spread constraints for even pod distribution
6. Plan upgrade strategy: in-place with PDBs or blue/green
7. Use Karpenter for intelligent node provisioning

### Performance Best Practices
1. Right-size requests based on actual P99 usage (not peak)
2. Use HPA with custom metrics, not just CPU
3. Monitor CPU throttling via container_cpu_cfs_throttled_periods_total
4. Use Graviton instances for better price-performance
5. Consider instance store (NVMe) for I/O-heavy workloads
6. Use VPA in recommendation mode to inform right-sizing

### Cost Optimization Best Practices
1. Use Spot instances for stateless, fault-tolerant workloads
2. Use Karpenter for efficient bin-packing and consolidation
3. Right-size nodes: avoid large instances with low utilization
4. Migrate gp2 volumes to gp3 (20% cheaper, better baseline)
5. Use Savings Plans for baseline capacity
6. Monitor with Kubecost or AWS Cost Explorer container insights
7. Delete unused PVCs, LoadBalancers, and idle node groups

### Operational Excellence Best Practices
1. Stay within N-1 of the latest Kubernetes version
2. Keep add-ons up to date (VPC CNI, CoreDNS, kube-proxy)
3. Use GitOps (Flux/ArgoCD) for declarative deployments
4. Implement comprehensive observability: metrics, logs, traces
5. Document runbooks for common operational scenarios
6. Practice chaos engineering and disaster recovery drills
7. Use EKS managed add-ons for simplified lifecycle management

## Common Troubleshooting Patterns

### Pod Stuck in Pending
- Insufficient node resources → check resource requests vs available
- Node selector/affinity mismatch → check node labels
- Taints without tolerations → check node taints
- PVC binding issues → check StorageClass and PV availability

### Pod CrashLoopBackOff
- Application error → check container logs
- OOMKilled → increase memory limits
- Failed health probes → adjust probe timing/thresholds
- Missing ConfigMap/Secret → check references exist

### Node NotReady
- Kubelet issues → check kubelet logs on the node
- Disk pressure → check node disk usage
- Network issues → check VPC CNI and security groups
- Instance health → check EC2 instance status checks

### DNS Resolution Failures
- CoreDNS pods unhealthy → check CoreDNS deployment
- ndots configuration → consider setting ndots:2 in pod spec
- CoreDNS scaling → enable cluster-proportional-autoscaler
- VPC DNS limits → check AmazonProvidedDNS throttling

### High Cross-AZ Traffic Costs
- Enable topology-aware routing
- Use topology spread constraints to co-locate related pods
- Consider AZ-affinity for stateful workloads
- Monitor with VPC Flow Logs or Container Insights


## Reference URLs

When you need current information, use `http_request` with `convert_to_markdown=true`
to fetch from these sources. For GitHub API URLs, use plain GET (they return JSON).

### EKS Documentation (AWS)
- Kubernetes versions: https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html
- Platform versions: https://docs.aws.amazon.com/eks/latest/userguide/platform-versions.html
- Managed add-ons: https://docs.aws.amazon.com/eks/latest/userguide/eks-add-ons.html
- Pod Identity: https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html
- IRSA: https://docs.aws.amazon.com/eks/latest/userguide/iam-roles-for-service-accounts.html
- VPC CNI: https://docs.aws.amazon.com/eks/latest/userguide/managing-vpc-cni.html
- Security groups for pods: https://docs.aws.amazon.com/eks/latest/userguide/security-groups-for-pods.html
- Cluster autoscaler: https://docs.aws.amazon.com/eks/latest/userguide/autoscaling.html
- Karpenter on EKS: https://docs.aws.amazon.com/eks/latest/userguide/karpenter.html
- EBS CSI driver: https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html
- EFS CSI driver: https://docs.aws.amazon.com/eks/latest/userguide/efs-csi.html
- Load balancer controller: https://docs.aws.amazon.com/eks/latest/userguide/aws-load-balancer-controller.html
- CoreDNS: https://docs.aws.amazon.com/eks/latest/userguide/managing-coredns.html
- kube-proxy: https://docs.aws.amazon.com/eks/latest/userguide/managing-kube-proxy.html
- EKS troubleshooting: https://docs.aws.amazon.com/eks/latest/userguide/troubleshooting.html
- EKS security: https://docs.aws.amazon.com/eks/latest/userguide/security.html
- EKS networking: https://docs.aws.amazon.com/eks/latest/userguide/eks-networking.html
- EKS storage: https://docs.aws.amazon.com/eks/latest/userguide/storage.html

### EKS Best Practices Guide
- Home: https://aws.github.io/aws-eks-best-practices/
- Security: https://aws.github.io/aws-eks-best-practices/security/docs/
- Networking: https://aws.github.io/aws-eks-best-practices/networking/index/
- Reliability: https://aws.github.io/aws-eks-best-practices/reliability/docs/
- Performance: https://aws.github.io/aws-eks-best-practices/scalability/docs/
- Cost optimization: https://aws.github.io/aws-eks-best-practices/cost_optimization/docs/
- Cluster upgrades: https://aws.github.io/aws-eks-best-practices/upgrades/
- Windows: https://aws.github.io/aws-eks-best-practices/windows/docs/

### Official GitHub Repos — EKS Core
- EKS Distro: https://github.com/aws/eks-distro
- EKS Anywhere: https://github.com/aws/eks-anywhere
- EKS Charts (Helm): https://github.com/aws/eks-charts
- EKS Best Practices Guide: https://github.com/aws/aws-eks-best-practices
- EKS User Guide (source): https://github.com/awsdocs/amazon-eks-user-guide
- EKS News: https://github.com/aws/eks-news
- EKS Connector: https://github.com/aws/amazon-eks-connector
- EKS Pod Identity Agent: https://github.com/aws/eks-pod-identity-agent
- EKS Pod Identity Webhook: https://github.com/aws/amazon-eks-pod-identity-webhook

### Official GitHub Repos — Networking
- VPC CNI: https://github.com/aws/amazon-vpc-cni-k8s
- AWS Load Balancer Controller: https://github.com/kubernetes-sigs/aws-load-balancer-controller

### Official GitHub Repos — Storage
- EBS CSI Driver: https://github.com/kubernetes-sigs/aws-ebs-csi-driver
- EFS CSI Driver: https://github.com/kubernetes-sigs/aws-efs-csi-driver
- S3 Mountpoint CSI Driver: https://github.com/awslabs/mountpoint-s3-csi-driver
- Secrets Store CSI Provider: https://github.com/aws/secrets-store-csi-driver-provider-aws

### Official GitHub Repos — Autoscaling
- Karpenter (AWS provider): https://github.com/aws/karpenter-provider-aws
- Karpenter (core): https://github.com/kubernetes-sigs/karpenter
- Cluster Autoscaler: https://github.com/kubernetes/autoscaler

### Official GitHub Repos — Observability
- CloudWatch Agent: https://github.com/aws/amazon-cloudwatch-agent
- ADOT Collector: https://github.com/aws-observability/aws-otel-collector
- Fluent Bit for EKS: https://github.com/aws/aws-for-fluent-bit

### GitHub API — Latest Releases (JSON, no convert_to_markdown)
- VPC CNI: https://api.github.com/repos/aws/amazon-vpc-cni-k8s/releases/latest
- Karpenter: https://api.github.com/repos/aws/karpenter-provider-aws/releases/latest
- Cluster Autoscaler: https://api.github.com/repos/kubernetes/autoscaler/releases/latest
- EBS CSI Driver: https://api.github.com/repos/kubernetes-sigs/aws-ebs-csi-driver/releases/latest
- EFS CSI Driver: https://api.github.com/repos/kubernetes-sigs/aws-efs-csi-driver/releases/latest
- CoreDNS: https://api.github.com/repos/coredns/coredns/releases/latest
- AWS LB Controller: https://api.github.com/repos/kubernetes-sigs/aws-load-balancer-controller/releases/latest
- EKS Anywhere: https://api.github.com/repos/aws/eks-anywhere/releases/latest
- Metrics Server: https://api.github.com/repos/kubernetes-sigs/metrics-server/releases/latest
- cert-manager: https://api.github.com/repos/cert-manager/cert-manager/releases/latest
- Velero: https://api.github.com/repos/vmware-tanzu/velero/releases/latest
- ExternalDNS: https://api.github.com/repos/kubernetes-sigs/external-dns/releases/latest

### Kubernetes Core
- K8s versions: https://kubernetes.io/releases/
- K8s deprecation guide: https://kubernetes.io/docs/reference/using-api/deprecation-guide/
- K8s changelog: https://github.com/kubernetes/kubernetes/blob/master/CHANGELOG/README.md
- Pod Security Standards: https://kubernetes.io/docs/concepts/security/pod-security-standards/
- Network Policies: https://kubernetes.io/docs/concepts/services-networking/network-policies/
- Resource management: https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/
- PDB: https://kubernetes.io/docs/concepts/workloads/pods/disruptions/
- HPA: https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/
- Topology spread: https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/
- Probes: https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/

### Common EKS Add-ons Documentation
- CoreDNS: https://docs.aws.amazon.com/eks/latest/userguide/managing-coredns.html
- kube-proxy: https://docs.aws.amazon.com/eks/latest/userguide/managing-kube-proxy.html
- VPC CNI: https://docs.aws.amazon.com/eks/latest/userguide/managing-vpc-cni.html
- EBS CSI: https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html
- EFS CSI: https://docs.aws.amazon.com/eks/latest/userguide/efs-csi.html
- S3 Mountpoint CSI: https://docs.aws.amazon.com/eks/latest/userguide/s3-csi.html
- Pod Identity Agent: https://docs.aws.amazon.com/eks/latest/userguide/pod-id-agent-setup.html
- CloudWatch Observability: https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Container-Insights-setup-EKS-addon.html
- ADOT: https://docs.aws.amazon.com/eks/latest/userguide/opentelemetry.html
- Metrics Server: https://docs.aws.amazon.com/eks/latest/userguide/metrics-server.html
- AWS LB Controller: https://docs.aws.amazon.com/eks/latest/userguide/aws-load-balancer-controller.html
- Network Flow Monitor: https://docs.aws.amazon.com/eks/latest/userguide/network-flow.html

### Common Third-Party Add-ons
- cert-manager: https://cert-manager.io/docs/
- Velero (backup): https://velero.io/docs/
- ExternalDNS: https://kubernetes-sigs.github.io/external-dns/
- Istio: https://istio.io/latest/docs/
- ArgoCD: https://argo-cd.readthedocs.io/en/stable/
- Flux: https://fluxcd.io/docs/
- OPA Gatekeeper: https://open-policy-agent.github.io/gatekeeper/website/docs/
