# hermes-observability-plugin

A native Hermes Desktop observability plugin. The consolidated specification is in [`docs/ISA.md`](docs/ISA.md); the Control room reference is [`docs/prototype/session-overview.html`](docs/prototype/session-overview.html). Version history is in [`CHANGELOG.md`](CHANGELOG.md).

## Control room

![Hermes Desktop Observability Control room with colorful status indicators, session activity, tool/MCP breakdown, and event inspector.](docs/assets/observability-control-room.png)

## Version 1.0.0

This release implements the first-release outcomes and safety boundary defined in the [ISA](docs/ISA.md). See the [Control room user guide](docs/USER_GUIDE.md) for the capture and investigation workflows; the [release evidence](docs/release/1.0.0-evidence.md) records automated verification and measured performance with its limits.

- Capture is prospective and local to the connected profile. Only sessions observed starting after the plugin is enabled are eligible; already-open sessions are skipped.
- The native Desktop UI provides a triage-first Control room, a cross-session Failure queue, and a searchable Trace ledger. It lists sessions within Overall, Today, or Since Reset windows, labels uncaptured/partial/observed coverage, shows per-session and per-tool known outcomes, and drills into mixed-family operational traces.
- Repeated-failure findings group explicit failed logical tool calls across at least two sessions by exact observer tool identity and a structured error code/type, or an exact error message after masking UUIDs and ISO timestamps only. MCP's observer name is the canonical combined `mcp__server__tool` identity; server/tool names remain part of the match key. No fuzzy matching or generated remediation is used.
- Findings show the match basis, distinct calls, affected sessions, first/last observation, observed timeout count, a short error excerpt, and example-call links into source-backed call detail. When the selected population includes excluded coverage, findings are labeled “at least N observed.” Provider API attempts from the main-loop observer hooks and retry metadata are displayed as a separate event family and never inflate tool invocation counts. Repeated-failure retry evidence is attached only from a paired provider attempt with exact session, turn, and API-request IDs; ambiguous, unmatched, or missing links remain explicitly unknown/partial. Unpaired completion callbacks remain visible but are excluded from confirmed attempt totals to avoid double-counting when lifecycle correlation is missing or mismatched. Approval requests and decisions are captured as another independent family, with explicit allow/deny/timeout/cancellation/unknown states, unmatched lifecycle visibility, and separate support/gap counts; they never alter tool outcomes or success-rate math. Subagent starts/stops are captured as a separate event family with parent/child IDs, goal, role, status, duration, and available summary; child links navigate to their source session, unmatched events remain Partial/Unknown, and child transcripts are never copied inline.
- Optional documentation links are configured in the plugin's profile-scoped settings, keyed by the exact observer tool identity, and limited to HTTPS. No URL is inferred or generated.
- Today uses the Desktop user's local calendar timezone. Sessions are included or excluded as a whole by their start timestamp, including sessions that continue across a day or reset boundary.
- Reset Metrics advances the plugin-owned Since Reset cutoff and never deletes or mutates source sessions or telemetry rows. Repeating reset replaces the cutoff; Overall remains unchanged.
- Each window reports included sessions and coverage/rate exclusions. Unknown and not-executed outcomes remain visible and are excluded from the success-rate denominator. Rates use only schema-compatible, gap-free sessions; observed counts from incomplete populations are lower-bound evidence.
- Call detail reads visible arguments/result and nearby visible conversation from canonical session messages on demand using exact session/tool-call IDs, alongside captured error metadata; it never copies source content into plugin telemetry, and missing or ambiguous matches remain unavailable.
- Store versioned, plugin-owned metadata tables in the connected profile's existing `state.db`; source-session deletion cascades to plugin rows.
- Persist only observer identity/correlation fields, timestamps, tool name, explicit outcome, duration, and structured error details. For provider requests, retain only provider/model identity, opaque API-attempt correlation, attempt/retry metadata, timing, allow-listed numeric usage counters, and explicit error metadata. Approval telemetry allowlists surfaces and decisions and retains supplied session/turn/tool-call IDs and timing; approval-pattern labels, command text, and descriptions are never retained. Subagent telemetry retains supplied parent/child IDs, role, bounded goal and summary, status, and duration; never copy child transcripts. Never copy tool arguments/results, sanitized/raw provider payloads, reasoning, or error messages.

- Failed-call detail includes a user-triggered Analyze action. It sends a bounded, allow-listed evidence prompt to a fresh session on the exact profile recorded with the detail; profile mismatches and stale details abort before submission. Source references and unavailable evidence are labeled. No provider reasoning, raw provider payloads, or child transcripts are included. A durable, profile-local marker under `.hermes-observability/analyze-read-only` (SHA-256 session-ID filename; no evidence stored) enables a `pre_tool_call` policy that blocks every tool in the follow-up session, including when session identity is unavailable, and fails closed on policy errors. Remediation must happen in a separate normal session after explicit approval. The action does not modify the source session or telemetry.

“Observed — no known gaps” is not a completeness guarantee: Hermes observer hooks fail open. The plugin stays opt-in through Hermes' plugin trust gates; after enablement, collection starts prospectively. The plugin uses a thread-local connection for its profile's `state.db`. On WAL databases, if no `database.synchronous` preference is set, only that plugin connection defaults to SQLite `NORMAL`; any explicit Hermes setting is honored, and non-WAL journal modes are left unchanged. Recent plugin-telemetry commits may be lost on sudden power failure, but the canonical source-session data is not written by this plugin. The measured synthetic benchmark and its limits are recorded in [release evidence](docs/release/1.0.0-evidence.md); it does not claim a hardware-independent latency guarantee or include model generation/Electron paint.

## Installation

The plugin has two parts: an agent/backend half that runs with the gateway profile, and a Desktop UI half that runs in the Hermes Desktop app. When Desktop connects to a remote gateway, install the halves on their respective machines.

### Install and enable the gateway half

Run this on the gateway host, using the Hermes profile whose sessions you want to observe:

```sh
hermes plugins install --enable https://github.com/cyferlawyn/hermes-observability-plugin.git
```

The `--enable` flag enables the agent/backend half in that profile. Verify its status with:

```sh
hermes plugins list --plain --no-bundled
```

### Install and enable the Desktop UI

In Hermes Desktop on the client machine, open **Settings → Capabilities → Plugins → Install from Git** and enter the same repository URL. For a remote gateway where the agent half is already installed, choose **Desktop only** (leave the agent/backend half unchecked). After installation, enable the **Observability** Desktop plugin. It adds an **Observability** item to the sidebar.

If both the gateway and Desktop app use the same local Hermes home, the installer can install both halves together instead.

### Verify capture

Start a new Hermes session after enabling the gateway half, then make a tool/MCP call. The Control room should list that session and its observed call. Capture is prospective: sessions that were already open or started before enablement are not backfilled. Only operational metadata is persisted in plugin telemetry; visible messages and tool arguments/results remain in Hermes' canonical session store and are read on demand only for exact source-backed call detail.

## Development

Run the storage and collector tests with:

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_desktop_analyze.cjs
node --check desktop/plugin.js
hermes plugins validate .
hermes plugins doctor .
python3 benchmarks/benchmark_release.py
node benchmarks/benchmark_desktop.cjs
```

The package layout follows Hermes' unified-plugin convention: `plugin.yaml` and `__init__.py` for the observer hooks, `dashboard/` for the profile-routed backend, and `desktop/plugin.js` for the local UI.
