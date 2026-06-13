# Reports

Each review writes a Markdown report to `reports/`:

```
reports/
├── eks-prod-assessment-20260608_120312.md
├── eks-prod-upgrade-readiness-20260608_131045.md
└── eks-staging-assessment-20260607_154420.md
```

Every report includes:

- An **executive summary** with the compliance score and a per-category breakdown
- **Quick wins:** fixes achievable in under 30 minutes
- A **findings table** with every check, its severity, status, and impacted resources
- **Per-finding detail:** impact, current state, and exact remediation commands
- **Trend analysis** against previous reviews of the same cluster

Reports are written to `reports/` as plain Markdown, so they are easy to share with your team or attach to tickets. They do contain cluster configuration details and IAM ARNs, so review them before sharing externally (see [Handling sensitive information](../reference/safety.md)).

## See a sample before you install

Want to judge the output quality first? Two real (sanitized) reports are included in the docs:

- [Assessment Report](../examples/sample-assessment-report.md): a full best-practice review with executive summary, findings table, per-finding remediation, and trend analysis.
- [Upgrade Readiness Report](../examples/sample-upgrade-readiness-report.md): a multi-hop (1.30 to 1.33) upgrade readiness assessment with a go or no-go verdict and ordered upgrade plan.

---

**Related:** [Slash Commands](slash-commands.md) · [Export to JIRA](../configuration/cli-flags.md) · [Safety Model](../reference/safety.md)
