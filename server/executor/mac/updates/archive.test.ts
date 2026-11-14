/** Ordinary Node package installation and async cleanup use the existing file-worker host. */
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  realpathSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test } from 'vitest';
import { FilesService } from '../files.ts';
import { runtime_archive } from './testing/runtime_archive.ts';
import { ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';

const roots: string[] = [],
  services: FilesService[] = [];
const revision = 'a'.repeat(40);
const manifest = {
  schema_version: 1,
  application_revision: revision,
  platform: 'darwin',
  architecture: 'arm64',
};
function fixture(unsafe = false) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'mac-runtime-')));
  roots.push(root);
  const { bytes, sha } = runtime_archive(revision, unsafe);
  const archive = join(root, 'archive.tar.gz'),
    releases = join(root, 'updates', 'releases');
  writeFileSync(archive, bytes);
  const files = new FilesService();
  services.push(files);
  return {
    root,
    archive,
    releases,
    destination: join(releases, revision + '-' + sha),
    files,
    install: () => files.extract_runtime(archive, releases, sha, bytes.length, revision),
  };
}
afterEach(async () => {
  for (const files of services.splice(0)) {
    await files.stop({ deadline: performance.now() / 1000 + 10 });
    await files.settled();
  }
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});
test('installs the ordinary Node runtime payload and its contained relative link', async () => {
  const f = fixture();
  expect(await f.install()).toBe(f.destination);
  expect(JSON.parse(readFileSync(join(f.destination, 'mac-runtime.json'), 'utf8'))).toEqual(
    manifest,
  );
  expect(readFileSync(join(f.destination, 'node/bin/node'), 'utf8')).toBe('synthetic executable');
  expect(statSync(join(f.destination, 'node/bin/node')).mode & 0o777).toBe(0o755);
  expect(readlinkSync(join(f.destination, 'bin/panels'))).toBe(
    '../server/executor/mac/entrypoint.ts',
  );
  expect(readdirSync(f.releases)).toEqual([f.destination.split('/').at(-1)]);
});
test('refuses a traversal member before release publication', async () => {
  const f = fixture(true);
  await expect(f.install()).rejects.toThrow();
  expect(existsSync(join(f.root, 'escape'))).toBe(false);
  expect(existsSync(f.releases)).toBe(false);
});
test('checks the approved digest before it decodes the runtime archive', async () => {
  const f = fixture();
  writeFileSync(f.archive, Buffer.alloc(readFileSync(f.archive).length));
  await expect(f.install()).rejects.toThrow('Runtime archive digest differs from approval.');
  expect(existsSync(f.releases)).toBe(false);
});
test('ordered async work finishes before queued cleanup after an expired stop observer', async () => {
  const f = fixture();
  await f.install();
  const cached = f.install(),
    record = f.files.read_runtime_record(join(f.destination, 'mac-runtime.json'));
  await expect(f.files.stop({ deadline: 0 })).rejects.toBeInstanceOf(ShutdownDeadlineExceeded);
  expect(await cached).toBe(f.destination);
  expect(JSON.parse((await record)!)).toEqual(manifest);
  await f.files.stop({ deadline: performance.now() / 1000 + 10 });
  await f.files.settled();
});
