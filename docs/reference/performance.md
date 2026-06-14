# Performance & Scale

A full review typically finishes in a few minutes. The six domain checks run sequentially, then a sub-agent compiles the report. Most of the wall-clock time goes to compiling the report, not running the checks.

Review time scales with **how much is deployed**, not node count alone. Domains like security and resiliency inspect every workload, so a cluster with thousands of pods takes longer and produces a larger report than a small one.

For very large or busy clusters, the agent applies automatic safety limits so a single session can't make an unbounded number of API calls. If a review stops early on a big cluster, run one domain at a time and the checks will complete in smaller passes.

---

**Related:** [Cost](cost.md) · [Environment Variables](../configuration/environment-variables.md)
