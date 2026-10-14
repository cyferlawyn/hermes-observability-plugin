# Decide how telemetry should guide tool/MCP instruction improvements

Type: grilling
Status: resolved

Blocked by: 03, 06

## Question

Which deterministic, machine-identifiable tool/MCP failure patterns should the dashboard group and surface, and what evidence should each finding show to support later manual investigation?

[Choose the first-release diagnostic workflows](02-primary-diagnostic-workflows.md) settled the boundary: use programmatic detection for clearly identifiable repeated errors (for example, invalid argument counts), with no LLM-based pattern discovery and no generated documentation edits or suggestions in the dashboard. Decision 08 adds a separate user-triggered Analyze session for an individually selected failure; any remediation requires separate explicit approval. This ticket should decide pattern signatures/grouping, confidence or frequency thresholds, and the evidence/context (and relevant documentation links, if useful) shown to the user for manual follow-up.

## Answer

Resolved: surface only deterministic, evidence-backed repeated errors. The signature and count rules are:

- Scope each signature to the exact tool identity; for MCP calls, include both the server and tool identity.
- Prefer a structured error code/type when available. Otherwise, match the exact error message/template after masking only allow-listed volatile identifiers or timestamps. Preserve semantically meaningful values such as status codes and expected/actual argument counts. Do not use fuzzy or semantic similarity.
- Report the matcher/rule basis instead of a numeric confidence score.
- Surface a cross-session finding after at least two distinct explicitly failed logical invocations in at least two distinct sessions within the selected Overall/Today/Since Reset window. Apply the established session-start window semantics. Provider retries attach to their logical invocation and do not inflate the failure count. Repeats confined to one session remain visible in that session’s trace but are not promoted as a cross-session finding.

Each finding shows its signature and match basis, tool/MCP identity, distinct failed-call and affected-session counts, first/last observation, retry/timeout counts, a short error excerpt, and links to the example calls/sessions. The call drill-down provides available full arguments/result and nearby visible context. Link documentation only when an explicit document link is associated with that tool/MCP; do not guess or generate documentation links or suggestions. The separately user-triggered Analyze action remains available from an individual failure as defined in decision 08.

The user chose the conservative deterministic signature, a two-failure/two-session surfacing threshold, and evidence-first findings with configured documentation links only.
