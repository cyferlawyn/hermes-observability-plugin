# Define the inspectable trace and “thinking exchange”

Type: grilling
Status: resolved

## Question

What exactly should count as an observable event in the ISA, and what does “thinking exchange” mean for this dashboard?

Decide which of these belong in a session trace: user/assistant messages; model/agent turn boundaries; tool calls with arguments, results, duration and errors; MCP server/tool identity and outcomes; retries/approvals; child-agent spawn, progress and completion; token/cost/model metadata; and summaries or references to intermediate work. Distinguish events actually available in Hermes from data that would require new instrumentation.

Set an explicit boundary around private reasoning: do not assume hidden chain-of-thought is exposed or safe to persist. If the user means visible assistant/tool/model exchanges, name those precisely and decide whether the dashboard needs full payloads, summaries, or metadata only.

## Answer

“Thinking exchange” means an **operational trace**, not hidden reasoning. Include visible user/assistant turns; model/API attempt metadata (model/provider, timing, usage, completion/error and retry details where available); tool/MCP identity, arguments/results, outcome and duration where available; approval events; and subagent start/stop relationships, status, goal, duration, and summary.

Do not treat provider-returned `reasoning*` fields as chain-of-thought; exclude them from the default dashboard view. Also exclude raw provider request/response payloads and do not inline full child transcripts in the parent trace. Use explicit unavailable/unknown states instead of inferring missing events. Existing session persistence provides message/tool history and aggregate usage; a fuller event-by-event trace (including provider-attempt, tool timing/outcome, approval, and subagent lifecycle detail) requires collecting the documented observer hooks rather than assuming it is already persisted.

The user clarified this is a personal dashboard containing only their own sessions and considers privacy a low concern; carry that context to the privacy/retention decision without expanding this trace-scope decision.

Evidence: [Observer Hooks](https://hermes-agent.nousresearch.com/docs/developer-guide/observer-hooks) documents API-attempt, tool, approval, and subagent lifecycle events; [Session Storage](https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage) documents persisted messages, tool-call fields, usage aggregates, and provider-returned reasoning fields.

## Comments

- User accepted the operational-trace recommendation. “Privacy is not a concern” in this context because the dashboard is for the user’s own sessions.
