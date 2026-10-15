const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')

const pluginPath = path.join(__dirname, '..', 'desktop', 'plugin.js')
const pluginSource = fs.readFileSync(pluginPath, 'utf8')
const actionStart = pluginSource.indexOf('function subscribeProfileChanges()')
const actionEnd = pluginSource.indexOf('\nfunction Metric(', actionStart)
assert.notEqual(actionStart, -1, 'Profile-bound Analyze helpers must exist')
assert.notEqual(actionEnd, -1, 'Analyze helpers must end before Metric')
const actionSource = `${pluginSource.slice(actionStart, actionEnd)}\n({ readCallDetail, startAnalyzeFollowUp })`

function makeProfileAtom(initial) {
  let activeProfile = initial
  const listeners = new Set()
  return {
    get: () => activeProfile,
    subscribe(listener) {
      listeners.add(listener)
      listener(activeProfile)
      return () => listeners.delete(listener)
    },
    set(profile) {
      activeProfile = profile
      for (const listener of listeners) listener(profile)
    }
  }
}

function loadAnalyzeHelpers(host) {
  return vm.runInNewContext(actionSource, { host, Error })
}

function loadActionWithPolicy(host, rest, sourceProfile = 'personal') {
  const { startAnalyzeFollowUp } = loadAnalyzeHelpers(host)
  return prompt => startAnalyzeFollowUp(prompt, rest, sourceProfile)
}

test('Analyze RPCs are wired only to the explicit failed-call button action', () => {
  assert.match(pluginSource, /onClick: onAnalyze/)
  assert.match(pluginSource, /onAnalyze: \(\) => analyzeCall\(event, visibleDetails\[event\.id\]\)/)
  assert.equal((pluginSource.match(/jsx\(AnalyzeAction/g) || []).length, 3)
  assert.equal((pluginSource.match(/startAnalyzeFollowUp\(/g) || []).length, 2)
  assert.match(pluginSource, /if \(event\.outcome !== 'failed' \|\| !detail\?\.analyze_prompt \|\| detail\.profile !== activeProfile\) return/)
})

test('call detail is discarded after an away-and-back profile change', async () => {
  const profileAtom = makeProfileAtom('personal')
  const host = { state: { profile: profileAtom } }
  const calls = []
  let resolveDetail
  const pendingDetail = new Promise(resolve => { resolveDetail = resolve })
  const rest = async path => {
    calls.push(path)
    return pendingDetail
  }

  const task = loadAnalyzeHelpers(host).readCallDetail(rest, { session_id: 'source-1', id: 'call-1' })
  profileAtom.set('work')
  profileAtom.set('personal')
  resolveDetail({ profile: 'personal', analyze_prompt: 'stale prompt' })

  await assert.rejects(task, /profile changed while reading call detail/)
  assert.deepEqual(calls, ['/sessions/source-1/calls/call-1/detail?profile=personal'])
})

test('Analyze creates and submits a separate session on the connected profile', async () => {
  const calls = []
  const host = {
    state: { profile: makeProfileAtom('personal') },
    request: async (method, params) => {
      calls.push([method, params])
      if (method === 'session.create') {
        return { session_id: 'runtime-follow-up', stored_session_id: 'stored-follow-up' }
      }
      return { accepted: true }
    },
    openSession: async (sessionId, options) => calls.push(['openSession', sessionId, options])
  }
  const rest = async (path, options) => {
    calls.push(['rest', path, options])
    return { read_only: true, profile: 'personal' }
  }
  const prompt = 'only the selected failed call evidence'
  const result = await loadActionWithPolicy(host, rest, 'personal')(prompt)

  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    ['session.create', { source: 'desktop', profile: 'personal' }],
    ['rest', '/analyze-sessions/runtime-follow-up/read-only?profile=personal', { method: 'POST' }],
    ['prompt.submit', { session_id: 'runtime-follow-up', text: prompt, profile: 'personal' }],
    ['openSession', 'stored-follow-up', { profile: 'personal', intent: 'tab' }]
  ])
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { storedSessionId: 'stored-follow-up', opened: true })
})

test('Analyze does not open the follow-up if the profile changes during prompt submission', async () => {
  const calls = []
  const profileAtom = makeProfileAtom('personal')
  const host = {
    state: { profile: profileAtom },
    request: async (method, params) => {
      calls.push([method, params])
      if (method === 'session.create') {
        return { session_id: 'runtime-follow-up', stored_session_id: 'stored-follow-up' }
      }
      if (method === 'prompt.submit') {
        profileAtom.set('work')
        profileAtom.set('personal')
        return { accepted: true }
      }
      return { accepted: true }
    },
    openSession: async (...args) => calls.push(['openSession', ...args])
  }
  const rest = async () => ({ read_only: true, profile: 'personal' })

  const result = await loadActionWithPolicy(host, rest, 'personal')('evidence')

  assert.deepEqual(JSON.parse(JSON.stringify(result)), {
    storedSessionId: 'stored-follow-up', opened: false, profileChanged: true
  })
  assert.deepEqual(calls.map(([method]) => method), ['session.create', 'prompt.submit'])
})

