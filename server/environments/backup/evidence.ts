/** Read-only proof shared by status, cleanup, and future backup publication. */
import { is_record as object } from '../../core/records.ts';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { lstat, stat, readFile, readdir } from 'node:fs/promises';
import { basename, dirname, join, resolve } from 'node:path';
import { expand_path, resolved_path } from '../paths.ts';
export { expand_path, resolved_path } from '../paths.ts';
export const RETENTION_COUNT = 3;
export const SNAPSHOT_FORMAT = 'panels-backup-v2';
export function missing(error: unknown): boolean {
  return (
    error instanceof Error && 'code' in error && ['ENOENT', 'ENOTDIR'].includes(String(error.code))
  );
}
/** Existence includes every object that stat follows, not only files and directories. */
export async function exists(path: string): Promise<boolean> {
  try {
    await stat(path);
    return true;
  } catch (error) {
    if (missing(error)) return false;
    throw error;
  }
}
export async function is_symlink(path: string): Promise<boolean> {
  try {
    return (await lstat(path)).isSymbolicLink();
  } catch (error) {
    if (missing(error)) return false;
    throw error;
  }
}
export async function is_directory(path: string): Promise<boolean> {
  try {
    return (await stat(path)).isDirectory();
  } catch (error) {
    if (missing(error)) return false;
    throw error;
  }
}
export async function is_file(path: string): Promise<boolean> {
  try {
    return (await stat(path)).isFile();
  } catch (error) {
    if (missing(error)) return false;
    throw error;
  }
}
export async function sha256_file(path: string): Promise<string> {
  const digest = createHash('sha256');
  for await (const chunk of createReadStream(path, { highWaterMark: 1024 * 1024 }))
    digest.update(chunk);
  return digest.digest('hex');
}
export async function relative_manifest(root: string): Promise<Record<string, string>> {
  const manifest: Record<string, string> = {};
  async function walk(directory: string, prefix = ''): Promise<void> {
    for (const name of await readdir(directory)) {
      const path = join(directory, name);
      if (await is_symlink(path)) continue;
      const relative = prefix + name;
      if (await is_directory(path)) await walk(path, relative + '/');
      else if (await is_file(path)) manifest[relative] = await sha256_file(path);
    }
  }
  await walk(root);
  return manifest;
}
export async function managed_files_valid(directory: string, recorded: unknown): Promise<boolean> {
  if (
    !object(recorded) ||
    !Array.isArray(recorded.roots) ||
    typeof recorded.manifest_sha256 !== 'string'
  )
    return false;
  const path = join(directory, 'manifest.json');
  if ((await is_symlink(directory)) || !(await is_directory(directory)) || (await is_symlink(path)))
    return false;
  let bytes: Buffer;
  try {
    bytes = await readFile(path);
  } catch {
    return false;
  }
  if (createHash('sha256').update(bytes).digest('hex') !== recorded.manifest_sha256) return false;
  let manifest: unknown;
  const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  try {
    manifest = JSON.parse(text);
  } catch {
    return false;
  }
  if (
    !object(manifest) ||
    recorded.roots.some((name) => typeof name !== 'string') ||
    JSON.stringify(Object.keys(manifest).sort()) !== JSON.stringify([...recorded.roots].sort())
  )
    return false;
  for (const name of recorded.roots as string[]) {
    const root = resolve(directory, name);
    if ((await is_symlink(root)) || !(await is_directory(root))) return false;
    const actual = await relative_manifest(root);
    const expected = manifest[name];
    if (
      !object(expected) ||
      JSON.stringify(Object.keys(actual).sort()) !== JSON.stringify(Object.keys(expected).sort()) ||
      Object.entries(actual).some(([path, digest]) => expected[path] !== digest)
    )
      return false;
  }
  return true;
}
async function read_metadata(path: string): Promise<unknown> {
  let bytes: Buffer;
  try {
    bytes = await readFile(path);
  } catch {
    return undefined;
  }
  // Invalid UTF-8 propagates; malformed JSON makes the metadata unavailable.
  const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}
export async function is_verified_snapshot(candidate: string): Promise<boolean> {
  const metadataPath = join(candidate, 'metadata.json');
  const databasePath = join(candidate, 'database.sqlite');
  if (
    (await is_symlink(candidate)) ||
    !(await is_directory(candidate)) ||
    (await is_symlink(metadataPath)) ||
    !(await is_file(metadataPath)) ||
    (await is_symlink(databasePath)) ||
    !(await is_file(databasePath))
  )
    return false;
  const metadata = await read_metadata(metadataPath);
  if (
    !object(metadata) ||
    metadata.format !== SNAPSHOT_FORMAT ||
    metadata.verified !== true ||
    metadata.sha256 !== (await sha256_file(databasePath))
  )
    return false;
  return (
    metadata.managed_files == null ||
    (await managed_files_valid(join(candidate, 'files'), metadata.managed_files))
  );
}
function compare_strings(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}
export async function verified_snapshots(backupDirectory: string): Promise<string[]> {
  const configured = expand_path(backupDirectory);
  if ((await is_symlink(configured)) || !(await is_directory(configured))) return [];
  const root = await resolved_path(configured);
  const snapshots: { path: string; created: string }[] = [];
  for (const name of await readdir(configured)) {
    if (!name.startsWith('snapshot-')) continue;
    const path = join(configured, name);
    if (
      (await is_symlink(path)) ||
      (await resolved_path(dirname(path))) !== root ||
      !(await is_verified_snapshot(path))
    )
      continue;
    const metadata = await read_metadata(join(path, 'metadata.json'));
    if (metadata === undefined) continue;
    snapshots.push({
      path,
      created:
        object(metadata) && typeof metadata.created_at === 'string' ? metadata.created_at : '',
    });
  }
  // Order raw timestamp text, then names. Age and retention remain separate.
  snapshots.sort(
    (a, b) =>
      -compare_strings(a.created, b.created) ||
      -compare_strings(basename(a.path), basename(b.path)),
  );
  return snapshots.map((snapshot) => snapshot.path);
}
export async function verified_snapshot_retention_plan(root: string): Promise<string[]> {
  return (await verified_snapshots(root)).slice(RETENTION_COUNT).reverse();
}
