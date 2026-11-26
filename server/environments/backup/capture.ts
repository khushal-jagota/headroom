/** Verified online snapshots in the existing backup-v2 format. */
import { createHash, randomUUID } from 'node:crypto';
import fs from 'node:fs/promises';
import { basename, dirname, join } from 'node:path';
import {
  checkpointStoppedDatabase,
  onlineBackup,
  verifyDatabaseIntegrity,
} from '../../core/database/index.ts';
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
  missing,
} from './evidence.ts';

const TEMPORARY_PREFIX = '.backup-';
// Under the temporary prefix, so cleanup of expired temporary folders also covers it.
const STAGED_PREFIX = TEMPORARY_PREFIX + 'staged-';
const CATCH_UP_READ_BYTES = 1024 * 1024;
const CATCH_UP_BLOCK_BYTES = 4096;

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

/**
 * While the home still serves: an online copy and its full integrity check, in a temporary
 * folder of the backup directory. The update's stopped capture publishes it.
 */
export async function stage_database_copy(
  sourceDatabase: string,
  backupDirectory: string,
): Promise<string> {
  const source = await resolved_path(sourceDatabase);
  const root = await resolved_path(backupDirectory);
  if (!(await is_file(source))) throw new Error(source);
  await fs.mkdir(root, { recursive: true });
  // One update prepares at a time. A staged copy left here belongs to an earlier attempt
  // that ended with its process.
  for (const name of await fs.readdir(root))
    if (name.startsWith(STAGED_PREFIX)) await remove_temporary(join(root, name));
  const temporary = await fs.mkdtemp(join(root, STAGED_PREFIX));
  try {
    const image = join(temporary, 'database.sqlite');
    await onlineBackup(source, image);
    verifyDatabaseIntegrity(image);
    return temporary;
  } catch (error) {
    await remove_temporary(temporary);
    throw error;
  }
}

/**
 * Make the staged image byte-identical to the stopped database. Only blocks that differ are
 * written. Returns the sha256 of the stopped database's bytes, read in the same pass.
 */
async function catch_up_staged_image(source: string, image: string): Promise<string> {
  checkpointStoppedDatabase(source);
  try {
    if ((await fs.stat(source + '-wal')).size !== 0)
      throw new Error('the stopped database kept frames in its WAL');
  } catch (error) {
    if (!missing(error)) throw error;
  }
  const digest = createHash('sha256');
  const stopped = await fs.open(source, 'r');
  try {
    const staged = await fs.open(image, 'r+');
    try {
      const wanted = Buffer.alloc(CATCH_UP_READ_BYTES),
        present = Buffer.alloc(CATCH_UP_READ_BYTES);
      let offset = 0;
      for (;;) {
        const { bytesRead: length } = await stopped.read(wanted, 0, CATCH_UP_READ_BYTES, offset);
        if (length === 0) break;
        const { bytesRead: existing } = await staged.read(present, 0, length, offset);
        digest.update(wanted.subarray(0, length));
        for (let block = 0; block < length; block += CATCH_UP_BLOCK_BYTES) {
          const end = Math.min(block + CATCH_UP_BLOCK_BYTES, length);
          if (wanted.compare(present, block, Math.min(end, existing), block, end) !== 0)
            await staged.write(wanted, block, end - block, offset + block);
        }
        offset += length;
      }
      await staged.truncate(offset);
      await staged.sync();
    } finally {
      await staged.close();
    }
  } finally {
    await stopped.close();
  }
  return digest.digest('hex');
}

async function remove_temporary(temporary: string): Promise<void> {
  await fs.rm(temporary, { recursive: true, force: true }).catch((cleanup: unknown) => {
    console.warn('backup capture could not remove its temporary copy', temporary, cleanup);
  });
}

/**
 * Without a staged copy: an online copy, its full integrity check and sha256, then retention.
 * With one, the source must be stopped: the staged copy catches up to it, and retention waits
 * for `verify_update_snapshot` after the home serves again.
 */
export async function create_database_backup(
  sourceDatabase: string,
  backupDirectory: string,
  deployedRevision: string,
  stagedCopy?: string,
): Promise<string> {
  const source = await resolved_path(sourceDatabase);
  const root = await resolved_path(backupDirectory);
  if (!(await is_file(source))) throw new Error(source);
  await fs.mkdir(root, { recursive: true });
  let temporary: string;
  if (stagedCopy === undefined) temporary = await fs.mkdtemp(join(root, TEMPORARY_PREFIX));
  else {
    temporary = await resolved_path(stagedCopy);
    if (dirname(temporary) !== root || !basename(temporary).startsWith(STAGED_PREFIX))
      throw new Error('the staged copy is not a temporary folder of the backup directory');
  }
  try {
    const image = join(temporary, 'database.sqlite');
    let sha256: string;
    if (stagedCopy === undefined) {
      await onlineBackup(source, image);
      verifyDatabaseIntegrity(image);
      sha256 = await sha256_file(image);
    } else {
      if (!(await is_file(image))) throw new Error('the staged copy has no database image');
      sha256 = await catch_up_staged_image(source, image);
    }
    const metadata: SnapshotMetadata = {
      format: SNAPSHOT_FORMAT,
      created_at: parseIsoDatetime(new Date().toISOString()).iso,
      deployed_revision: deployedRevision,
      sha256,
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
    if (stagedCopy === undefined)
      await apply_verified_snapshot_retention(root, await verified_snapshot_retention_plan(root));
    return snapshot;
  } catch (error) {
    await remove_temporary(temporary);
    throw error;
  }
}

/**
 * After an update's home serves again: the full integrity check that the stopped window
 * skipped, then the retention that it deferred. A damaged image loses its verified mark, so
 * retention and restore no longer count it.
 */
export async function verify_update_snapshot(
  snapshot: string,
  backupDirectory: string,
): Promise<void> {
  const path = await resolved_path(snapshot);
  if (!(await is_verified_snapshot(path)))
    throw new Error('the update snapshot no longer matches its recorded sha256');
  try {
    verifyDatabaseIntegrity(join(path, 'database.sqlite'));
  } catch (error) {
    const metadata_path = join(path, 'metadata.json');
    const metadata = JSON.parse(await fs.readFile(metadata_path, 'utf8')) as SnapshotMetadata;
    const replacement = metadata_path + '.unverified';
    await fs.writeFile(
      replacement,
      JSON.stringify({ ...metadata, verified: false }, null, 2) + '\n',
    );
    await fs.rename(replacement, metadata_path);
    throw error;
  }
  await apply_verified_snapshot_retention(
    backupDirectory,
    await verified_snapshot_retention_plan(backupDirectory),
  );
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
