# Command-line Flags

eksreview takes a couple of optional command-line flags. Pass them through the launcher (`./eksreview <flags>`) or directly to `python main.py <flags>`.

| Flag | Description |
|---|---|
| `--session <id>` | Resume a previous session by ID (sessions are stored in `.sessions/`) |
| `--no-shell` | Read-only mode: removes the shell tool so the agent can never execute commands. `/fix` and `/investigate` still suggest commands and example manifests |

With no flags, eksreview starts a fresh interactive session in its default read-write mode, where remediations run only after you confirm each command.

---

**Related:** [Environment Variables](environment-variables.md) · [Safety Model](../reference/safety.md)
