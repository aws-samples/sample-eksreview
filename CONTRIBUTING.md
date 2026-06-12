# Contributing Guidelines

Thank you for your interest in contributing to our project. Whether it's a bug report, new feature, correction, or additional
documentation, we greatly value feedback and contributions from our community.

Please read through this document before submitting any issues or pull requests to ensure we have all the necessary
information to effectively respond to your bug report or contribution.

## Reporting Bugs/Feature Requests

We welcome you to use the GitHub issue tracker to report bugs or suggest features.

When filing an issue, please check existing open, or recently closed, issues to make sure somebody else hasn't already
reported the issue. Please try to include as much information as you can. Details like these are incredibly useful:

* A reproducible test case or series of steps
* The version of our code being used
* Any modifications you've made relevant to the bug
* Anything unusual about your environment or deployment

## Contributing via Pull Requests

Contributions via pull requests are much appreciated. Before sending us a pull request, please ensure that:

1. You are working against the latest source on the *main* branch.
2. You check existing open, and recently merged, pull requests to make sure someone else hasn't addressed the problem already.
3. You open an issue to discuss any significant work - we would hate for your time to be wasted.

To send us a pull request, please:

1. Fork the repository.
2. Modify the source; please focus on the specific change you are contributing. If you also reformat all the code, it will be hard for us to focus on your change.
3. Ensure local tests pass.
4. Commit to your fork using clear commit messages.
5. Send us a pull request, answering any default questions in the pull request interface.
6. Pay attention to any automated CI failures reported in the pull request, and stay involved in the conversation.

GitHub provides additional documentation on [forking a repository](https://help.github.com/articles/fork-a-repo/) and
[creating a pull request](https://help.github.com/articles/creating-a-pull-request/).

## Architecture and design decisions

Before making non-trivial changes, please read:

- [`docs/architecture.md`](docs/architecture.md) — single source of
  truth for how the agent is structured and how it stays safe.
  Update this doc in the same PR when you change the architecture
  or the safety model.
- [`docs/adr/README.md`](docs/adr/README.md) — Architecture Decision
  Records. Read the relevant ADRs before changing a decision they
  document. Add a new ADR when your change is hard to reverse, has
  clear alternatives a reasonable engineer might pick differently,
  or affects safety / performance / cost.

You don't need an ADR for renames, bug fixes, dependency bumps, or
test-only changes. Use the [template](docs/adr/0000-template.md)
when you do write one.

## Project layout

```
eksreview/
├── eksreview                       # Launcher (auto-activates venv)
├── install.sh                      # One-command setup
├── main.py                         # Entrypoint: config, MCP, agent factory
├── pyproject.toml                  # Project metadata + dependencies
├── eks_review_agent/               # Agent source
│   ├── cli/                        # REPL and slash commands
│   ├── core/                       # Model, prompts, steering, observability
│   ├── orchestration/              # Sub-agent pipelines + MCP integration
│   ├── reports/                    # Report search + JIRA export
│   ├── knowledge/                  # Knowledge base + skills
│   └── ui/                         # Terminal UI + logging
├── mcp-server/                     # Bundled EKS Review MCP server (checks)
├── skills/                         # Report/investigation templates
├── examples/                       # Sample reports (assessment + upgrade)
├── docs/                           # Architecture + ADRs
├── reports/                        # Generated reports (runtime)
└── tests/                          # pytest suite
```

## Tests and coverage

Run the test suite locally before opening a PR:

```bash
pip install -e ".[dev]"
pytest --cov --cov-fail-under=60
```

The CI workflow (`.github/workflows/security.yml`) runs the same
suite plus `pip-audit`, `bandit`, and `ruff --select S` on every PR.

## Finding contributions to work on

Looking at the existing issues is a great way to find something to contribute on. As our projects, by default, use the default
GitHub issue labels (enhancement/bug/duplicate/help wanted/invalid/question/wontfix), looking at any 'help wanted' issues is a
great place to start.

## Code of Conduct

This project has adopted the [Amazon Open Source Code of Conduct](https://aws.github.io/code-of-conduct).
For more information see the [Code of Conduct FAQ](https://aws.github.io/code-of-conduct-faq) or contact
opensource-codeofconduct@amazon.com with any additional questions or comments.

## Security issue notifications

If you discover a potential security issue in this project we ask that you notify AWS/Amazon Security via our
[vulnerability reporting page](http://aws.amazon.com/security/vulnerability-reporting/). Please do **not** create a public
GitHub issue.

## Licensing

See the [LICENSE](LICENSE) file for our project's licensing. We will ask you to confirm the licensing of your contribution.
