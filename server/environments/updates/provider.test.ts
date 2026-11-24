/** Durable publications need no retained Actions metadata; old pinned downloads still use Actions. */
import { afterEach, expect, test } from 'vitest';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { GitHubUpdateProvider } from './provider.ts';
import { TRUSTED_RELEASE_WORKFLOW } from '../release/source.ts';
import { parse_release } from './codec.ts';
import { validate_release } from './service.ts';
const roots: string[] = [];
afterEach(async () => {
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});
const sha = 'a'.repeat(40),
  digest = 'b'.repeat(64);
function publication() {
  return {
    artifact_source: 'github-release',
    publication_run_id: 42,
    artifact_id: 21,
    archive_sha256: digest,
    schema_floor: 'base',
    expires_at: null,
    source_run: {
      repository: 'owner/repo',
      source_sha: sha,
      event: 'push',
      ref: 'refs/heads/main',
      workflow_path: TRUSTED_RELEASE_WORKFLOW,
      workflow_ref: `owner/repo/${TRUSTED_RELEASE_WORKFLOW}@refs/heads/main`,
      run_id: '42',
      run_attempt: '1',
    },
    manifest: {
      app_sha: sha,
      source_digest: digest,
      artifact_digest: digest,
      runtime_contract: {
        version: 2,
        system: 'linux',
        architecture: 'x86_64',
        node_major: 22,
        minimum_node_version: '22.18.0',
        inventory_sha256: digest,
        platforms: [],
        cpu_features: [],
        external_libraries: [],
        external_executables: [],
      },
    },
    mac: {
      artifact_source: 'github-release',
      artifact_id: 22,
      archive_sha256: digest,
      archive_size: 3,
    },
    parts: {
      server: { app_sha: sha, artifact_digest: digest },
      screens: { sha256: digest },
      machines: { darwin: { archive_sha256: digest, build_id: null } },
    },
    compatibility: {
      ios: { minimum_build: 42 },
      desktop_shell: { minimum_bridge_version: 2 },
      machine: { minimum_protocol_version: 7, maximum_protocol_version: 8 },
      outbox_format: 5,
    },
  };
}
function release_entry() {
  return {
    draft: false,
    prerelease: false,
    tag_name: `server-${sha}-42-1`,
    target_commitish: sha,
    published_at: '2026-11-24T01:00:00Z',
    assets: [
      { id: 20, name: 'publication.json' },
      { id: 21, name: 'app.zip' },
      { id: 22, name: 'mac.zip' },
    ],
  };
}
async function setup(
  payload = publication(),
  pages = [[release_entry()]],
  receipts = new Map([[20, payload]]),
) {
  const root = await mkdtemp(join(tmpdir(), 'panels-release-provider-'));
  roots.push(root);
  const token = join(root, 'token');
  await writeFile(token, 'secret');
  const calls: string[] = [];
  const provider = new GitHubUpdateProvider('owner/repo', token, {
    fetch: async (url, options) => {
      const path = new URL(String(url)).pathname;
      calls.push(path);
      expect(options?.headers).toHaveProperty('Authorization', 'Bearer secret');
      if (path.endsWith('/releases'))
        return Response.json(
          pages[Number(new URL(String(url)).searchParams.get('page')) - 1] ?? [],
        );
      const proof = /\/releases\/assets\/(\d+)$/.exec(path);
      if (proof && receipts.has(Number(proof[1]))) {
        expect(options?.headers).toHaveProperty('Accept', 'application/octet-stream');
        return Response.json(receipts.get(Number(proof[1])));
      }
      if (path.endsWith('/actions/artifacts/7')) return Response.json({ expired: false });
      if (path.endsWith('/releases/assets/21') || path.endsWith('/actions/artifacts/7/zip'))
        return new Response('zip');
      throw new Error(`Unexpected request: ${path}`);
    },
  });
  return { provider, root, calls };
}
test('durable publication persists source, parts, and offered compatibility without Actions evidence', async () => {
  const { provider, root, calls } = await setup();
  const release = await provider.available();
  expect(release).not.toBeNull();
  expect(parse_release(JSON.parse(JSON.stringify(release)))).toEqual(release);
  expect(release?.mac?.artifact_source).toBe('github-release');
  expect(release?.parts?.machines.darwin?.build_id).toBeNull();
  expect(release?.compatibility).toEqual(publication().compatibility);
  validate_release(release!);
  await provider.download_artifact(
    release!.artifact_id,
    join(root, 'asset.zip'),
    undefined,
    release!.artifact_source,
  );
  expect(await readFile(join(root, 'asset.zip'), 'utf8')).toBe('zip');
  expect(calls.every((path) => !path.includes('/actions/'))).toBe(true);
});
test('selects the newest eligible publication across pages when GitHub lists the older release first', async () => {
  const older = release_entry();
  const newer_payload = publication();
  const newer_sha = 'c'.repeat(40);
  newer_payload.publication_run_id = 43;
  newer_payload.source_run.source_sha = newer_sha;
  newer_payload.source_run.run_id = '43';
  newer_payload.manifest.app_sha = newer_sha;
  newer_payload.parts.server.app_sha = newer_sha;
  const newer = {
    ...older,
    tag_name: `server-${newer_sha}-43-1`,
    target_commitish: newer_sha,
    published_at: '2026-11-24T03:00:00Z',
    assets: older.assets.map((asset) =>
      asset.name === 'publication.json' ? { ...asset, id: 30 } : asset,
    ),
  };
  const ignored = { ...older, tag_name: 'desktop-latest' };
  const { provider, calls } = await setup(
    publication(),
    [
      [older, ...Array.from({ length: 99 }, () => ignored)],
      [
        newer,
        {
          ...newer,
          draft: true,
          published_at: '2026-11-25T01:00:00Z',
          assets: [{ id: 40, name: 'publication.json' }],
        },
        {
          ...newer,
          prerelease: true,
          published_at: '2026-11-26T01:00:00Z',
          assets: [{ id: 50, name: 'publication.json' }],
        },
      ],
    ],
    new Map([
      [20, publication()],
      [30, newer_payload],
    ]),
  );
  expect(await provider.available()).toMatchObject({
    manifest: { app_sha: newer_sha },
    proof_artifact_id: 30,
    publication_run_id: 43,
  });
  expect(calls.filter((path) => path.endsWith('/releases'))).toHaveLength(2);
});
test('refuses an eligible publication whose date cannot establish its order', async () => {
  const { provider } = await setup(publication(), [
    [{ ...release_entry(), published_at: 'invalid' }],
  ]);
  await expect(provider.available()).rejects.toThrow('Invalid server publication date');
});
test('refuses a release whose archive ID does not name its declared asset', async () => {
  const payload = publication();
  payload.artifact_id = 22;
  await expect((await setup(payload)).provider.available()).rejects.toThrow('asset identity');
});
test('refuses untrusted release provenance', async () => {
  const payload = publication();
  payload.source_run.ref = 'refs/heads/staging';
  await expect((await setup(payload)).provider.available()).rejects.toThrow(
    'trusted repository evidence',
  );
});
test('retains Actions download interpretation for old pinned attempts', async () => {
  const { provider, root, calls } = await setup();
  await provider.download_artifact(7, join(root, 'legacy.zip'));
  expect(calls).toEqual([
    '/repos/owner/repo/actions/artifacts/7',
    '/repos/owner/repo/actions/artifacts/7/zip',
  ]);
  const { compatibility: _compatibility, ...older } = publication();
  expect(parse_release({ ...older, proof_artifact_id: 20 }).compatibility).toBeUndefined();
  expect(() =>
    parse_release({ ...publication(), artifact_source: undefined, proof_artifact_id: 20 }),
  ).toThrow('no expiry');
});
