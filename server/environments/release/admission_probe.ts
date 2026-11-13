/** Trusted admitting-server code, passed to a private host Node. Never a candidate entrypoint. */
export const NATIVE_NODE_PROBE = String.raw`
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { basename, isAbsolute, join, relative, sep } from 'node:path';
import { closeSync, openSync, readFileSync, realpathSync, statSync, unlinkSync } from 'node:fs';
import { createCipheriv, createDecipheriv, createHash } from 'node:crypto';
import { createSecureContext } from 'node:tls';
import { brotliCompressSync, brotliDecompressSync, deflateSync, inflateSync } from 'node:zlib';

const root = realpathSync(process.argv[1]);
const state = realpathSync(process.cwd());
const version = /^22\.(\d+)\.(\d+)$/.exec(process.versions.node);
assert(version && Number(version[1]) >= 18, 'runtime requires Node 22 >=22.18.0');
assert.equal(process.platform, 'linux');
assert.equal(process.arch, 'x64');
const inside = (path) => {
  const suffix = relative(root, path);
  return suffix !== '..' && !suffix.startsWith('..' + sep) && !isAbsolute(suffix);
};
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
const loaded = () => [...new Set(readFileSync('/proc/self/maps', 'utf8').split('\n')
  .map((line) => line.trim().split(/\s+/).slice(5).join(' '))
  .filter((file) => file.startsWith('/') && (basename(file).includes('.so') || file.endsWith('.node')))
  .map((file) => realpathSync(file)))].sort();
// All trusted host-only operations precede this baseline. No candidate module ran yet.
const baseline = new Set(loaded());
const worker = realpathSync(join(root, 'server/conversation/usage/usageWorker.ts'));
assert(inside(worker) && statSync(worker).isFile(), 'usage worker must be a candidate regular file');
const require = createRequire(join(root, 'server/package.json'));
const package_path = realpathSync(join(root, 'server/package.json'));
assert(inside(package_path), 'server package metadata resolves outside candidate');
const dependencies = JSON.parse(readFileSync(package_path, 'utf8')).dependencies;
assert(dependencies && typeof dependencies === 'object');
const package_paths = {};
for (const name of Object.keys(dependencies).sort()) {
  const path = realpathSync(require.resolve(name));
  assert(inside(path), 'package resolves outside candidate: ' + name);
  package_paths[name] = path;
}
for (const name of ['better-sqlite3', 'koffi', '@anthropic-ai/claude-agent-sdk'])
  assert(Object.hasOwn(package_paths, name), 'server/package.json lacks ' + name);
const Database = require('better-sqlite3');
const database = new Database(':memory:');
try {
  database.exec('CREATE TABLE proof(value INTEGER NOT NULL)');
  database.prepare('INSERT INTO proof VALUES (?)').run(42);
  assert.equal(database.prepare('SELECT value FROM proof').get().value, 42);
} finally { database.close(); }
const koffi = require('koffi');
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
  assert(inside(realpathSync(path)), 'module loads outside candidate: ' + path);
const loaded_libraries = loaded();
const glibc = new Set([
  'ld-linux-x86-64.so.2', 'libc.so.6', 'libdl.so.2', 'libm.so.6',
  'libpthread.so.0', 'libresolv.so.2', 'librt.so.1', 'libutil.so.1',
]);
for (const path of loaded_libraries) {
  // Native addons are candidate payloads, never glibc or a permitted host baseline addon.
  if (path.endsWith('.node')) assert(inside(path), 'addon loaded outside candidate: ' + path);
  else assert(baseline.has(path) || inside(path) || glibc.has(basename(path)),
    'new library loaded outside candidate: ' + path);
}
assert(loaded_libraries.some((path) => path.endsWith('.node') && inside(path)),
  'native addon loads are absent from the library map');
console.log(JSON.stringify({
  node_version: process.versions.node, native: 'passed', loaded_libraries, package_paths,
}));
`;
