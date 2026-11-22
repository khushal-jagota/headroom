/**
 * New boundary, no existing coverage: the package a move serves must be the running revision
 * for the target's platform. The cache comes first; a source fills it; altered bytes, another
 * revision or another platform are refused. Uses a synthetic archive in a private folder.
 */
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { create } from 'tar';
import { afterEach, expect, test } from 'vitest';
import { sha256_file } from '../../environments/server_state/checksums.ts';
import type { AppManifest } from '../../environments/updates/contracts.ts';
import { ServerPackageCache, SERVER_PACKAGE_RECEIPT } from './cache.ts';

const revision = 'd'.repeat(40);
const folders: string[] = [];
afterEach(() => {
  for (const folder of folders.splice(0)) rmSync(folder, { recursive: true, force: true });
});

const manifest = {
  app_sha: revision,
  source_digest: '1'.repeat(64),
  artifact_digest: '2'.repeat(64),
  runtime_contract: {
    version: 2,
    system: 'linux',
    architecture: 'x86_64',
    node_major: 22,
    minimum_node_version: '22.18.0',
    inventory_sha256: '3'.repeat(64),
    platforms: [],
    cpu_features: [],
    external_libraries: [],
    external_executables: [],
  },
} as unknown as AppManifest;

async function archive(root: string, name: string, app_sha = revision): Promise<string> {
  const app = join(root, `${name}-app`);
  mkdirSync(app);
  writeFileSync(
    join(app, 'manifest.json'),
    JSON.stringify({ format: 'panels-app-v1', ...manifest, app_sha }),
  );
  const path = join(root, `${name}.tar.gz`);
  await create({ gzip: true, cwd: app, file: path }, ['manifest.json']);
  return path;
}

test('the cache serves a checked package, a source fills it, and other bytes or revisions are refused', async () => {
  const root = mkdtempSync(join(tmpdir(), 'panels-move-packages-'));
  folders.push(root);
  const good = await archive(root, 'good');
  const downloads: string[] = [];
  const source = {
    download: async (_platform: string, _revision: string, destination: string) => {
      downloads.push(destination);
      copyFileSync(good, destination);
      return { manifest, archive_sha256: await sha256_file(good) };
    },
  };
  const cache = new ServerPackageCache(root, source);
  const signal = new AbortController().signal;
  const fetched = await cache.get('linux', revision, signal);
  expect(fetched).toMatchObject({
    platform: 'linux',
    revision,
    archive_sha256: await sha256_file(good),
  });
  expect(await cache.get('linux', revision, signal)).toEqual(fetched);
  expect(downloads).toHaveLength(1);
  await expect(cache.get('darwin', revision, signal)).rejects.toThrow(/not a darwin server/);

  const receipt = join(cache.directory('linux', revision), SERVER_PACKAGE_RECEIPT);
  copyFileSync(await archive(root, 'other', 'e'.repeat(40)), cache.archive_path('linux', revision));
  await expect(cache.get('linux', revision, signal)).rejects.toThrow(/differs from its receipt/);
  const other = await archive(root, 'other-receipt', 'e'.repeat(40));
  copyFileSync(other, cache.archive_path('linux', revision));
  const { size } = statSync(other);
  writeFileSync(
    receipt,
    JSON.stringify({ manifest, archive_sha256: await sha256_file(other), archive_size: size }),
  );
  await expect(cache.get('linux', revision, signal)).rejects.toThrow(/expected SHA|does not match/);
  await expect(
    new ServerPackageCache(join(root, 'empty'), null).get('linux', revision, signal),
  ).rejects.toThrow(/No linux server package is cached/);
});
