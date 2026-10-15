# Ideal State Artifact: Hermes Agent Observability Dashboard

**Status:** Version 1.0.0 implementation candidate is present in this repository. This ISA remains the acceptance contract; verification evidence and outstanding live-performance validation are recorded in [release evidence](release/1.0.0-evidence.md).

## 1. Purpose

Give the user a reliable, inspectable view of what happened during their personal Hermes sessions: visible conversation turns, model/API attempts, tool and MCP calls, approvals, and subagent lifecycles. The dashboard should make it straightforward to find a failed call, understand its observable context, inspect cross-session usage and deterministic repeat failures, and launch a user-directed follow-up analysis.

The trace describes observable operations. It does not evaluate whether an agent fulfilled a user's intent and is not a record of hidden chain-of-thought.

## 2. First-release outcomes

The first release supports these workflows:

1. **Diagnose one session:** find a session, inspect its chronological trace, open a failed tool/MCP call, and see its identity, available arguments and result/error, timing/status, nearby visible conversation, and related subagent context.
2. **Inspect cross-session usage and failures:** review tool/MCP activity, explicit failures, retries/timeouts, and subagent use; navigate from a deterministic repeated-error signature to example calls and sessions.
3. **Launch an Analyze follow-up:** from an individual failed call, start a separate session seeded with the available failure evidence and nearby visible context. That session can diagnose and propose a resolution; changes require separate explicit approval.

## 3. Product and hosting boundary

- Deliver the initial experience as a **native Hermes Desktop plugin**.
- The UI runs in Hermes Desktop on the inspection machine. Its Python backend runs in the connected personal remote profile, where the session store resides.
- The renderer calls the backend through Desktop's profile-routed plugin host API. It must not open remote SQLite directly or hold a broad API credential.
- Install the UI half on the Desktop machine and enable the backend half in the connected personal profile. Keep data and queries scoped to that one profile.
- Do not build a separate LAN SPA or Hermes Web Dashboard plugin for this release. A later standalone SPA would require a server-side proxy/BFF or a purpose-built least-privilege endpoint; do not expose `API_SERVER_KEY` to browser code.

## 4. Trace scope and content

### Include when observable

- Visible user and assistant turns and model/agent turn boundaries.
- Model/provider and API-attempt metadata, timing, usage, completion/error, and retries where exposed by the observer contract.
- Tool/MCP identity, arguments/results by reference to the existing session content, invocation timing, observable status/outcome, and structured error details where available.
- Approval events and their observable outcomes.
- Subagent start/stop relationships, parent/child IDs, goal, status, duration, and available summary.
- Event and telemetry-schema version metadata needed to interpret coverage.

Existing Hermes session/message storage remains authoritative for visible messages and tool-call content. Plugin telemetry records correlate to those source records rather than duplicating their full contents without need.

### Exclude

- Provider `reasoning*` fields and hidden chain-of-thought.
- Raw provider request/response payloads.
- Full child transcripts inline in the parent session trace.
- Any inferred task/session success; a tool call is evaluated only by its observable status/return indicators.

Show unavailable or uncertain values as unknown/incomplete rather than inferring them.

## 5. Capture and persistence

- Collection is enabled by default for the active personal profile and begins prospectively.
- Exclude sessions already open when capture becomes active, and do not backfill observer-level history for pre-existing sessions. Sessions started after capture activation are eligible.
- Capture documented observer lifecycle events for session/turn/API/tool/MCP/approval/subagent activity. Observer callbacks are read-only and must remain fail-open: a collector error must not alter agent execution.
- Store telemetry in plugin-owned, namespaced, versioned event tables in the connected profile's Hermes `state.db`. Leave Hermes' core tables unchanged.
- Correlate plugin events to source sessions and observer-provided task, turn, API-request, tool-call, and parent/child IDs. Retain event timestamps, event category, status/outcome, duration, structured error details, and `telemetry_schema_version` where available. Use existing session/message records for visible text and arguments/results.
- Link event rows to source sessions with deletion cascade. Deleting/pruning a source session removes its event rows and aggregate contribution.
- Reset is non-destructive: it moves the active `Since Reset` cutoff; it does not delete sessions or telemetry.

### Conceptual event record

