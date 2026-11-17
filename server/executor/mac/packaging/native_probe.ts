/** Executed only from the relocated release archive by its bundled Node. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import Database from 'better-sqlite3';
import { MacService } from '../host/service.ts';
import { FilesService } from '../files.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../../../lifecycle/configuration.ts';
assert.equal(process.platform, 'darwin');
assert.equal(process.arch, 'arm64');
assert.equal(typeof MacService.open, 'function');
const database = new Database(':memory:');
try {
  assert.deepEqual(database.prepare('SELECT 26 AS protocol').get(), { protocol: 26 });
} finally {
  database.close();
}
const files = new FilesService();
try {
  const source = join(process.argv[2]!, 'native-source'),
    target = join(process.argv[2]!, 'native-copy');
  const bytes = Buffer.from('packaged Darwin file transfer\n');
  await writeFile(source, bytes);
  await files.copy_verified(
    source,
    target,
    createHash('sha256').update(bytes).digest('hex'),
    bytes.length,
  );
  assert.deepEqual(await readFile(target), bytes);
} finally {
  try {
    await files.stop({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  } finally {
    await files.settled();
  }
}
process.stdout.write(
  JSON.stringify({
    node_version: process.versions.node,
    native_files: 'passed',
    sqlite: 'passed',
    host_import: 'passed',
  }) + '\n',
);
