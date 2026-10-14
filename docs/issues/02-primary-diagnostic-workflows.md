# Choose the first-release diagnostic workflows

Type: grilling
Status: resolved

## Question

Which concrete questions should the ISA make easy to answer for a single session and across sessions?

Consider the user's stated goals: inspect each session's tool and MCP calls, identify failures and success rates, understand subagent usage, and find recurring friction that could be fixed with clearer tool/MCP instructions. Choose and prioritize the first-release workflows (for example, “why did this call fail?”, “which MCP tools have the most retries/timeouts?”, “which subagent branches stalled?”, or “what instruction change might reduce repeated misuse?”). Define the minimum useful session overview and drill-down flow without turning the dashboard into a general analytics product.

## Answer

First release must support both:

- **Per-session diagnosis:** a compact session overview and chronological trace; drill into a tool/MCP failure with its inputs, result/error, and surrounding turn or subagent context.
- **Cross-session patterns:** aggregate activity and failure/retry/timeout patterns by tool and MCP server/tool, with a path from a pattern to example sessions.
- **Programmatic discovery:** surface repeated errors only when a clear deterministic signature can identify them (for example, repeated calls with the wrong number of arguments). Do not use LLM-based analysis to discover or rank patterns, or generate documentation edits/suggestions in the dashboard; show evidence for follow-up. Decision 08 adds a separate user-triggered Analyze session for an individually selected failure; edits still require separate explicit approval.

Exact success-rate calculations belong to [Define call outcomes and trustworthy metrics](03-outcome-taxonomy-and-metrics.md). Pattern-matching rules and the evidence shown with detected patterns remain for the dependent guidance-improvement decision.

## Comments

- User accepted the per-session and cross-session workflows. They specifically want Q3 scoped to programmatic discovery of clearly identifiable repeated errors; automatic LLM-based pattern discovery is deferred. A selected failure may be sent to a separate Analyze session per decision 08; edits/remediation require separate explicit approval.
