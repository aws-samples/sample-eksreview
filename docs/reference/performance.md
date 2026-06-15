# Performance

A full review typically finishes in a few minutes. The six domain checks run sequentially, then a sub-agent compiles the report. Most of the wall-clock time goes to compiling the report, not running the checks.

Review time scales with **how many resources are deployed**, not node count alone. Domains like security and resiliency inspect every workload, so a cluster with thousands of pods takes longer and produces a larger report than a small one.

As a safeguard, eksreview caps how many tool calls it will make in a single session, which protects against runaway loops and unexpected API or model costs. Normal use stays well under it: a full review is only a handful of calls, and even dozens of reviews back to back in one sitting won't reach the limit. In the rare case you do hit it, start a new session and the count resets.
