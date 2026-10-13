// Multi-tab session renewal tests for src/lib/auth.ts.
//
//   npm run test:auth        (node --test scripts/test-auth-multitab.mjs)
//
// The frontend has no test runner, so this compiles auth.ts with the
// TypeScript already in node_modules and runs one copy per simulated TAB, each
// in its own vm context: its own module state (_refreshing, _authEpoch, the
// channel), its own sessionStorage, and a localStorage wrapper over ONE shared
// store that fires `storage` events at the OTHER tabs, as a browser does.
// BroadcastChannel, fetch and the Web Locks API are tiny fakes so every
// ordering is deterministic. Timers run 10x faster than the real ones
// (the 4 s peer wait becomes 400 ms) so the suite takes a few seconds.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import vm from 'node:vm'
import ts from 'typescript'

const here = path.dirname(fileURLToPath(import.meta.url))
// AUTH_SRC lets a mutation check point the suite at a deliberately broken copy.
const AUTH_SRC = process.env.AUTH_SRC || path.join(here, '..', 'src', 'lib', 'auth.ts')
const COMPILED = ts.transpileModule(readFileSync(AUTH_SRC, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText

const TIME_SCALE = 10
const TOKEN_KEY = 'fp_access_token'
const sleep = ms => new Promise(r => setTimeout(r, ms))

let seq = 0
function makeToken(expInSeconds) {
  const b64 = o => Buffer.from(JSON.stringify(o)).toString('base64url')
  const payload = { sub: 'u1', tenant_id: 't1', exp: Math.floor(Date.now() / 1000) + expInSeconds }
  seq++
  // Unique signature tail: the code fingerprints tokens by their last 16 chars.
  return `${b64({ alg: 'HS256' })}.${b64(payload)}.sig-${String(seq).padStart(20, '0')}`
}
const expired = () => makeToken(-60)
const fresh = () => makeToken(900)
const USER = { id: 'u1', email: 'a@stockai-e2e.io', full_name: null, role: 'admin', tenant_id: 't1' }

/** One browser: shared localStorage, a refresh endpoint, a channel bus, a lock manager. */
function makeBrowser({ broadcast = true, locks = true } = {}) {
  const store = new Map()
  const tabs = []
  const channels = new Set()
  const lockQueues = new Map()
  const server = {
    calls: [],           // refresh tokens the endpoint received, in order
    delayMs: 0,          // virtual ms (scaled like the timers)
    accept: () => true,  // (refreshToken) => boolean
  }

  function fireStorage(origin, key, oldValue, newValue) {
    for (const tab of tabs) {
      if (tab === origin) continue
      setImmediate(() => tab.dispatchStorage({ key, oldValue, newValue, storageArea: tab.g.localStorage }))
    }
  }

  class FakeBroadcastChannel {
    constructor(name) { this.name = name; this.listeners = new Set(); this.closed = false; channels.add(this) }
    addEventListener(type, fn) { if (type === 'message') this.listeners.add(fn) }
    removeEventListener(type, fn) { this.listeners.delete(fn) }
    postMessage(data) {
      const copy = structuredClone(data)
      for (const ch of channels) {
        if (ch === this || ch.closed || ch.name !== this.name) continue
        setImmediate(() => ch.listeners.forEach(fn => fn({ data: copy })))
      }
    }
    close() { this.closed = true; channels.delete(this) }
  }

  const lockManager = {
    request(name, cb) {
      const prev = lockQueues.get(name) || Promise.resolve()
      const run = prev.then(() => cb())
      lockQueues.set(name, run.catch(() => {}))
      return run
    },
  }

  async function fakeFetch(url, init) {
    assert.equal(url, '/api/auth/refresh')
    const { refresh_token } = JSON.parse(init.body)
    server.calls.push(refresh_token)
    if (server.delayMs) await sleep(server.delayMs / TIME_SCALE)
    if (!server.accept(refresh_token)) {
      return { ok: false, status: 401, json: async () => ({ error_code: 'refresh_token_invalid' }) }
    }
    const token = fresh()
    return { ok: true, status: 200, json: async () => ({ data: { access_token: token } }) }
  }

  function openTab(name) {
    const session = new Map()
    const storageListeners = new Set()
    const tab = { name }
    const localStorage = {
      getItem: k => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => { const old = localStorage.getItem(k); store.set(k, String(v)); fireStorage(tab, k, old, String(v)) },
      removeItem: k => { const old = localStorage.getItem(k); store.delete(k); if (old !== null) fireStorage(tab, k, old, null) },
    }
    const sessionStorage = {
      getItem: k => (session.has(k) ? session.get(k) : null),
      setItem: (k, v) => session.set(k, String(v)),
      removeItem: k => session.delete(k),
    }
    const g = {
      localStorage, sessionStorage, fetch: fakeFetch, atob, console, structuredClone,
      setTimeout: (fn, ms) => setTimeout(fn, (ms || 0) / TIME_SCALE),
      clearTimeout: id => clearTimeout(id),
      navigator: locks ? { locks: lockManager } : {},
      addEventListener: (type, fn) => { if (type === 'storage') storageListeners.add(fn) },
      removeEventListener: (type, fn) => { if (type === 'storage') storageListeners.delete(fn) },
      exports: {},
    }
    if (broadcast) g.BroadcastChannel = FakeBroadcastChannel
    g.window = g
    g.module = { exports: g.exports }
    vm.createContext(g)
    vm.runInContext(COMPILED, g, { filename: `auth.ts[tab ${name}]` })
    Object.assign(tab, {
      g, auth: g.module.exports,
      refresh: () => session.get('fp_refresh_token') ?? null,
      dispatchStorage: e => [...storageListeners].forEach(fn => fn(e)),
    })
    tabs.push(tab)
    return tab
  }

  /** Write the shared token as some other actor (a login elsewhere) would. */
  function writeShared(key, value) {
    const old = store.has(key) ? store.get(key) : null
    if (value === null) store.delete(key); else store.set(key, value)
    fireStorage(null, key, old, value)
  }

  /** A raw channel endpoint for the test itself (a tab with no auth module). */
  function listen(onMessage) {
    const ch = new FakeBroadcastChannel('fp_auth')
    ch.addEventListener('message', e => onMessage(e.data))
    return ch
  }

  return { store, server, openTab, writeShared, listen, sharedToken: () => store.get(TOKEN_KEY) ?? null }
}

// Let queued storage events / channel messages land.
const settle = () => sleep(5)

test('a tab without a refresh token renews through a tab that has one', async () => {
  const br = makeBrowser()
  const A = br.openTab('A'), B = br.openTab('B')
  const t0 = expired()
  A.auth.setAuth(t0, 'R-A', USER)
  await settle()
  assert.equal(B.refresh(), null, 'B must not see A\'s refresh token')

  assert.equal(await B.auth.tryRefresh(t0), true)
  assert.equal(br.server.calls.length, 1)
  assert.equal(br.server.calls[0], 'R-A')
  assert.notEqual(br.sharedToken(), t0)
  assert.equal(B.auth.isAuthenticated(), true)
  assert.equal(A.auth.isAuthenticated(), true)
  assert.equal(B.refresh(), null, 'the refresh token never travels to B')
})

test('nobody can renew: false, and the failing tab does not wipe the shared token', async () => {
  const br = makeBrowser()
  const B = br.openTab('B'), C = br.openTab('C')
  const t0 = expired()
  br.writeShared(TOKEN_KEY, t0)
  await settle()

  const [b, c] = await Promise.all([B.auth.tryRefresh(t0), C.auth.tryRefresh(t0)])
  assert.equal(b, false)
  assert.equal(c, false)
  assert.equal(br.server.calls.length, 0)

  B.auth.endSession(t0)
  assert.equal(br.sharedToken(), t0, 'a tab without a refresh token must not wipe the shared token')
  assert.equal(B.auth.isAuthenticated(), false)

  // A tab that DOES hold the refresh token opens late (e.g. was throttled) and
  // can still renew for everybody, B included.
  const A = br.openTab('A')
  A.g.sessionStorage.setItem('fp_refresh_token', 'R-A')
  assert.equal(await A.auth.tryRefresh(t0), true)
  assert.equal(B.auth.isAuthenticated(), true, 'a renewed token is not the one B saw refused')
})

test('the server refuses the refresh token: the asking tab gets false', async () => {
  const br = makeBrowser()
  const A = br.openTab('A'), B = br.openTab('B')
  const t0 = expired()
  A.auth.setAuth(t0, 'R-A', USER)
  br.server.accept = () => false
  await settle()
  assert.equal(await B.auth.tryRefresh(t0), false)
  assert.equal(br.server.calls.length, 1)
  assert.equal(br.sharedToken(), t0)
})

test('a server-refused but clock-valid token does not bounce /login back into the app', async () => {
  const br = makeBrowser()
  const B = br.openTab('B')
  const t = fresh()
  br.writeShared(TOKEN_KEY, t)
  await settle()
  assert.equal(B.auth.isAuthenticated(), true)
  assert.equal(await B.auth.tryRefresh(t), false)
  B.auth.endSession(t)
  assert.equal(br.sharedToken(), t)
  assert.equal(B.auth.isAuthenticated(), false, 'B must not treat the refused token as a session')
})

test('logout in A while B waits: B stays logged out and the renewal is dropped', async () => {
  const br = makeBrowser()
  const A = br.openTab('A'), B = br.openTab('B')
  const t0 = expired()
  A.auth.setAuth(t0, 'R-A', USER)
  br.server.delayMs = 1500
  await settle()

  const pending = B.auth.tryRefresh(t0)
  await sleep(20)  // A received the request and its refresh is in flight
  assert.equal(br.server.calls.length, 1)
  const loggedOutAt = Date.now()
  A.auth.clearAuth()
  assert.equal(await pending, false)
  // B learns of the logout from the storage event, not by sitting out the 4 s wait.
  assert.ok(Date.now() - loggedOutAt < 4000 / TIME_SCALE / 2, 'B must stop waiting as soon as A logs out')
  await sleep(br.server.delayMs / TIME_SCALE + 20)  // let A's refresh come back
  assert.equal(br.sharedToken(), null, 'the in-flight renewal must not resurrect the session')
  assert.equal(B.auth.isAuthenticated(), false)
  assert.equal(A.auth.isAuthenticated(), false)
})

test('several tabs expiring together produce exactly one refresh request', async () => {
  const br = makeBrowser()
  const A = br.openTab('A'), B = br.openTab('B'), C = br.openTab('C'), D = br.openTab('D')
  const t0 = expired()
  A.auth.setAuth(t0, 'R-A', USER)
  br.server.delayMs = 300
  await settle()

  const results = await Promise.all([
    A.auth.tryRefresh(t0), B.auth.tryRefresh(t0), C.auth.tryRefresh(t0), D.auth.tryRefresh(t0),
    B.auth.tryRefresh(t0),  // a second 401 inside B shares B's wait
  ])
  assert.deepEqual(results, [true, true, true, true, true])
  assert.equal(br.server.calls.length, 1)

  // A request for the same stale token that arrives after the renewal landed
  // (posted before the asker saw the new token) is answered without the network.
  const replies = []
  const bus = br.listen(msg => replies.push(msg.type))
  bus.postMessage({ type: 'refresh_request', stale: t0.slice(-16) })
  await sleep(30)
  assert.deepEqual(replies, ['refreshed'])
  assert.equal(br.server.calls.length, 1)
})

test('two tabs holding refresh tokens answer one request with one network call', async () => {
  const br = makeBrowser()
  const A1 = br.openTab('A1'), A2 = br.openTab('A2'), B = br.openTab('B')
  const t0 = expired()
  A1.auth.setAuth(t0, 'R-1', USER)
  A2.g.sessionStorage.setItem('fp_refresh_token', 'R-2')
  br.server.delayMs = 200
  await settle()

  assert.equal(await B.auth.tryRefresh(t0), true)
  await sleep(50)
  assert.equal(br.server.calls.length, 1)
})

test('a token already renewed by another tab is used without any request', async () => {
  const br = makeBrowser()
  const A = br.openTab('A'), B = br.openTab('B')
  const t0 = expired()
  A.auth.setAuth(t0, 'R-A', USER)
  await settle()
  br.writeShared(TOKEN_KEY, fresh())  // renewed between B's 401 and its retry
  await settle()
  assert.equal(await B.auth.tryRefresh(t0), true)
  assert.equal(await A.auth.tryRefresh(t0), true)
  assert.equal(br.server.calls.length, 0)
})

test('a tab that surrendered its refresh token after a logout does not answer', async () => {
  const br = makeBrowser()
  const A = br.openTab('A'), B = br.openTab('B'), X = br.openTab('X')
  A.auth.setAuth(fresh(), 'R-A', USER)
  await settle()
  X.auth.clearAuth()          // logout in another tab
  await settle()
  assert.equal(A.refresh(), null, 'A surrendered its refresh token')

  const t0 = expired()
  br.writeShared(TOKEN_KEY, t0)  // a stale token reappears in shared storage
  await settle()
  assert.equal(await B.auth.tryRefresh(t0), false)
  assert.equal(br.server.calls.length, 0, 'A must not renew with a surrendered token')
})

test('a late answer within the grace window still counts; past it does not', async () => {
  const late = makeBrowser()
  const A = late.openTab('A'), B = late.openTab('B')
  let t0 = expired()
  A.auth.setAuth(t0, 'R-A', USER)
  late.server.delayMs = 4300   // past the 4 s wait, inside the 600 ms grace
  await settle()
  assert.equal(await B.auth.tryRefresh(t0), true)

  const tooLate = makeBrowser()
  const A2 = tooLate.openTab('A'), B2 = tooLate.openTab('B')
  t0 = expired()
  A2.auth.setAuth(t0, 'R-A', USER)
  tooLate.server.delayMs = 6000
  await settle()
  assert.equal(await B2.auth.tryRefresh(t0), false)
})

test('concurrent 401s in the tab holding the refresh token share one request', async () => {
  const br = makeBrowser()
  const A = br.openTab('A')
  const t0 = expired()
  A.auth.setAuth(t0, 'R-A', USER)
  br.server.delayMs = 100
  const results = await Promise.all([A.auth.tryRefresh(t0), A.auth.tryRefresh(t0), A.auth.tryRefresh()])
  assert.deepEqual(results, [true, true, true])
  assert.equal(br.server.calls.length, 1)
})

test('without BroadcastChannel the old behaviour holds', async () => {
  const br = makeBrowser({ broadcast: false })
  const A = br.openTab('A'), B = br.openTab('B')
  const t0 = expired()
  A.auth.setAuth(t0, 'R-A', USER)
  await settle()
  const startedAt = Date.now()
  assert.equal(await B.auth.tryRefresh(t0), false)
  assert.ok(Date.now() - startedAt < 4000 / TIME_SCALE / 2, 'old behaviour answers at once, no peer wait')
  assert.equal(br.server.calls.length, 0)
  B.auth.endSession(t0)
  assert.equal(br.sharedToken(), null, 'old behaviour: the failing tab ends the session')
  // The tab that holds the refresh token still renews itself as before.
  const br2 = makeBrowser({ broadcast: false })
  const A2 = br2.openTab('A')
  A2.auth.setAuth(t0, 'R-A', USER)
  assert.equal(await A2.auth.tryRefresh(t0), true)
  assert.equal(br2.server.calls.length, 1)
})

test('a tab holding the refresh token that is refused ends the session for all', async () => {
  const br = makeBrowser()
  const A = br.openTab('A')
  const t0 = expired()
  A.auth.setAuth(t0, 'R-A', USER)
  br.server.accept = () => false
  assert.equal(await A.auth.tryRefresh(t0), false)
  A.auth.endSession(t0)
  assert.equal(br.sharedToken(), null)
})
