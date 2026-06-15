# Permissions

eksreview needs three things: the **AWS APIs** (EKS, EC2, IAM, STS), the **Kubernetes API** of each cluster, and **Amazon Bedrock** for the model. It runs read-only by default, and every change goes through a confirmation prompt, so most users only need the read-only IAM policy below.

## Read-only IAM policy (default, recommended)

This is all you need for reviews, upgrade-readiness checks, and `/investigate`. Attach it to the IAM role or user eksreview runs as.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EKSReviewReadOnly",
      "Effect": "Allow",
      "Action": [
        "eks:DescribeCluster",
        "eks:ListClusters",
        "eks:DescribeClusterVersions",
        "eks:ListAddons",
        "eks:DescribeAddon",
        "eks:DescribeAddonVersions",
        "eks:ListNodegroups",
        "eks:DescribeNodegroup",
        "eks:ListInsights",
        "eks:DescribeInsight",
        "eks:ListAccessEntries",
        "ec2:DescribeInstances",
        "ec2:DescribeSubnets",
        "ec2:DescribeRouteTables",
        "ec2:DescribeLaunchTemplateVersions",
        "autoscaling:DescribeAutoScalingGroups",
        "cloudwatch:GetMetricStatistics",
        "servicequotas:GetServiceQuota",
        "iam:ListRolePolicies",
        "iam:GetRolePolicy",
        "iam:ListAttachedRolePolicies",
        "iam:GetPolicy",
        "iam:GetPolicyVersion",
        "sts:GetCallerIdentity"
      ],
      "Resource": "*"
    },
    {
      "Sid": "Bedrock",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
        "arn:aws:bedrock:*:*:inference-profile/*"
      ]
    }
  ]
}
```

A few of these actions support specific check domains: `autoscaling:DescribeAutoScalingGroups` powers the Cluster Autoscaler checks, `cloudwatch:GetMetricStatistics` powers the observability checks, and `servicequotas:GetServiceQuota` powers the service-limit checks in upgrade readiness. The Bedrock statement grants both the foundation models and the cross-region **inference profiles** the agent invokes by default (the `global.`/`us.` profiles route through `inference-profile/*`).

## Write permissions (only for `/fix`)

eksreview never writes to AWS or your cluster unless you run `/fix` and confirm a command. The read-only setup above is enough for everything else.

If you intend to apply remediations, the principal needs **elevated permissions** scoped to what you actually fix: elevated IAM actions for AWS-side changes (e.g. `eks:UpdateClusterConfig`) and/or edit-level Kubernetes RBAC for manifest changes.

## Cluster access (Kubernetes RBAC)

IAM gets eksreview to the AWS APIs, but reading pods, deployments, RBAC, and other in-cluster objects requires your IAM principal to be **mapped access to your cluster**. Without this, the AWS calls succeed but the Kubernetes checks fail.

If you review **multiple clusters**, the same IAM principal must be granted this read access (an access entry with `AmazonEKSAdminViewPolicy`, or the equivalent `aws-auth` mapping) in **each** cluster you want to review. The mapping is per-cluster, so a principal mapped only in cluster A can describe resources in A but its Kubernetes checks will fail against cluster B until it's mapped there. Repeat the steps below for every cluster.

Map your principal using an **EKS access entry** (recommended) or the legacy `aws-auth` ConfigMap:

```bash
# EKS access entry (recommended): grant the IAM role cluster read access
aws eks create-access-entry \
  --cluster-name my-cluster \
  --region us-east-1 \
  --principal-arn arn:aws:iam::111122223333:role/eksreview-readonly

aws eks associate-access-policy \
  --cluster-name my-cluster \
  --region us-east-1 \
  --principal-arn arn:aws:iam::111122223333:role/eksreview-readonly \
  --access-scope type=cluster \
  --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSAdminViewPolicy
```

The review reads pods, deployments, statefulsets, daemonsets, namespaces, RBAC (roles and bindings), network policies, storage classes, PDBs, and HPAs. Use **`AmazonEKSAdminViewPolicy`** rather than `AmazonEKSViewPolicy`: the standard view policy maps to the Kubernetes `view` role, which by design excludes RBAC resources (roles and rolebindings), so the security checks that inspect RBAC would fail under it. `AmazonEKSAdminViewPolicy` grants read access to those resources without granting write access or Secret values. For `/fix` operations that apply manifests, use a role mapped with edit/admin scope instead.

Verify your mapping with:

```bash
kubectl auth can-i list pods --all-namespaces
```

For running Bedrock and EKS in different accounts, see [Credentials & Cross-Account](../configuration/credentials.md).
