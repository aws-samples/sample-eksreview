---
name: eks-upgrade-readiness
description: Compile a comprehensive EKS upgrade readiness report from compacted MCP check results. Verifies component compatibility and generates an ordered upgrade plan.
allowed-tools: save_report think shell http_request knowledge_search file_write file_read
---

# EKS Upgrade Readiness Skill

## Reading Check Data

Results are provided as compacted JSON with short keys:
- `v`: version transition (e.g., "1.30->1.31")
- `blockers`: count of critical non-compliant checks
- `warnings`: count of non-critical non-compliant checks
- `passed`: list of "ID:Check Name" strings (e.g., "U1:Cluster Version and Support Status")
- `failed`: list of objects with:
  - `id` = check ID (e.g., U1, U5, U19)
  - `n` = check name
  - `s` = severity: C=Critical, H=High, M=Medium, L=Low
  - `d` = details (use as finding description)
  - `r` = impacted resources (list or namespace-grouped dict)
  - `t` = upgrade timing: b=before upgrade, a=after upgrade

When `r` is a dict like `{"scale-test":["app-1","app-2","... and 38 more"]}`,
the total count is 3 shown + 38 more = 41 resources.

## Workflow

1. `think` — First analysis pass:
   - Parse the compacted check results
   - Identify blockers (severity C that are non-compliant)
   - Correlate related findings (e.g., AL2 AMI + kubelet skew = same node group)
   - What data is missing from the MCP output for remediation?

2. `http_request` — Verify component and addon compatibility.

   **EKS Managed Addons (U5):** The MCP tool calls `describe-addon-versions` for the target
   K8s version and returns: current addon version, whether it's compatible with the target,
   the default version for the target, and the latest compatible version. Use this data
   directly in the report — no additional lookups needed for managed addons.

   The report MUST include concrete version numbers. Example:
   > vpc-cni: v1.16.0 (current) → v1.19.2-eksbuild.1 (default for K8s 1.32, per U5)
   Never write `<VERSION>` placeholders — always use the real versions from MCP output.

   **Self-managed addons (U6):** The MCP tool returns detected versions from container images
   (e.g., `coredns:1.10.1`, `kube-proxy:1.29.0`). These are NOT managed by the EKS addon
   system, so the MCP cannot check compatibility. Use `http_request` to verify:
   - CoreDNS: `http_request(url="https://github.com/coredns/coredns/releases", convert_to_markdown=true)`
   - kube-proxy: version must match the K8s control plane version (1:1 mapping)
   - VPC CNI: `http_request(url="https://docs.aws.amazon.com/eks/latest/userguide/managing-vpc-cni.html", convert_to_markdown=true)`
   State clearly: "Self-managed <addon> v<current> needs update to v<target> for K8s <version>".

   **Third-party components (U19/U20):** For each component detected by the MCP tool,
   fetch the official compatibility matrix:
   - Karpenter: `http_request(url="https://karpenter.sh/docs/upgrading/compatibility/", convert_to_markdown=true)`
   - AWS LB Controller: `http_request(url="https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/deploy/installation/", convert_to_markdown=true)`
   - Istio: `http_request(url="https://istio.io/latest/docs/releases/supported-releases/", convert_to_markdown=true)`
   Compare the detected version against the compatibility matrix for the target K8s version.
   State clearly: "Component X vY.Z is/is not compatible with K8s <target>".

3. `shell` — Fill gaps ONLY. Run targeted read-only commands for specific missing data.
   See "When to Run Shell Commands" below.

4. `think` — Second analysis pass:
   - Correlate MCP data + http_request results + shell output
   - Determine the correct upgrade step ORDER (dependencies matter:
     fix blockers -> control plane -> nodes -> addons -> components)
   - For each blocker, synthesize remediation from the data
   - Identify any findings that affect the same resources (group them)

5. Write the report following the Report Format below.

6. `save_report` — ALWAYS save with report_type="upgrade-readiness".
   This produces: `<cluster>-upgrade-readiness-<timestamp>.md`

## Data Rules

