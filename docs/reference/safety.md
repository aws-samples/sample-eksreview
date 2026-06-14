# Safety Model

eksreview is designed to be safe to point at production clusters:

- **No changes without your confirmation.** The agent freely runs read-only commands (`get`, `describe`, `list`). Any write or mutate operation requires an explicit `y` at a confirmation prompt before it runs.
- **`--no-shell` for a hard read-only guarantee.** Removes the shell tool entirely, so the agent cannot execute any command. `/fix` and `/investigate` still suggest commands and manifests for you to run yourself.
- **Hard block on destructive commands.** A guard at the execution boundary blocks high-blast-radius commands (cluster/nodegroup deletion, `terminate-instances`, `kubectl delete namespace/node`, `kubectl drain`, `helm uninstall`, `rm -rf /`, `drop database`, etc.) even if you confirm them.
- **Prompt-injection tripwire** on free-text `/fix`, `/investigate`, and `/upgrade` input.

These are layered defenses, not a substitute for least-privilege IAM and Kubernetes RBAC.

## Handling sensitive information

- **Reports contain security posture.** Generated reports include IAM ARNs, cluster configuration, and the list of failing security controls. They're written to `reports/` with owner-only permissions on POSIX systems. Treat them as sensitive, and review them before sharing or attaching to tickets.
- **Don't paste secrets into prompts.** Anything you type goes to the model. Don't paste credentials, tokens, or Kubernetes Secret values into your requests.
- **The agent does not read Kubernetes Secret values** as part of a review. Diagnostics use read-only metadata commands; avoid asking it to dump secret contents.
- **Use AWS Secrets Manager / Parameter Store or IRSA / EKS Pod Identity** for application credentials rather than storing them in manifests the agent might surface.
- **The knowledge base is local.** Anything you index with `/knowledge add` is stored in `.knowledge/` on your machine; nothing is uploaded.

---

**Related:** [Permissions](permissions.md) · [Command-line Flags](../configuration/cli-flags.md)
