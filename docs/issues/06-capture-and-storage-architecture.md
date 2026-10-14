# Choose the event capture and storage architecture

Type: grilling
Status: resolved

Blocked by: 03, 04, 05

## Question

What should be the source of truth for the dashboard’s trace and aggregate data?

Decide whether to read existing Hermes session persistence on demand, derive a local index/materialized view, add lifecycle instrumentation, or combine these. Specify how the design handles old sessions versus new sessions, incomplete/interrupted runs, schema evolution, profile boundaries, and data deletion. Base the choice on verified Hermes capabilities and avoid duplicating sensitive content without a clear need.

## Comments

- [Set the telemetry privacy and retention boundary](04-privacy-retention-and-redaction.md) fixed the metric-window semantics: Overall/Today/Since Reset are filters over available sessions, and Reset only moves a cutoff—it does not delete data. Today and Since Reset assign whole sessions by local session-start timestamp. Any separately stored event rows must be keyed to the source session and removed when that session is deleted/pruned.
- Verified from the current [Observer Hooks](https://hermes-agent.nousresearch.com/docs/developer-guide/observer-hooks) contract: read-only hooks expose session/turn/API/tool/subagent IDs, lifecycle timing and outcome fields, interrupted/incomplete session endings, and `telemetry_schema_version`; callbacks are fail-open. This supports prospective event capture without changing tool execution.
- Existing `state.db` sessions/messages provide history, tool-call objects, and tool results for best-effort historical views, but not a complete durable event-by-event trace (e.g., reliable call timing and all terminal statuses). Do not claim historical observer-level coverage.
- Local source: `SessionDB.delete_session(s)` explicitly deletes messages then session rows in a write transaction (`hermes_state_sessions.py`); the canonical DB connection enables SQLite foreign keys (`hermes_state.py`). A plugin-owned events table in that same DB could attach to `sessions` with `ON DELETE CASCADE`; the standard observer contract has no session-deletion hook. This is a stronger deletion guarantee than a separate sidecar DB, but means the plugin must manage its namespaced schema/migrations alongside Hermes' internal store.

## Answer

Resolved: use the connected personal profile's Hermes `state.db` as the source of truth. Existing `sessions` and `messages` remain authoritative for session metadata and visible transcript/tool content; do not backfill or reconstruct telemetry for sessions that predate observer capture. Exclude sessions already open when capture is enabled; include only sessions that begin afterward.

Capture observer lifecycle events prospectively in plugin-owned, namespaced, versioned event tables in that same profile database. Correlate records by the observer-provided session, task, turn, API-request, tool-call, and parent/child IDs; retain event timestamps, status/outcome, duration and structured error details. Keep raw provider request/response payloads and provider reasoning fields out, consistent with the trace boundary. Version the plugin tables independently and retain the observer `telemetry_schema_version` so future additive hook fields can be handled without changing Hermes' tables.

Use session foreign-key cascade for observer rows so deleting/pruning a source session removes its trace and metrics contribution. An invocation with a start but no terminal event is shown as `unknown`/incomplete, outside success-rate denominators, with the capture gap visible—not inferred as a failure. This feature remains scoped to the connected personal profile; it does not aggregate across profiles.

Evidence: [Observer Hooks](https://hermes-agent.nousresearch.com/docs/developer-guide/observer-hooks) documents read-only lifecycle hooks, correlation fields and schema versioning. Local source verifies session deletion runs through `SessionDB.delete_session(s)` and that the canonical DB connection enables foreign keys. The user approved the in-database event tables, no retrospective view, exclusion of already-open sessions, and unknown/incomplete treatment.