The implementation should map available observer fields into a versioned plugin-owned event model without changing Hermes' core schema. Each stored lifecycle event should retain, where available:

| Field group | Required content |
|---|---|
| Event identity | Plugin event-row ID, event family/type, and lifecycle phase. |
| Source session | Hermes session ID as a foreign key; deleting the source session cascades to its telemetry. |
| Correlation | Observer session/task/turn/API-request/tool-call and parent/child IDs when supplied. |
| Tool/MCP identity | Built-in tool identity or MCP server plus tool identity, when applicable. |
| Timing | Event timestamp and duration where supplied or safely derived from paired lifecycle events. |
| Outcome | Observable status/outcome, including explicit not-executed, failed, unknown, or incomplete states where relevant. |
| Error evidence | Structured error type/code/details when exposed; do not infer success or failure from semantics. |
| Source content reference | Message/tool-call reference for reading visible arguments/results from existing session storage rather than duplicating them. |
| Schema metadata | Observer `telemetry_schema_version` and the plugin's independent schema version. |

Retain enough per-session/event-family/tool capture metadata to derive the coverage states below: capture eligibility, supported event family, schema version, and detectable lifecycle gaps. Missing optional observer fields remain unknown; do not fabricate them.

### Access and privacy

This is a single-user feature for the active personal profile. Keep collection local to that profile and enabled by default. Display full visible session content and tool/MCP arguments/results without adding another redaction layer, while continuing to exclude raw provider payloads and provider reasoning fields. The Desktop host manages the remote connection; plugin renderer code must not receive broad server credentials.

## 6. Coverage and comparability

Record coverage per session and per event family/exact tool using these states:

| State | Meaning |
|---|---|
| **Not captured** | Collection was not active for the session, or the event family is unsupported. |
| **Partial** | Some telemetry exists, but a known gap, unmatched lifecycle, or interruption prevents reliable absence claims. |
| **Observed—no known gaps** | Capture was eligible and supported, and no gap was detected in the observed lifecycle. This is not proof that no event was silently lost. |
| **Unknown** | Available capture/schema metadata is insufficient to assess coverage. |

A true zero is allowed only for an `Observed—no known gaps` session/event-family cohort. If a selected period also contains Not captured, Partial, or Unknown sessions, show a zero only for the eligible observed subset and display included/excluded session counts; do not describe the whole period as having zero occurrences.

For success rates and direct cross-session comparisons:

- Include only sessions marked `Observed—no known gaps` for the same event family and exact tool identity, with compatible capture support and telemetry schema.
- Compare matched eligible subsets only. Show included and excluded session counts for each selected time window.
- If coverage or schema compatibility is insufficient, suppress the rate comparison and show known counts with coverage labels. Do not invent a numeric coverage percentage.
- Do not call `Observed—no known gaps` “complete”; the observer is fail-open and may not expose every silent loss.

Evidence-positive deterministic failure findings may still use explicit failures observed in Partial sessions. If the relevant population contains Partial, Unknown, or Not captured sessions, show the finding as “at least N observed,” include example calls, and do not infer absence or compare/rank prevalence across mismatched coverage.

## 7. Outcomes and metrics

Treat each tool/MCP invocation as a black box and classify it only from observable status/return indicators:

- `ok` is a success.
- `error` or another explicit failure indicator (including explicit timeout/partial-failure status) is a failure; retain the failure type for breakdowns.
- Blocked calls, approval denials/timeouts, and cancellations that did not execute are visible as not-executed outcomes and excluded from the success-rate denominator.
- Missing or ambiguous outcomes are `unknown`, visible but excluded from the denominator.
- If a partial result has an explicit failure indicator, count it as a failure. Do not infer outcome from result semantics.

Count each dispatched tool/MCP invocation once. Calculate:

`success rate = ok / (ok + explicit failures)`

Track provider API attempts/retries separately; do not inflate tool-call counts with provider retries. A later tool invocation after an error is a new call. Never claim an overall task/session-success rate.

### Time windows

Use the display labels **Overall**, **Today**, and **Since Reset** (labels may be renamed without changing semantics):

- **Overall:** all still-available, undeleted sessions. For telemetry metrics, show the observed eligible subset and excluded coverage counts.
- **Today:** sessions whose start timestamp falls on the current local calendar day.
- **Since Reset:** sessions whose start timestamp is at or after the latest Reset Metrics cutoff, interpreted in local time.

