# Define trace coverage and cross-session comparability

Type: grilling
Status: resolved

Blocked by: 06

## Question

How should the dashboard represent gaps in event coverage across built-in tools, MCP calls, and subagents, and when should cross-session comparisons be qualified or suppressed because the observed data is incomplete or not comparable?

Decide how to distinguish a true zero from an unobserved event, how to show coverage/completeness for a time range or tool family, and what minimum coverage is needed before comparing success rates or repeated-error counts. Use the chosen capture/storage architecture as the factual boundary; do not infer missing activity as success or failure.

## Answer

Resolved: qualify coverage per session and per event family/exact tool, using four states. [GLOSSARY.md](../GLOSSARY.md) defines these terms.

- **Not captured:** collection was not active for the session, or that event family is unsupported.
- **Partial:** telemetry exists, but a known gap or incomplete lifecycle prevents reliable absence claims.
- **Observed—no known gaps:** capture was eligible and supported, with no detected gaps in the observed lifecycle. This is not proof that no event was silently lost; observer callbacks are fail-open.
- **Unknown:** available metadata or schema information cannot establish coverage.

A true zero may be stated only for an `Observed—no known gaps` session/event-family cohort. If a selected Overall/Today/Since Reset window also contains sessions that are Not captured, Partial, or Unknown, show the zero only for the eligible observed subset and display the excluded-session counts; do not describe the whole window as having zero occurrences.

For success rates and direct cross-session metric comparisons, use only sessions marked `Observed—no known gaps` for the same event family and exact tool identity, with compatible capture support and telemetry schema. Show included and excluded session counts for each selected window. Compare matched eligible subsets; if coverage or schema compatibility is insufficient, suppress the rate comparison and show known counts with their coverage labels. Do not impose an unsupported numeric coverage percentage.

Evidence-positive deterministic error findings may still use explicitly observed failures from Partial sessions. When the relevant population includes Partial, Unknown, or Not captured sessions, label such counts as “at least N observed,” show example calls, and do not infer absence or compare/rank prevalence across mismatched coverage. Apply the separate two-failures/two-sessions finding threshold from [Decide how telemetry should guide tool/MCP instruction improvements](09-guidance-improvement-feedback-loop.md).

The user selected these four coverage states, the categorical comparison gate, visible included/excluded counts, and lower-bound labeling for findings from incomplete populations.

Consolidated artifact: [ISA.md](../ISA.md).