test('Analyze does not submit the evidence if the read-only policy cannot be installed', async () => {
  const calls = []
  const host = {
    state: { profile: makeProfileAtom('personal') },
    request: async (method, params) => {
      calls.push([method, params])
      return { session_id: 'runtime-unlocked', stored_session_id: 'stored-unlocked' }
    },
    openSession: async () => assert.fail('must not open an unprotected Analyze session')
  }
  const rest = async () => { throw new Error('policy endpoint unavailable') }

  await assert.rejects(loadActionWithPolicy(host, rest)('sensitive evidence'), /policy endpoint unavailable/)
  assert.deepEqual(calls.map(([method]) => method), ['session.create'])
})

test('Analyze aborts when the connected profile changes during policy installation', async () => {
  const calls = []
  const profileAtom = makeProfileAtom('personal')
  const host = {
    state: { profile: profileAtom },
    request: async (method, params) => {
      calls.push([method, params])
      return { session_id: 'runtime-new', stored_session_id: 'stored-new' }
    },
    openSession: async () => assert.fail('must not open an incomplete Analyze session')
  }
  const rest = async () => {
    calls.push(['rest'])
    profileAtom.set('work')
    return { read_only: true, profile: 'personal' }
  }

  await assert.rejects(
    loadActionWithPolicy(host, rest, 'personal')('evidence'),
    /profile changed during policy installation/
  )
  assert.deepEqual(calls.map(([method]) => method), ['session.create', 'rest'])
})

test('Analyze rejects stale detail when the connected profile is different', async () => {
  const calls = []
  const host = {
    state: { profile: makeProfileAtom('work') },
    request: async (method, params) => {
      calls.push([method, params])
      return { session_id: 'runtime-new', stored_session_id: 'stored-new' }
    },
    openSession: async () => assert.fail('must not open a stale-profile Analyze session')
  }
  const rest = async () => { calls.push(['rest']); return { read_only: true, profile: 'personal' } }

  await assert.rejects(
    loadActionWithPolicy(host, rest, 'personal')('stale evidence'),
    /selected call belongs to a different profile/
  )
  assert.deepEqual(calls, [])
})

test('Analyze aborts when the profile changes away and back during policy installation', async () => {
  const calls = []
  const profileAtom = makeProfileAtom('personal')
  const host = {
    state: { profile: profileAtom },
    request: async (method, params) => {
      calls.push([method, params])
      return { session_id: 'runtime-new', stored_session_id: 'stored-new' }
    },
    openSession: async () => assert.fail('must not open a policy-mismatched session')
  }
  const rest = async (path, options) => {
    calls.push(['rest', path, options])
    profileAtom.set('work')
    profileAtom.set('personal')
    return { read_only: true, profile: 'personal' }
  }

  await assert.rejects(
    loadActionWithPolicy(host, rest, 'personal')('evidence'),
    /profile changed during policy installation/
  )
  assert.deepEqual(calls.map(([method]) => method), ['session.create', 'rest'])
})

test('Analyze fails closed when session creation omits required IDs', async () => {
  const calls = []
  const host = {
    state: { profile: makeProfileAtom('personal') },
    request: async (method, params) => {
      calls.push([method, params])
      return { session_id: 'runtime-only' }
    },
    openSession: async () => assert.fail('must not open an incomplete session')
  }

  await assert.rejects(loadActionWithPolicy(host, async () => ({}))('evidence'), /both runtime and stored IDs/)
  assert.deepEqual(calls.map(([method]) => method), ['session.create'])
})

test('a prompt submission failure never opens or targets the source session', async () => {
  const calls = []
  const host = {
    state: { profile: makeProfileAtom('personal') },
    request: async (method, params) => {
      calls.push([method, params])
      if (method === 'session.create') {
        return { session_id: 'runtime-new', stored_session_id: 'stored-new' }
      }
      throw new Error('submit refused')
    },
    openSession: async (...args) => calls.push(['openSession', ...args])
  }

  await assert.rejects(loadActionWithPolicy(host, async () => ({ read_only: true, profile: 'personal' }))('evidence'), /submit refused/)
  assert.deepEqual(calls.map(([method]) => method), ['session.create', 'prompt.submit'])
  assert.equal(calls[1][1].session_id, 'runtime-new')
})

