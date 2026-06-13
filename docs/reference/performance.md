# Performance & Scale

A full review typically completes in a few minutes. The six domain checks run sequentially, then a sub-agent compiles the report. Most of the wall-clock time is the report compilation, not the checks themselves.

Review time scales with **how much is deployed**, not cluster node count alone. Domains like security and resiliency inspect every workload, so a cluster with thousands of pods takes longer and produces a larger report than a small one. For very large or busy clusters, two safety valves apply automatically:

- `MCP_RATE_LIMIT_SOFT` (default 200) warns when a session crosses that many MCP calls.
- `MCP_RATE_LIMIT_HARD` (default 500) stops further MCP calls in a session.

If you hit these on a large cluster, run one domain at a time or raise the limits via environment variables.

---

**Related:** [Cost](cost.md) · [Environment Variables](../configuration/environment-variables.md)
