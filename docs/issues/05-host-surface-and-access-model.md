# Choose where the observability experience lives

Type: grilling
Status: resolved

Blocked by: 01, 02

## Question

Which host surface best delivers the chosen workflows: a Hermes Desktop integration, a Hermes Web Dashboard plugin/tab, or a separate local SPA (or a deliberately staged combination)?

Compare supported extension points, ability to browse existing sessions, access to local session data, distribution/maintenance burden, privacy and authentication, and whether the view should follow one profile or multiple profiles. Hermes documentation distinguishes the Web Dashboard plugin system from the native Desktop plugin SDK; do not treat them as one extension mechanism. Recommend one initial surface and explain what evidence would justify another.

## Comments

- User uses Hermes Desktop with a remote gateway; the gateway machine differs from the dashboard client. A LAN-hosted SPA is a plausible alternative. User rarely uses the Hermes Web Dashboard, so a Web Dashboard plugin is not an attractive default. Compare local UI with remote-profile data access when choosing.
- Verified from current Desktop SDK docs/source: plugin UI runs on the Desktop machine; `ctx.rest` / profile-routed host APIs can target a remote gateway, while the plugin's Python route runs on the backend profile and can read its session store. UI and backend halves must be installed/enabled on their respective machines; remote connection authentication is handled by Desktop rather than exposing connection tokens to plugin code.
- A standalone LAN SPA can use the documented session REST API, but its `API_SERVER_KEY` grants the broad agent toolset, including terminal; CORS is not a least-privilege credential. A browser-held key is not appropriate. A separate server-side proxy/BFF or a purpose-built scoped endpoint is needed. Sources: [Desktop Plugin SDK](https://hermes-agent.nousresearch.com/docs/developer-guide/desktop-plugin-sdk), [Multi-connection Desktop](https://hermes-agent.nousresearch.com/docs/user-guide/multi-connection-desktop), [API Server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server).

## Answer

Resolved: use a native Hermes Desktop plugin for the initial observability surface. Its UI runs on the Desktop inspection machine, and its Python backend runs on the connected remote gateway/profile, where it can access that profile's session data. Route UI requests through the Desktop plugin host API to the backend plugin API; do not have renderer code reach into the remote SQLite database. Install the UI half in the Desktop machine's app-level plugin directory and install/enable the backend half in the personal remote profile. Keep the initial view scoped to the connected personal profile rather than aggregating profiles.

This fits the user's remote-gateway topology without a separately hosted SPA, and avoids the broad `API_SERVER_KEY` exposure required by a browser SPA using the standard API. The user approved this direction. A future standalone SPA would need a server-side proxy/BFF or a purpose-built least-privilege read-only endpoint; do not place `API_SERVER_KEY` in browser code. The Web Dashboard plugin is not selected because the user usually avoids the Hermes Web Dashboard.

Evidence: [Desktop Plugin SDK](https://hermes-agent.nousresearch.com/docs/developer-guide/desktop-plugin-sdk) documents local Desktop UI plugins and Python backend routes reachable through `ctx.rest`; [Multi-connection Desktop](https://hermes-agent.nousresearch.com/docs/user-guide/multi-connection-desktop) documents connection/profile-aware requests; [API Server authentication](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server#authentication) states `API_SERVER_KEY` grants access to the agent toolset, including terminal. Verified against local source at commit `dce1e9b37581dd62e480a9064dc04a709c2940d3`.