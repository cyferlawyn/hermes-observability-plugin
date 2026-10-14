# Set the telemetry privacy and retention boundary

Type: grilling
Status: resolved

Blocked by: 01

## Question

What session content and metadata may the observability feature capture, retain, display, and export?

Decide whether tool arguments/results and message text are stored verbatim, summarized, redacted, or represented by metadata/pointers; how secrets and personal data are protected; who can access the view; whether the feature is opt-in; and the retention/deletion behavior. Include raw request/response bodies and any “thinking” material explicitly. Prefer the minimum data needed for the diagnostic workflows, and make clear whether existing session history is reused or copied into a separate store.

## Comments

- User confirmed a personal, single-user dashboard may show full visible session content and tool/MCP arguments/results without an additional redaction layer; raw provider payloads and provider reasoning fields remain excluded per the trace decision.
- User wants local event collection enabled by default. Existing historical sessions may show only the data already available; new observer events begin prospectively.
- User chose the three metric windows—“Overall,” “Today,” and “Since Reset” (labels provisional)—with whole-session inclusion by local session-start timestamp. The reset boundary is non-destructive and confirmed below.

## Answer

This is a personal, single-user feature. Keep collection local and enabled by default for the active profile; show full visible session content and tool/MCP arguments/results without adding a redaction layer. Continue to exclude raw provider request/response payloads and provider `reasoning*` fields per [Define the inspectable trace and “thinking exchange”](01-trace-scope-and-thinking-exchange.md). Historical sessions show only data already available; detailed observer-event capture is prospective.

The metric windows are semantic filters (display names remain provisional):

- **Overall:** all still-available, undeleted sessions.
- **Today:** sessions whose start timestamp falls on the current local calendar day.
- **Since Reset:** sessions whose start timestamp is at or after the most recent Reset Metrics cutoff, in local time.

Reset moves the cutoff only; it never deletes sessions or telemetry. Repeating Reset replaces the active cutoff while all still-available data remains in Overall. A session started before a window boundary is not included in that window even if it remains active afterward. Deleting/pruning a source session removes its detailed trace and its contribution to all aggregates. Any observer data stored separately must be tied to the source session and follow this deletion rule; the precise read/index/event-store design is for [Choose the event capture and storage architecture](06-capture-and-storage-architecture.md).
