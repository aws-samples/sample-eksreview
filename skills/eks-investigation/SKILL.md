---
name: eks-investigation
description: Deep investigation of EKS review findings. Performs root cause analysis by researching best practices, gathering live diagnostic evidence, and correlating with workload patterns.
allowed-tools: think knowledge_search http_request shell file_read report_search
---

# EKS Investigation Skill

You are performing a deep investigation of a specific EKS finding.
Follow this procedure exactly, step by step.

## Investigation Procedure

### Step 1: IDENTIFY
Use `report_search` to locate the specific finding from the saved review report.
Note the check name, risk level, impacted resources, and what was flagged.
If no report exists, tell the user to run a review first.

### Step 2: RESEARCH
Before running any commands, search for context:
- Use `knowledge_search` to find best practices related to this finding
  (e.g., search for "VPC CNI prefix delegation" or "pod security standards")
- If the knowledge base has relevant content, read it carefully
- Use `http_request` to fetch the latest AWS documentation if needed
- Use `think` to summarize what the best practice recommends

### Step 3: GATHER EVIDENCE
Run read-only diagnostic commands to understand the current state.
Choose commands relevant to the finding type. Examples:
- Networking: kubectl get svc -A --field-selector spec.type=LoadBalancer, kubectl describe node <node-name>
- Security: kubectl get clusterrolebindings -o wide, kubectl auth can-i --list, kubectl get networkpolicy -A
- Resiliency: kubectl get pdb -A -o wide, kubectl top nodes, kubectl get hpa -A
- Autoscaling: kubectl describe deployment cluster-autoscaler -n kube-system

Run 3-5 targeted commands. Do NOT run broad discovery commands (kubectl get all -A, kubectl get pods -A).
Each command should answer a specific question about the finding.

### Step 4: CORRELATE
Use `think` to connect the evidence:
- Compare what the cluster does vs what best practices recommend (from step 2)
- Identify patterns — is this affecting specific workloads or the whole cluster?
- Check for cascading effects — does this finding cause or worsen other findings?

### Step 5: ASSESS IMPACT
Determine if this is a real risk for this specific cluster:
- FALSE POSITIVE: the finding is technically correct but doesn't apply
  (e.g., "no HPA" on a batch job that shouldn't autoscale)
- LOW RISK: the finding is valid but the blast radius is small
- HIGH RISK: the finding is valid and affects critical workloads
- CRITICAL: the finding is actively causing issues right now

### Step 6: ROOT CAUSE
Identify the underlying reason this finding exists:
- Configuration gap (never set up)
- Drift (was configured but changed)
- Design choice (intentional tradeoff)
- Version issue (outdated component)

### Step 7: REPORT
Present the investigation results in plain text:

  Finding: <check name>

  Best Practice Reference:
    <what the knowledge base / AWS docs recommend>

  Evidence Gathered:
    <summary of each diagnostic command and what it showed>

  Analysis:
    <correlation between evidence and best practices>

  Impact Assessment: <FALSE POSITIVE / LOW / HIGH / CRITICAL>
    <why this assessment>

  Root Cause:
    <the underlying reason>

  Recommendation:
    <specific, actionable next steps>
    <include whether /fix can help or if manual changes are needed>

## Rules
- Shell commands are for evidence gathering only — read-only (get, describe, list, top, logs, auth can-i)
- The shell tool prompts the user to confirm each command before it runs — do not ask for confirmation separately.
- Never run modification commands (apply, patch, delete, scale, edit, create, replace, set)
- Use `report_search` to find the specific finding from the saved report.
  Only use `file_read` if report_search doesn't return what you need.
  Do NOT re-run MCP review tools.
- Do NOT skip the knowledge_search step — always check for best practices first
- Use `think` between steps to plan the next diagnostic
- Use PLAIN TEXT output — no markdown tables or bold

## Example Output

  Finding: Run the application as a non-root user

  Best Practice Reference:
    EKS Security Best Practices recommends setting runAsNonRoot: true
    and runAsUser: 1000 in the pod securityContext. This prevents
    container breakout from escalating to root on the host.

  Evidence Gathered:
    kubectl get pods -n app-ns -o jsonpath='{.items[*].spec.securityContext}'
    → 9 pods have no securityContext set (empty or missing)
    kubectl get psp (deprecated) → no PodSecurityPolicies found
    kubectl get ns app-ns --show-labels → no PSA labels configured

  Analysis:
    The namespace has no Pod Security Admission enforcement, and
    deployments were created without securityContext. This is a
    configuration gap, not drift — it was never set up.

  Impact Assessment: HIGH
    74 workloads across 8 namespaces. A container escape in any of
    these would grant root access to the node.

  Root Cause: Configuration gap — securityContext never configured.

  Recommendation:
    Use /fix to add runAsNonRoot: true to application deployments.
    For kube-system DaemonSets (aws-node, ebs-csi-node), these
    legitimately require root — do not modify.
