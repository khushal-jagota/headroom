/** Risk: server publication steals desktop latest, or exposes a manifest before all checked assets exist.
 * Existing archive proofs do not exercise the GitHub Release write boundary. These isolated adapter
 * cases reuse temporary files and mock HTTP; neither starts a browser or a connected host.
 */
import { expect, test } from 'vitest';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parse } from 'yaml';
import { publish_github_release } from './github_publication.ts';

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'github-publication-'));
  await mkdir(join(root, 'inputs'));
  await mkdir(join(root, 'receipt'));
  const sha = 'a'.repeat(40);
  const source_run = {
    repository: 'owner/repo',
    source_sha: sha,
    event: 'push',
    ref: 'refs/heads/main',
    workflow_path: '.github/workflows/publish-typescript-release.yml',
    workflow_ref: 'owner/repo/.github/workflows/publish-typescript-release.yml@refs/heads/main',
    run_id: '10',
    run_attempt: '2',
  };
  const names = [
    'app.zip',
    'mac.zip',
    'linux-executor.zip',
    'mac-home.zip',
    'build.zip',
    'mac-home-build.zip',
    'machine-compatibility-linux.zip',
    'machine-compatibility-darwin.zip',
  ];
  const artifacts = [];
  for (const [index, name] of names.entries()) {
    await writeFile(join(root, 'inputs', name), name);
    artifacts.push({
      id: index + 1,
      digest: 'sha256:' + createHash('sha256').update(name).digest('hex'),
    });
  }
  const receipt = {
    publication_run_id: 10,
    artifact_id: 1,
    manifest: { app_sha: sha },
    parts: {
      server: { app_sha: sha, artifact_digest: 'b'.repeat(64) },
      screens: { sha256: 'c'.repeat(64) },
      machines: { 'darwin-arm64': { archive_sha256: 'd'.repeat(64), build_id: null } },
    },
    mac: { artifact_id: 2 },
    linux_executor: { artifact_id: 3 },
    mac_home: { artifact_id: 4 },
    expires_at: '2000-01-01T00:00:00Z',
  };
  await writeFile(join(root, 'receipt/publication.json'), JSON.stringify(receipt));
  await writeFile(
    join(root, 'receipt/publication-audit.json'),
    JSON.stringify({ source_run, artifacts }),
  );
  return { root, sha, receipt };
}

test('permanent server publication keeps desktop latest and binds every consumer to permanent assets', async () => {
  const { root, sha, receipt } = await fixture();
  const requests: { method: string; url: string; body: Record<string, unknown> }[] = [];
  const uploaded = new Map<string, { id: number; bytes: string }>();
  const desktop = 'desktop-v1';
  let latest = desktop;
  let published = false;
  const tag = `server-${sha}-10-2`;
  try {
    const url = await publish_github_release({
      repository: 'owner/repo',
      source_sha: sha,
      publication_directory: root,
      token: 'secret',
      signal: AbortSignal.timeout(10_000),
      fetch: (async (input, init) => {
        const url = String(input),
          method = init!.method!;
        if (method === 'POST' && url.includes('/assets?')) {
          const name = new URL(url).searchParams.get('name')!;
          expect(published).toBe(false);
          const blob = init!.body as Blob;
          const bytes = await blob.text();
          const id = 100 + uploaded.size;
          uploaded.set(name, { id, bytes });
          return Response.json({
            id,
            name,
            size: blob.size,
            digest: 'sha256:' + createHash('sha256').update(bytes).digest('hex'),
          });
        }
        const body = JSON.parse(init!.body as string);
        requests.push({ method, url, body });
        if (body.make_latest !== 'false') latest = tag;
        if (method === 'POST') {
          expect(body).toMatchObject({
            draft: true,
            make_latest: 'false',
            target_commitish: sha,
            tag_name: tag,
          });
          return Response.json({ id: 55 });
        }
        expect(uploaded.has('publication.json')).toBe(true);
        published = true;
        return Response.json({ draft: false, tag_name: tag });
      }) as typeof fetch,
    });
    expect(url).toBe(`https://github.com/owner/repo/releases/tag/${tag}`);
    expect(latest).toBe(desktop);
    expect(requests.map(({ method, body }) => [method, body.make_latest])).toEqual([
      ['POST', 'false'],
      ['PATCH', 'false'],
    ]);
    const durable = JSON.parse(uploaded.get('publication.json')!.bytes);
    expect(durable).toMatchObject({
      artifact_source: 'github-release',
      expires_at: null,
      parts: receipt.parts,
      artifact_id: uploaded.get('app.zip')!.id,
      source_run: { source_sha: sha, run_id: '10', run_attempt: '2' },
    });
    for (const [part, name] of [
      ['mac', 'mac.zip'],
      ['linux_executor', 'linux-executor.zip'],
      ['mac_home', 'mac-home.zip'],
    ])
      expect(durable[part!]).toMatchObject({
        artifact_source: 'github-release',
        artifact_id: uploaded.get(name!)!.id,
      });
    expect([...uploaded.keys()]).toEqual([
      'app.zip',
      'mac.zip',
      'linux-executor.zip',
      'mac-home.zip',
      'build.zip',
      'mac-home-build.zip',
      'machine-compatibility-linux.zip',
      'machine-compatibility-darwin.zip',
      'publication-audit.json',
      'publication-actions.json',
      'publication.json',
    ]);
    const workflow = parse(
      await readFile(
        new URL('../../../.github/workflows/publish-typescript-release.yml', import.meta.url),
        'utf8',
      ),
    );
    expect(workflow.permissions.contents).toBe('read');
    expect(workflow.jobs.receipt.permissions.contents).toBe('write');
    const steps = workflow.jobs.receipt.steps;
    expect(steps.at(-1).run).toContain('github_publication_main.ts');
    expect(steps.at(-2).with.name).toBe('panels-publication-proof');
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('changed checked ZIP leaves the release as a draft and never publishes', async () => {
  const { root, sha } = await fixture();
  const methods: string[] = [];
  try {
    await writeFile(join(root, 'inputs/mac.zip'), 'different bytes');
    await expect(
      publish_github_release({
        repository: 'owner/repo',
        source_sha: sha,
        publication_directory: root,
        token: 'secret',
        signal: AbortSignal.timeout(10_000),
        fetch: (async (input, init) => {
          methods.push(init!.method!);
          if (!String(input).includes('/assets?')) return Response.json({ id: 55 });
          const blob = init!.body as Blob;
          const bytes = await blob.text();
          return Response.json({
            id: 100,
            name: 'app.zip',
            size: blob.size,
            digest: 'sha256:' + createHash('sha256').update(bytes).digest('hex'),
          });
        }) as typeof fetch,
      }),
    ).rejects.toThrow('differs from checked Actions artifact');
    expect(methods).toEqual(['POST', 'POST']);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
