"use strict";

// Measure plugin JSX tree construction/traversal with representative synthetic data.
// This is a CPU-only renderer harness, not an Electron DOM/layout/paint benchmark.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { performance } = require('node:perf_hooks');

const root = path.resolve(__dirname, '..');
const sourceText = fs.readFileSync(path.join(root, 'desktop/plugin.js'), 'utf8');
const start = sourceText.indexOf('const ID =');
const end = sourceText.indexOf('\nconst plugin = {', start);
if (start < 0 || end < 0) throw new Error('Could not isolate ControlRoom component source.');
const componentSource = `${sourceText.slice(start, end)}\n;({ ControlRoom })`;

const sessions = Array.from({ length: 80 }, (_, index) => ({
  id: `session-${String(index).padStart(3, '0')}`,
  title: `Synthetic diagnostic session ${index}`,
  started_at: 1_750_000_000 + index * 60,
  ended_at: 1_750_000_360 + index * 60,
  coverage_state: index % 13 === 0 ? 'partial' : 'observed_no_known_gaps',
  call_count: 20 + (index % 12),
  call_counts: { total: 20 + (index % 12), ok: 18, failed: index % 5 === 0 ? 2 : 1, not_executed: 0, unknown: index % 13 === 0 ? 1 : 0 },
  call_success_rate: index % 13 === 0 ? null : 0.9
}));
const toolNames = Array.from({ length: 25 }, (_, index) => index % 2
  ? `mcp__server${index % 5}__tool${index}` : `tool_${index}`);
const toolBreakdown = toolNames.map((tool_name, index) => ({
  tool_name,
  calls: { total: 92, ok: 84, failed: index % 4 === 0 ? 5 : 3, not_executed: 1, unknown: 2 },
  rate_calls: { ok: 84, failed: 5, denominator: 89 },
  success_rate: 84 / 89,
  coverage: { total_sessions: 80, observed_sessions: 62, partial_sessions: 5, unknown_sessions: 3, not_captured_sessions: 10 }
}));
const repeated_failures = Array.from({ length: 20 }, (_, index) => ({
  tool_name: toolNames[index % toolNames.length],
  identity_kind: toolNames[index % toolNames.length].startsWith('mcp__') ? 'mcp' : 'tool',
  signature: `synthetic error signature ${index}`,
  match_basis: 'structured_error_code',
  distinct_failed_calls: 2 + (index % 10),
  affected_sessions: 2 + (index % 7),
  first_observed_at: 1_750_000_000 + index,
  last_observed_at: 1_750_000_500 + index,
  timeout_count: index % 3,
  retry_count: null,
  error_excerpt: `synthetic failure detail ${index}`,
  at_least_n_observed: index % 4 === 0,
  examples: [{ session_id: sessions[index].id, call_id: `tool-call-${index}`, observed_at: 1_750_000_500 + index }]
}));
const metrics = {
  calls: { total: 2303, ok: 2131, failed: 172, not_executed: 0, unknown: 0 },
  rate_calls: { ok: 2131, failed: 172, denominator: 2303 },
  success_rate: 2131 / 2303,
  included_sessions: 80, observed_sessions: 41, rate_included_sessions: 41,
  rate_excluded_sessions: 39, partial_sessions: 0, unknown_sessions: 0,
  excluded_sessions: 39, reset_cutoff: null
};
const provider_attempts = {
  attempts: { total: 133, ok: 120, failed: 13, unknown: 0 },
  retryable_failures: 7, unpaired_events: 0, success_rate: 120 / 133,
  supported_sessions: 80, observed_sessions: 41, partial_sessions: 0, unknown_sessions: 0,
  unsupported_sessions: 0, not_captured_sessions: 0, rate_excluded_sessions: 39,
  rate_attempts: { ok: 120, failed: 13, denominator: 133 }
};
const overview = {
  available: true,
  observer_schema_version: 'hermes.observer.v1',
  metrics,
  provider_attempts,
  approvals: { events: { requests: 18, allowed: 15, denied: 2, timed_out: 1, cancelled: 0, unknown: 0 }, supported_sessions: 80 },
  subagents: { events: { total: 23, succeeded: 19, failed: 2, interrupted: 1, unknown: 1 }, supported_sessions: 80 },
  sessions,
  tool_breakdown: toolBreakdown,
  repeated_failures
};
const timeline = Array.from({ length: 96 }, (_, index) => {
  const timestamp = 1_750_000_100 + index * 3;
  const family = index % 8;
  if (family === 0 || family === 5) return { id: `message-${index}`, kind: 'message', role: family === 0 ? 'user' : 'assistant', content: `Synthetic visible turn ${index}`, timestamp, has_tool_calls: family === 5 };
  if (family === 1) return { id: `provider-${index}`, kind: 'provider_attempt', provider: 'synthetic-provider', model: 'synthetic-model', outcome: index % 3 ? 'ok' : 'failed', timestamp };
  if (family === 2 || family === 6) return { id: `tool-call-${index}`, kind: 'tool_call', tool_name: toolNames[index % toolNames.length], outcome: index % 4 ? 'ok' : 'failed', timestamp, duration_ms: 28 + index, capture_state: 'paired', session_id: sessions[0].id, error_code: index % 4 ? null : 'E_SYNTHETIC' };
  if (family === 3) return { id: `approval-${index}`, kind: 'approval', outcome: index % 4 ? 'allowed' : 'denied', timestamp, capture_state: 'paired' };
  return { id: `subagent-${index}`, kind: 'subagent', status: index % 5 ? 'succeeded' : 'interrupted', child_role: 'researcher', child_goal: `Synthetic child goal ${index}`, timestamp, capture_state: 'paired' };
});
const trace = {
  session: sessions[0], calls: [], timeline,
  provider_attempts: [], approvals: [], subagents: [],
  provider_coverage: { support_state: 'supported', coverage_state: 'observed_no_known_gaps' },
  approval_coverage: { support_state: 'supported', coverage_state: 'observed_no_known_gaps' },
  subagent_coverage: { support_state: 'supported', coverage_state: 'observed_no_known_gaps' }
};

