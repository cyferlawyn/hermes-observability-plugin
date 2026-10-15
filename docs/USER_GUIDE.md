# Control room user guide

The Observability plugin reports observable session operations. It does not infer whether Hermes fulfilled the user's intent, and it never shows hidden provider reasoning.

## Start collecting

1. Install and enable the agent/backend half in the profile to inspect; see [README installation](../README.md#installation).
2. Install and enable the Desktop half on the client app.
3. Start a **new** Hermes session after enabling collection, then make a tool or MCP call. Capture is prospective; existing sessions are not backfilled.

## Investigate a session

1. Open **Observability → Control room**.
2. Choose a time window: **Overall**, **Today**, or **Since Reset**. Windows include whole sessions by their start time. Reset Metrics advances the non-destructive cutoff; it does not delete telemetry or source sessions.
3. Filter/select a recent session. Coverage is labeled explicitly. “Observed — no known gaps” is not a completeness guarantee.
4. Use the **Tool & MCP breakdown** to compare exact tool identities and known executed outcomes. Unknown/not-executed outcomes remain visible and are excluded from the success-rate denominator.
5. Select an event in **Session activity**. The inspector shows event-specific metadata. For a tool call, choose **Inspect source detail** to read available arguments, result/error, and nearby visible conversation from the exact canonical source records. Ambiguous or missing matches are not guessed.
6. A failed-call detail may offer **Analyze**. Clicking it starts a separate, tool-blocked follow-up containing bounded evidence. Any remediation needs separate explicit approval; the source session and telemetry remain unchanged.

## Work a cross-session failure

1. Open the **Failure queue** view.
2. Start with the session list, which prioritizes sessions with observed explicit failures or unknown outcomes.
3. Review repeated deterministic signatures. A surfaced signature represents distinct explicit failures across multiple sessions; incomplete populations are labeled as lower bounds. Provider retries remain separate and do not increase the logical tool-call count.
4. Choose **Inspect example** to open the source session in Control room. Provider retry evidence is attached only from a paired attempt with exact session, turn, and API-request IDs; ambiguous, unmatched, or missing correlations remain partial/unknown and never add logical tool calls. Configured documentation links appear only for an exact tool/MCP identity match; the plugin never guesses a URL.

Optional documentation links are configured in **Settings → Plugins → Observability → Tool/MCP documentation links** as a JSON object mapping the complete observer tool identity to an HTTPS URL. For example:

```json
{
  "mcp__calendar__search_events": "https://docs.example.org/calendar/search-events"
}
```

MCP keys must include the full `mcp__server__tool` name. Invalid URLs and partial/fuzzy identity matches are ignored. No documentation links are configured by default.

## Search the trace ledger

1. Open **Trace ledger** after selecting a session in Control room.
2. Filter by event family, status, tool/provider identity, error, or visible message text.
3. Select a row to inspect its summary, then choose **Open full inspector** for the detailed Control room inspector. Unknown/incomplete records stay visible and are not presented as success.

## Reading coverage and rates

- **Observed — no known gaps:** capture was eligible/supported and no gap was detected. Silent loss remains possible.
- **Partial:** an unmatched lifecycle, known gap, or interruption prevents reliable absence claims.
- **Unknown:** metadata is insufficient to assess coverage.
- **Not captured:** collection was inactive, the session predates enablement, or the family is unsupported.

Only compatible observed subsets contribute to rates. A zero applies only to the eligible observed subset, not to uncaptured or unknown sessions. Incomplete-population failure evidence is shown as “at least N observed.”
