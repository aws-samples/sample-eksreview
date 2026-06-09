---
name: eks-report-compiler
description: Compile an EKS assessment report from raw MCP check results. Used by the report compilation sub-agent.
allowed-tools: save_report knowledge_search file_write file_read
---

# EKS Report Compiler Skill

You are compiling a structured EKS operations review report from raw check results.

## Rules

1. The report must be complete — include every individual check in the summary table
   and every failed check as a detailed finding. Never group multiple checks into a
   single row. Each check gets its own row with its own S.No.
2. For each failed check include:
   - Numbered finding (1, 2, 3...)
   - Check name from the `n` field
   - One-line metadata: **Severity | Category | Remediation Type**
   - One-line impact statement
   - Resources from the `r` field (reconstruct namespace paths from grouped dicts)
   - Current State from the `d` field
   - 2-3 sentence finding explanation
   - Remediation with copy-paste commands
   - Considerations (pod restarts, service disruption, access loss)
3. Group detailed findings by severity (Critical, High, Medium, Low).
4. Never fabricate data. If a check returned an error, write "Not determined — run
   <command> to verify" instead of inventing values.
5. Remediation commands must use real values from the check data. Mark user-specific
   values as: `# TEMPLATE — replace <VALUE> before running`
6. Use full markdown formatting (headers, bold, tables, code blocks).

## Reading Check Data

Results come per domain under `## DOMAIN [PASS/FAIL]` headers.
Use the domain name (SECURITY, RESILIENCY, NETWORKING, KARPENTER, CLUSTER-AUTOSCALER,
OBSERVABILITY) as the Category for each check in the summary table.

Each domain's output is compact JSON:
- `passed`: list of check name strings (compliant — include in summary table as PASS)
- `failed`: list of objects with:
  - `n` = check name
  - `s` = severity: C=Critical, H=High, M=Medium, L=Low
  - `d` = details (use as Current State in findings)
  - `r` = impacted resources (list or namespace-grouped dict)

When `r` is a dict like `{"scale-test":["app-1","app-2","... and 38 more"]}`,
the total count is 3 shown + 38 more = 41 resources. Report as:
"41 resources in scale-test (e.g. app-1, app-2)"

When `r` is a dict like `{"scale-test":["app-1","app-2"],"kube-system":["coredns"]}`,
expand to full paths: scale-test/app-1, scale-test/app-2, kube-system/coredns.

## Summary Table Row Examples

GOOD (failed check with resources):
| 16 | Run the application as a non-root user | Security | High | ❌ FAIL | Manifest | ~120 min | 74 workloads across 8 namespaces |

GOOD (passed check):
| 1 | Leverage EKS Cluster Access Manager | Security | — | ✅ PASS | — | — | — |

BAD (grouped checks):
| 71-81 | Various CA checks | Cluster Autoscaler | — | ✅ PASS | — | — | — |

## What Good Looks Like vs Bad

BAD (summarized group):
> "Multiple resiliency checks failed including probes, PDBs, and anti-affinity."

GOOD (every check listed individually):
> ### 1. Use liveness probes
> **High | Resiliency | Manifest change required**
> **Impact:** Missing liveness probes prevent Kubernetes from detecting and restarting unhealthy containers.
> **Resources:** 70 across 5 namespaces (scale-test: 41, perf-test-ns: 10, app-ns: 8, kube-system: 7, unprotected-ns: 4)

BAD (fabricated resource):
> scale-test/scale-app-42

GOOD (from check data only):
> scale-test: 41 resources (e.g. scale-app-1, scale-app-10, scale-app-11)

## Report Format

````markdown
# EKS Operations Review Report
## Cluster: <cluster-name> (<region>)
**Generated:** <date>
**Review Type:** Comprehensive Best Practices Assessment

---

## Executive Summary

<Brief overview with total issue count and overall compliance assessment>

### Issue Breakdown by Category
- **Security:** N passed, N failed
- **Resiliency:** N passed, N failed
- **Networking:** N passed, N failed
- **Autoscaling (Karpenter/CA):** N passed, N failed
- **Observability:** N passed, N failed

### Priority Classification
- **Critical:** N issues requiring immediate action
- **High:** N issues impacting production readiness
- **Medium:** N issues for operational improvement
- **Low:** N issues for optimization

### Quick Wins (fixable in <30 minutes)
| # | Finding | Fix Type | Effort | Command Preview |
|---|---------|----------|--------|-----------------|
| 1 | <check name> | AWS CLI | ~5 min | `aws eks update-cluster-config ...` |
| 2 | <check name> | Patchable | ~5 min | `kubectl patch ...` |
| ... up to 5 quick wins ... |

### Requires Planning (maintenance window or manifest changes)
- <finding> — <one-line reason why it needs planning>

### Trend Analysis (if previous reports exist)
<Compliance score changes, resolved/new/persistent failures>

---

## Check Results Summary

| S.No | Check Name | Category | Severity | Status | Fix Type | Effort | Impacted Resources |
|------|-----------|----------|----------|--------|----------|--------|--------------------|
| 1 | <check name> | Security | High | ❌ FAIL | AWS CLI | ~5 min | <count> across <N> namespaces |
| 2 | <check name> | Security | Medium | ✅ PASS | — | — | — |
| 3 | <check name> | Resiliency | High | ❌ FAIL | Manifest | ~30 min | 70 across 5 namespaces |
| ... for ALL checks across ALL domains ... |

---

## Critical Issues

### 1. <Check Name>
**High | Security | AWS CLI**
**Impact:** <one-line why this matters>
**Resources:** <resource list or count>
**Current State:** <from d field>

<2-3 sentence finding explanation>

**Remediation:**
```bash
<specific commands to fix>
```
**Considerations:** <what could go wrong — pod restarts, service disruption, access loss.
State "No expected negative impact" if safe.>

### 2. <Next Check>
...

---

## High Priority Issues
<same format as above>

---

## Medium Priority Issues
<same format as above>

---

## Low Priority Issues
<same format as above>

````

## Severity Definitions

- **Critical** — Immediate security risk or availability impact. Fix now.
- **High** — Significant risk. Address within days.
- **Medium** — Best practice deviation. Plan within weeks.
- **Low** — Optimization opportunity. Address when convenient.