Classify whole sessions by session-start time. A session started before a boundary remains outside that window even if it continues afterward. Repeating Reset replaces the cutoff; it does not affect Overall or delete stored data.

## 8. Deterministic repeated-error findings

Surface only evidence-backed, deterministic repetitions; do not use an LLM classifier, fuzzy semantic grouping, or generated documentation suggestions in the dashboard.

- Scope a signature to exact tool identity; for MCP, include both server and tool identity.
- Prefer a structured error code/type. Otherwise match the exact message/template after masking only explicitly allow-listed volatile IDs/timestamps.
- Preserve semantically meaningful values such as status codes and expected/actual argument counts. Do not normalize values speculatively.
- Display the signature/matcher basis, not a probability-confidence score.
- Surface a cross-session finding after at least **two distinct explicitly failed logical invocations in at least two distinct sessions** within the selected Overall/Today/Since Reset window. Repeats confined to one session remain in that session's trace but do not become cross-session findings.
- Provider retries attach to the logical invocation and do not increase the finding's distinct failed-call count.
- Each finding shows tool/MCP identity, signature and match basis, distinct failed-call and affected-session counts, first/last observation, retry/timeout counts, a short error excerpt, and links to example calls/sessions. Drill into the source call for available arguments/result and nearby visible context.
- Link documentation only when an explicit document link is associated with that tool/MCP. Do not guess links or generate improvement text. The separate user-triggered Analyze action is the manual follow-up path.

## 9. User-triggered Analyze flow

A failed-call detail includes an **Analyze** action. Clicking it starts a fresh, separate session on the connected personal profile with a narrow prompt containing available selected-call evidence and nearby visible context. Include relevant identity, arguments/result/error, and event/session references when available; do not include provider reasoning, raw provider payloads, or full child transcripts. Identify the evidence fields in the UI. The source session and trace remain unchanged.

The new session may inspect the problem and propose a fix. Applying changes to tools, MCP configuration, skills, or documentation requires a separate explicit approval in that follow-up task. Analyze is not automatic error diagnosis or approval to modify the system.

## 10. Primary interface

Anchor the first-release landing view on the **Control room** prototype:

- Captured-session list and tool/MCP breakdown.
- Selected-session summary and chronological trace.
- Event inspector with call detail and subagent activity.
- Coverage states and eligible/excluded counts wherever an absence, rate, or comparison is shown.

Keep a failure-first view as a cross-session diagnostic path and a dense Trace ledger as a secondary inspection mode. Keep event/session drill-down links available from aggregates. The existing prototype is synthetic and illustrative only: [session-overview.html](prototype/session-overview.html).

## 11. Acceptance criteria

Implementation evidence and measured performance limits for this release are recorded in [docs/release/1.0.0-evidence.md](release/1.0.0-evidence.md). The evidence distinguishes automated checks, synthetic benchmarks, and live/manual integration that still requires a Desktop user.

### Correctness and coverage

- Controlled fixtures achieve **100% expected event/outcome matches** for the defined supported scenarios: tool/MCP success and explicit failure; blocked/cancelled/not-executed; unknown/incomplete; provider retries; approval responses; subagent start/stop; interrupted sessions; reset; and source-session deletion.
- Verify outcome math exactly: one count per invocation, only `ok` and explicit failures in the success-rate denominator, retries separate, and unknown/not-executed excluded.
- Verify each coverage state, true-zero behavior, filtered included/excluded counts, schema incompatibility handling, and the “at least N observed” label on incomplete populations.
- Verify deletion removes linked observer rows and aggregate contributions; Reset moves only the filter cutoff.

### Workflow and safety

- A guided walkthrough demonstrates finding a failed call and its context from Control room, navigating from a repeated-error finding to example calls/sessions, and inspecting subagent relationships. There is no numeric time-to-answer SLA; record observations without inventing one.
- Analyze starts a separate session with selected failure evidence and nearby visible context, leaves the source trace unchanged, and requires separate explicit approval for remediation edits.
- The UI never presents missing/unknown activity as success or zero and never claims hidden reasoning or intent fulfillment.

### Performance

