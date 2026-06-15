# Local Data

eksreview writes to three local directories only (owner-only on POSIX):

| Path | Contents |
|---|---|
| `reports/` | Generated Markdown reports |
| `.knowledge/` | The SQLite knowledge base (EKS Best Practices Guide + anything you indexed) |
| `.sessions/` | Conversation session state for `--session` resume |

Nothing is uploaded anywhere. To remove all local state:

```bash
rm -rf reports/ .knowledge/ .sessions/
```

Clearing these directories is safe at any time. The next run recreates them and re-syncs the EKS Best Practices Guide into a fresh knowledge base.
