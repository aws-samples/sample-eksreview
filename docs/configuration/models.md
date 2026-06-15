# Models & Regions

eksreview calls Anthropic Claude models on Amazon Bedrock.

## Default: global cross-region inference profiles

By default the agent uses **global** cross-region inference profiles (the `global.` prefix). These route to commercial regions worldwide, so they work from any commercial region. For most users this is the right choice: set your AWS credentials and a region, and you're done.

The default model is **Claude Opus 4.8** (the latest and most capable). You can switch to a faster, cheaper Sonnet model at any time, including mid-session.

## Available models

`/model` lists these models and marks the active one. The descriptions match what you see in the CLI:

| Name | Notes |
|---|---|
| `claude-opus-4.8` | Latest and most capable, 1M context (default) |
| `claude-opus-4.6` | Most capable, 1M context |
| `claude-opus-4.5` | Previous gen Opus, 1M context |
| `claude-sonnet-4.6` | Fast and capable, 1M context |
| `claude-sonnet-4.5` | Previous gen Sonnet, 1M context |

Short aliases work too: `opus` maps to `claude-opus-4.8` and `sonnet` maps to `claude-sonnet-4.6`.

## Switching models with `/model`

`/model` shows the available models and the active one; `/model <name>` switches:

```text
/model            # list models, show current + profile scope
/model sonnet     # switch to a faster, cheaper model
/model opus       # switch back
```

Switching takes effect immediately for the rest of the session. See [Cost](../reference/cost.md) for why Sonnet is cheaper.

## Pinning a region with `MODEL_ID`

If you need the model to run in a specific geography (for data-residency or latency reasons), set `MODEL_ID` to a **regional** inference profile instead of relying on the global default:

```bash
export MODEL_ID=us.anthropic.claude-sonnet-4-6   # pin to the US region
```

When you pin a regional endpoint this way, subsequent `/model` switches stay in that same region (e.g. switching to Opus uses the `us.` Opus profile). The startup banner shows which scope is active: `(global)` by default, or `(us)` / `(eu)` / etc. when pinned.

`BEDROCK_AWS_REGION` controls which region the Bedrock API call is sent to; if unset it falls back to `AWS_REGION`.

## Choosing an option

| You want… | Do this |
|---|---|
| The simplest setup (recommended) | Nothing; the global default works everywhere |
| Lower cost / faster responses | `/model sonnet` (or set `MODEL_ID` to a Sonnet profile) |
| The model pinned to a specific regional endpoint | Set `MODEL_ID` to a regional profile (e.g. `us.anthropic.…`) |
| Bedrock in a different account than the cluster | See [Credentials & Cross-Account](credentials.md) |

Whatever you start with, you are not locked in. Model choice is a runtime decision, so you can switch with `/model` whenever the workload calls for more capability or lower cost.