- The release bar is **no noticeable slowdown**, not a numeric SLO.
- Before setting numeric latency limits, benchmark observer-enabled versus observer-disabled turn behavior and dashboard query/render latency on a representative dataset; record p50/p95 and review visible impact.

## 12. Deferred and out of scope

- Implementing/deploying the collector or dashboard is outside the scope of this specification document; implementation and deployment guidance live in the repository's code and README.
- Cross-install or cross-profile aggregation, exports, and advanced trend analysis.
- Automatic LLM-based error classification/recommendations, generated documentation edits, or unattended remediation.
- Hosted third-party telemetry analytics as a default.
- A standalone SPA or Web Dashboard plugin for the first release.
- Raw provider payloads, hidden reasoning, and full child transcripts in the parent trace.

A user-triggered Analyze session is included; any remediation remains separately approved.

## 13. Explicit implementation risks

1. **Hermes database coupling:** Plugin-owned event tables share Hermes' internal `state.db`. Namespacing, independent schema versioning/migrations, SQLite write contention, deletion cascade, and compatibility across supported Hermes upgrades must be validated.
2. **Fail-open observer hooks and telemetry durability:** A collector callback error or abrupt process stop may leave events silently missing or lifecycle pairs unmatched. Surface detected gaps; do not relabel `Observed—no known gaps` as guaranteed complete. On WAL databases, if `database.synchronous` is unset, the plugin's thread-local connection uses `NORMAL` for plugin-owned telemetry only; explicit Hermes settings are honored. Sudden power loss may lose recent telemetry commits, while Hermes' core session-data connection is unaffected.
3. **Prospective-only history:** Pre-enable and already-open sessions are not observer-captured. Early cross-session patterns may be sparse; coverage counts must make this visible.
4. **Performance target:** No numeric SLO is set. Synthetic p50/p95 evidence is recorded in the release report, but real model/provider turns and Electron paint still require live validation before making a broader “no noticeable slowdown” claim.
5. **Analyze context transfer:** The action sends selected failure evidence into a new model session. Keep the prompt narrow, identify the fields included, and preserve the source trace.
6. **Plugin topology:** Desktop UI and backend must be installed/enabled on the appropriate local and connected remote profile surfaces; the UI must remain profile-routed and must not handle broad server credentials.

## 14. Source decisions and references

Decision provenance is preserved in the [GitHub Wayfinder map](https://github.com/cyferlawyn/hermes-observability-plugin/issues/1) and its closed child issues:

- [Define the inspectable trace and “thinking exchange”](https://github.com/cyferlawyn/hermes-observability-plugin/issues/2)
- [Choose the first-release diagnostic workflows](https://github.com/cyferlawyn/hermes-observability-plugin/issues/3)
- [Define call outcomes and trustworthy metrics](https://github.com/cyferlawyn/hermes-observability-plugin/issues/4)
- [Set the telemetry privacy and retention boundary](https://github.com/cyferlawyn/hermes-observability-plugin/issues/5)
- [Choose where the observability experience lives](https://github.com/cyferlawyn/hermes-observability-plugin/issues/6)
- [Choose the event capture and storage architecture](https://github.com/cyferlawyn/hermes-observability-plugin/issues/7)
- [Prototype the session overview and trace drill-down](https://github.com/cyferlawyn/hermes-observability-plugin/issues/8)
- [Set ISA acceptance criteria and first-release boundary](https://github.com/cyferlawyn/hermes-observability-plugin/issues/9)
- [Decide how telemetry should guide tool/MCP instruction improvements](https://github.com/cyferlawyn/hermes-observability-plugin/issues/10)
- [Define trace coverage and cross-session comparability](https://github.com/cyferlawyn/hermes-observability-plugin/issues/11)

Coverage terminology is defined in [GLOSSARY.md](GLOSSARY.md). The chosen prototype is [session-overview.html](prototype/session-overview.html).

Relevant Hermes references:

- [Observer Hooks](https://hermes-agent.nousresearch.com/docs/developer-guide/observer-hooks)
- [Session Storage](https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage)
- [Desktop Plugin SDK](https://hermes-agent.nousresearch.com/docs/developer-guide/desktop-plugin-sdk)
- [Multi-connection Desktop](https://hermes-agent.nousresearch.com/docs/user-guide/multi-connection-desktop)
- [API Server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)
