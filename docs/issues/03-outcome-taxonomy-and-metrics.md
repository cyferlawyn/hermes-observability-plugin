# Define call outcomes and trustworthy metrics

Type: grilling
Status: resolved

Blocked by: 01

## Question

How should the ISA classify tool, MCP, and subagent outcomes, and calculate success rates so the numbers are meaningful?

Decide the denominator and treatment of errors, timeouts, cancellations, user denials/approvals, retries, partial results, and calls that fail before the agent recovers. Distinguish call-level success from task/session success; decide which rates, counts, durations, retry rates, and subagent measures are necessary for the selected workflows. Specify how unknown or missing outcomes are displayed rather than quietly counted as success.

## Answer

Treat each tool/MCP invocation as a black box. Classify its outcome from the observable tool status/return indicators only: `ok` is success; `error` or another explicit failure indicator (including an explicit timeout/partial-failure outcome) is a failure, with the failure type retained for breakdowns. Do not judge whether the tool fulfilled the intent of its call.

Count each dispatched tool/MCP invocation once. Calculate its success rate as `ok / (ok + explicit failures)` over known executed invocations. A later invocation after an error is a new call, not a rewrite of the earlier outcome. Track underlying provider API attempts and retries separately; do not inflate tool-call counts with them.

Blocked calls, approval denials/timeouts, and cancellations that did not execute remain visible as separate not-executed outcomes and are excluded from the call success-rate denominator. Missing/ambiguous outcomes remain `unknown`, also outside the denominator. Show those counts beside the rate rather than treating them as successes. If a partial result carries an explicit failure indicator, count it as failure; do not infer failure/success from semantics when no indicator exists.

For sessions and subagents, show observable completion/interruption status and duration, plus associated call outcomes. Do not claim an overall task-success rate: whether the user's intent was fulfilled is outside the dashboard's scope.

## Comments

- User confirmed the proposed categories and clarified the black-box boundary: count the tool's observable success or one of its failure indicators; judging whether the tool fulfilled its intent is out of scope.
