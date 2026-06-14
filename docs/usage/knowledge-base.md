# Knowledge Base

eksreview keeps a local, SQLite-backed knowledge base with BM25 keyword search. On first launch it auto-syncs the official **EKS Best Practices Guide** PDF (about 1,400 searchable chunks), then re-checks for updates on later launches.

You can index your own content:

```
/knowledge add my-runbooks ~/docs/runbooks
/knowledge add eks-pdf ~/Downloads/eks-best-practices.pdf
/knowledge search pod security standards
```

The agent searches the knowledge base automatically when you ask best-practice or "how/why" questions, and cites it in the answer. Supported file types include Markdown, text, YAML, source files, and PDFs (PDF text extraction uses `pdfminer.six`).

To confirm the knowledge base is working, run `/knowledge show` (lists entries and chunk counts) and `/knowledge search <query>` (returns ranked matches with scores).

---

**Related:** [Slash Commands](slash-commands.md) · [Example Prompts](example-prompts.md)
