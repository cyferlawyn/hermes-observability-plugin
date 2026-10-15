import { host, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'hermes-observability-plugin'
const EMPTY_METRICS = {
  calls: { total: 0, ok: 0, failed: 0, not_executed: 0, unknown: 0 },
  rate_calls: { ok: 0, failed: 0, denominator: 0 },
  success_rate: null,
  total_sessions: 0,
  included_sessions: 0,
  captured_sessions: 0,
  observed_sessions: 0,
  rate_included_sessions: 0,
  partial_sessions: 0,
  unknown_sessions: 0,
  excluded_sessions: 0,
  rate_excluded_sessions: 0
}

const EMPTY_PROVIDER_ATTEMPTS = {
  attempts: { total: 0, ok: 0, failed: 0, unknown: 0 },
  unpaired_events: 0,
  retryable_failures: 0,
  success_rate: null,
  supported_sessions: 0,
  observed_sessions: 0,
  partial_sessions: 0,
  unknown_sessions: 0,
  unsupported_sessions: 0,
  not_captured_sessions: 0,
  rate_excluded_sessions: 0,
  rate_attempts: { ok: 0, failed: 0, denominator: 0 }
}
const EMPTY_APPROVALS = {
  events: { requests: 0, allowed: 0, denied: 0, timed_out: 0, cancelled: 0, unknown: 0 },
  unpaired_responses: 0,
  supported_sessions: 0,
  observed_sessions: 0,
  partial_sessions: 0,
  unknown_sessions: 0,
  unsupported_sessions: 0,
  not_captured_sessions: 0
}

const rootStyle = {
  display: 'flex', flexDirection: 'column', gap: 12, padding: 16,
  height: '100%', minHeight: 0, overflow: 'auto', color: 'var(--ui-text-primary)',
  fontSize: 12, lineHeight: 1.45
}
const cardStyle = {
  border: '1px solid var(--ui-stroke-secondary)', borderRadius: 8, padding: 12,
  minWidth: 0
}
const mutedStyle = { color: 'var(--ui-text-tertiary)' }
const buttonStyle = {
  color: 'var(--ui-text-primary)', background: 'transparent',
  border: '1px solid var(--ui-stroke-secondary)', borderRadius: 5,
  padding: '5px 9px', cursor: 'pointer', font: 'inherit'
}

function displayTime(value) {
  if (value === null || value === undefined) return '—'
  const date = new Date(Number(value) * 1000)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString()
}

function coverageLabel(state) {
  return ({
    observed_no_known_gaps: 'Observed — no known gaps',
    partial: 'Partial',
    unknown: 'Unknown',
    not_captured: 'Not captured'
  })[state] || 'Unknown'
}

function outcomeLabel(outcome) {
  return ({
    ok: 'Success', failed: 'Failed', not_executed: 'Not executed', unknown: 'Unknown'
  })[outcome] || 'Unknown'
}

function providerCoverageLabel(coverage) {
  const support = coverage?.support_state || 'unknown'
  const state = coverage?.coverage_state || 'unknown'
  const supportText = ({ supported: 'supported', unsupported: 'unsupported', unknown: 'support unknown' })[support]
  const stateText = ({
    observed_no_known_gaps: 'Observed — no known gaps',
    partial: 'Partial',
    unknown: 'Unknown',
    not_captured: 'Not captured'
  })[state] || 'Unknown'
  return `${supportText} · ${stateText}`
}

function providerOutcomeLabel(outcome) {
  return ({ ok: 'Response received', failed: 'Request failed', unknown: 'Outcome unknown' })[outcome] || 'Outcome unknown'
}

function approvalOutcomeLabel(outcome, captureState) {
  if (captureState === 'request_only') return 'Request · no observed decision'
  if (captureState === 'response_only') return 'Decision · request unmatched'
  return ({
    allowed: 'Allowed', denied: 'Denied', timed_out: 'Timed out',
    cancelled: 'Cancelled', unknown: 'Unknown'
  })[outcome] || 'Unknown'
}

function approvalCoverageLabel(coverage) {
  if (!coverage) return 'Unknown approval coverage'
  const support = coverage.support_state || 'unknown'
  const state = coverage.coverage_state || 'unknown'
  const stateText = ({
    observed_no_known_gaps: 'Observed — no known gaps',
    partial: 'Partial', unknown: 'Unknown', not_captured: 'Not captured'
  })[state] || 'Unknown'
  return `${support} · ${stateText}`
}

function matchBasisLabel(basis) {
  return ({
    structured_error_code: 'Structured error code',
    structured_error_type: 'Structured error type',
    exact_error_message: 'Exact error message',
    exact_message_masked_uuid: 'Exact message; UUIDs masked',
    exact_message_masked_timestamp: 'Exact message; ISO timestamps masked',
    exact_message_masked_uuid_timestamp: 'Exact message; UUIDs and ISO timestamps masked'
  })[basis] || 'Deterministic exact match'
}

function formatDetailValue(value) {
  if (value === null || value === undefined) return 'Unavailable'
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

function Metric({ label, value, detail }) {
  return jsxs('div', {
    style: { ...cardStyle, flex: '1 1 130px' },
    children: [
      jsx('div', { style: mutedStyle, children: label }),
      jsx('div', { style: { fontSize: 20, fontWeight: 650, marginTop: 3 }, children: value }),
      jsx('div', { style: { ...mutedStyle, marginTop: 2 }, children: detail })
    ]
  })
}

function ControlRoom({ rest }) {
  const [overview, setOverview] = useState(null)
  const [selectedId, setSelectedId] = useState('')
  const [trace, setTrace] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [refreshKey, setRefreshKey] = useState(0)
  const [timeWindow, setTimeWindow] = useState('Overall')
  const [resetting, setResetting] = useState(false)
  const [details, setDetails] = useState({})
  const [detailLoading, setDetailLoading] = useState('')
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'

  async function inspectCall(event) {
    setDetailLoading(event.id)
    setDetails(value => ({ ...value, [event.id]: null }))
    try {
      const detail = await rest(`/sessions/${encodeURIComponent(event.session_id)}/calls/${encodeURIComponent(event.id)}/detail`)
      setDetails(value => ({ ...value, [event.id]: detail }))
    } catch (err) {
      setDetails(value => ({ ...value, [event.id]: { error: err?.message || 'Could not read source detail.' } }))
    } finally {
      setDetailLoading('')
    }
  }

  function inspectFindingExample(example) {
    setDetails({})
    setSelectedId(example.session_id)
    inspectCall({ id: example.call_id, session_id: example.session_id })
  }

  async function resetMetrics() {
    setResetting(true)
    setError('')
    try {
      await rest('/reset', { method: 'POST' })
      setSelectedId('')
      setTimeWindow('Since Reset')
      setRefreshKey(value => value + 1)
    } catch (err) {
      setError(err?.message || 'Could not reset the metrics window.')
    } finally {
      setResetting(false)
    }
  }

  useEffect(() => {
    let live = true
    setLoading(true)
    setError('')
    rest(`/overview?window=${encodeURIComponent(timeWindow)}&timezone=${encodeURIComponent(timezone)}`)
      .then(data => {
        if (!live) return
        setOverview(data)
        const sessions = Array.isArray(data?.sessions) ? data.sessions : []
        if (!sessions.some(session => session.id === selectedId)) {
          setSelectedId(sessions[0]?.id || '')
        }
      })
      .catch(err => { if (live) setError(err?.message || 'Could not read this profile’s telemetry.') })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [rest, refreshKey, timeWindow, timezone])

  useEffect(() => {
    if (!selectedId) {
      setTrace(null)
      return undefined
    }
    let live = true
    setTrace(null)
    rest(`/sessions/${encodeURIComponent(selectedId)}/trace?window=${encodeURIComponent(timeWindow)}&timezone=${encodeURIComponent(timezone)}`)
      .then(data => { if (live) setTrace(data) })
      .catch(err => { if (live) setError(err?.message || 'Could not read this session trace.') })
    return () => { live = false }
  }, [rest, selectedId, refreshKey, timeWindow, timezone])

  const sessions = Array.isArray(overview?.sessions) ? overview.sessions : []
  const metrics = overview?.metrics || EMPTY_METRICS
  const providerMetrics = overview?.provider_attempts || EMPTY_PROVIDER_ATTEMPTS
  const approvalMetrics = overview?.approvals || EMPTY_APPROVALS
  const approvalCounts = approvalMetrics.events || EMPTY_APPROVALS.events
  const approvalCoverage = trace?.approval_coverage
  const approvalEvents = Array.isArray(trace?.approvals) ? trace.approvals : []
  const providerCounts = providerMetrics.attempts || EMPTY_PROVIDER_ATTEMPTS.attempts
  const providerRate = providerMetrics.success_rate === null || providerMetrics.success_rate === undefined
    ? '—' : `${(providerMetrics.success_rate * 100).toFixed(1)}%`
  const providerEvents = Array.isArray(trace?.provider_attempts) ? trace.provider_attempts : []
  const providerCoverage = trace?.provider_coverage
  const calls = metrics.calls || EMPTY_METRICS.calls
  const rateCalls = metrics.rate_calls || EMPTY_METRICS.rate_calls
  const rate = metrics.success_rate === null || metrics.success_rate === undefined
    ? '—' : `${(metrics.success_rate * 100).toFixed(1)}%`
  const windowDescription = timeWindow === 'Today'
    ? 'Today uses the connected profile’s local calendar day.'
    : timeWindow === 'Since Reset'
      ? `Sessions started at or after the latest reset cutoff${metrics.reset_cutoff == null ? ' (no reset has been recorded)' : ` (${displayTime(metrics.reset_cutoff)})`}.`
      : 'Overall includes all still-available sessions.'
  const selected = sessions.find(session => session.id === selectedId)
    || (trace?.session?.id === selectedId ? trace.session : null)
  const events = Array.isArray(trace?.calls) ? trace.calls : []
  const findings = Array.isArray(overview?.repeated_failures) ? overview.repeated_failures : []
  const findingsCoverageIncomplete = Number(metrics.rate_excluded_sessions || 0) > 0

  return jsxs('div', {
    style: rootStyle,
    children: [
      jsxs('header', {
        style: { display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 },
        children: [
          jsxs('div', { children: [
            jsx('h1', { style: { fontSize: 18, margin: '0 0 3px' }, children: 'Control room' }),
            jsx('div', { style: mutedStyle, children: 'Prospective operational traces for the connected profile.' })
          ] }),
          jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }, children: [
            jsx('select', {
              'aria-label': 'Metrics time window',
              value: timeWindow,
              onChange: event => { setDetails({}); setSelectedId(''); setTimeWindow(event.target.value) },
              style: { ...buttonStyle, padding: '5px 7px' },
              children: [
                jsx('option', { value: 'Overall', children: 'Overall' }),
                jsx('option', { value: 'Today', children: 'Today' }),
                jsx('option', { value: 'Since Reset', children: 'Since Reset' })
              ]
            }),
            jsx('button', {
              type: 'button', style: buttonStyle, disabled: resetting,
              onClick: resetMetrics,
              children: resetting ? 'Resetting…' : 'Reset Metrics'
            }),
            jsx('button', {
              type: 'button', style: buttonStyle,
              onClick: () => setRefreshKey(value => value + 1),
              children: loading ? 'Loading…' : 'Refresh'
            })
          ] })
        ]
      }),
      error ? jsx('div', {
        role: 'alert', style: { ...cardStyle, borderColor: 'var(--ui-danger, var(--ui-stroke-secondary))' },
        children: error
      }) : null,
      overview?.available === false ? jsx('div', {
        style: cardStyle, children: overview.reason || 'No session database is available for this profile.'
      }) : null,
      jsxs('div', {
        style: { display: 'flex', flexWrap: 'wrap', gap: 8 },
        children: [
          jsx(Metric, { label: 'Observed tool calls', value: calls.total, detail: `${calls.ok} success · ${calls.failed} failed` }),
          jsx(Metric, { label: 'Success rate', value: rate, detail: `${rateCalls.denominator} explicit outcomes · ${metrics.rate_included_sessions || 0} eligible / ${metrics.rate_excluded_sessions || 0} excluded for coverage` }),
          jsx(Metric, { label: 'Not executed / unknown', value: `${calls.not_executed} / ${calls.unknown}`, detail: 'Excluded from rate denominator' }),
          jsx(Metric, { label: 'Coverage', value: `${metrics.included_sessions || 0} sessions included`, detail: `${metrics.observed_sessions || 0} rate-eligible · ${metrics.partial_sessions || 0} partial · ${metrics.unknown_sessions || 0} unknown · ${metrics.excluded_sessions || 0} not captured` })
        ]
      }),
      jsx('div', {
        style: { ...mutedStyle, fontSize: 11 },
        children: `${timeWindow} window. ${windowDescription} Sessions are included by start time; a session crossing a boundary stays wholly on its start side. Rates use only compatible observed sessions; excluded coverage remains visible. Observed counts from incomplete sessions are lower-bound evidence. “Observed — no known gaps” is not a completeness guarantee because observer hooks fail open.`
      }),
      jsxs('section', {
        style: cardStyle,
        children: [
          jsxs('div', { children: [
            jsx('h2', { style: { fontSize: 13, margin: '0 0 3px' }, children: 'Provider API attempts' }),
            jsx('div', { style: mutedStyle, children: 'Main-loop provider requests exposed by observer hooks; request attempts and observer-reported retry metadata are separate from tool/MCP invocation counts. Unpaired callbacks are shown separately. Payloads and reasoning are never stored.' })
          ] }),
          jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }, children: [
            jsx(Metric, { label: 'API attempts', value: providerCounts.total, detail: `${providerCounts.ok} responses · ${providerCounts.failed} failed · ${providerCounts.unknown} unknown · ${providerMetrics.unpaired_events || 0} unpaired callbacks excluded` }),
            jsx(Metric, { label: 'Retryable API failures', value: providerMetrics.retryable_failures || 0, detail: 'Observer-classified retryable failures; not a count of confirmed retries' }),
            jsx(Metric, { label: 'API success rate', value: providerRate, detail: `${(providerMetrics.rate_attempts || EMPTY_PROVIDER_ATTEMPTS.rate_attempts).denominator} explicit outcomes · ${providerMetrics.observed_sessions || 0} compatible / ${providerMetrics.rate_excluded_sessions || 0} excluded sessions` }),
            jsx(Metric, { label: 'Provider-family coverage', value: `${providerMetrics.supported_sessions || 0} supported`, detail: `${providerMetrics.observed_sessions || 0} observed · ${providerMetrics.partial_sessions || 0} partial · ${providerMetrics.unknown_sessions || 0} unknown · ${providerMetrics.unsupported_sessions || 0} unsupported · ${providerMetrics.not_captured_sessions || 0} not captured` })
          ] }),
          jsx('div', { style: { ...mutedStyle, marginTop: 5 }, children: providerCounts.total === 0 && providerMetrics.supported_sessions === 0
            ? 'No compatible provider API observer coverage is available in this window; zero attempts cannot be inferred.'
            : `Coverage is provider-family and schema-specific. Eligible sessions with no known lifecycle gaps may contribute a true zero; partial, unknown, and uncaptured sessions are excluded from the API success rate. Schema: ${overview?.observer_schema_version || 'unknown'}.` })
        ]
      }),
      jsxs('section', {
        style: cardStyle,
        children: [
          jsxs('div', { children: [
            jsx('h2', { style: { fontSize: 13, margin: '0 0 3px' }, children: 'Approval lifecycle' }),
            jsx('div', { style: mutedStyle, children: 'Observer-reported approval requests and decisions are a separate event family. Approval outcomes do not classify tool execution or change call success-rate math; command text is not retained.' })
          ] }),
          jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }, children: [
            jsx(Metric, {
              label: 'Approval requests observed',
              value: approvalMetrics.supported_sessions > 0 ? `≥${approvalCounts.requests}` : '—',
              detail: approvalMetrics.supported_sessions > 0
                ? `${approvalCounts.allowed} allowed · ${approvalCounts.denied} denied · ${approvalCounts.timed_out} timed out · ${approvalCounts.cancelled} cancelled · ${approvalCounts.unknown} unknown`
                : 'No compatible approval-family support; zero requests cannot be inferred.'
            }),
            jsx(Metric, {
              label: 'Unmatched decisions',
              value: approvalMetrics.supported_sessions > 0 ? approvalMetrics.unpaired_responses : '—',
              detail: 'Visible decisions without a safely matched request; excluded from request totals.'
            }),
            jsx(Metric, {
              label: 'Approval coverage',
              value: `${approvalMetrics.observed_sessions || 0} observed`,
              detail: `${approvalMetrics.supported_sessions || 0} supported · ${approvalMetrics.partial_sessions || 0} partial · ${approvalMetrics.unknown_sessions || 0} unknown · ${approvalMetrics.unsupported_sessions || 0} unsupported · ${approvalMetrics.not_captured_sessions || 0} not captured`
            })
          ] }),
          jsx('div', { style: { ...mutedStyle, marginTop: 5 }, children: approvalMetrics.supported_sessions > 0
            ? 'Counts include only sessions with compatible approval observer support; events from unknown or unsupported schema sessions are excluded and cannot be interpreted as zero. Incomplete and unmatched lifecycles remain visible.'
            : 'No compatible approval observer coverage is available in this window; do not interpret missing events as zero.' })
        ]
      }),
      jsxs('section', {
        style: cardStyle,
        children: [
          jsxs('div', { children: [
            jsx('h2', { style: { fontSize: 13, margin: '0 0 3px' }, children: 'Repeated failures' }),
            jsx('div', { style: mutedStyle, children: 'Exact tool identity; structured error code/type preferred, otherwise exact message with UUID/ISO-timestamp masking only. No fuzzy grouping or generated remediation.' })
          ] }),
          findings.length === 0
            ? jsx('div', {
              style: { ...mutedStyle, marginTop: 8 },
              children: overview === null
                ? 'Loading repeated-failure evidence…'
                : findingsCoverageIncomplete
                  ? 'No qualifying signature found in observed events. Coverage is incomplete, so this is not evidence that failures are absent.'
                  : 'No qualifying cross-session failure signature in this window.'
            })
            : findings.map(finding => jsxs('article', {
              style: { borderTop: '1px solid var(--ui-stroke-secondary)', padding: '9px 0 3px', marginTop: 8 },
              children: [
                jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: 8 }, children: [
                  jsxs('strong', { children: [finding.identity_kind === 'mcp' ? 'MCP · ' : 'Tool · ', finding.tool_name] }),
                  jsx('span', { style: mutedStyle, children: finding.at_least_n_observed
                    ? `At least ${finding.distinct_failed_calls} observed failed invocations · ${finding.affected_sessions} sessions`
                    : `${finding.distinct_failed_calls} distinct failed invocations · ${finding.affected_sessions} sessions` })
                ] }),
                jsxs('div', { style: { marginTop: 4, overflowWrap: 'anywhere' }, children: [
                  jsx('strong', { children: `${matchBasisLabel(finding.match_basis)}: ` }), finding.signature
                ] }),
                jsx('div', { style: { ...mutedStyle, marginTop: 3 }, children: `First ${displayTime(finding.first_observed_at)} · Last ${displayTime(finding.last_observed_at)} · ${finding.timeout_count} observed timeouts · provider API attempts are tracked separately` }),
                jsx('div', { style: { marginTop: 4, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }, children: finding.error_excerpt }),
                jsxs('div', { style: { marginTop: 6 }, children: [
                  jsx('strong', { children: 'Example calls' }),
                  ...finding.examples.map(example => jsx('button', {
                    type: 'button',
                    style: { ...buttonStyle, display: 'block', width: '100%', textAlign: 'left', marginTop: 4 },
                    onClick: () => inspectFindingExample(example),
                    children: `${displayTime(example.observed_at)} · session ${example.session_id} · call ${example.call_id}`
                  }, `${example.session_id}-${example.call_id}`))
                ] })
              ]
            }, `${finding.tool_name}-${finding.signature}-${finding.match_basis}`))
        ]
      }),
      jsxs('div', {
        style: { display: 'grid', gridTemplateColumns: 'minmax(190px, 0.8fr) minmax(280px, 1.7fr)', gap: 10, minHeight: 280 },
        children: [
          jsxs('section', { style: { ...cardStyle, overflow: 'auto', minHeight: 0 }, children: [
            jsx('h2', { style: { fontSize: 13, margin: '0 0 8px' }, children: 'Sessions' }),
            loading && !overview ? jsx('div', { style: mutedStyle, children: 'Loading sessions…' }) : null,
            !loading && sessions.length === 0 ? jsx('div', { style: mutedStyle, children: 'No sessions found.' }) : null,
            ...sessions.map(session => jsx('button', {
              type: 'button',
              onClick: () => { setDetails({}); setSelectedId(session.id) },
              style: {
                display: 'block', width: '100%', textAlign: 'left', padding: '8px 7px', marginBottom: 4,
                border: '1px solid var(--ui-stroke-secondary)', borderRadius: 5,
                background: selectedId === session.id ? 'var(--chrome-action-hover)' : 'transparent',
                color: 'var(--ui-text-primary)', cursor: 'pointer', font: 'inherit'
              },
              children: jsxs('span', { style: { display: 'flex', flexDirection: 'column', gap: 2 }, children: [
                jsx('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: session.title || session.id }),
                jsx('span', { style: mutedStyle, children: `${displayTime(session.started_at)} · ${coverageLabel(session.coverage_state)}` }),
                jsx('span', { style: mutedStyle, children: `${session.call_count} observed calls` })
              ] })
            }, session.id))
          ] }),
          jsxs('section', { style: { ...cardStyle, overflow: 'auto', minHeight: 0 }, children: [
            selected ? jsxs('div', { style: { marginBottom: 10 }, children: [
              jsx('h2', { style: { fontSize: 13, margin: '0 0 3px' }, children: selected.title || selected.id }),
              jsx('div', { style: mutedStyle, children: `${displayTime(selected.started_at)} · ${coverageLabel(selected.coverage_state)}` })
            ] }) : jsx('div', { style: mutedStyle, children: 'Select a session to inspect its captured trace.' }),
            selected && events.length === 0 ? jsx('div', { style: mutedStyle, children: selected.coverage_state === 'observed_no_known_gaps' ? 'No observed tool calls in this session.' : 'No captured tool calls. This session may predate collection.' }) : null,
            ...events.map(event => jsxs('article', {
              style: { borderTop: '1px solid var(--ui-stroke-secondary)', padding: '8px 0' },
              children: [
                jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: 8 }, children: [
                  jsx('strong', { children: event.tool_name }),
                  jsx('span', { style: mutedStyle, children: outcomeLabel(event.outcome) })
                ] }),
                jsx('div', { style: { ...mutedStyle, marginTop: 2 }, children: `${displayTime(event.started_at || event.completed_at)}${event.duration_ms == null ? '' : ` · ${event.duration_ms} ms`}` }),
                event.error_code || event.error_type || event.error_message ? jsxs('div', {
                  style: { marginTop: 5, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' },
                  children: [event.error_code ? `${event.error_code}: ` : '', event.error_type ? `${event.error_type}: ` : '', event.error_message || 'Explicit failure; no message supplied.']
                }) : null,
                event.capture_state !== 'paired' ? jsx('div', { style: { ...mutedStyle, marginTop: 4 }, children: 'Lifecycle incomplete; outcome coverage is partial.' }) : null,
                jsx('button', {
                  type: 'button', style: { ...buttonStyle, marginTop: 7 },
                  onClick: () => inspectCall(event),
                  children: detailLoading === event.id ? 'Loading source detail…' : 'Inspect source detail'
                }),
                details[event.id] ? (details[event.id].error
                  ? jsx('div', { role: 'alert', style: { marginTop: 7 }, children: details[event.id].error })
                  : details[event.id].available
                    ? jsxs('div', { style: { marginTop: 8, display: 'grid', gap: 7 }, children: [
                      jsxs('div', { children: [
                        jsx('strong', { children: 'Arguments' }),
                        jsx('pre', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', margin: '3px 0' }, children: formatDetailValue(details[event.id].source.arguments) })
                      ] }),
                      jsxs('div', { children: [
                        jsx('strong', { children: 'Source result' }),
                        details[event.id].source.result_status === 'ambiguous'
                          ? jsx('div', { style: mutedStyle, children: 'Multiple exact source result records; result unavailable.' })
                          : jsx('pre', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', margin: '3px 0' }, children: formatDetailValue(details[event.id].source.result) })
                      ] }),
                      details[event.id].call.error_code || details[event.id].call.error_type || details[event.id].call.error_message
                        ? jsxs('div', { children: [
                          jsx('strong', { children: 'Captured error' }),
                          jsx('div', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }, children: `${details[event.id].call.error_code ? `${details[event.id].call.error_code}: ` : ''}${details[event.id].call.error_type ? `${details[event.id].call.error_type}: ` : ''}${details[event.id].call.error_message || ''}` })
                        ] }) : null,
                      jsxs('div', { children: [
                        jsx('strong', { children: 'Nearby visible conversation' }),
                        ...details[event.id].context.map((item, index) => jsxs('div', {
                          style: { borderLeft: '2px solid var(--ui-stroke-secondary)', paddingLeft: 7, marginTop: 4, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' },
                          children: [jsx('span', { style: mutedStyle, children: `${item.role}: ` }), item.content]
                        }, `${index}-${item.role}`))
                      ] })
                    ] })
                    : jsx('div', { style: { ...mutedStyle, marginTop: 7 }, children: details[event.id].source.status === 'ambiguous'
                      ? 'Source record correlation is ambiguous; no source content was selected.'
                      : 'No exact source session/tool-call match. Source context is unavailable; no fuzzy match was attempted.' })) : null
              ]
            }, event.id))
          ] })
        ]
      }),
      jsxs('section', {
        style: cardStyle,
        children: [
          jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: 8 }, children: [
            jsx('h2', { style: { fontSize: 13, margin: 0 }, children: 'Provider API trace' }),
            jsx('span', { style: mutedStyle, children: providerCoverageLabel(providerCoverage) })
          ] }),
          jsx('div', { style: { ...mutedStyle, marginTop: 3 }, children: providerCoverage?.observer_schema_version
            ? `Observer schema ${providerCoverage.observer_schema_version} · ${providerCoverage.attempt_count} attempts · ${providerCoverage.unpaired_event_count || 0} unpaired callbacks excluded from attempt count. No request/response payload or reasoning content is retained.`
            : 'Provider-family support or coverage is unknown for this session.' }),
          providerEvents.length === 0
            ? jsx('div', { style: { ...mutedStyle, marginTop: 8 }, children: providerCoverage?.coverage_state === 'observed_no_known_gaps'
              ? 'No provider API attempts observed in this session.'
              : 'No provider attempts captured; absence cannot be inferred from this coverage state.' })
            : providerEvents.map(event => jsxs('article', {
              style: { borderTop: '1px solid var(--ui-stroke-secondary)', padding: '8px 0', marginTop: 7 },
              children: [
                jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: 8 }, children: [
                  jsx('strong', { children: `${event.capture_state === 'post_only' ? 'Unpaired outcome · ' : ''}${event.provider || 'Provider unknown'} · ${event.model || 'Model unknown'}` }),
                  jsx('span', { style: mutedStyle, children: providerOutcomeLabel(event.outcome) })
                ] }),
                jsx('div', { style: { ...mutedStyle, marginTop: 2, overflowWrap: 'anywhere' }, children: `API call ${event.api_call_count ?? 'unknown'} · request ${event.api_request_id || 'ID unavailable'} · ${displayTime(event.started_at || event.ended_at)}${event.api_duration == null ? '' : ` · ${Number(event.api_duration).toFixed(2)} s`}` }),
                event.status_code != null || event.error_type || event.retry_count != null || event.retryable != null
                  ? jsx('div', { style: { marginTop: 4 }, children: `${event.error_type || 'API outcome'}${event.status_code == null ? '' : ` · HTTP ${event.status_code}`}${event.retry_count == null ? '' : ` · observer retry counter ${event.retry_count}`}${event.retryable == null ? '' : ` · retryable ${event.retryable ? 'yes' : 'no'}`}` }) : null,
                event.finish_reason || event.response_model
                  ? jsx('div', { style: { ...mutedStyle, marginTop: 3 }, children: [event.response_model ? `Response model ${event.response_model}` : '', event.finish_reason ? `Finish reason ${event.finish_reason}` : ''].filter(Boolean).join(' · ') }) : null,
                Object.keys(event.usage || {}).length
                  ? jsx('div', { style: { ...mutedStyle, marginTop: 3 }, children: `Usage: ${Object.entries(event.usage).map(([key, value]) => `${key} ${value}`).join(' · ')}` }) : null,
                event.capture_state !== 'paired'
                  ? jsx('div', { style: { ...mutedStyle, marginTop: 4 }, children: event.capture_state === 'post_only'
                    ? 'Unpaired API callback; excluded from attempt totals because no safe lifecycle match was available.'
                    : 'API attempt has no observed completion; provider coverage is partial.' }) : null
              ]
            }, event.id))
        ]
      }),
      jsxs('section', {
        style: cardStyle,
        children: [
          jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: 8 }, children: [
            jsx('h2', { style: { fontSize: 13, margin: 0 }, children: 'Approval trace' }),
            jsx('span', { style: mutedStyle, children: approvalCoverageLabel(approvalCoverage) })
          ] }),
          jsx('div', { style: { ...mutedStyle, marginTop: 3 }, children: approvalCoverage?.observer_schema_version
            ? `Observer schema ${approvalCoverage.observer_schema_version} · ${approvalCoverage.request_count} requests · ${approvalCoverage.unpaired_response_count} unmatched decisions. Command text is not retained.`
            : 'Approval-family support or coverage is unknown for this session.' }),
          approvalEvents.length === 0
            ? jsx('div', { style: { ...mutedStyle, marginTop: 8 }, children: approvalCoverage?.coverage_state === 'observed_no_known_gaps'
              ? 'No approval events observed in this session.'
              : 'No approval events captured; absence cannot be inferred from this coverage state.' })
            : approvalEvents.map(event => jsxs('article', {
              style: { borderTop: '1px solid var(--ui-stroke-secondary)', padding: '8px 0', marginTop: 7 },
              children: [
                jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: 8 }, children: [
                  jsx('strong', { children: approvalOutcomeLabel(event.outcome, event.capture_state) }),
                  jsx('span', { style: mutedStyle, children: event.choice || 'Choice unknown' })
                ] }),
                jsx('div', { style: { ...mutedStyle, marginTop: 2, overflowWrap: 'anywhere' }, children: `${event.surface || 'Surface unknown'} · ${displayTime(event.requested_at || event.decided_at)}${event.duration_ms == null ? '' : ` · ${Number(event.duration_ms).toFixed(0)} ms`}` }),
                event.tool_call_id || event.turn_id
                  ? jsx('div', { style: { marginTop: 4, overflowWrap: 'anywhere' }, children: [
                    event.tool_call_id ? `tool call ${event.tool_call_id}` : '',
                    event.turn_id ? `turn ${event.turn_id}` : ''
                  ].filter(Boolean).join(' · ') }) : null,
                event.decided_by ? jsx('div', { style: { ...mutedStyle, marginTop: 3 }, children: `Decision source ${event.decided_by}` }) : null,
                event.related_call_status === 'matched'
                  ? jsx('div', { style: { ...mutedStyle, marginTop: 3 }, children: `Related invocation ${event.related_call_id}` })
                  : event.related_call_status === 'ambiguous'
                    ? jsx('div', { style: { ...mutedStyle, marginTop: 3 }, children: 'Related invocation ID is ambiguous; no call was selected.' })
                    : null,
                event.capture_state !== 'paired'
                  ? jsx('div', { style: { ...mutedStyle, marginTop: 4 }, children: event.capture_state === 'response_only'
                    ? 'Unmatched approval decision; shown as evidence but not counted as a confirmed request.'
                    : 'Approval request has no observed decision; approval-family coverage is partial.' }) : null
              ]
            }, event.id))
        ]
      })
    ]
  })
}

const plugin = {
  id: ID,
  name: 'Observability',
  description: 'Local operational trace and outcome metrics for the connected profile.',
  defaultEnabled: false,
  register(ctx) {
    ctx.register({
      id: 'control-room',
      area: ROUTES_AREA,
      data: { path: '/observability' },
      render: () => jsx(ControlRoom, { rest: ctx.rest })
    })
    ctx.register({
      id: 'navigation',
      area: SIDEBAR_NAV_AREA,
      order: 70,
      data: { path: '/observability', label: 'Observability', codicon: 'pulse' }
    })
    ctx.register({
      id: 'open-command',
      area: PALETTE_AREA,
      data: {
        id: `${ID}.open`,
        label: 'Open Observability',
        keywords: ['observability', 'trace', 'tool calls', 'sessions'],
        run: () => host.navigate('/observability')
      }
    })
  }
}

export default plugin
