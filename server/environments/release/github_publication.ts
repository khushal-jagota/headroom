/** Publish the checked bytes as permanent assets, without replacing the desktop's latest release. */
import { openAsBlob } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { is_record } from '../../core/records.ts';
import { sha256_file } from '../backup/evidence.ts';
import type { PublicationSourceRun } from './publication_contracts.ts';
import type { GitHubPublicationOptions } from './github_publication_contracts.ts';
import { require_publication_run } from './publication_identity.ts';

function record(value: unknown): Record<string, unknown> {
  if (!is_record(value)) throw new Error('Invalid permanent publication evidence');
  return value;
}
function id(value: unknown): number {
  if (!Number.isSafeInteger(value) || Number(value) < 1)
    throw new Error('Invalid permanent publication identity');
  return Number(value);
}

export async function publish_github_release(options: GitHubPublicationOptions): Promise<string> {
  const { publication_directory: root, repository, source_sha, signal } = options;
  if (!/^[\w.-]+\/[\w.-]+$/.test(repository)) throw new Error('Invalid release repository');
  const json = async (path: string) => record(JSON.parse(await readFile(path, 'utf8')));
  const receipt = await json(join(root, 'receipt/publication.json'));
  const audit = await json(join(root, 'receipt/publication-audit.json'));
  const publisher = record(audit.publishing_run) as unknown as PublicationSourceRun;
  require_publication_run(publisher);
  const source = record(audit.source_run) as unknown as PublicationSourceRun;
  require_publication_run(source);
  if (
    source.repository !== repository ||
    publisher.repository !== repository ||
    publisher.source_sha !== source_sha ||
    publisher.run_id !== source.run_id ||
    publisher.run_attempt !== source.run_attempt ||
    receipt.publication_run_id !== Number(source.run_id) ||
    record(receipt.manifest).app_sha !== source.source_sha ||
    !is_record(receipt.parts)
  )
    throw new Error('Permanent publication differs from checked source');
  const fetcher = options.fetch ?? fetch;
  const api = `https://api.github.com/repos/${repository}`;
  const request = async (url: string, method: string, body: BodyInit, content_type: string) => {
    signal.throwIfAborted();
    const response = await fetcher(url, {
      method,
      body,
      signal,
      headers: {
        Authorization: `Bearer ${options.token}`,
        Accept: 'application/vnd.github+json',
        'Content-Type': content_type,
      },
    });
    if (!response.ok) {
      await response.body?.cancel();
      throw new Error(`GitHub release publication refused (${response.status})`);
    }
    return record(await response.json());
  };
  const tag = `server-${source.source_sha}-${source.run_id}-${source.run_attempt}`;
  const draft = await request(
    `${api}/releases`,
    'POST',
    JSON.stringify({
      tag_name: tag,
      target_commitish: source.source_sha,
      name: `Panels server ${source.source_sha.slice(0, 12)}`,
      draft: true,
      prerelease: false,
      make_latest: 'false',
    }),
    'application/json',
  );
  const release_id = id(draft.id);
  const upload = async (name: string, body: Blob, sha256?: string) => {
    const result = await request(
      `https://uploads.github.com/repos/${repository}/releases/${release_id}/assets?name=${encodeURIComponent(name)}`,
      'POST',
      body,
      body.type || 'application/octet-stream',
    );
    if (
      result.name !== name ||
      result.size !== body.size ||
      (sha256 !== undefined && result.digest !== `sha256:${sha256}`)
    )
      throw new Error('GitHub release asset differs from checked bytes');
    return id(result.id);
  };
  if (!Array.isArray(audit.artifacts)) throw new Error('Publication audit has no artifacts');
  const artifacts = audit.artifacts.map(record);
  const checked_zip = async (name: string, original_id: unknown) => {
    const path = join(root, 'inputs', name);
    const original = artifacts.find((item) => item.id === original_id);
    const sha256 = await sha256_file(path);
    if (original === undefined || original.digest !== `sha256:${sha256}`)
      throw new Error('Permanent asset differs from checked Actions artifact');
    return upload(name, await openAsBlob(path, { type: 'application/zip' }), sha256);
  };
  const permanent: Record<string, unknown> = {
    ...receipt,
    source_run: source,
    publishing_run: publisher,
    artifact_source: 'github-release',
    expires_at: null,
    artifact_id: await checked_zip('app.zip', receipt.artifact_id),
  };
  for (const [key, name] of [
    ['mac', 'mac.zip'],
    ['linux_executor', 'linux-executor.zip'],
    ['mac_home', 'mac-home.zip'],
  ] as const) {
    if (receipt[key] === undefined) continue;
    const part = record(receipt[key]);
    permanent[key] = {
      ...part,
      artifact_source: 'github-release',
      artifact_id: await checked_zip(name, part.artifact_id),
    };
  }
  // The proof and all producer evidence remain available after Actions retention ends.
  for (const name of [
    'build.zip',
    'mac-home-build.zip',
    'machine-compatibility-linux.zip',
    'machine-compatibility-darwin.zip',
  ]) {
    const path = join(root, 'inputs', name);
    const sha256 = await sha256_file(path);
    if (!artifacts.some((item) => item.digest === `sha256:${sha256}`))
      throw new Error('Permanent build evidence differs from checked Actions artifact');
    await upload(name, await openAsBlob(path, { type: 'application/zip' }), sha256);
  }
  await upload(
    'publication-audit.json',
    new Blob([JSON.stringify(audit) + '\n'], { type: 'application/json' }),
  );
  await upload(
    'publication-actions.json',
    new Blob([JSON.stringify(receipt) + '\n'], { type: 'application/json' }),
  );
  await upload(
    'publication.json',
    new Blob([JSON.stringify(permanent) + '\n'], { type: 'application/json' }),
  );
  const published = await request(
    `${api}/releases/${release_id}`,
    'PATCH',
    JSON.stringify({ draft: false, make_latest: 'false' }),
    'application/json',
  );
  if (published.draft !== false || published.tag_name !== tag)
    throw new Error('GitHub release publication is uncertain');
  return `https://github.com/${repository}/releases/tag/${tag}`;
}