Every fact in the report must have a source. No exceptions.

| Data type | Allowed source |
|-----------|---------------|
| Resource names, IPs, versions | MCP tool output or shell command you ran |
| Addon compatible versions | MCP tool output (from describe_addon_versions for target K8s) |
| Self-managed addon compatibility | `http_request` to official docs/releases |
| Third-party component compatibility | `http_request` to official compatibility matrix |
| Check pass/fail status | MCP tool output only |
| Remediation commands | Constructed from real data above |
| Best practice context | Knowledge base or your training knowledge |
| Estimated durations | Standard AWS ranges only (control plane: 15-25 min) |

If data is missing: write "Not determined" and provide the command to get it.

### What Good Looks Like vs Bad

BAD (hallucinated version):
> Update vpc-cni to v1.18.3-eksbuild.2

GOOD (from MCP data):
> Update vpc-cni from v1.16.0 (current) to v1.18.0-eksbuild.1 (default for K8s 1.33,
> per check U5 output)

BAD (invented resource):
> Node ip-10-0-3-142 has kubelet 1.30

GOOD (from MCP data):
> 2 nodes have kubelet version skew (per check U8): ip-10-0-1-45 (v1.31.0),
> ip-10-0-2-67 (v1.31.0)

BAD (assumed pass without data):
> All Helm releases use current API versions.

GOOD (check errored):
> U25 CHECK ERROR: Failed to decode Helm secrets. Run manually:
> `helm list -A -o json` to verify chart API versions.

## When to Run Shell Commands

The shell tool prompts the user to confirm each command before it executes — you do not need to call a separate confirmation step.

Run shell commands ONLY when ALL of these are true:
1. A check is non-passing (blocker or warning)
2. The MCP output lacks a specific detail needed for remediation
3. The command is read-only (get, describe, list, version, top)

Acceptable examples:
- `kubectl get nodes -o wide` — when U8 flags skew but MCP didn't include node IPs
- `aws eks describe-addon-versions --addon-name coredns --kubernetes-version 1.33` — when U5 didn't return compatible versions
- `helm list -A -o json` — when U25 found deprecated APIs but release names are unclear
- `kubectl get pdb -A -o wide` — when U12 flags missing PDBs and you need selector details

NEVER run:
- Broad discovery (`kubectl get all -A`, `kubectl get pods -A`)
- Write operations (`kubectl apply`, `kubectl patch`, `aws eks update-*`)
- Individual MCP review tools (`check_eks_security`, `check_eks_resiliency`)

## Error Handling

- If `check_eks_upgrade_readiness` fails entirely: report the error, suggest the user
  check cluster connectivity and IAM permissions. Do NOT fall back to individual checks.
- If individual checks within the result have errors: report as "CHECK ERROR" with the
  error message. Do NOT mark as PASS or FAIL.

## Verdict Logic

Mechanical — do not override with judgment:
- 0 blockers (Critical-severity + timing=before) = **READY** (or READY WITH WARNINGS if warnings exist)
- 1+ blockers = **NOT READY** — list what must be fixed
- Critical + timing=after = urgent post-upgrade action, NOT a blocker (e.g., kube-proxy update)
- High-severity findings are strong warnings, not blockers — they won't break the upgrade
  but should be addressed for zero-downtime

## Upgrade Plan Rules

- ONLY include steps for components that exist (detected by MCP tool)
- If Karpenter not detected: no Karpenter step
- If no self-managed nodes: no self-managed node step
- If no Helm releases: no Helm upgrade step
- Use real version numbers from MCP output in commands
- Mark any command with user-specific values as: `# TEMPLATE — replace <VALUE> before running`

## Report Format

Terminal output: PLAIN TEXT. No markdown tables, bold, or headers. Code blocks OK.
Saved report: FULL MARKDOWN with tables, code blocks, headers.


````markdown
# EKS Upgrade Readiness Report

## Cluster: <cluster-name>

