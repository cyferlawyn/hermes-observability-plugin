import { host, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, useValue } from '@hermes/plugin-sdk'
import { useEffect, useRef, useState } from 'react'
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
const EMPTY_SUBAGENTS = {
  events: { total: 0, succeeded: 0, failed: 0, interrupted: 0, unknown: 0 },
  unmatched_starts: 0,
  unmatched_stops: 0,
  identity_gaps: 0,
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

function subagentCoverageLabel(coverage) {
  if (!coverage) return 'Unknown subagent coverage'
  const support = coverage.support_state || 'unknown'
  const state = coverage.coverage_state || 'unknown'
  const stateText = ({
    observed_no_known_gaps: 'Observed — no known gaps',
    partial: 'Partial', unknown: 'Unknown', not_captured: 'Not captured'
  })[state] || 'Unknown'
  return `${support} · ${stateText}`
}

function subagentStatusLabel(status, captureState) {
  if (captureState === 'start_only') return 'Started · no observed stop'
  if (captureState === 'stop_only') return 'Stopped · start unmatched'
  return ({ succeeded: 'Succeeded', failed: 'Failed', interrupted: 'Interrupted', unknown: 'Unknown' })[status] || 'Unknown'
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

function timelineEventLabel(event) {
  if (event.kind === 'message') return event.role === 'user' ? 'User turn' : 'Assistant turn'
  if (event.kind === 'provider_attempt') return 'Provider API attempt'
  if (event.kind === 'tool_call') return event.tool_name || 'Tool call'
  if (event.kind === 'approval') return 'Approval'
  if (event.kind === 'subagent') return `Subagent${event.child_role ? ` · ${event.child_role}` : ''}`
  return 'Observer event'
}

function timelineEventOutcome(event) {
  if (event.kind === 'message') return event.has_tool_calls ? 'Visible turn · tool calls' : 'Visible turn'
  if (event.kind === 'provider_attempt') return providerOutcomeLabel(event.outcome)
  if (event.kind === 'tool_call') return outcomeLabel(event.outcome)
  if (event.kind === 'approval') return approvalOutcomeLabel(event.outcome, event.capture_state)
  if (event.kind === 'subagent') return subagentStatusLabel(event.status, event.capture_state)
  return 'Outcome unknown'
}

function timelineEventTime(event) {
  return event.timestamp ?? event.started_at ?? event.completed_at ?? event.ended_at
    ?? event.requested_at ?? event.decided_at ?? event.stopped_at
}

function timelineEventTimeValue(event) {
  const value = timelineEventTime(event)
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (value == null) return null
  const text = String(value).trim()
  if (/^-?\d+(?:\.\d+)?$/.test(text)) {
    const numeric = Number(text)
    return Number.isFinite(numeric) ? numeric : null
  }
  const parsed = Date.parse(text)
  return Number.isFinite(parsed) ? parsed : null
}

function eventAccent(event) {
  if (event.kind === 'message') return 'var(--theme-primary)'
  const outcome = event.kind === 'approval' ? event.outcome
    : event.kind === 'subagent' ? event.status : event.outcome
  if (['failed', 'denied'].includes(outcome)) return 'var(--ui-danger, #f85149)'
  if (['unknown', 'interrupted', 'not_executed'].includes(outcome)
      || event.capture_state && !['paired'].includes(event.capture_state)) return 'var(--ui-warning, #d29922)'
  if (['ok', 'allowed', 'succeeded'].includes(outcome)) return 'var(--ui-success, #00b5ab)'
  return 'var(--ui-stroke-secondary)'
}

function subscribeProfileChanges() {
  const profileAtom = host.state.profile
  const initialProfile = profileAtom.get()
  let changed = false
  const unsubscribe = profileAtom.subscribe(profile => {
    if (profile !== initialProfile) changed = true
  })
  return {
    profile: initialProfile,
    changed: () => changed || profileAtom.get() !== initialProfile,
    dispose: unsubscribe
  }
}

async function readCallDetail(rest, event) {
  const watch = subscribeProfileChanges()
  const profile = watch.profile
  try {
    if (typeof profile !== 'string' || !profile) {
      throw new Error('The connected profile cannot be verified; call detail was not loaded.')
    }
    const detail = await rest(
      `/sessions/${encodeURIComponent(event.session_id)}/calls/${encodeURIComponent(event.id)}/detail?profile=${encodeURIComponent(profile)}`
    )
    if (watch.changed()) {
      throw new Error('The connected profile changed while reading call detail; result discarded.')
    }
    if (detail?.profile !== profile) {
      throw new Error('Call detail profile could not be verified.')
    }
    return detail
  } finally {
    watch.dispose()
  }
}

async function startAnalyzeFollowUp(prompt, rest, sourceProfile) {
  const watch = subscribeProfileChanges()
  const profile = sourceProfile
  try {
    if (typeof profile !== 'string' || !profile) {
      throw new Error('The failed call is not bound to a verifiable connected profile; no Analyze session was created.')
    }
    if (watch.profile !== profile || watch.changed()) {
      throw new Error('The selected call belongs to a different profile; no Analyze session was created.')
    }
    if (typeof rest !== 'function') {
      throw new Error('Could not enforce the read-only Analyze session policy; no prompt was submitted.')
    }

    const created = await host.request('session.create', { source: 'desktop', profile })
    if (watch.changed()) {
      throw new Error('The connected profile changed before the Analyze session was protected; no prompt was submitted.')
    }
    const sessionId = created?.session_id
    const storedSessionId = created?.stored_session_id
    if (!sessionId || !storedSessionId) {
      throw new Error('Hermes did not return both runtime and stored IDs for the follow-up session.')
    }

    const policy = await rest(
      `/analyze-sessions/${encodeURIComponent(sessionId)}/read-only?profile=${encodeURIComponent(profile)}`,
      { method: 'POST' }
    )
    if (watch.changed()) {
      throw new Error('The connected profile changed during policy installation; no prompt was submitted.')
    }
    if (policy?.read_only !== true || policy?.profile !== profile) {
      throw new Error('The read-only policy was installed in a different profile or could not be verified; no prompt was submitted.')
    }

    await host.request('prompt.submit', { session_id: sessionId, text: prompt, profile })
    if (watch.changed()) {
      return { storedSessionId, opened: false, profileChanged: true }
    }
    try {
      await host.openSession(storedSessionId, { profile, intent: 'tab' })
      return { storedSessionId, opened: true }
    } catch {
      return { storedSessionId, opened: false }
    }
  } finally {
    watch.dispose()
  }
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

function AnalyzeAction({ event, detail, activeProfile, analyzingCallId, analyzeStatus, onAnalyze }) {
  if (event.outcome !== 'failed' || !detail?.analyze_prompt || detail.profile !== activeProfile) return null
  return jsxs('section', {
    style: { borderTop: '1px solid var(--ui-stroke-secondary)', paddingTop: 8, display: 'grid', gap: 6 },
    children: [
      jsx('strong', { children: 'Analyze this failed call' }),
      jsx('div', { style: mutedStyle, children: 'Only clicking Analyze creates a fresh session on this connected profile. It contains the selected tool identity, outcome/timing, captured error, exact source arguments/result when available, nearby visible conversation, and source references. Missing evidence is labeled unavailable; provider reasoning, raw provider payloads, and child transcripts are excluded. The source session and telemetry are not changed. All tool calls in the follow-up are blocked by a durable policy hook.' }),
      jsx('div', { style: mutedStyle, children: 'The analysis session is enforced read-only. Any remediation must be carried out in a separate normal session after explicit approval.' }),
      jsx('button', {
        type: 'button', style: buttonStyle,
        disabled: Boolean(analyzingCallId),
        onClick: onAnalyze,
        children: analyzingCallId === event.id ? 'Starting Analyze session…' : 'Analyze'
      }),
      analyzeStatus.callId === event.id && analyzeStatus.message
        ? jsx('div', { role: 'status', style: mutedStyle, children: analyzeStatus.message }) : null
    ]
  })
}

function ControlRoom({ rest }) {
  const connectedProfile = useValue(host.state.profile)
  const previousProfile = useRef(connectedProfile)
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
  const [analyzingCallId, setAnalyzingCallId] = useState('')
  const [analyzeStatus, setAnalyzeStatus] = useState({ callId: '', message: '' })
  const [selectedEventId, setSelectedEventId] = useState('')
  const [sessionSearch, setSessionSearch] = useState('')
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'

  useEffect(() => {
    if (previousProfile.current === connectedProfile) return
    previousProfile.current = connectedProfile
    setOverview(null)
    setSelectedId('')
    setTrace(null)
    setDetails({})
    setSelectedEventId('')
    setSessionSearch('')
    setDetailLoading('')
    setAnalyzingCallId('')
    setAnalyzeStatus({ callId: '', message: '' })
    setError('')
    setRefreshKey(value => value + 1)
  }, [connectedProfile])

  async function inspectCall(event) {
    setDetailLoading(event.id)
    setDetails(value => ({ ...value, [event.id]: null }))
    try {
      const detail = await readCallDetail(rest, event)
      setDetails(value => ({ ...value, [event.id]: detail }))
    } catch (err) {
      if (host.state.profile.get() === connectedProfile) {
        setDetails(value => ({ ...value, [event.id]: { profile: connectedProfile, error: err?.message || 'Could not read source detail.' } }))
      }
    } finally {
      setDetailLoading('')
    }
  }

  async function analyzeCall(event, detail) {
    if (analyzingCallId || event.outcome !== 'failed' || !detail?.analyze_prompt) return
    if (!detail.profile || detail.profile !== host.state.profile.get()) {
      setAnalyzeStatus({ callId: event.id, message: 'The selected call belongs to a different or unverified profile; no session was created.' })
      return
    }
    setAnalyzingCallId(event.id)
    setAnalyzeStatus({ callId: event.id, message: '' })
    try {
      const result = await startAnalyzeFollowUp(detail.analyze_prompt, rest, detail.profile)
      if (result.profileChanged) {
        if (host.state.profile.get() === detail.profile) {
          setAnalyzeStatus({
            callId: event.id,
            message: `The prompt was submitted to the original profile, but the profile changed during submission; the follow-up was not opened. Session: ${result.storedSessionId}`
          })
        }
        return
      }
      setAnalyzeStatus({
        callId: event.id,
        message: result.opened
          ? `Analyze follow-up started in a separate session (${result.storedSessionId}).`
          : `Analyze follow-up started, but the Desktop could not open it. Session: ${result.storedSessionId}`
      })
    } catch (err) {
      setAnalyzeStatus({ callId: event.id, message: err?.message || 'Could not start the Analyze follow-up.' })
    } finally {
      setAnalyzingCallId('')
    }
  }

  function inspectFindingExample(example) {
    setDetails({})
    setTrace(null)
    setSelectedEventId(String(example.call_id))
    setSelectedId(example.session_id)
    inspectCall({ id: example.call_id, session_id: example.session_id })
  }

  async function resetMetrics() {
    setResetting(true)
    setError('')
    try {
      await rest('/reset', { method: 'POST' })
      setSelectedId('')
      setTrace(null)
      setSelectedEventId('')
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
    // The overview window filters sessions; an explicitly selected related session may cross it.
    rest(`/sessions/${encodeURIComponent(selectedId)}/trace?window=Overall&timezone=${encodeURIComponent(timezone)}`)
      .then(data => { if (live) setTrace(data) })
      .catch(err => { if (live) setError(err?.message || 'Could not read this session trace.') })
    return () => { live = false }
  }, [rest, selectedId, refreshKey, timezone])

  const sessions = Array.isArray(overview?.sessions) ? overview.sessions : []
  const metrics = overview?.metrics || EMPTY_METRICS
  const providerMetrics = overview?.provider_attempts || EMPTY_PROVIDER_ATTEMPTS
  const approvalMetrics = overview?.approvals || EMPTY_APPROVALS
  const subagentMetrics = overview?.subagents || EMPTY_SUBAGENTS
  const subagentCounts = subagentMetrics.events || EMPTY_SUBAGENTS.events
  const subagentCoverage = trace?.subagent_coverage
  const subagentEvents = Array.isArray(trace?.subagents) ? trace.subagents : []
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
  const selectedNotInOverview = Boolean(selected && !sessions.some(session => session.id === selectedId))
  const events = Array.isArray(trace?.calls) ? trace.calls : []
  const timeline = (Array.isArray(trace?.timeline) ? trace.timeline : events.map(event => ({
    ...event, kind: 'tool_call', timestamp: event.started_at || event.completed_at
  }))).map((event, index) => ({ event, index, time: timelineEventTimeValue(event) }))
    .sort((left, right) => {
      if (left.time == null && right.time == null) return left.index - right.index
      if (left.time == null) return 1
      if (right.time == null) return -1
      return left.time - right.time || left.index - right.index
    })
    .map(item => item.event)
  const selectedTimelineEvent = timeline.find(event => String(event.id) === String(selectedEventId)) || timeline[0] || null
  const toolBreakdown = Array.isArray(overview?.tool_breakdown) ? overview.tool_breakdown : []
  const filteredSessions = sessions.filter(session => `${session.title || ''} ${session.id || ''}`
    .toLowerCase().includes(sessionSearch.trim().toLowerCase()))
  const visibleDetails = Object.fromEntries(
    Object.entries(details).filter(([, detail]) => detail?.profile === connectedProfile)
  )
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
              onChange: event => { setDetails({}); setTrace(null); setSelectedEventId(''); setSelectedId(''); setTimeWindow(event.target.value) },
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
          jsx(Metric, { label: 'Call success', value: rate, detail: `${calls.ok} ok / ${rateCalls.denominator} known executed` }),
          jsx(Metric, { label: 'Invocations', value: calls.total, detail: 'Tool + MCP calls observed' }),
          jsx(Metric, { label: 'Explicit failures', value: calls.failed, detail: 'Observer-reported failed outcomes' }),
          jsx(Metric, { label: 'Unknown / not executed', value: `${calls.unknown} / ${calls.not_executed}`, detail: 'Visible; excluded from success-rate denominator' }),
          jsx(Metric, { label: 'Retryable API failures', value: providerMetrics.retryable_failures || 0, detail: 'Observer-classified; not confirmed retries' })
        ]
      }),
      jsx('div', {
        style: { ...mutedStyle, fontSize: 11 },
        children: `${timeWindow} · ${windowDescription} Coverage: ${metrics.included_sessions || 0} sessions included; ${metrics.observed_sessions || 0} observed, ${metrics.partial_sessions || 0} partial, ${metrics.unknown_sessions || 0} unknown, ${metrics.excluded_sessions || 0} not captured. Sessions are counted wholly by start time. Incomplete observations are lower-bound evidence; “no known gaps” is not a completeness guarantee.`
      }),
      jsxs('div', {
        style: { display: 'flex', flexWrap: 'wrap', alignItems: 'stretch', gap: 10 },
        children: [
          jsxs('div', {
            style: { flex: '0.9 1 235px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 10 },
            children: [
              jsxs('section', {
                style: { ...cardStyle, overflow: 'auto', maxHeight: 'min(55vh, 560px)' },
                children: [
                  jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }, children: [
                    jsx('h2', { style: { fontSize: 13, margin: '0 0 8px' }, children: 'Recent sessions' }),
                    jsx('span', { style: mutedStyle, children: `${filteredSessions.length} shown` })
                  ] }),
                  jsx('input', {
                    type: 'search', value: sessionSearch, placeholder: 'Filter sessions…', 'aria-label': 'Filter sessions',
                    onChange: event => setSessionSearch(event.target.value),
                    style: { width: '100%', boxSizing: 'border-box', color: 'var(--ui-text-primary)', background: 'var(--dt-input, transparent)', border: '1px solid var(--ui-stroke-secondary)', borderRadius: 5, padding: '6px 8px', font: 'inherit', marginBottom: 7 }
                  }),
                  loading && !overview ? jsx('div', { style: mutedStyle, children: 'Loading sessions…' }) : null,
                  !loading && filteredSessions.length === 0 ? jsx('div', { style: { ...mutedStyle, padding: '8px 2px' }, children: 'No matching sessions.' }) : null,
                  ...filteredSessions.map(session => jsx('button', {
                    type: 'button', 'aria-pressed': selectedId === session.id,
                    onClick: () => { setTrace(null); setDetails({}); setSelectedEventId(''); setSelectedId(session.id) },
                    style: {
                      display: 'block', width: '100%', textAlign: 'left', padding: '8px 7px', marginBottom: 4,
                      border: `1px solid ${selectedId === session.id ? 'var(--theme-primary)' : 'var(--ui-stroke-secondary)'}`,
                      borderRadius: 6, background: selectedId === session.id ? 'var(--chrome-action-hover)' : 'transparent',
                      color: 'var(--ui-text-primary)', cursor: 'pointer', font: 'inherit'
                    },
                    children: jsxs('span', { style: { display: 'flex', flexDirection: 'column', gap: 2 }, children: [
                      jsx('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: selectedId === session.id ? 600 : 500 }, children: session.title || session.id }),
                      jsx('span', { style: mutedStyle, children: `${displayTime(session.started_at)} · ${coverageLabel(session.coverage_state)}` }),
                      jsx('span', { style: mutedStyle, children: `${session.call_count || 0} observed calls` })
                    ] })
                  }, session.id))
                ]
              }),
              jsxs('section', {
                style: cardStyle,
                children: [
                  jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', gap: 8 }, children: [
                    jsx('h2', { style: { fontSize: 13, margin: 0 }, children: 'Tool & MCP breakdown' }),
                    jsx('span', { style: mutedStyle, children: timeWindow })
                  ] }),
                  jsx('div', { style: { ...mutedStyle, marginTop: 4 }, children: 'Known executed outcomes only; unknowns stay visible and are excluded from the rate.' }),
                  toolBreakdown.length === 0
                    ? jsx('div', { style: { ...mutedStyle, marginTop: 9 }, children: 'No tool calls in this window.' })
                    : jsx('div', { style: { display: 'flex', flexDirection: 'column', gap: 7, marginTop: 9, maxHeight: 310, overflow: 'auto' }, children:
                      toolBreakdown.map(tool => {
                        const toolCalls = tool.calls || {}
                        const toolRateCalls = tool.rate_calls || {}
                        const toolRate = tool.success_rate == null ? null : Number(tool.success_rate)
                        const percent = toolRate == null ? 0 : Math.max(0, Math.min(100, toolRate * 100))
                        const coverage = tool.coverage || {}
                        return jsxs('div', {
                          style: { display: 'grid', gridTemplateColumns: 'minmax(0,1fr) auto', gap: 7, alignItems: 'center', borderTop: '1px solid var(--ui-stroke-tertiary)', paddingTop: 7 },
                          children: [
                            jsxs('div', { style: { minWidth: 0 }, children: [
                              jsx('div', { style: { overflowWrap: 'anywhere', fontWeight: 550 }, children: tool.tool_name }),
                              jsx('div', { style: { ...mutedStyle, fontSize: 11 }, children: `${toolCalls.total || 0} calls · ${toolCalls.failed || 0} failed · ${toolCalls.unknown || 0} unknown` }),
                              jsx('div', { style: { height: 4, background: 'var(--ui-stroke-tertiary)', borderRadius: 4, overflow: 'hidden', marginTop: 5 }, children: jsx('div', { style: { height: '100%', width: `${percent}%`, background: (toolCalls.failed || toolCalls.unknown) ? 'var(--ui-warning, #d29922)' : 'var(--ui-success, #00b5ab)' } }) }),
                              jsx('div', { style: { ...mutedStyle, fontSize: 10, marginTop: 3 }, children: `${toolRateCalls.denominator || 0} rate-eligible calls · ${coverage.observed_sessions || 0} observed / ${coverage.partial_sessions || 0} partial sessions` })
                            ] }),
                            jsx('strong', { style: { color: toolRate === null ? 'var(--ui-text-tertiary)' : toolRate >= 0.9 ? 'var(--ui-success, #00b5ab)' : 'var(--ui-warning, #d29922)', fontVariantNumeric: 'tabular-nums' }, children: toolRate === null ? '—' : `${(toolRate * 100).toFixed(0)}%` })
                          ]
                        }, tool.tool_name)
                      })
                    })
                ]
              })
            ]
          }),
          jsxs('section', {
            style: { ...cardStyle, flex: '1.65 1 420px', minWidth: 0, overflow: 'auto', maxHeight: 'min(75vh, 820px)' },
            children: [
              jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8, marginBottom: 8 }, children: [
                jsxs('div', { children: [
                  jsx('h2', { style: { fontSize: 14, margin: '0 0 3px' }, children: 'Session activity' }),
                  jsx('div', { style: { color: 'var(--ui-text-primary)', fontWeight: 550 }, children: selected?.title || selected?.id || 'Choose a session' }),
                  jsx('div', { style: mutedStyle, children: selected ? `${displayTime(selected.started_at)} · ${coverageLabel(selected.coverage_state)}${selectedNotInOverview ? ' · opened through relationship' : ''}` : 'Select a session to inspect its captured trace.' })
                ] }),
                jsx('span', { style: { ...mutedStyle, whiteSpace: 'nowrap' }, children: `${timeline.length} events` })
              ] }),
              selected && timeline.length === 0 ? jsx('div', { style: { ...mutedStyle, padding: 10 }, children: selected.coverage_state === 'observed_no_known_gaps' ? 'No operational events observed in this session.' : 'No captured activity. This session may predate collection.' }) : null,
              ...timeline.map(event => {
                const isSelected = selectedTimelineEvent && String(selectedTimelineEvent.id) === String(event.id)
                const eventTitle = event.kind === 'message'
                  ? (event.role === 'user' ? 'User turn' : event.has_tool_calls ? 'Assistant turn · tool calls' : 'Assistant turn')
                  : event.kind === 'provider_attempt'
                    ? `Provider attempt · ${event.provider || 'Provider'} · ${event.model || 'model unknown'}`
                    : event.kind === 'tool_call'
                      ? `Tool call · ${event.tool_name || 'unknown tool'}`
                      : event.kind === 'approval'
                        ? `Approval · ${approvalOutcomeLabel(event.outcome, event.capture_state)}`
                        : event.kind === 'subagent'
                          ? `Subagent${event.child_role ? ` · ${event.child_role}` : ''}`
                          : timelineEventLabel(event)
                const eventTime = timelineEventTime(event)
                const description = event.kind === 'message'
                  ? event.content || (event.has_tool_calls ? 'Visible assistant turn initiated tool calls.' : 'Empty visible turn.')
                  : event.kind === 'provider_attempt'
                    ? `${timelineEventOutcome(event)}${event.api_duration == null ? '' : ` · ${Number(event.api_duration).toFixed(2)} s`}${event.capture_state !== 'paired' ? ' · lifecycle incomplete' : ''}`
                    : event.kind === 'tool_call'
                      ? `${timelineEventOutcome(event)}${event.duration_ms == null ? '' : ` · ${Number(event.duration_ms).toFixed(0)} ms`}${event.capture_state !== 'paired' ? ' · lifecycle incomplete' : ''}`
                      : event.kind === 'approval'
                        ? `${event.surface || 'Approval surface unknown'} · ${timelineEventOutcome(event)}`
                        : event.kind === 'subagent'
                          ? `${timelineEventOutcome(event)}${event.duration_ms == null ? '' : ` · ${Number(event.duration_ms).toFixed(0)} ms`}`
                          : timelineEventOutcome(event)
                return jsx('button', {
                  type: 'button', 'aria-pressed': Boolean(isSelected),
                  onClick: () => { setSelectedEventId(String(event.id)); if (event.kind === 'tool_call') inspectCall(event) },
                  style: { display: 'grid', gridTemplateColumns: '76px 3px minmax(0,1fr)', gap: 9, width: '100%', textAlign: 'left', padding: '8px 7px', marginTop: 4, border: `1px solid ${isSelected ? 'var(--theme-primary)' : 'var(--ui-stroke-tertiary)'}`, borderRadius: 6, background: isSelected ? 'var(--chrome-action-hover)' : 'transparent', color: 'var(--ui-text-primary)', cursor: 'pointer', font: 'inherit' },
                  children: [
                    jsx('span', { style: { ...mutedStyle, fontSize: 10, paddingTop: 2, fontVariantNumeric: 'tabular-nums' }, children: displayTime(eventTime) }),
                    jsx('span', { 'aria-hidden': true, style: { width: 3, borderRadius: 3, background: eventAccent(event), minHeight: 25 } }),
                    jsxs('span', { style: { minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 }, children: [
                      jsxs('span', { style: { display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'baseline' }, children: [
                        jsx('strong', { children: eventTitle }),
                        jsx('span', { style: { ...mutedStyle, fontSize: 10 }, children: event.kind === 'message' ? timelineEventOutcome(event) : event.kind === 'tool_call' ? outcomeLabel(event.outcome) : event.kind === 'provider_attempt' ? providerOutcomeLabel(event.outcome) : event.kind === 'approval' ? timelineEventOutcome(event) : event.kind === 'subagent' ? subagentStatusLabel(event.status, event.capture_state) : '' })
                      ] }),
                      jsx('span', { style: { color: event.kind === 'message' ? 'var(--ui-text-secondary)' : 'var(--ui-text-tertiary)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: event.kind === 'message' ? 62 : 'none', overflow: event.kind === 'message' ? 'hidden' : 'visible' }, children: description })
                    ] })
                  ]
                }, `${event.kind}-${event.id}`)
              }),
              jsx('div', { style: { ...mutedStyle, fontSize: 10, marginTop: 9 }, children: 'Operational trace only: visible turns and observer lifecycle metadata. Provider reasoning, payloads, tool arguments and results are not copied into telemetry.' })
            ]
          }),
          jsxs('section', {
            style: { ...cardStyle, flex: '1 1 300px', minWidth: 0, alignSelf: 'flex-start' },
            children: [
              jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline', marginBottom: 8 }, children: [
                jsx('h2', { style: { fontSize: 13, margin: 0 }, children: 'Event inspector' }),
                jsx('span', { style: { ...mutedStyle, fontSize: 10 }, children: selectedTimelineEvent ? displayTime(timelineEventTime(selectedTimelineEvent)) : '' })
              ] }),
              !selectedTimelineEvent ? jsx('div', { style: mutedStyle, children: 'Choose an event in the trace to inspect its evidence.' }) : jsxs('div', { children: [
                jsxs('div', { style: { borderLeft: `3px solid ${eventAccent(selectedTimelineEvent)}`, paddingLeft: 9, marginBottom: 10 }, children: [
                  jsx('strong', { children: timelineEventLabel(selectedTimelineEvent) }),
                  jsx('div', { style: { ...mutedStyle, marginTop: 3 }, children: timelineEventOutcome(selectedTimelineEvent) })
                ] }),
                selectedTimelineEvent.kind === 'message'
                  ? jsx('div', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', padding: 8, borderRadius: 5, background: 'var(--ui-bg-elevated, transparent)', maxHeight: 280, overflow: 'auto' }, children: selectedTimelineEvent.content || (selectedTimelineEvent.has_tool_calls ? 'Visible assistant turn initiated tool calls; tool calls are shown as separate events.' : 'No visible message text.') })
                  : null,
                selectedTimelineEvent.kind === 'tool_call' ? (() => {
                  const event = selectedTimelineEvent
                  const detail = visibleDetails[event.id]
                  return jsxs('div', { children: [
                    jsx('div', { style: { ...mutedStyle, marginBottom: 7 }, children: `${event.tool_name} · ${outcomeLabel(event.outcome)}${event.duration_ms == null ? '' : ` · ${Number(event.duration_ms).toFixed(0)} ms`}` }),
                    event.error_code || event.error_type || event.error_message ? jsx('div', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', padding: 7, borderRadius: 5, border: '1px solid var(--ui-stroke-secondary)', marginBottom: 7 }, children: `${event.error_code ? `${event.error_code}: ` : ''}${event.error_type ? `${event.error_type}: ` : ''}${event.error_message || 'Explicit failure; no message supplied.'}` }) : null,
                    event.capture_state !== 'paired' ? jsx('div', { style: { ...mutedStyle, marginBottom: 7 }, children: 'Lifecycle incomplete; outcome coverage is partial.' }) : null,
                    jsx('button', { type: 'button', style: buttonStyle, onClick: () => inspectCall(event), children: detailLoading === event.id ? 'Loading source detail…' : detail ? 'Reload source detail' : 'Inspect source detail' }),
                    detail?.error ? jsx('div', { role: 'alert', style: { ...mutedStyle, marginTop: 7 }, children: detail.error }) : null,
                    detail?.available ? jsxs('div', { style: { display: 'grid', gap: 8, marginTop: 10 }, children: [
                      jsxs('div', { children: [jsx('strong', { children: 'Arguments' }), jsx('pre', { style: { ...mutedStyle, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 180, overflow: 'auto', margin: '3px 0' }, children: formatDetailValue(detail.source.arguments) })] }),
                      jsxs('div', { children: [jsx('strong', { children: 'Source result' }), detail.source.result_status === 'ambiguous' ? jsx('div', { style: mutedStyle, children: 'Multiple exact source result records; result unavailable.' }) : jsx('pre', { style: { ...mutedStyle, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 180, overflow: 'auto', margin: '3px 0' }, children: formatDetailValue(detail.source.result) })] }),
                      jsxs('div', { children: [jsx('strong', { children: 'Nearby visible conversation' }), ...detail.context.map((item, index) => jsxs('div', { style: { borderLeft: '2px solid var(--ui-stroke-secondary)', paddingLeft: 7, marginTop: 4, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }, children: [jsx('span', { style: mutedStyle, children: `${item.role}: ` }), item.content] }, `${index}-${item.role}`))] }),
                      jsx(AnalyzeAction, { event, detail, activeProfile: connectedProfile, analyzingCallId, analyzeStatus, onAnalyze: () => analyzeCall(event, detail) })
                    ] }) : null,
                    detail && !detail.available && !detail.error ? jsx('div', { style: { ...mutedStyle, marginTop: 7 }, children: detail.source?.status === 'ambiguous' ? 'Source correlation is ambiguous; no source content was selected.' : 'No exact source match. Context is unavailable; no fuzzy match was attempted.' }) : null
                  ] })
                })() : null,
                selectedTimelineEvent.kind === 'provider_attempt' ? jsxs('div', { style: { display: 'grid', gap: 5 }, children: [
                  jsx('div', { children: `${selectedTimelineEvent.provider || 'Provider unknown'} · ${selectedTimelineEvent.model || 'Model unknown'}` }),
                  selectedTimelineEvent.api_request_id ? jsx('div', { style: { ...mutedStyle, overflowWrap: 'anywhere' }, children: `Request ${selectedTimelineEvent.api_request_id}` }) : null,
                  selectedTimelineEvent.error_type || selectedTimelineEvent.status_code ? jsx('div', { style: { ...mutedStyle }, children: `${selectedTimelineEvent.error_type || 'API outcome'}${selectedTimelineEvent.status_code == null ? '' : ` · HTTP ${selectedTimelineEvent.status_code}`}` }) : null,
                  selectedTimelineEvent.retry_count != null || selectedTimelineEvent.retryable != null ? jsx('div', { style: mutedStyle, children: `${selectedTimelineEvent.retry_count == null ? '' : `Observer retry counter ${selectedTimelineEvent.retry_count}`}${selectedTimelineEvent.retryable == null ? '' : ` · retryable ${selectedTimelineEvent.retryable ? 'yes' : 'no'}`}` }) : null,
                  Object.keys(selectedTimelineEvent.usage || {}).length ? jsx('div', { style: { ...mutedStyle, overflowWrap: 'anywhere' }, children: `Allow-listed usage: ${Object.entries(selectedTimelineEvent.usage).map(([key, value]) => `${key} ${value}`).join(' · ')}` }) : null,
                  jsx('div', { style: { ...mutedStyle, marginTop: 3 }, children: 'Request/response payload and provider reasoning are not retained.' })
                ] }) : null,
                selectedTimelineEvent.kind === 'approval' ? jsxs('div', { style: { display: 'grid', gap: 5 }, children: [
                  jsx('div', { children: `${selectedTimelineEvent.surface || 'Surface unknown'} · ${selectedTimelineEvent.choice || 'Choice unknown'}` }),
                  selectedTimelineEvent.tool_call_id || selectedTimelineEvent.turn_id ? jsx('div', { style: { ...mutedStyle, overflowWrap: 'anywhere' }, children: [selectedTimelineEvent.tool_call_id ? `Tool call ${selectedTimelineEvent.tool_call_id}` : '', selectedTimelineEvent.turn_id ? `Turn ${selectedTimelineEvent.turn_id}` : ''].filter(Boolean).join(' · ') }) : null,
                  selectedTimelineEvent.capture_state !== 'paired' ? jsx('div', { style: mutedStyle, children: 'Unmatched approval lifecycle; not interpreted as a confirmed request outcome.' }) : null,
                  jsx('div', { style: { ...mutedStyle, marginTop: 3 }, children: 'Command text is not retained. Approval outcomes do not change tool success-rate math.' })
                ] }) : null,
                selectedTimelineEvent.kind === 'subagent' ? jsxs('div', { style: { display: 'grid', gap: 5 }, children: [
                  selectedTimelineEvent.child_goal ? jsx('div', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }, children: selectedTimelineEvent.child_goal }) : null,
                  selectedTimelineEvent.child_summary ? jsxs('div', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }, children: [jsx('strong', { children: 'Summary: ' }), selectedTimelineEvent.child_summary] }) : null,
                  jsx('div', { style: { ...mutedStyle, overflowWrap: 'anywhere' }, children: `Parent ${selectedTimelineEvent.parent_session_id || 'ID unavailable'} · Child ${selectedTimelineEvent.child_session_id || 'ID unavailable'}` }),
                  selectedTimelineEvent.capture_state !== 'paired' ? jsx('div', { style: mutedStyle, children: 'Unmatched lifecycle; subagent-family coverage is partial.' }) : null,
                  jsx('div', { style: { ...mutedStyle, marginTop: 3 }, children: 'Child transcripts are not copied into the Control Room.' }),
                  selectedTimelineEvent.relationship === 'parent' && selectedTimelineEvent.child_session_id && selectedTimelineEvent.child_session_available ? jsx('button', { type: 'button', style: { ...buttonStyle, justifySelf: 'start' }, onClick: () => { setTrace(null); setDetails({}); setSelectedEventId(''); setSelectedId(selectedTimelineEvent.child_session_id) }, children: 'Open child session' }) : null
                ] }) : null
              ] })
            ]
          })
        ]
      }),
      jsxs('section', {
        style: cardStyle,
        children: [
          jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }, children: [
            jsx('h2', { style: { fontSize: 13, margin: 0 }, children: 'Repeated failure signatures' }),
            jsx('span', { style: mutedStyle, children: findingsCoverageIncomplete ? 'Observed groups · exclusions present' : `${findings.length} groups` })
          ] }),
          findings.length === 0
            ? jsx('div', { style: { ...mutedStyle, marginTop: 7 }, children: 'No qualifying cross-session failure signature in this window.' })
            : jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: 5, marginTop: 7 }, children: [
              ...findings.slice(0, 3).map(finding => jsxs('div', {
                style: { display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 8, borderTop: '1px solid var(--ui-stroke-tertiary)', paddingTop: 7 },
                children: [
                  jsxs('div', { style: { minWidth: 0 }, children: [
                    jsx('strong', { children: `${finding.identity_kind === 'mcp' ? 'MCP' : 'Tool'} · ${finding.tool_name}` }),
                    jsx('div', { style: { ...mutedStyle, overflowWrap: 'anywhere' }, children: `${finding.at_least_n_observed ? 'At least ' : ''}${finding.distinct_failed_calls} failed calls · ${finding.affected_sessions} sessions · ${finding.error_excerpt || finding.signature}` })
                  ] }),
                  finding.examples?.[0] ? jsx('button', { type: 'button', style: buttonStyle, onClick: () => inspectFindingExample(finding.examples[0]), children: 'Inspect example' }) : null
                ]
              }, `${finding.tool_name}-${finding.signature}-${finding.match_basis}`)),
              findings.length > 3 ? jsx('div', { style: mutedStyle, children: `${findings.length - 3} more in the deep-dive records below.` }) : null
            ] })
        ]
      }),
      jsx('details', {
        style: cardStyle,
        children: [
          jsx('summary', { style: { cursor: 'pointer', fontWeight: 600 }, children: `Deep dive · repeated failures, coverage and full lifecycle records${findings.length ? ` · ${findings.length} repeated failure groups` : ''}` }),
          jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: 10, marginTop: 10 }, children: [
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
            jsx('h2', { style: { fontSize: 13, margin: '0 0 3px' }, children: 'Subagent lifecycle' }),
            jsx('div', { style: mutedStyle, children: 'Delegated child starts and stops are tracked as their own event family. Child goals and available summaries are shown without copying child transcripts.' })
          ] }),
          jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }, children: [
            jsx(Metric, {
              label: 'Child lifecycles observed',
              value: subagentMetrics.supported_sessions > 0 ? `≥${subagentCounts.total}` : '—',
              detail: `${subagentCounts.succeeded} succeeded · ${subagentCounts.failed} failed · ${subagentCounts.interrupted} interrupted · ${subagentCounts.unknown} unknown`
            }),
            jsx(Metric, {
              label: 'Unmatched lifecycle events',
              value: subagentMetrics.supported_sessions > 0
                ? (subagentMetrics.unmatched_starts || 0) + (subagentMetrics.unmatched_stops || 0)
                : '—',
              detail: subagentMetrics.supported_sessions > 0
                ? `${subagentMetrics.unmatched_starts || 0} starts without stops · ${subagentMetrics.unmatched_stops || 0} stops without starts · ${subagentMetrics.identity_gaps || 0} missing parent/child IDs`
                : 'No compatible subagent-family support; unmatched counts cannot be inferred.'
            }),
            jsx(Metric, {
              label: 'Subagent coverage',
              value: `${subagentMetrics.observed_sessions || 0} observed`,
              detail: `${subagentMetrics.supported_sessions || 0} supported · ${subagentMetrics.partial_sessions || 0} partial · ${subagentMetrics.unknown_sessions || 0} unknown · ${subagentMetrics.unsupported_sessions || 0} unsupported · ${subagentMetrics.not_captured_sessions || 0} not captured`
            })
          ] }),
          jsx('div', { style: { ...mutedStyle, marginTop: 5 }, children: subagentMetrics.supported_sessions > 0
            ? 'Only compatible subagent-family sessions contribute counts. Unmatched or interrupted lifecycles remain visible and coverage is not inferred from tool-call coverage.'
            : 'No compatible subagent observer coverage is available in this window; missing events cannot be interpreted as zero.' })
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
              jsx('div', { style: mutedStyle, children: `${displayTime(selected.started_at)} · ${coverageLabel(selected.coverage_state)}` }),
              selectedNotInOverview ? jsx('div', { style: mutedStyle, children: 'Opened through a relationship; not shown in the filtered session list.' }) : null
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
                visibleDetails[event.id] ? (visibleDetails[event.id].error
                  ? jsx('div', { role: 'alert', style: { marginTop: 7 }, children: visibleDetails[event.id].error })
                  : visibleDetails[event.id].available
                    ? jsxs('div', { style: { marginTop: 8, display: 'grid', gap: 7 }, children: [
                      jsxs('div', { children: [
                        jsx('strong', { children: 'Arguments' }),
                        jsx('pre', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', margin: '3px 0' }, children: formatDetailValue(visibleDetails[event.id].source.arguments) })
                      ] }),
                      jsxs('div', { children: [
                        jsx('strong', { children: 'Source result' }),
                        visibleDetails[event.id].source.result_status === 'ambiguous'
                          ? jsx('div', { style: mutedStyle, children: 'Multiple exact source result records; result unavailable.' })
                          : jsx('pre', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', margin: '3px 0' }, children: formatDetailValue(visibleDetails[event.id].source.result) })
                      ] }),
                      visibleDetails[event.id].call.error_code || visibleDetails[event.id].call.error_type || visibleDetails[event.id].call.error_message
                        ? jsxs('div', { children: [
                          jsx('strong', { children: 'Captured error' }),
                          jsx('div', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }, children: `${visibleDetails[event.id].call.error_code ? `${visibleDetails[event.id].call.error_code}: ` : ''}${visibleDetails[event.id].call.error_type ? `${visibleDetails[event.id].call.error_type}: ` : ''}${visibleDetails[event.id].call.error_message || ''}` })
                        ] }) : null,
                      jsxs('div', { children: [
                        jsx('strong', { children: 'Nearby visible conversation' }),
                        ...visibleDetails[event.id].context.map((item, index) => jsxs('div', {
                          style: { borderLeft: '2px solid var(--ui-stroke-secondary)', paddingLeft: 7, marginTop: 4, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' },
                          children: [jsx('span', { style: mutedStyle, children: `${item.role}: ` }), item.content]
                        }, `${index}-${item.role}`))
                      ] }),
                      jsx(AnalyzeAction, {
                        event,
                        detail: visibleDetails[event.id],
                        activeProfile: connectedProfile,
                        analyzingCallId,
                        analyzeStatus,
                        onAnalyze: () => analyzeCall(event, visibleDetails[event.id])
                      })
                    ] })
                    : jsxs('div', { style: { ...mutedStyle, marginTop: 7 }, children: [
                      jsx('div', { children: visibleDetails[event.id].source.status === 'ambiguous'
                        ? 'Source record correlation is ambiguous; no source content was selected.'
                        : 'No exact source session/tool-call match. Source context is unavailable; no fuzzy match was attempted.' }),
                      jsx(AnalyzeAction, {
                        event,
                        detail: visibleDetails[event.id],
                        activeProfile: connectedProfile,
                        analyzingCallId,
                        analyzeStatus,
                        onAnalyze: () => analyzeCall(event, visibleDetails[event.id])
                      })
                    ] }) ) : null
              ]
            }, event.id))
          ] })
        ]
      }),
      jsxs('section', {
        style: cardStyle,
        children: [
          jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: 8 }, children: [
            jsx('h2', { style: { fontSize: 13, margin: 0 }, children: 'Subagent relationships' }),
            jsx('span', { style: mutedStyle, children: subagentCoverageLabel(subagentCoverage) })
          ] }),
          jsx('div', { style: { ...mutedStyle, marginTop: 3 }, children: subagentCoverage?.observer_schema_version
            ? `Observer schema ${subagentCoverage.observer_schema_version} · ${subagentCoverage.event_count || 0} lifecycle events emitted by this session · ${subagentCoverage.unmatched_starts || 0} unmatched starts · ${subagentCoverage.unmatched_stops || 0} unmatched stops · ${subagentCoverage.identity_gaps || 0} missing-ID events. Child transcripts are not copied.`
            : 'Subagent-family support or coverage is unknown for this session.' }),
          subagentEvents.length === 0
            ? jsx('div', { style: { ...mutedStyle, marginTop: 8 }, children: subagentCoverage?.coverage_state === 'observed_no_known_gaps'
              ? 'No subagent lifecycle events observed in this session.'
              : 'No subagent events captured; absence cannot be inferred from this coverage state.' })
            : subagentEvents.map(event => {
              const relatedSessionId = event.relationship === 'parent'
                ? event.child_session_id : event.parent_session_id
              const relation = event.relationship === 'parent' ? 'Child session' : 'Parent session'
              const relatedSessionAvailable = event.relationship === 'parent'
                ? event.child_session_available : event.parent_session_available
              return jsxs('article', {
                style: { borderTop: '1px solid var(--ui-stroke-secondary)', padding: '8px 0', marginTop: 7 },
                children: [
                  jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: 8 }, children: [
                    jsx('strong', { children: `${event.relationship === 'parent' ? 'Child' : 'Parent'}${event.child_role ? ` · ${event.child_role}` : ''}` }),
                    jsx('span', { style: mutedStyle, children: subagentStatusLabel(event.status, event.capture_state) })
                  ] }),
                  jsx('div', { style: { ...mutedStyle, marginTop: 2, overflowWrap: 'anywhere' }, children: `Parent ${event.parent_session_id || 'ID unavailable'} · Child ${event.child_session_id || 'ID unavailable'} · ${displayTime(event.started_at || event.stopped_at)}${event.duration_ms == null ? '' : ` · ${Number(event.duration_ms).toFixed(0)} ms`}` }),
                  event.parent_turn_id || event.parent_subagent_id || event.child_subagent_id
                    ? jsx('div', { style: { ...mutedStyle, marginTop: 3, overflowWrap: 'anywhere' }, children: [
                      event.parent_turn_id ? `parent turn ${event.parent_turn_id}` : '',
                      event.parent_subagent_id ? `parent agent ${event.parent_subagent_id}` : '',
                      event.child_subagent_id ? `child agent ${event.child_subagent_id}` : ''
                    ].filter(Boolean).join(' · ') }) : null,
                  event.child_goal ? jsxs('div', { style: { marginTop: 5, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }, children: [jsx('strong', { children: 'Goal: ' }), event.child_goal] }) : null,
                  event.child_summary ? jsxs('div', { style: { marginTop: 5, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }, children: [jsx('strong', { children: 'Summary: ' }), event.child_summary] }) : null,
                  event.capture_state !== 'paired'
                    ? jsx('div', { style: { ...mutedStyle, marginTop: 4 }, children: 'Lifecycle unmatched; subagent-family coverage is partial.' }) : null,
                  relatedSessionId && relatedSessionAvailable ? jsx('button', {
                    type: 'button', style: { ...buttonStyle, marginTop: 6 },
                    onClick: () => { setDetails({}); setSelectedId(relatedSessionId) },
                    children: `Open related ${relation.toLowerCase()} ${relatedSessionId}`
                  }) : jsx('div', { style: { ...mutedStyle, marginTop: 5 }, children: relatedSessionId
                    ? `${relation} ${relatedSessionId} is not present in source storage; navigation is unavailable.`
                    : `${relation} navigation unavailable because its session ID was not observed.` })
                ]
              }, event.id)
            })
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
      }),
          ] }),
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
