/** One file digest, relative manifest, and exact verifier for backup and server state. */
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { lstat, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { is_record } from '../../core/records.ts';
import type { ChecksumManifest, ManifestOptions } from './contracts.ts';

export async function sha256_file(path: string): Promise<string> {
  const digest = createHash('sha256');
  for await (const chunk of createReadStream(path, { highWaterMark: 1024 * 1024 }))
    digest.update(chunk);
  return digest.digest('hex');
}

export async function relative_manifest(
  root: string,
  options: ManifestOptions = {},
): Promise<ChecksumManifest> {
  const manifest: ChecksumManifest = Object.create(null);
  async function walk(directory: string, prefix = ''): Promise<void> {
    for (const name of (await readdir(directory)).sort()) {
      const relative = prefix + name;
      if (options.exclude?.(relative)) continue;
      const path = join(directory, name);
      const info = await lstat(path);
      if (info.isDirectory()) await walk(path, relative + '/');
      else if (info.isFile()) manifest[relative] = await sha256_file(path);
      else if (options.strict) throw new Error(`Server state is not a regular file: ${path}`);
    }
  }
  const root_info = await lstat(root);
  if (!root_info.isDirectory()) throw new Error(`Manifest root is not a directory: ${root}`);
  await walk(root);
  return manifest;
}

/** Compare the complete set, so additions and omissions fail alongside corrupt bytes. */
export function manifests_equal(actual: ChecksumManifest, recorded: unknown): boolean {
  return (
    is_record(recorded) &&
    JSON.stringify(Object.keys(actual).sort()) === JSON.stringify(Object.keys(recorded).sort()) &&
    Object.entries(actual).every(([path, digest]) => recorded[path] === digest)
  );
}
