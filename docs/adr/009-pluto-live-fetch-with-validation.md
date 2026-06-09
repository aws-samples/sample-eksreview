# ADR-009: Live pluto fetch with validation, not commit pinning

## Status
Accepted (2026-05-23)

## Context

The upgrade-readiness check scans cluster manifests and Helm releases
for deprecated Kubernetes APIs. The deprecation database it uses
comes from FairwindsOps' [pluto](https://github.com/FairwindsOps/pluto)
project — specifically `versions.yaml` on the `master` branch.

The original code fetched it live on every startup. Finding M3 in
the security review flagged this: a compromised pluto branch could
inject false deprecation data, causing the agent to recommend
unnecessary or harmful migration steps.

The textbook fix is "pin to a specific commit hash." But that
freezes the deprecation list, which defeats the reason it's fetched
live in the first place — Kubernetes deprecates new APIs over time,
and a stale list misses real problems.

## Decision

Keep fetching from `master`, but validate the response structurally
before trusting it. If validation fails, fall through to the
bundled YAML in `data/k8s_deprecated_versions.yaml` (which exists
for offline / first-run cases anyway).

Validation in `eks_upgrade_handler._validate_deprecation_db`:

1. **Size cap** — reject responses > 5 MB (defense against hostile
   redirects).
2. **Content-Type sanity** — reject HTML error pages.
3. **Top-level shape** — must be a mapping with a
   `deprecated-versions` key holding a list.
4. **Absolute floor** — reject if entry count < 50 (real list has
   500+).
5. **Shrink detection** — reject if entries < bundled / 2 (catches
   half-truncated upstream commits).
6. **Per-entry schema** — each entry must be a dict with `version`
   + `kind` (identity), and at least one of `removed-in` /
   `deprecated-in` (lifecycle). 5% per-entry tolerance for
   forward-compat with new optional fields.

When validation fails, log the matched failure, fall through to the
bundled file. Bundled file is updated periodically when we ship a
release.

## Alternatives considered

- **Pin to a specific commit SHA.** The textbook security fix.
  Rejected because it makes the deprecation list go stale. By
  the time we notice the staleness, users have already missed
  real upgrade blockers. Trades a small attack-surface improvement
  for a larger correctness regression.

- **Cache + multi-fetch convergence.** Cache the last successful
  fetch on disk. Compare the next fetch against the cache; only
  update if N fetches in a row agree. A single-shot poisoning has
  to persist across multiple runs to take effect. Significantly
  more code and runtime state for marginal gain — the validation
  rules above already block the obvious tampering shapes.

- **Drop the live fetch entirely.** Always use the bundled YAML.
  Loses the upstream improvements between releases. Rejected.

- **Hash-verify the response against an expected SHA-256.** Same
  problem as commit pinning — freezes the data unless we ship a
  new release.

## Consequences

**Positive**
- Live updates continue to work — the upgrade-readiness check
  reflects new K8s deprecations as soon as Fairwinds publishes.
- Targeted attacks have to either preserve the schema (which
  makes injecting harmful data narrow) or compromise enough of
  the upstream branch to look plausible across all six checks.
- Offline / first-run users still work via the bundled fallback.
- Validation rules are tested in
  `tests/_smoke_m3_v2.py`-style scenarios — not currently in the
  test suite but worth backporting.

**Negative**
- The deprecation DB remains a third-party trust dependency.
  We're betting the validation rules catch the realistic attack
  shapes; we can't prove they catch all of them.
- The bundled fallback drifts over time. We have to remember to
  update it on releases.

**Neutral**
- If FairwindsOps moves pluto to a release-tag publishing model,
  we can pin to tags (which would balance freshness and
  integrity better than commit SHAs).

## References

- M3 finding in the security review.
- `mcp-server/awslabs/eks_review_mcp_server/eks_upgrade_handler.py::_validate_deprecation_db`
  — the validator.
- Bundled fallback:
  `mcp-server/awslabs/eks_review_mcp_server/data/k8s_deprecated_versions.yaml`.
