# Changelog

## 1.0.0

- Adds prospective, profile-local capture of visible turns, provider/API attempts, tool/MCP calls, approvals, and subagent lifecycles.
- Adds coverage-aware metrics, exact-identity tool breakdowns, deterministic repeated-failure findings, and exact-source call detail.
- Adds three Desktop investigation views: Control room, Failure queue, and searchable Trace ledger.
- Adds optional exact-identity HTTPS documentation links; no links are guessed.
- Adds a user-triggered Analyze follow-up with a durable, fail-closed read-only tool policy.
- Reuses a thread-local profile database connection; on WAL it uses NORMAL only when no explicit Hermes synchronous preference is configured.
- Preserves source-session privacy, profile routing, non-destructive reset, and unknown/incomplete outcome semantics.

Verification and measured performance limitations are recorded in [release evidence](docs/release/1.0.0-evidence.md). The user-facing workflow is in the [Control room guide](docs/USER_GUIDE.md).
