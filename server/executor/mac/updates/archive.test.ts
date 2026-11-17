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
import { gunzipSync, gzipSync } from 'node:zlib';
import { createHash } from 'node:crypto';
import { inspect_mac_archive } from '../../../environments/release/archive.ts';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { afterEach, expect, test } from 'vitest';
import { FilesService } from '../files.ts';
import { runtime_archive } from './testing/runtime_archive.ts';
import { LINUX_EXECUTOR } from '../../../environments/release/contracts.ts';
import type { RuntimeTarget } from '../../../environments/release/contracts.ts';
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
function fixture(unsafe = false, target?: RuntimeTarget) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'mac-runtime-')));
  roots.push(root);
  const { bytes, sha } = runtime_archive(revision, unsafe, target);
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
    install: () => files.extract_runtime(archive, releases, sha, bytes.length, revision, target),
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

test('admits Linux executor archives and retained revisions without a private interpreter', async () => {
  const f = fixture(false, LINUX_EXECUTOR);
  expect(await f.install()).toBe(f.destination);
  expect(existsSync(join(f.destination, 'node'))).toBe(false);
  expect(existsSync(join(f.destination, '.venv'))).toBe(false);
  expect(await f.files.read_runtime_revision(f.destination, revision, LINUX_EXECUTOR)).toBe(
    revision,
  );
  expect(await f.install()).toBe(f.destination);
  const marker = join(f.destination, 'mac-runtime.json');
  const original = readFileSync(marker, 'utf8');
  writeFileSync(marker, JSON.stringify({ ...JSON.parse(original), role: 'home' }));
  await expect(
    f.files.read_runtime_revision(f.destination, revision, LINUX_EXECUTOR),
  ).rejects.toThrow('does not match linux/x86_64/executor');
  await expect(f.install()).rejects.toThrow('does not match linux/x86_64/executor');
  writeFileSync(marker, original);
  const path = join(f.destination, 'manifest.json');
  const document = JSON.parse(readFileSync(path, 'utf8'));
  writeFileSync(path, JSON.stringify({ ...document, app_sha: 'b'.repeat(40) }));
  await expect(
    f.files.read_runtime_revision(f.destination, revision, LINUX_EXECUTOR),
  ).rejects.toThrow('app manifest SHA does not match expected SHA');
  writeFileSync(path, JSON.stringify(document));
  const payload = join(f.destination, 'runtime/contract.json');
  writeFileSync(payload, '{}');
  await expect(
    f.files.read_runtime_revision(f.destination, revision, LINUX_EXECUTOR),
  ).rejects.toThrow('Runtime manifest contract differs from payload.');
  writeFileSync(payload, JSON.stringify(document.runtime_contract));
  writeFileSync(join(f.destination, 'runtime/inventory.json'), 'changed');
  await expect(
    f.files.read_runtime_revision(f.destination, revision, LINUX_EXECUTOR),
  ).rejects.toThrow('Runtime inventory differs from contract.');
});

test('inspects a native Linux executor without a private Node and rejects an unbound inventory', async () => {
  const f = fixture(false, LINUX_EXECUTOR);
  const owner = new ChildProcesses();
  const controller = new AbortController();
  const inspect = () => {
    const bytes = readFileSync(f.archive);
    return inspect_mac_archive(
      f.archive,
      createHash('sha256').update(bytes).digest('hex'),
      bytes.length,
      {
        child_processes: owner,
        signal: controller.signal,
        deadline: performance.now() / 1000 + 10,
        platform: 'linux',
      },
    );
  };
  try {
    const result = await inspect();
    expect(result.node).toBe(false);
    expect(result.entrypoint).toBe(true);
    expect(result.native_linux).toMatchObject({ version: 2, app_sha: revision, panels: true });
    const raw = gunzipSync(readFileSync(f.archive));
    const inventory = raw.indexOf(Buffer.from('runtime/inventory.json'));
    expect(inventory).toBeGreaterThanOrEqual(0);
    raw[inventory + 512] = '!'.charCodeAt(0);
    writeFileSync(f.archive, gzipSync(raw));
    await expect(inspect()).rejects.toThrow('Runtime inventory differs from contract.');
  } finally {
    controller.abort();
    owner.seal();
    await owner.settled();
  }
});
