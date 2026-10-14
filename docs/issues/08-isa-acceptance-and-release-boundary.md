# Set ISA acceptance criteria and first-release boundary

Type: grilling
Status: resolved

Blocked by: 02, 03, 04, 05, 06, 07

## Question

What must be true for the ISA to be complete and for its proposed first release to be considered useful and safe?

Set measurable acceptance criteria for trace coverage, outcome accuracy, session/subagent visibility, diagnostic time-to-answer, privacy, local performance, and behavior when data is missing. Decide what is deliberately deferred (for example, cross-install aggregation, advanced trend analysis, recommendations, exports, or automatic documentation changes). Record unresolved implementation questions as explicit risks rather than hiding them inside the artifact.

## Answer

Resolved: the ISA is complete when it specifies the confirmed trace scope, metrics, capture/store, Desktop host, Control room overview, first-release boundary, explicit risks, and the acceptance checks below. This closes the planning decision; it does not implement the observer or dashboard.

### Acceptance checks

- **Coverage and missing data:** In controlled fixtures, 100% of expected event/outcome results must match for the defined supported paths (tool/MCP success and explicit failure, blocked/cancelled or otherwise not executed, unknown/incomplete, provider retries, approval responses, subagent start/stop, interrupted sessions, reset, and deletion). The live UI must show coverage by event category and surface missing/unknown events explicitly; an unobserved event is never presented as zero or success.
- **Outcome accuracy:** Apply [Define call outcomes and trustworthy metrics](03-outcome-taxonomy-and-metrics.md) exactly: each executed invocation once; only `ok` and explicit failures in the success-rate denominator; blocked, cancelled, and unknown outcomes remain visible outside it; provider attempts/retries separate; no task/session success inference.
- **Session and subagent diagnosis:** From the Control room, the user can reach a call’s tool/MCP identity, available arguments/result/error, duration/status, surrounding visible turn, and linked parent/child subagent lifecycle. Interrupted or incomplete activity remains marked as such. Full child transcripts and provider reasoning are not required in the parent view.
- **Usability:** No numeric time-to-answer threshold. A guided scenario walkthrough must let the user locate a failed call and its context, then navigate from a deterministically surfaced repeated-error signature to its example sessions; record usability observations without claiming a timed SLO.
- **Performance:** The release bar is no noticeable slowdown, not a fixed latency SLO. Before setting numeric limits, benchmark observer-enabled versus observer-disabled turn behavior and dashboard query/render latency on a representative dataset; record p50/p95 and review any visible impact.
- **Privacy and safety:** Keep collection scoped to the connected personal profile, prospective only, and in the chosen profile-local store. Exclude raw provider payloads and `reasoning*` fields; deletion removes related event rows and metrics; Reset moves a filter cutoff only. The user-triggered **Analyze** action starts a separate session seeded only with available selected-call evidence and nearby visible context (not hidden reasoning, raw provider payloads, or full child transcripts); edits/remediation require a separate explicit approval, and the source trace is unchanged.

### First-release boundary

Include the user-triggered Analyze action as an explicit exception to automatic-analysis deferral: it investigates a selected failure in a separate session, but does not silently edit tools, MCP configuration, skills, or documentation. No automatic LLM classifier, generated documentation edits, exports, cross-install aggregation, or advanced trend analysis. Surface only deterministic, evidence-backed repeated errors; exact grouping/threshold rules remain with [Guidance improvement feedback loop](09-guidance-improvement-feedback-loop.md).

### Explicit implementation risks

1. Plugin-owned event tables share Hermes' internal `state.db`; table namespacing/versioning, schema migration, SQLite write contention, and cascade deletion must be tested across supported Hermes upgrades.
2. Observer callbacks are fail-open, and an abrupt process stop may leave unmatched starts; those records must remain visible as unknown/incomplete, not be repaired by inference.
3. Existing sessions and sessions already open at enablement are excluded, so cross-session comparisons begin prospectively and may be sparse at first.
4. Performance numbers remain open until the agreed benchmark; do not claim a numeric latency guarantee in the ISA before measurement.
5. Analyze sends failure evidence into a new model session; the UI should identify the fields included, keep the prompt narrow, and preserve the original trace unchanged.

The user approved the fixture-coverage gate, declined a numeric usability time limit, chose benchmarking before numeric performance targets, kept exports/cross-install/advanced trends deferred, and added the user-triggered Analyze flow with separate approval for any edits.
