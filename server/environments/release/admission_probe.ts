/** Trusted probe source. Candidate package and worker code runs with the host user's rights. */
import { GLIBC_LIBRARIES } from '../updates/logic/runtime_contract.ts';
import { SYSTEM_LIBRARY_DIRECTORIES } from './native/configuration.ts';
export const NATIVE_NODE_PROBE = String.raw`
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { basename, dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { Worker } from 'node:worker_threads';
import { closeSync, existsSync, openSync, readFileSync, realpathSync, statSync, unlinkSync } from 'node:fs';
import { createCipheriv, createDecipheriv, createHash } from 'node:crypto';
import { createSecureContext } from 'node:tls';
import { brotliCompressSync, brotliDecompressSync, deflateSync, inflateSync } from 'node:zlib';

const root = realpathSync(process.argv[1]);
const state = realpathSync(process.cwd());
const version = /^22\.(\d+)\.(\d+)$/.exec(process.versions.node);
assert(version && Number(version[1]) >= 18, 'runtime requires Node 22 >=22.18.0');
assert.equal(process.platform, 'linux');
assert.equal(process.arch, 'x64');
const { path_is_within } = await import(${JSON.stringify(new URL('../../core/path_containment.ts', import.meta.url).href)});
const system_directories = new Set(${JSON.stringify(SYSTEM_LIBRARY_DIRECTORIES)}
  .filter((path) => existsSync(path)).map((path) => realpathSync(path)));
const glibc = new Set(${JSON.stringify(GLIBC_LIBRARIES)});
const proof = Buffer.from('proof');
createSecureContext();
assert.equal(createHash('sha256').update(proof).digest('hex').length, 64);
const cipher = createCipheriv('aes-256-gcm', Buffer.alloc(32), Buffer.alloc(12));
const ciphertext = Buffer.concat([cipher.update(proof), cipher.final()]);
const decipher = createDecipheriv('aes-256-gcm', Buffer.alloc(32), Buffer.alloc(12));
decipher.setAuthTag(cipher.getAuthTag());
assert.deepEqual(Buffer.concat([decipher.update(ciphertext), decipher.final()]), proof);
assert.deepEqual(inflateSync(deflateSync(proof)), proof);
assert.deepEqual(brotliDecompressSync(brotliCompressSync(proof)), proof);
const date = new Intl.DateTimeFormat('en-US', {
  year: 'numeric', month: '2-digit', day: '2-digit', timeZone: 'UTC',
}).format(new Date(0));
assert.equal(date, '01/01/1970');
const host_node = realpathSync(process.execPath);
const loaded = () => [...new Set(readFileSync('/proc/self/maps', 'utf8').split('\n')
  .map((line) => line.trim().split(/\s+/))
  .filter((parts) => parts[1]?.includes('x') && parts[5]?.startsWith('/'))
  .map((parts) => realpathSync(parts.slice(5).join(' ')))
  .filter((path) => path !== host_node))].sort();
// All trusted host-only operations precede this baseline. No candidate module ran yet.
const baseline = new Set(loaded());
const worker = realpathSync(join(root, 'server/conversation/usage/usageWorker.ts'));
assert(path_is_within(root, worker) && statSync(worker).isFile(), 'usage worker must be a candidate regular file');
const require = createRequire(join(root, 'server/package.json'));
const package_path = realpathSync(join(root, 'server/package.json'));
assert(path_is_within(root, package_path), 'server package metadata resolves outside candidate');
const dependencies = JSON.parse(readFileSync(package_path, 'utf8')).dependencies;
assert(dependencies && typeof dependencies === 'object');
const package_paths = {}, modules = new Map();
for (const name of Object.keys(dependencies).sort()) {
  const url = import.meta.resolve(name, pathToFileURL(package_path).href);
  const path = realpathSync(fileURLToPath(url));
  assert(path_is_within(root, path), 'package resolves outside candidate: ' + name);
  package_paths[name] = path;
  modules.set(name, await import(pathToFileURL(path).href));
}
for (const name of ['better-sqlite3', 'koffi', '@anthropic-ai/claude-agent-sdk'])
  assert(Object.hasOwn(package_paths, name), 'server/package.json lacks ' + name);
const Database = modules.get('better-sqlite3').default;
const database = new Database(':memory:');
try {
  database.exec('CREATE TABLE proof(value INTEGER NOT NULL)');
  database.prepare('INSERT INTO proof VALUES (?)').run(42);
  assert.equal(database.prepare('SELECT value FROM proof').get().value, 42);
} finally { database.close(); }
const koffi = modules.get('koffi').default;
const libc = koffi.load('libc.so.6');
assert.equal(libc.func('int getpid()')(), process.pid);
const flock = libc.func('int flock(int fd, int operation)');
const lock_path = join(state, 'native-probe.lock');
const descriptor = openSync(lock_path, 'wx', 0o600);
try {
  assert.equal(flock(descriptor, 2 | 4), 0);
  assert.equal(flock(descriptor, 8), 0);
} finally { closeSync(descriptor); unlinkSync(lock_path); }
for (const path of Object.keys(require.cache))
  assert(path_is_within(root, realpathSync(path)), 'module loads outside candidate: ' + path);
const usage = new Worker(pathToFileURL(worker), {
  execArgv: [], workerData: { stateDir: state, injectedRates: true, injectedClock: true },
});
try {
  await new Promise((resolve, reject) => {
    usage.once('error', reject);
    usage.once('exit', (code) => reject(new Error('usage worker exited before admission: ' + code)));
    usage.on('message', (message) => {
      if (message.kind === 'callback') usage.postMessage({ kind: 'callback-result', id: message.id,
        value: message.callback === 'clock' ? 0 : { proof: { input_cost_per_token: 1, output_cost_per_token: 2 } } });
      else if (message.kind === 'error') reject(new Error(message.error.message));
      else if (message.kind === 'result') {
        try { assert.equal(message.value.knownModels, 1); resolve(); } catch (error) { reject(error); }
      }
    });
    usage.postMessage({ kind: 'refresh', id: 1 });
  });
const loaded_libraries = loaded();
for (const path of loaded_libraries) {
  // Native addons are candidate payloads, never glibc or a permitted host baseline addon.
  if (path.endsWith('.node')) assert(path_is_within(root, path), 'addon loaded outside candidate: ' + path);
  else assert(baseline.has(path) || path_is_within(root, path) || (system_directories.has(dirname(path)) && glibc.has(basename(path))),
    'new library loaded outside candidate: ' + path);
}
assert(loaded_libraries.some((path) => path.endsWith('.node') && path_is_within(root, path)),
  'native addon loads are absent from the library map');
console.log(JSON.stringify({
  node_version: process.versions.node, native: 'passed', loaded_libraries, package_paths,
}));
} finally { await usage.terminate(); }
`;
