/**
 * The sealed server package for a target platform at the running revision. The cache in
 * `<data>/server-move/packages/<platform>-<revision>/` comes first; a release source fills it.
 */
import { createReadStream } from 'node:fs';
import { mkdir, mkdtemp, readFile, rename, rm, stat, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pipeline } from 'node:stream/promises';
import { Writable } from 'node:stream';
import { Parser } from 'tar';
import type { ReadEntry } from 'tar';
import { sha256_file } from '../../environments/server_state/checksums.ts';
import { manifest_identity } from '../../environments/release/logic/manifest.ts';
import { parse_optional_runtime_contract } from '../../environments/updates/logic/runtime_contract.ts';
import {
  APP_ARCHIVE_NAME,
  APP_FORMAT,
  ARCHIVE_MAX_COMPRESSED_BYTES,
} from '../../environments/release/configuration.ts';
import type { AppManifest } from '../../environments/updates/contracts.ts';
import type { ServerMovePackage, ServerMovePlatform } from '../contracts.ts';
import { SERVER_MOVE_DIRECTORY } from '../contracts.ts';
import { SERVER_MOVE_MAX_MANIFEST_BYTES } from '../configuration.ts';

export const SERVER_PACKAGE_RECEIPT = 'receipt.json';

/** `receipt.json` next to the cached archive. */
export interface ServerPackageReceipt {
  readonly manifest: AppManifest;
  readonly archive_sha256: string;
  readonly archive_size: number;
}

/** A trusted publication host for server packages. It writes the archive and names its receipt. */
export interface ServerPackageSource {
  download(
    platform: ServerMovePlatform,
    revision: string,
    archive: string,
    signal: AbortSignal,
  ): Promise<{ readonly manifest: AppManifest; readonly archive_sha256: string }>;
}

const TARGETS: Readonly<Record<ServerMovePlatform, { system: string; architecture: string }>> = {
  linux: { system: 'linux', architecture: 'x86_64' },
  darwin: { system: 'darwin', architecture: 'arm64' },
};

/**
 * The manifest names this revision and a home runtime for this platform. The archive's own
 * `manifest.json` carries its format; the publication's manifest is the parsed form without it.
 */
function require_manifest(
  value: unknown,
  platform: ServerMovePlatform,
  revision: string,
  published = false,
) {
  if (value === null || typeof value !== 'object')
    throw new Error('The server package manifest is invalid.');
  const identity = manifest_identity(
    published ? { ...value, format: APP_FORMAT } : value,
    revision,
  );
  const contract = parse_optional_runtime_contract(
    (value as Record<string, unknown>).runtime_contract,
  );
  const target = TARGETS[platform];
  if (
    contract === null ||
    contract.system !== target.system ||
    contract.architecture !== target.architecture ||
    (contract.system === 'darwin' && contract.role !== 'home')
  )
    throw new Error(`The server package is not a ${platform} server.`);
  return identity;
}

async function archive_manifest(archive: string): Promise<unknown> {
  let found: Buffer | null = null;
  let too_large = false;
  const parser = new Parser({
    onReadEntry: (entry: ReadEntry) => {
      const path = entry.path.replace(/^(\.\/)+/, '');
      if (path !== 'manifest.json' || found !== null) return void entry.resume();
      const chunks: Buffer[] = [];
      let size = 0;
      entry.on('data', (chunk: Buffer) => {
        size += chunk.length;
        if (size > SERVER_MOVE_MAX_MANIFEST_BYTES) too_large = true;
        else chunks.push(chunk);
      });
      entry.on('end', () => {
        if (!too_large) found = Buffer.concat(chunks);
      });
    },
  });
  await pipeline(createReadStream(archive), parser as unknown as Writable);
  if (too_large || found === null) throw new Error('The server package has no manifest.');
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(found));
}

/** The checks for a cached or a downloaded package: digest, size, and both manifests. */
export async function validate_server_package(
  archive: string,
  receipt: ServerPackageReceipt,
  platform: ServerMovePlatform,
  revision: string,
): Promise<void> {
  const published = require_manifest(receipt.manifest, platform, revision, true);
  if (!/^[0-9a-f]{64}$/.test(receipt.archive_sha256))
    throw new Error('The server package receipt has no valid digest.');
  const info = await stat(archive);
  if (
    !info.isFile() ||
    info.size !== receipt.archive_size ||
    info.size <= 0 ||
    info.size > ARCHIVE_MAX_COMPRESSED_BYTES
  )
    throw new Error('The server package size differs from its receipt.');
  if ((await sha256_file(archive)) !== receipt.archive_sha256)
    throw new Error('The server package digest differs from its receipt.');
  const inside = require_manifest(await archive_manifest(archive), platform, revision);
  if (
    inside.source_digest !== published.source_digest ||
    inside.artifact_digest !== published.artifact_digest
  )
    throw new Error('The server package differs from its publication.');
}

export class ServerPackageCache {
  readonly root: string;
  private readonly source: ServerPackageSource | null;
  constructor(data_directory: string, source: ServerPackageSource | null) {
    this.root = join(data_directory, SERVER_MOVE_DIRECTORY, 'packages');
    this.source = source;
  }
  directory(platform: ServerMovePlatform, revision: string): string {
    if (!/^[0-9a-f]{40}$/.test(revision)) throw new Error('The revision is not a commit SHA.');
    return join(this.root, `${platform}-${revision}`);
  }
  archive_path(platform: ServerMovePlatform, revision: string): string {
    return join(this.directory(platform, revision), APP_ARCHIVE_NAME);
  }
  private async cached(
    platform: ServerMovePlatform,
    revision: string,
  ): Promise<ServerPackageReceipt | null> {
    let text: string;
    try {
      text = await readFile(
        join(this.directory(platform, revision), SERVER_PACKAGE_RECEIPT),
        'utf8',
      );
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
      throw error;
    }
    return JSON.parse(text) as ServerPackageReceipt;
  }
  async get(
    platform: ServerMovePlatform,
    revision: string,
    signal: AbortSignal,
  ): Promise<ServerMovePackage> {
    const directory = this.directory(platform, revision);
    let receipt = await this.cached(platform, revision);
    if (receipt === null) {
      if (this.source === null)
        throw new Error(`No ${platform} server package is cached and no release source is set.`);
      await mkdir(this.root, { recursive: true, mode: 0o700 });
      const work = await mkdtemp(join(this.root, '.download-'));
      try {
        const archive = join(work, APP_ARCHIVE_NAME);
        const published = await this.source.download(platform, revision, archive, signal);
        signal.throwIfAborted();
        const downloaded: ServerPackageReceipt = {
          manifest: published.manifest,
          archive_sha256: published.archive_sha256,
          archive_size: (await stat(archive)).size,
        };
        await validate_server_package(archive, downloaded, platform, revision);
        await writeFile(join(work, SERVER_PACKAGE_RECEIPT), JSON.stringify(downloaded) + '\n', {
          flag: 'wx',
          mode: 0o600,
        });
        signal.throwIfAborted();
        await rename(work, directory);
        receipt = downloaded;
      } finally {
        await rm(work, { recursive: true, force: true });
      }
    } else
      await validate_server_package(join(directory, APP_ARCHIVE_NAME), receipt, platform, revision);
    return {
      platform,
      revision,
      archive_sha256: receipt.archive_sha256,
      archive_size: receipt.archive_size,
    };
  }
}
