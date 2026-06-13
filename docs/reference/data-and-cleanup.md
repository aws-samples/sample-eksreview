# Data & Cleanup

eksreview writes only to three local directories (owner-only on POSIX):

| Path | Contents |
|---|---|
| `reports/` | Generated Markdown reports |
| `.knowledge/` | The SQLite knowledge base (EKS Best Practices Guide + anything you indexed) |
| `.sessions/` | Conversation session state for `--session` resume |

Nothing is uploaded anywhere. To remove all local state:

```bash
rm -rf reports/ .knowledge/ .sessions/
```

---

**Related:** [Safety Model](safety.md) · [Environment Variables](../configuration/environment-variables.md)
