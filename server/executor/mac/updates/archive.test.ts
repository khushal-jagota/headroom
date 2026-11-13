/** Ordinary Node package installation and async cleanup use the existing file-worker host. */
import { createHash } from 'node:crypto';
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
import { gzipSync } from 'node:zlib';
import { Header } from 'tar';
import { afterEach, expect, test } from 'vitest';
import { FilesService } from '../files.ts';
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
  const members = unsafe
    ? [{ path: '../escape', data: 'outside' }]
    : [
        { path: 'mac-runtime.json', data: JSON.stringify(manifest) },
        { path: 'node/bin/node', data: 'synthetic executable', mode: 0o755 },
        { path: 'server/executor/mac/entrypoint.ts', data: 'synthetic entrypoint' },
        { path: 'bin/panels', data: '', link: '../server/executor/mac/entrypoint.ts' },
      ];
  const blocks: Buffer[] = [];
  for (const member of members) {
    const data = Buffer.from(member.data),
      link = 'link' in member ? member.link : undefined;
    const header = new Header({
      path: member.path,
      type: link ? 'SymbolicLink' : 'File',
      size: data.length,
      mode: 'mode' in member ? member.mode : 0o644,
      mtime: new Date(0),
      linkpath: link,
    });
    const encoded = Buffer.alloc(512);
    header.encode(encoded);
    blocks.push(encoded, data, Buffer.alloc((512 - (data.length % 512)) % 512));
  }
  const bytes = gzipSync(Buffer.concat([...blocks, Buffer.alloc(1024)]));
  const archive = join(root, 'archive.tar.gz'),
    releases = join(root, 'updates', 'releases');
  writeFileSync(archive, bytes);
  const sha = createHash('sha256').update(bytes).digest('hex');
  const files = new FilesService();
  services.push(files);
  return {
    root,
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
