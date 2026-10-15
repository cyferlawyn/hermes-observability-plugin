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
  assert.equal((pluginSource.match(/jsx\(AnalyzeAction/g) || []).length, 2)
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
