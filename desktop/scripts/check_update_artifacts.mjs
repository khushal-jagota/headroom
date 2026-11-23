// The update manifest must describe the exact ZIP that passed the signature checks.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { readFile, stat } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { basename, join, resolve } from 'node:path';
const { load } = createRequire(import.meta.url)('js-yaml');
const [directoryArgument, version] = process.argv.slice(2);
assert(directoryArgument && version, 'Usage: check_update_artifacts.mjs DIRECTORY VERSION');
const directory = resolve(directoryArgument);
const manifest = load(await readFile(join(directory, 'latest-mac.yml'), 'utf8'));
assert.equal(manifest.version, version);
assert(Number.isInteger(manifest.panelsBridgeVersion) && manifest.panelsBridgeVersion >= 1, 'Missing native bridge capability');
assert(Array.isArray(manifest.files) && manifest.files.length > 0, 'Missing update files');
for (const file of manifest.files) {
  assert.equal(file.url, basename(file.url), 'Update file must belong to this release directory');
  const path = join(directory, file.url);
  const hash = createHash('sha512');
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  assert.equal(file.sha512, hash.digest('base64'), `Checksum mismatch: ${file.url}`);
  assert.equal(file.size, (await stat(path)).size, `Size mismatch: ${file.url}`);
}
assert(manifest.files.some(file => file.url.endsWith('.zip')), 'Missing macOS update ZIP');
console.log(`Verified updater manifest and ZIP for ${version}`);
