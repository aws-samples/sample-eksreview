# EKS Upgrade Readiness Report
## Cluster: eks-prod (us-west-2)
**Generated:** 2026-05-23
**Target Version:** 1.32

---

| Field | Value |
|-------|-------|
| Cluster | eks-prod |
| Current Version | 1.30 |
| Target Version | 1.32 |
| Verdict | NO-GO |
| Blockers | 2 |
| Warnings | 3 |

---

## Go / No-Go Decision

**Verdict: NO-GO**

Two critical blockers must be resolved before upgrading. The metrics-server addon is in a degraded state, and Karpenter v0.30 is incompatible with K8s 1.32. Both must be addressed before the cluster can safely move to 1.32.

After fixing the blockers, schedule the upgrade during a maintenance window. The three warnings (deprecated APIs, AMI age, observability gaps) can be addressed after the upgrade.

---

## Check Results Summary

| ID | Check | Category | Severity | Status | Timing | Resources |
|----|-------|----------|----------|--------|--------|-----------|
| U1 | Cluster Version and Support Status | Control Plane | Critical | ✅ PASS | Before | — |
| U7 | Addon Health Issues | Addons | Critical | ❌ BLOCKER | Before | metrics-server |
| U19 | Karpenter Compatibility | Workloads | High | ⚠️ WARNING | Before | karpenter v0.30 |
| U20 | AWS LB Controller Compatibility | Workloads | High | ✅ PASS | Before | — |
| U25 | Deprecated APIs in Helm Releases | Workloads | Critical | ❌ BLOCKER | Before | 3 helm releases |

---

## Detailed Findings

### 1. Addon Health Issues (U7)
**Severity:** Critical
**Category:** Addons

The metrics-server addon is reporting status DEGRADED. Upgrading EKS while a managed addon is in this state is unsupported.

**Remediation:**
```bash
aws eks describe-addon --cluster-name eks-prod --addon-name metrics-server --region us-west-2
aws eks update-addon --cluster-name eks-prod --addon-name metrics-server --region us-west-2
```

### 2. Karpenter Compatibility (U19)
**Severity:** High
**Category:** Workloads

Karpenter v0.30 is not compatible with K8s 1.32. Upgrade to Karpenter v0.32 first.

**Remediation:**
```bash
helm upgrade karpenter oci://public.ecr.aws/karpenter/karpenter --version 0.32.0 -n karpenter
```
