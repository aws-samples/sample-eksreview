# Command-line Flags

Pass flags through the launcher (`./eksreview <flags>`) or directly (`python main.py <flags>`):

| Flag | Description |
|---|---|
| `--session <id>` | Resume a previous session by ID (sessions are stored in `.sessions/`) |
| `--no-shell` | Read-only mode: removes the shell tool so the agent can never execute commands. `/fix` and `/investigate` still suggest commands and example manifests |

---

**Related:** [Environment Variables](environment-variables.md) · [Safety Model](../reference/safety.md)
