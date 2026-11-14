/** Verified online snapshots in the existing backup-v2 format. */
import { createHash, randomUUID } from 'node:crypto';
import fs from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { onlineBackup, verifyDatabaseIntegrity } from '../../core/database/index.ts';
import { parseIsoDatetime } from '../../core/clock.ts';
import type {
  ManagedRootName,
  ManagedFilesManifest,
  SnapshotMetadata,
  ManagedFilesEvidence,
} from './contracts.ts';
import {
  expand_path,
  resolved_path,
  is_symlink,
  is_directory,
  is_file,
  relative_manifest,
  managed_files_valid,
  sha256_file,
  SNAPSHOT_FORMAT,
  is_verified_snapshot,
  verified_snapshot_retention_plan,
} from './evidence.ts';

export async function managed_file_roots(
  databasePath: string,
): Promise<Record<ManagedRootName, string>> {
  const parent = dirname(await resolved_path(databasePath));
  return {
    files: join(parent, 'files'),
    'worker-settings': join(parent, 'worker-settings'),
    skills: join(parent, 'skills'),
  };
}

/** Follow only the source root. Preserve every descendant link. */
export async function copy_managed_tree(source: string, destination: string): Promise<void> {
  await fs.cp(await fs.realpath(source), destination, {
    recursive: true,
    dereference: false,
    verbatimSymlinks: true,
    preserveTimestamps: true,
  });
}

async function capture_managed_files(
  source: string,
  destination: string,
): Promise<ManagedFilesEvidence | undefined> {
  const roots = await managed_file_roots(source);
  const present: string[] = [];
  for (const name of ['worker-settings', 'skills'] as const) {
    try {
      await fs.stat(roots[name]);
      present.push(name);
    } catch (error) {
      if (!(
        error instanceof Error &&
        'code' in error &&
        ['ENOENT', 'ENOTDIR'].includes(String(error.code))
      ))
        throw error;
    }
  }
  if (!present.length) return undefined;
  await fs.mkdir(destination, { recursive: true });
  const manifest: ManagedFilesManifest = {};
  for (const name of present as Exclude<ManagedRootName, 'files'>[]) {
    const target = join(destination, name);
    await copy_managed_tree(roots[name], target);
    manifest[name] = await relative_manifest(target);
  }
  const bytes = Buffer.from(JSON.stringify(manifest, null, 2) + '\n');
  await fs.writeFile(join(destination, 'manifest.json'), bytes);
  return {
    roots: Object.keys(manifest).sort(),
    manifest_sha256: createHash('sha256').update(bytes).digest('hex'),
  };
}

export async function create_database_backup(
  sourceDatabase: string,
  backupDirectory: string,
  deployedRevision: string,
): Promise<string> {
  const source = await resolved_path(sourceDatabase);
  const root = await resolved_path(backupDirectory);
  if (!(await is_file(source))) throw new Error(source);
  await fs.mkdir(root, { recursive: true });
  const temporary = await fs.mkdtemp(join(root, '.backup-'));
  try {
    const image = join(temporary, 'database.sqlite');
    await onlineBackup(source, image);
    verifyDatabaseIntegrity(image);
    const metadata: SnapshotMetadata = {
      format: SNAPSHOT_FORMAT,
      created_at: parseIsoDatetime(new Date().toISOString()).iso,
      deployed_revision: deployedRevision,
      sha256: await sha256_file(image),
      verified: true,
    };
    const managed = await capture_managed_files(source, join(temporary, 'files'));
    if (managed !== undefined && !(await managed_files_valid(join(temporary, 'files'), managed)))
      throw new Error('managed file capture failed verification');
    await fs.writeFile(
      join(temporary, 'metadata.json'),
      JSON.stringify({ ...metadata, ...(managed ? { managed_files: managed } : {}) }, null, 2) +
        '\n',
    );
    const timestamp = new Date()
      .toISOString()
      .replace(/[-:]/g, '')
      .replace(/\.\d{3}Z$/, 'Z');
    const snapshot = join(
      root,
      `snapshot-${timestamp}-${randomUUID().replaceAll('-', '').slice(0, 8)}`,
    );
    await fs.rename(temporary, snapshot);
    await apply_verified_snapshot_retention(root, await verified_snapshot_retention_plan(root));
    return snapshot;
  } catch (error) {
    await fs.rm(temporary, { recursive: true, force: true }).catch(() => {});
    throw error;
  }
}

export async function apply_verified_snapshot_retention(
  backupDirectory: string,
  candidates: readonly string[],
): Promise<void> {
  const configured = expand_path(backupDirectory);
  if ((await is_symlink(configured)) || !(await is_directory(configured)))
    throw new Error('backup retention root is no longer a regular directory');
  const root = await resolved_path(configured);
  const plan = new Set(
    await Promise.all(
      (await verified_snapshot_retention_plan(configured)).map((path) => resolved_path(path)),
    ),
  );
  for (const candidate of candidates) {
    const path = await resolved_path(candidate);
    if (
      (await is_symlink(candidate)) ||
      dirname(path) !== root ||
      !plan.has(path) ||
      !(await is_verified_snapshot(path))
    )
      throw new Error('backup retention target is no longer verified');
    await fs.rm(path, { recursive: true });
  }
}
