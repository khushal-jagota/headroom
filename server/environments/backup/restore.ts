/** Stopped-destination restore, with independent database and managed-root rollback. */
import { randomUUID } from 'node:crypto';
import fs from 'node:fs/promises';
import { basename, dirname, join } from 'node:path';
import { verifyDatabaseIntegrity } from '../../core/database/index.ts';
import { assert_test_database_path } from '../../core/test_paths.ts';
import type { ManagedFilesEvidence, ManagedRootName, RestoreOptions } from './contracts.ts';
import { copy_managed_tree, managed_file_roots } from './capture.ts';
import {
  expand_path,
  resolved_path,
  is_symlink,
  is_verified_snapshot,
  managed_files_valid,
  missing,
  sha256_file,
  SNAPSHOT_FORMAT,
} from './evidence.ts';

async function exists(path: string): Promise<boolean> {
  try {
    await fs.stat(path);
    return true;
  } catch (error) {
    if (missing(error)) return false;
    throw error;
  }
}
async function present(path: string): Promise<boolean> {
  return (await exists(path)) || (await is_symlink(path));
}
function sibling(path: string, kind: 'tmp' | 'old'): string {
  return join(
    dirname(path),
    `.${basename(path)}.restore-${kind}-${randomUUID().replaceAll('-', '')}`,
  );
}
async function make_owner_writable_tree(root: string): Promise<void> {
  await fs.chmod(root, (await fs.stat(root)).mode | 0o700);
  for (const entry of await fs.readdir(root)) {
    const path = join(root, entry),
      info = await fs.lstat(path);
    if (info.isSymbolicLink()) continue;
    if (info.isDirectory()) await make_owner_writable_tree(path);
    else await fs.chmod(path, info.mode | 0o600);
  }
}
async function restore_managed_files(
  source: string,
  recorded: ManagedFilesEvidence,
  destination: string,
): Promise<void> {
  const roots = await managed_file_roots(destination);
  const replaced: { target: string; old?: string }[] = [];
  try {
    for (const name of recorded.roots) {
      const target = roots[name as ManagedRootName];
      // Python's fixed root map refuses unknown recorded names at the same boundary.
      if (target === undefined) throw new Error(`unknown managed root: ${name}`);
      await fs.mkdir(dirname(target), { recursive: true });
      const staged = sibling(target, 'tmp');
      let old: string | undefined;
      try {
        await copy_managed_tree(join(source, name), staged);
        await make_owner_writable_tree(staged);
        if (await present(target)) {
          old = sibling(target, 'old');
          await fs.rename(target, old);
        }
        await fs.rename(staged, target);
      } catch (error) {
        if (old !== undefined && !(await present(target))) await fs.rename(old, target);
        await fs.rm(staged, { recursive: true, force: true }).catch(() => {});
        throw error;
      }
      replaced.push({ target, old });
    }
    for (const { old } of replaced)
      if (old !== undefined) await fs.rm(old, { recursive: true, force: true }).catch(() => {});
  } catch (error) {
    for (const { target, old } of replaced.reverse()) {
      await fs.rm(target, { recursive: true, force: true }).catch(() => {});
      if (old !== undefined) await fs.rename(old, target);
    }
    throw error;
  }
}

export async function restore_database_snapshot(
  snapshot: string,
  destinationDatabase: string,
  options: RestoreOptions,
): Promise<void> {
  if (!options.live_stopped) throw new Error('live environment must be stopped before restore');
  const admitted = assert_test_database_path(expand_path(destinationDatabase));
  const destination = await resolved_path(admitted);
  snapshot = expand_path(snapshot);
  if ((await is_symlink(snapshot)) || !(await is_verified_snapshot(snapshot)))
    throw new Error('snapshot is not verified');
  snapshot = await resolved_path(snapshot);
  const image = join(snapshot, 'database.sqlite');
  let metadata: unknown;
  try {
    const bytes = await fs.readFile(join(snapshot, 'metadata.json'));
    metadata = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch (error) {
    if (error instanceof SyntaxError || (error instanceof Error && 'code' in error))
      throw new Error('snapshot is not verified');
    throw error;
  }
  if (
    metadata === null ||
    typeof metadata !== 'object' ||
    Array.isArray(metadata) ||
    !('format' in metadata) ||
    metadata.format !== SNAPSHOT_FORMAT ||
    !('verified' in metadata) ||
    metadata.verified !== true ||
    !('sha256' in metadata) ||
    metadata.sha256 !== (await sha256_file(image))
  )
    throw new Error('snapshot is not verified');
  verifyDatabaseIntegrity(image);
  const managed = 'managed_files' in metadata ? metadata.managed_files : undefined;
  if (managed != null && !(await managed_files_valid(join(snapshot, 'files'), managed)))
    throw new Error('snapshot is not verified');
  await fs.mkdir(dirname(destination), { recursive: true });
  const temporary = sibling(destination, 'tmp');
  const sidecars: { original: string; old: string }[] = [];
  for (const suffix of ['-wal', '-shm']) {
    const original = destination + suffix;
    if (await exists(original)) sidecars.push({ original, old: sibling(original, 'old') });
  }
  try {
    await fs.copyFile(image, temporary);
    await fs.chmod(temporary, (await fs.stat(image)).mode | 0o600);
    const times = await fs.stat(image);
    await fs.utimes(temporary, times.atime, times.mtime);
    verifyDatabaseIntegrity(temporary);
    try {
      for (const { original, old } of sidecars) await fs.rename(original, old);
      await fs.rename(temporary, destination);
    } catch (error) {
      for (const { original, old } of [...sidecars].reverse())
        if ((await exists(old)) && !(await exists(original))) await fs.rename(old, original);
      throw error;
    }
    for (const { old } of sidecars) await fs.rm(old, { force: true });
  } finally {
    for (const path of [temporary, temporary + '-wal', temporary + '-shm'])
      await fs.rm(path, { force: true });
  }
  if (managed != null)
    await restore_managed_files(
      join(snapshot, 'files'),
      managed as ManagedFilesEvidence,
      destination,
    );
}