let requestedView = 'control';
let hookIndex = 0;
const jsx = (type, props) => ({ type, props: props || {} });
const host = { state: { profile: { get: () => 'benchmark-profile', subscribe: () => () => {} } } };
const hooks = {
  host, jsx, jsxs: jsx, Intl, Date, Number, Object, Array, String, encodeURIComponent,
  useValue: atom => atom.get(), useRef: value => ({ current: value }), useEffect: () => {},
  useState: initial => {
    const index = hookIndex++;
    const values = [overview, sessions[0].id, trace, '', false, 0, 'Overall', false, {}, '', '', { callId: '', message: '' }, 'tool-call-2', ''];
    if (index === 14) return [requestedView, () => {}];
    return [index < values.length ? values[index] : initial, () => {}];
  }
};
const { ControlRoom } = vm.runInNewContext(componentSource, hooks);
function traverse(node) {
  if (node == null || typeof node === 'boolean') return 0;
  if (Array.isArray(node)) return node.reduce((count, child) => count + traverse(child), 0);
  if (typeof node !== 'object') return 0;
  if (typeof node.type === 'function') return traverse(node.type(node.props || {}));
  return 1 + traverse(node.props?.children);
}
function renderSample(view) {
  requestedView = view;
  hookIndex = 0;
  return traverse(ControlRoom({ rest: async () => ({}) }));
}
function summarize(samples) {
  const ordered = [...samples].sort((a, b) => a - b);
  const at = p => ordered[Math.max(0, Math.ceil(p * ordered.length) - 1)];
  return { p50_ms: Number(at(0.50).toFixed(3)), p95_ms: Number(at(0.95).toFixed(3)), samples: ordered.length };
}
function measure(view) {
  for (let i = 0; i < 5; i++) renderSample(view);
  const samples = [];
  for (let i = 0; i < 60; i++) {
    const start = performance.now();
    renderSample(view);
    samples.push(performance.now() - start);
  }
  return summarize(samples);
}

const results = Object.fromEntries(['control', 'failures', 'ledger'].map(view => [view, measure(view)]));
console.log(JSON.stringify({
  environment: { node: process.version, platform: process.platform, arch: process.arch },
  dataset: { sessions: sessions.length, observed_tool_calls: metrics.calls.total, tool_identities: toolBreakdown.length, findings: repeated_failures.length, selected_trace_events: timeline.length },
  renderer: 'ControlRoom element construction + recursive JSX-tree traversal; no Electron DOM/layout/paint',
  views: results
}, null, 2));
