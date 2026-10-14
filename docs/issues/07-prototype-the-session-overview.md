# Prototype the session overview and trace drill-down

Type: prototype
Status: resolved

Blocked by: 02, 03, 05

## Question

What session overview and interaction pattern best exposes the chosen diagnostics at a glance?

Create a cheap, concrete wireframe/prototype for the agreed host surface: session list or selector, success/failure summary, tool/MCP breakdown, subagent activity, and a chronological trace with useful drill-down. Use representative but clearly synthetic example data; do not copy real session contents into the prototype. Validate whether the layout helps locate a failing call and understand its context without drowning the user in logs.

## Comments

- Prototype asset: [session-overview.html](../prototype/session-overview.html). It contains three switchable layout variants—Control room, Failure queue, and Trace ledger—with synthetic data, window filters, event drill-down, subagent detail, and a demo-only Reset Metrics action.
- Verified in the browser: all three variants rendered; switching layouts and filters, opening a session/event detail, filtering ledger rows, and the non-destructive reset demo behaved as intended. At 1280px and 390px viewports, the page itself had no horizontal overflow (the ledger's wide table scrolls within its own container).
- The prototype was migrated from the planning workspace into `docs/prototype/session-overview.html`; it remains a static synthetic-data mockup with no live session access.

## Answer

Resolved: use **Control room** as the anchor for the first-release session overview. Keep its balanced composition: captured-session list and tool/MCP breakdown on the left; selected-session summary and chronological trace in the center; event inspector and subagent activity on the right. Keep the failure-signature view as a cross-session diagnostic path and the Trace ledger as a denser drill-down option, rather than making either the primary landing layout.

The user selected Control room. The linked prototype uses synthetic examples and demonstrates the agreed outcome denominator, unknown-state visibility, selected event arguments/results, subagent lifecycle, Overall/Today/Since Reset controls, and non-destructive reset behavior. No production implementation is part of this map.