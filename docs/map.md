# ISA: Hermes Agent Observability Dashboard

## Destination

An implementation-ready Ideal State Artifact for a personal-use observability dashboard over Hermes sessions, specifying the user workflows, event/data model, metrics, integration surface, and acceptance criteria needed to inspect tool/MCP calls and subagent activity reliably.

## Notes

- This map plans the ISA; it does not implement instrumentation or the dashboard. The consolidated draft is [ISA.md](ISA.md); detailed decisions remain in their tickets.
- Treat the active personal Hermes installation as the initial context unless a decision expands it. Favor local-first handling of session data.
- Load `wayfinder` and `hermes-agent` when continuing. Use current Hermes docs/source for capability claims; the documented Web Dashboard plugin system and native Desktop plugin SDK are distinct extension surfaces.
- Interpret “thinking exchange” carefully: decide what visible model/agent exchange and lifecycle data is useful. Do not assume hidden chain-of-thought is available or suitable for logging.
- The user wants enough evidence to diagnose tool/MCP friction and improve their instructions/documentation; the ISA should connect observable failure patterns to actionable improvement, without silently editing tools or docs.

## Decisions so far

- [Define the inspectable trace and “thinking exchange”](issues/01-trace-scope-and-thinking-exchange.md): Operational trace of visible turns, API/tool/MCP and subagent lifecycle; exclude provider reasoning fields and raw API payloads by default.
- [Choose the first-release diagnostic workflows](issues/02-primary-diagnostic-workflows.md): Include session-level drill-down and cross-session tool/MCP failure patterns; surface only deterministic, evidence-backed repetitions, with no automatic LLM pattern discovery or generated edits; a selected failure may be sent to a separate Analyze session per ticket 08.
- [Define call outcomes and trustworthy metrics](issues/03-outcome-taxonomy-and-metrics.md): Count each known executed tool/MCP call by its observable black-box outcome; separate retries and not-executed/unknown outcomes, and do not infer task success.

- [Set the telemetry privacy and retention boundary](issues/04-privacy-retention-and-redaction.md): Personal, local-only collection; full visible/tool content; Today and Since Reset filter by local session-start timestamps, with Reset non-destructive and session deletion removing its trace and aggregates.
- [Choose where the observability experience lives](issues/05-host-surface-and-access-model.md): Native Desktop plugin: local UI paired with a backend on the connected personal remote profile; no separate SPA or Web Dashboard for the initial release.
- [Choose the event capture and storage architecture](issues/06-capture-and-storage-architecture.md): Use versioned plugin-owned event tables in the connected profile’s `state.db`; capture prospectively only, exclude sessions already open at enablement, cascade on deletion, and mark unmatched completions unknown/incomplete.
- [Prototype the session overview and trace drill-down](issues/07-prototype-the-session-overview.md): Anchor the first-release UI on Control room’s balanced overview/timeline/inspector composition; retain failure-first and dense-ledger views as secondary drill-downs.
- [Set ISA acceptance criteria and first-release boundary](issues/08-isa-acceptance-and-release-boundary.md): Fixture-driven event/outcome correctness; qualitative usability; benchmark before numeric performance targets; include user-triggered, approval-gated Analyze; defer exports, cross-install aggregation and advanced trends.
- [Decide how telemetry should guide tool/MCP instruction improvements](issues/09-guidance-improvement-feedback-loop.md): Match only same-tool structured or exact normalized error signatures; surface at two failed invocations across two sessions; show evidence and configured doc links without fuzzy grouping or generated suggestions.
- [Define trace coverage and cross-session comparability](issues/10-trace-coverage-and-comparison-trust.md): Use four per-session/event-family coverage states; allow zeros and rates only for compatible observed subsets, display excluded counts, and label incomplete findings as lower bounds.

## Not yet specified

<!-- No additional fog identified at this point. -->

## Out of scope

- Implementing the instrumentation or dashboard; this map ends at an approved ISA.
- Unattended/automatic diagnosis or edits to tools, MCP servers, skills, or documentation. A user-triggered Analyze action may start a separate evidence-seeded session; remediation still requires separate explicit approval.
- Sending session telemetry to a hosted third-party analytics service as a default. Revisit only if the destination is explicitly broadened.
