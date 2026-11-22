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
  };
}
async function setup(payload = publication()) {
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
        return Response.json([
          {
            draft: false,
            prerelease: false,
            tag_name: `server-${sha}-42-1`,
            target_commitish: sha,
            assets: [
              { id: 20, name: 'publication.json' },
              { id: 21, name: 'app.zip' },
              { id: 22, name: 'mac.zip' },
            ],
          },
        ]);
      if (path.endsWith('/releases/assets/20')) {
        expect(options?.headers).toHaveProperty('Accept', 'application/octet-stream');
        return Response.json(payload);
      }
      if (path.endsWith('/actions/artifacts/7')) return Response.json({ expired: false });
      if (path.endsWith('/releases/assets/21') || path.endsWith('/actions/artifacts/7/zip'))
        return new Response('zip');
      throw new Error(`Unexpected request: ${path}`);
    },
  });
  return { provider, root, calls };
}
test('durable publication survives absent Actions evidence and persists source and parts', async () => {
  const { provider, root, calls } = await setup();
  const release = await provider.available();
  expect(release).not.toBeNull();
  expect(parse_release(JSON.parse(JSON.stringify(release)))).toEqual(release);
  expect(release?.mac?.artifact_source).toBe('github-release');
  expect(release?.parts?.machines.darwin?.build_id).toBeNull();
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
  expect(() =>
    parse_release({ ...publication(), artifact_source: undefined, proof_artifact_id: 20 }),
  ).toThrow('no expiry');
});
