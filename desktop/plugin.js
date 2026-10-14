import { host, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'hermes-observability-plugin'
const EMPTY_METRICS = {
  calls: { total: 0, ok: 0, failed: 0, not_executed: 0, unknown: 0 },
  rate_calls: { ok: 0, failed: 0, denominator: 0 },
  success_rate: null,
  total_sessions: 0,
  captured_sessions: 0,
  observed_sessions: 0,
  partial_sessions: 0,
  unknown_sessions: 0,
  excluded_sessions: 0,
  rate_excluded_sessions: 0
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
  const [details, setDetails] = useState({})
  const [detailLoading, setDetailLoading] = useState('')

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

  useEffect(() => {
    let live = true
    setLoading(true)
    setError('')
    rest('/overview')
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
  }, [rest, refreshKey])

  useEffect(() => {
    if (!selectedId) {
      setTrace(null)
      return undefined
    }
    let live = true
    setTrace(null)
    rest(`/sessions/${encodeURIComponent(selectedId)}/trace`)
      .then(data => { if (live) setTrace(data) })
      .catch(err => { if (live) setError(err?.message || 'Could not read this session trace.') })
    return () => { live = false }
  }, [rest, selectedId, refreshKey])

  const sessions = Array.isArray(overview?.sessions) ? overview.sessions : []
  const metrics = overview?.metrics || EMPTY_METRICS
  const calls = metrics.calls || EMPTY_METRICS.calls
  const rateCalls = metrics.rate_calls || EMPTY_METRICS.rate_calls
  const rate = metrics.success_rate === null || metrics.success_rate === undefined
    ? '—' : `${(metrics.success_rate * 100).toFixed(1)}%`
  const selected = sessions.find(session => session.id === selectedId)
  const events = Array.isArray(trace?.calls) ? trace.calls : []

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
          jsx('button', {
            type: 'button', style: buttonStyle,
            onClick: () => setRefreshKey(value => value + 1),
            children: loading ? 'Loading…' : 'Refresh'
          })
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
          jsx(Metric, { label: 'Success rate', value: rate, detail: `${rateCalls.denominator} explicit outcomes · ${metrics.rate_excluded_sessions || 0} sessions excluded for coverage` }),
          jsx(Metric, { label: 'Not executed / unknown', value: `${calls.not_executed} / ${calls.unknown}`, detail: 'Excluded from rate denominator' }),
          jsx(Metric, { label: 'Coverage', value: `${metrics.observed_sessions || 0} rate-eligible`, detail: `${metrics.partial_sessions || 0} partial · ${metrics.unknown_sessions || 0} unknown · ${metrics.excluded_sessions || 0} not captured` })
        ]
      }),
      jsx('div', {
        style: { ...mutedStyle, fontSize: 11 },
        children: 'Overall window only in this MVP slice. Rates exclude partial/unknown/uncaptured sessions; observed counts from incomplete sessions are lower-bound evidence. “Observed — no known gaps” is not a completeness guarantee because observer hooks fail open.'
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
                event.error_type || event.error_message ? jsxs('div', {
                  style: { marginTop: 5, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' },
                  children: [event.error_type ? `${event.error_type}: ` : '', event.error_message || 'Explicit failure; no message supplied.']
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
                      details[event.id].call.error_type || details[event.id].call.error_message
                        ? jsxs('div', { children: [
                          jsx('strong', { children: 'Captured error' }),
                          jsx('div', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }, children: `${details[event.id].call.error_type ? `${details[event.id].call.error_type}: ` : ''}${details[event.id].call.error_message || ''}` })
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
