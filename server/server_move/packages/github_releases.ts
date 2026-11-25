/**
 * The server package from the permanent GitHub Release of one revision: `app.zip` for Linux,
 * `mac-home.zip` for Apple Silicon. The trust checks are those of the update provider's
 * release reader. Credentials stay outside records and errors.
 */
import { createWriteStream } from 'node:fs';
import { mkdtemp, open, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { Readable } from 'node:stream';
import type { ReadableStream } from 'node:stream/web';
import { pipeline } from 'node:stream/promises';
import { is_record } from '../../core/records.ts';
import {
  release_request_headers,
  TRUSTED_RELEASE_WORKFLOW,
} from '../../environments/release/source.ts';
import { APP_ARCHIVE_NAME } from '../../environments/release/configuration.ts';
import { with_zip_member } from '../../environments/updates/zip.ts';
import type { AppManifest } from '../../environments/updates/contracts.ts';
import type { ServerMovePlatform } from '../contracts.ts';
import {
  SERVER_MOVE_PACKAGE_DOWNLOAD_SECONDS,
  SERVER_MOVE_RELEASE_REQUEST_SECONDS,
} from '../configuration.ts';
import type { ServerPackageSource } from './cache.ts';

const REFUSED = 'The release host refused the server package request.';
function record(value: unknown): Record<string, unknown> {
  if (!is_record(value)) throw new Error(REFUSED);
  return value;
}
function list(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) throw new Error(REFUSED);
  return value.map(record);
}

export class GitHubReleaseServerPackages implements ServerPackageSource {
  private readonly repository: string;
  private readonly token_file: string | null;
  private readonly fetch: typeof fetch;
  constructor(
    repository: string,
    token_file: string | null,
    fetcher: typeof fetch = globalThis.fetch,
  ) {
    if (!/^[\w.-]+\/[\w.-]+$/.test(repository)) throw new Error('Invalid release repository');
    this.repository = repository;
    this.token_file = token_file;
    this.fetch = fetcher;
  }
  private async get(path: string, signal: AbortSignal, binary = false): Promise<Response> {
    const headers = release_request_headers(
      this.token_file,
      binary ? 'application/octet-stream' : 'application/vnd.github+json',
    );
    let response: Response;
    try {
      response = await this.fetch(`https://api.github.com/repos/${this.repository}/${path}`, {
        method: 'GET',
        redirect: 'follow',
        signal,
        headers,
      });
    } catch (error) {
      signal.throwIfAborted();
      throw new Error('The release host is unavailable.', { cause: error });
    }
    if (!response.ok) {
      await response.body?.cancel().catch(() => {});
      throw new Error(REFUSED);
    }
    return response;
  }
  private async json(path: string, signal: AbortSignal, binary = false): Promise<unknown> {
    const request = AbortSignal.any([
      signal,
      AbortSignal.timeout(SERVER_MOVE_RELEASE_REQUEST_SECONDS * 1000),
    ]);
    const response = await this.get(path, request, binary);
    try {
      return JSON.parse(await response.text());
    } catch (error) {
      throw new Error(REFUSED, { cause: error });
    }
  }
  /** The checked publication of this revision, with its server archive asset. */
  private async publication(
    platform: ServerMovePlatform,
    revision: string,
    signal: AbortSignal,
  ): Promise<{ manifest: AppManifest; archive_sha256: string; asset_id: number }> {
    for (let page = 1; ; page++) {
      const releases = list(await this.json(`releases?per_page=100&page=${page}`, signal));
      for (const release of releases) {
        if (
          release.draft !== false ||
          release.prerelease !== false ||
          typeof release.tag_name !== 'string' ||
          !release.tag_name.startsWith(`server-${revision}-`)
        )
          continue;
        const assets = list(release.assets);
        const proof = assets.find((asset) => asset.name === 'publication.json');
        if (proof === undefined) continue;
        const payload = record(await this.json(`releases/assets/${proof.id}`, signal, true));
        const source = record(payload.source_run);
        if (
          source.repository !== this.repository ||
          source.workflow_path !== TRUSTED_RELEASE_WORKFLOW ||
          source.workflow_ref !==
            `${this.repository}/${TRUSTED_RELEASE_WORKFLOW}@refs/heads/main` ||
          source.ref !== 'refs/heads/main' ||
          source.event !== 'push' ||
          source.source_sha !== revision ||
          typeof source.run_id !== 'string' ||
          !/^[1-9][0-9]*$/.test(source.run_id) ||
          typeof source.run_attempt !== 'string' ||
          !/^[1-9][0-9]*$/.test(source.run_attempt) ||
          release.tag_name !== `server-${revision}-${source.run_id}-${source.run_attempt}` ||
          release.target_commitish !== revision ||
          payload.publication_run_id !== Number(source.run_id)
        )
          throw new Error('The publication differs from trusted repository evidence.');
        const selected = platform === 'darwin' ? payload.mac_home : payload;
        if (!is_record(selected)) continue;
        const name = platform === 'darwin' ? 'mac-home.zip' : 'app.zip';
        if (
          selected.artifact_source !== 'github-release' ||
          !Number.isSafeInteger(selected.artifact_id) ||
          !assets.some((asset) => asset.id === selected.artifact_id && asset.name === name) ||
          typeof selected.archive_sha256 !== 'string' ||
          !is_record(selected.manifest)
        )
          throw new Error('The publication asset differs from its release.');
        return {
          manifest: selected.manifest as unknown as AppManifest,
          archive_sha256: selected.archive_sha256,
          asset_id: selected.artifact_id as number,
        };
      }
      if (releases.length < 100)
        throw new Error(`No ${platform} server package is published for ${revision}.`);
    }
  }
  async download(
    platform: ServerMovePlatform,
    revision: string,
    archive: string,
    signal: AbortSignal,
  ): Promise<{ manifest: AppManifest; archive_sha256: string }> {
    const deadline = AbortSignal.any([
      signal,
      AbortSignal.timeout(SERVER_MOVE_PACKAGE_DOWNLOAD_SECONDS * 1000),
    ]);
    const published = await this.publication(platform, revision, deadline);
    const work = await mkdtemp(join(dirname(archive), '.asset-'));
    try {
      const zip = join(work, 'asset.zip');
      const response = await this.get(`releases/assets/${published.asset_id}`, deadline, true);
      if (response.body === null) throw new Error(REFUSED);
      const output = await open(zip, 'wx', 0o600);
      try {
        for await (const chunk of Readable.fromWeb(response.body as ReadableStream)) {
          deadline.throwIfAborted();
          await output.write(chunk);
        }
      } finally {
        await output.close();
      }
      await with_zip_member(zip, APP_ARCHIVE_NAME, (input) =>
        pipeline(input, createWriteStream(archive, { flags: 'wx', mode: 0o600 }), {
          signal: deadline,
        }),
      );
    } finally {
      await rm(work, { recursive: true, force: true });
    }
    return { manifest: published.manifest, archive_sha256: published.archive_sha256 };
  }
}
