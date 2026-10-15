# ISA: Hermes Agent Observability Dashboard

## Destination

An implementation-ready Ideal State Artifact for a personal-use observability dashboard over Hermes sessions, specifying the user workflows, event/data model, metrics, integration surface, and acceptance criteria needed to inspect tool/MCP calls and subagent activity reliably.

## Notes

- This is the historical decision map for the ISA; it planned the specification and did not implement the collector/dashboard. The project implementation and current release evidence are documented in [README.md](../README.md) and [release evidence](release/1.0.0-evidence.md).
- Treat the active personal Hermes installation as the initial context unless a decision expands it. Favor local-first handling of session data.
- Load `wayfinder` and `hermes-agent` when continuing. Use current Hermes docs/source for capability claims; the documented Web Dashboard plugin system and native Desktop plugin SDK are distinct extension surfaces.
- Interpret “thinking exchange” carefully: decide what visible model/agent exchange and lifecycle data is useful. Do not assume hidden chain-of-thought is available or suitable for logging.
- The user wants enough evidence to diagnose tool/MCP friction and improve their instructions/documentation; the ISA should connect observable failure patterns to actionable improvement, without silently editing tools or docs.

## Decisions so far

- [Define the inspectable trace and “thinking exchange”](https://github.com/cyferlawyn/hermes-observability-plugin/issues/2): Operational trace of visible turns, API/tool/MCP and subagent lifecycle; exclude provider reasoning fields and raw API payloads by default.
- [Choose the first-release diagnostic workflows](https://github.com/cyferlawyn/hermes-observability-plugin/issues/3): Include session-level drill-down and cross-session tool/MCP failure patterns; surface only deterministic, evidence-backed repetitions, with no automatic LLM pattern discovery or generated edits; a selected failure may be sent to a separate Analyze session per [Set ISA acceptance criteria and first-release boundary](https://github.com/cyferlawyn/hermes-observability-plugin/issues/9).
- [Define call outcomes and trustworthy metrics](https://github.com/cyferlawyn/hermes-observability-plugin/issues/4): Count each known executed tool/MCP call by its observable black-box outcome; separate retries and not-executed/unknown outcomes, and do not infer task success.

- [Set the telemetry privacy and retention boundary](https://github.com/cyferlawyn/hermes-observability-plugin/issues/5): Personal, local-only collection; full visible/tool content; Today and Since Reset filter by local session-start timestamps, with Reset non-destructive and session deletion removing its trace and aggregates.
- [Choose where the observability experience lives](https://github.com/cyferlawyn/hermes-observability-plugin/issues/6): Native Desktop plugin: local UI paired with a backend on the connected personal remote profile; no separate SPA or Web Dashboard for the initial release.
- [Choose the event capture and storage architecture](https://github.com/cyferlawyn/hermes-observability-plugin/issues/7): Use versioned plugin-owned event tables in the connected profile’s `state.db`; capture prospectively only, exclude sessions already open at enablement, cascade on deletion, and mark unmatched completions unknown/incomplete.
- [Prototype the session overview and trace drill-down](https://github.com/cyferlawyn/hermes-observability-plugin/issues/8): Anchor the first-release UI on Control room’s balanced overview/timeline/inspector composition; retain failure-first and dense-ledger views as secondary drill-downs.
- [Set ISA acceptance criteria and first-release boundary](https://github.com/cyferlawyn/hermes-observability-plugin/issues/9): Fixture-driven event/outcome correctness; qualitative usability; benchmark before numeric performance targets; include user-triggered, approval-gated Analyze; defer exports, cross-install aggregation and advanced trends.
- [Decide how telemetry should guide tool/MCP instruction improvements](https://github.com/cyferlawyn/hermes-observability-plugin/issues/10): Match only same-tool structured or exact normalized error signatures; surface at two failed invocations across two sessions; show evidence and configured doc links without fuzzy grouping or generated suggestions.
- [Define trace coverage and cross-session comparability](https://github.com/cyferlawyn/hermes-observability-plugin/issues/11): Use four per-session/event-family coverage states; allow zeros and rates only for compatible observed subsets, display excluded counts, and label incomplete findings as lower bounds.

## Not yet specified

<!-- No additional fog identified at this point. -->

## Out of scope

- Implementing instrumentation or the dashboard was out of scope for this decision map; it is now covered by the versioned project implementation.
- Unattended/automatic diagnosis or edits to tools, MCP servers, skills, or documentation. A user-triggered Analyze action may start a separate evidence-seeded session; remediation still requires separate explicit approval.
- Sending session telemetry to a hosted third-party analytics service as a default. Revisit only if the destination is explicitly broadened.
