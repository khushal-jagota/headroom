const { test } = require('node:test');
const assert = require('node:assert/strict');
const { mkdtempSync, mkdirSync, rmSync, writeFileSync, readFileSync, existsSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { ScreenStore, hash, verifyPayload } = require(join(process.env.PANELS_DESKTOP_TEST_BUILD, 'screens/store.js'));
const { ScreenController } = require(join(process.env.PANELS_DESKTOP_TEST_BUILD, 'screens/controller.js'));
function payload(text) {
  const bytes = Buffer.from(text);
  const manifest = { version: 1, files: [{ path: 'web/dist/index.html', sha256: hash(bytes) }] };
  return { digest: hash(JSON.stringify(manifest)), value: { manifest, files: { 'web/dist/index.html': bytes.toString('base64') } } };
}
function host(t) {
  const root = mkdtempSync(join(tmpdir(), 'panels-screen-test-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const baseline = join(root, 'baseline');
  mkdirSync(baseline);
  return { root, store: new ScreenStore(join(root, 'screens'), baseline) };
}
// New risks beyond the signed probe: malformed network payloads must never write paths,
// and a cached copy must be rechecked after disk changes. No browser host is needed.
test('payload admission rejects tampered bytes and escaping paths, and startup rechecks disk', t => {
  const { root, store } = host(t);
  const good = payload('good');
  const corrupt = structuredClone(good.value);
  corrupt.files['web/dist/index.html'] = Buffer.from('bad').toString('base64');
  assert.throws(() => store.install(corrupt, good.digest), /mismatch/);
  const climb = { version: 1, files: [{ path: 'web/dist/../../../outside', sha256: hash('bad') }] };
  assert.throws(() => verifyPayload({ manifest: climb, files: {} }, hash(JSON.stringify(climb))), /Invalid screen file/);
  store.select('server-a');
  store.install(good.value, good.digest);
  store.activate({ digest: good.digest, appSha: 'release-a' });
  store.ready();
  writeFileSync(join(store.root, 'web/dist/index.html'), 'disk changed');
  store.select('server-a');
  assert.equal(store.current, null);
  assert.equal(store.root, store.baseline);
  assert.equal(existsSync(join(root, 'outside')), false);
});
// New failure boundary: a process can die before the page reports ready. The persisted
// trial must roll back once and remain rejected across probes/restarts.
test('unconfirmed startup rolls back once, retains waiting copies, and isolates another server', t => {
  const { store } = host(t);
  const a = payload('a'), b = payload('b'), c = payload('c'), waiting = payload('waiting'), obsolete = payload('obsolete');
  store.select('server-a');
  store.install(a.value, a.digest); store.activate({ digest: a.digest, appSha: 'a' }); store.ready();
  store.install(b.value, b.digest); store.activate({ digest: b.digest, appSha: 'b' });
  store.install(c.value, c.digest); store.activate({ digest: c.digest, appSha: 'c' });
  const restarted = new ScreenStore(store.folder, store.baseline);
  restarted.select('server-a');
  assert.equal(restarted.current.digest, a.digest);
  assert.equal(restarted.rejected, c.digest);
  restarted.install(waiting.value, waiting.digest); restarted.waiting = waiting.digest;
  restarted.install(obsolete.value, obsolete.digest); restarted.cleanup();
  assert.equal(restarted.verify(waiting.digest), true);
  assert.equal(existsSync(join(store.folder, obsolete.digest)), false);
  const resumed = new ScreenStore(store.folder, store.baseline);
  resumed.select('server-a');
  assert.equal(resumed.waiting, waiting.digest);
  assert.equal(resumed.verify(waiting.digest), true);
  resumed.select('server-b');
  assert.equal(resumed.waiting, null);
  assert.equal(resumed.current, null);
  assert.equal(resumed.root, resumed.baseline);
  assert.equal(existsSync(join(store.folder, waiting.digest)), false);
});
function controller(t, overrides = {}) {
  const { store } = host(t);
  const copy = payload('new');
  let allowed = false;
  let hasWindow = true;
  let appSha = 'release-a';
  let currentDigest = copy.digest;
  let address = 'server-a';
  const calls = [], presentations = [];
  const meta = () => ({ screens: { sha256: currentDigest }, waiting_screens: null, app_sha: appSha, compatibility: { desktop_shell: { minimum_bridge_version: 2 } } });
  const dependencies = {
    address: () => address,
    canActivate: () => hasWindow,
    request: async path => new Response(JSON.stringify(path === '/api/meta' ? meta() : copy.value), { headers: { 'content-type': 'application/json' } }),
    requireBridgeVersion: async floor => { calls.push(floor); return allowed; },
    present: (value, reload) => presentations.push({ value, reload }),
    ...overrides,
  };
  return { store, copy, calls, presentations, controller: new ScreenController(store, dependencies),
    allow: () => { allowed = true; }, close: () => { hasWindow = false; }, open: () => { hasWindow = true; }, sha: value => { appSha = value; }, address: value => { address = value; }, digest: value => { currentDigest = value; } };
}
// The updater proof does not prove screen activation order, rejection persistence, or
// same-screen/new-server release guards. Exercise the native owner without Electron.
test('activation waits for the bridge and a window, binds the release, and never retries rollback', async t => {
  const host = controller(t);
  await host.controller.connect();
  assert.equal(host.store.current, null);
  assert.deepEqual(host.calls, [2]);
  host.allow(); host.close();
  await host.controller.check();
  assert.equal(host.store.pending, false);
  host.open(); await host.controller.check();
  assert.equal(host.store.current.digest, host.copy.digest);
  assert.equal(host.store.pending, true);
  host.controller.ready();
  host.sha('release-b'); await host.controller.check();
  assert.equal(host.store.current.appSha, 'release-b');
  host.controller.failed();
  assert.equal(host.store.current.appSha, 'release-a');
  const before = host.calls.length;
  await host.controller.check();
  assert.equal(host.calls.length, before);
  assert.equal(host.store.current.appSha, 'release-a');
  await host.controller.retry();
  assert.equal(host.store.current.appSha, 'release-b');
  assert.equal(host.store.pending, true);
});
// A late response after Change Server must not install or activate old-server code.
test('a late network result cannot activate after a server change; offline first run uses baseline', async t => {
  t.mock.method(console, 'warn', () => {});
  let release, begin;
  const started = new Promise(resolve => { begin = resolve; });
  const response = new Promise(resolve => { release = resolve; });
  const host = controller(t, { request: () => { begin(); return response; } });
  const connecting = host.controller.connect();
  await started;
  host.address('server-b');
  release(new Response(JSON.stringify({ screens: { sha256: host.copy.digest } })));
  await connecting;
  assert.equal(host.store.current, null);
  assert.equal(host.store.root, host.store.baseline);
  assert.equal(host.presentations.filter(item => item.reload).length, 0);
  const offline = controller(t, { request: async () => { throw new Error('offline'); } });
  await offline.controller.connect();
  assert.equal(offline.store.root, offline.store.baseline);
});
// The shell page must use ordinary owner HTTP actions with the server release guard.
// Execute its real script in a small DOM adapter; no native bridge or browser is needed.
test('the old-server page retries discovery and posts the server API update with its release guard', async () => {
  const { runInNewContext } = require('node:vm');
  const elements = new Map(['status', 'update', 'retry'].map(id => [id, { disabled: false, textContent: '', handlers: {}, addEventListener(type, handler) { this.handlers[type] = handler; } }]));
  const calls = [];
  let available = false;
  let refused = false;
  let failedAttempt = false;
  const script = readFileSync(join(__dirname, '../../server_update_page/server-update.js'), 'utf8');
  runInNewContext(script, {
    document: { getElementById: id => elements.get(id) },
    fetch: async (path, options) => {
      calls.push({ path, options });
      if (options?.method === 'POST') return { ok: !refused, status: 409, json: async () => ({ detail: 'Release changed' }) };
      return { ok: true, json: async () => ({ app_sha: 'running-server', available: available ? { app_sha: 'next-server' } : null, allowed_actions: failedAttempt ? ['retry'] : available ? ['update'] : [], attempt: failedAttempt ? { attempt_id: 'failed-attempt' } : null }) };
    },
  });
  const settle = () => new Promise(resolve => setImmediate(resolve));
  await settle();
  assert.equal(elements.get('update').disabled, true);
  available = true;
  elements.get('retry').handlers.click(); await settle();
  assert.equal(elements.get('update').disabled, false);
  refused = true;
  await elements.get('update').handlers.click();
  assert.equal(elements.get('status').textContent, 'Release changed');
  assert.equal(elements.get('update').disabled, false);
  refused = false;
  await elements.get('update').handlers.click();
  const post = calls.find(call => call.options?.method === 'POST');
  assert.equal(post.path, '/api/updates/install');
  assert.equal(post.options.headers['X-Panels-App-SHA'], 'running-server');
  assert.deepEqual(JSON.parse(post.options.body), { app_sha: 'next-server' });
  assert.match(elements.get('status').textContent, /updating/);
  failedAttempt = true; available = false;
  elements.get('retry').handlers.click(); await settle();
  assert.equal(elements.get('update').textContent, 'Try again');
  await elements.get('update').handlers.click();
  const retry = calls.at(-1);
  assert.equal(retry.path, '/api/updates/attempts/failed-attempt/retry');
  assert.equal(retry.options.headers['X-Panels-App-SHA'], 'running-server');
  assert.deepEqual(JSON.parse(retry.options.body), {});
});
