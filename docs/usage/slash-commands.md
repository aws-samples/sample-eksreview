# Slash Commands

Most of what you do in eksreview can be driven with plain language, but slash commands give you direct, precise control over a specific action. Type `/help` inside the app at any time to see them. The table below lists every command and what it does, followed by notes on the few with behavior worth knowing.

| Command | Purpose |
|---|---|
| `/help` | Show all commands |
| `/upgrade <cluster> [region] [to <version>]` | Run upgrade readiness assessment |
| `/fix <description>` | Guided remediation for a finding (requires a review first) |
| `/investigate <description>` | Root cause analysis (loads the latest report if none in session) |
| `/export [path]` | Export findings to a JIRA-importable CSV |
| `/model [name]` | Show models, or switch (e.g. `/model sonnet`) |
| `/context` | Show context-window usage and approximate session cost |
| `/tools` | List all loaded tools and their status |
| `/knowledge show` | List knowledge base entries |
| `/knowledge add <name> <path>` | Index local files or a PDF |
| `/knowledge search <query>` | Search the knowledge base directly |
| `/knowledge remove <name>` | Remove an entry |
| `/knowledge update <name>` | Re-index an entry from its source |
| `/knowledge clear` | Remove all knowledge base entries |
| `/skill list` | List loaded review skills |
| `/skill add <name> <path>` | Add a custom skill from a path |
| `/skill remove <name>` | Remove a custom skill |
| `/skill info <name>` | Show details for a skill |
| `/exit` (or `/quit`) | End the session |

Notes on specific commands:

- **`/fix`** needs a review to have run in the current session. It pulls findings from the saved report instead of re-scanning, handles one fix at a time, and confirms before it runs anything.
- **`/investigate`** works even without a review in the session. If none has run, it loads the most recent assessment report from `reports/` and investigates against that.
- **`/upgrade`** takes the cluster name plus an optional region and target version, in any order, e.g. `/upgrade eks-prod us-east-1 to 1.31`. Omit the version and it auto-detects the next minor version.
- **`/export`** uses the last report from the session, or a path you give it. With neither, it lists recent reports.