test('Control Room presents tool breakdown and a unified, chronological session activity view', () => {
  const start = pluginSource.indexOf('const ID =')
  const end = pluginSource.indexOf('\nconst plugin = {', start)
  assert.notEqual(start, -1)
  assert.notEqual(end, -1)
  const source = `${pluginSource.slice(start, end)}\n;({ ControlRoom })`
  const overview = {
    available: true,
    observer_schema_version: 'hermes.observer.v1',
    metrics: {
      calls: { total: 4, ok: 3, failed: 1, not_executed: 0, unknown: 0 },
      rate_calls: { ok: 3, failed: 1, denominator: 4 }, success_rate: 0.75,
      included_sessions: 1, observed_sessions: 1, rate_included_sessions: 1,
      rate_excluded_sessions: 0, partial_sessions: 0, unknown_sessions: 0,
      excluded_sessions: 0, reset_cutoff: null
    },
    provider_attempts: {}, approvals: {}, subagents: {},
    tool_breakdown: [{
      tool_name: 'mcp__files__read',
      calls: { total: 4, ok: 3, failed: 1, not_executed: 0, unknown: 0 },
      rate_calls: { ok: 3, failed: 1, denominator: 4 }, success_rate: 0.75,
      coverage: { total_sessions: 1, observed_sessions: 1, partial_sessions: 0, unknown_sessions: 0, not_captured_sessions: 0 }
    }],
    sessions: [{
      id: 's1', title: 'Read incident notes', started_at: 200,
      coverage_state: 'observed_no_known_gaps', call_count: 4
    }],
    repeated_failures: []
  }
  const trace = {
    session: overview.sessions[0], calls: [], provider_attempts: [], approvals: [], subagents: [],
    timeline: [
      { id: 'sub1', kind: 'subagent', status: 'succeeded', child_role: 'researcher', child_goal: 'Check source notes', started_at: 205, timestamp: 205, capture_state: 'paired' },
      { id: 'a1', kind: 'approval', outcome: 'allowed', requested_at: 204, timestamp: 204, capture_state: 'paired' },
      { id: 'c1', kind: 'tool_call', id: 'c1', tool_name: 'mcp__files__read', outcome: 'failed', started_at: 203, timestamp: 203, capture_state: 'paired', session_id: 's1' },
      { id: 'p1', kind: 'provider_attempt', provider: 'copilot', model: 'gpt-test', outcome: 'ok', started_at: 202, timestamp: 202 },
      { id: 'm1', kind: 'message', role: 'user', content: 'Find the incident cause', timestamp: 201, has_tool_calls: false }
    ]
  }
  let stateIndex = 0
  const host = { state: { profile: { get: () => 'personal', subscribe: () => () => {} } } }
  const jsx = (type, props) => ({ type, props: props || {} })
  const jsxs = jsx
  const hooks = {
    host, jsx, jsxs, Intl, Date, Number, Object, Array, String, encodeURIComponent,
    useValue: atom => atom.get(), useRef: value => ({ current: value }), useEffect: () => {},
    useState: initial => {
      const index = stateIndex++
      const value = index === 0 ? overview : index === 1 ? 's1' : index === 2 ? trace : initial
      return [value, () => {}]
    }
  }
  const { ControlRoom } = vm.runInNewContext(source, hooks)
  const renderedNodes = []
  const render = node => {
    if (node == null || typeof node === 'boolean') return ''
    if (Array.isArray(node)) return node.map(render).join(' ')
    if (typeof node !== 'object') return String(node)
    if (typeof node.type === 'function') return render(node.type(node.props || {}))
    renderedNodes.push(node)
    return `${Object.entries(node.props || {}).filter(([key]) => key !== 'children').map(([, value]) => typeof value === 'string' ? value : '').join(' ')} ${render(node.props?.children)}`
  }
  const text = render(ControlRoom({ rest: async () => ({}) }))

  assert.match(text, /Tool & MCP breakdown/)
  assert.match(text, /mcp__files__read/)
  assert.match(text, /Session activity/)
  assert.match(text, /Find the incident cause/)
  assert.match(text, /Provider attempt/)
  assert.match(text, /Approval/)
  assert.match(text, /Subagent/)
  const chronology = [
    'Find the incident cause', 'Provider attempt · copilot', 'Tool call · mcp__files__read',
    'Approval', 'Subagent · researcher'
  ].map(marker => text.indexOf(marker))
  assert.ok(chronology.every(index => index >= 0), 'each event family is present in the timeline')
  assert.deepEqual(chronology, [...chronology].sort((a, b) => a - b), 'mixed event families render in timestamp order')
  const statusTones = [...new Set(renderedNodes.map(node => node.props?.['data-status-tone']).filter(Boolean))]
  assert.ok(['info', 'success', 'danger', 'subagent'].every(tone => statusTones.includes(tone)), 'event status chips use distinct semantic colors')
  assert.ok(renderedNodes.some(node => node.props?.['data-metric-tone'] === 'warning'), 'sub-90% success metric is highlighted as cautionary')
  assert.ok(renderedNodes.some(node => node.type === 'section' && String(node.props?.style?.border || '').includes('--dt-border')), 'panels use the stronger prototype-style border')
})