| Field | Value |
|-------|-------|
| **Current Version** | <from MCP: current_version> |
| **Target Version** | <from MCP: target_version> |
| **Assessment Date** | <today's date> |
| **Upgrade Readiness** | <READY / READY WITH WARNINGS / NOT READY> |
| **Checks Run** | <total count> |
| **Passed** | <count> |
| **Blockers** | <count from MCP: blockers> |
| **Warnings** | <count from MCP: warnings> |

---

## Go / No-Go Decision

<One paragraph: readiness rationale based on blocker count and what must be fixed.
 Reference specific check IDs.>

---

## Check Results Summary

| ID | Check | Category | Severity | Status | Timing | Impacted Resources |
|----|-------|----------|----------|--------|--------|--------------------|
| U1 | Cluster Version | Control Plane | High | PASS | — | — |
| U9 | AMI Type | Data Plane | Critical | BLOCKER | Before | <exact names> |
| U12 | PDB Coverage | Workload | High | WARNING | Before | <exact names> |
| ... for ALL 38 checks ... |

---

## Blockers (Must Fix Before Upgrade)

### 1. <Check Name> (<Check ID>)

| Field | Detail |
|-------|--------|
| **Severity** | <from check result> |
| **Category** | <from check definition> |
| **Upgrade Timing** | <from check result: before/after> |

**Impacted Resources:**
- <exact resource name from MCP output>
- <exact resource name from MCP output>

**Finding:** <what the check detected — quote MCP details field>

**Remediation:**
```bash
# <description of what this does>
<exact command using real values from MCP output>
```

**Considerations:** <what could go wrong — pod restarts, service disruption, rollback risk, breaking dependent services>

---

## Warnings (Recommended)

### N. <Check Name> (<Check ID>)

| Field | Detail |
|-------|--------|
| **Severity** | <severity> |
| **Upgrade Timing** | <before/after> |

**Finding:** <what was detected>

**Recommendation:** <what to do and when>

---

## Upgrade Plan

Execute in order after resolving all blockers.

### Step 1: Pre-Upgrade Fixes

| # | Action | Command | Notes |
|---|--------|---------|-------|
| 1a | <from blocker remediation> | `<command>` | <considerations> |

### Step 2: Upgrade Control Plane

```bash
aws eks update-cluster-version \
  --name <cluster-name> \
  --kubernetes-version <target-version> \
  --region <region>
```

Monitor: `aws eks describe-update --name <cluster> --update-id <id>`
Duration: 15-25 minutes. Zero application downtime.

### Step 3: Upgrade Node Groups

| Node Group | Type | Command |
|------------|------|---------|
| <only groups that exist per MCP> | Managed | `aws eks update-nodegroup-version ...` |

### Step 4: Upgrade EKS Addons

Use the exact version numbers from U5 check data. Never use `--resolve-conflicts` without `--addon-version`.

| Addon | Current | Target (for K8s X.Y) | Default | Command |
|-------|---------|----------------------|---------|---------|
| <only addons that need updating per U5> | <from U5: current ver> | <from U5: latest compatible> | <from U5: yes/no> | `aws eks update-addon --cluster-name <cluster> --addon-name <name> --addon-version <target ver from U5> --resolve-conflicts PRESERVE --region <region>` |

### Step 5: Upgrade Self-Managed Components
<only if detected by U6/U18/U19/U20>

Includes self-managed core addons (U6) AND third-party components (U18-U20).
Use real version numbers from MCP output + http_request verification.

| Component | Current Version | Required Version | Source | Command |
|-----------|----------------|-----------------|--------|---------|
| <from MCP> | <from MCP image tag> | <from http_request compatibility check> | <URL checked> | <command> |

### Step 6: Post-Upgrade Verification

```bash
kubectl version
kubectl get nodes -o wide
kubectl get pods -n kube-system
kubectl get pods -A --field-selector status.phase!=Running
```

---

## Passed Checks

| ID | Check | Category | Details |
|----|-------|----------|---------|
| <for each passing check — one row with short details from MCP> |

---

## Appendix: Third-Party Components

| Component | Version | Namespace | Notes |
|-----------|---------|-----------|-------|
| <only if U20 detected components> |

````
