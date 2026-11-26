/** Verified online snapshots in the existing backup-v2 format. */
import { createHash, randomUUID } from 'node:crypto';
import fs from 'node:fs/promises';
import { basename, dirname, join } from 'node:path';
import {
  onlineBackup,
  readStoppedDatabaseFile,
  verifyDatabaseIntegrity,
} from '../../core/database/index.ts';
import { parseIsoDatetime } from '../../core/clock.ts';
import {
  alarm_backup_integrity_failure,
  alarm_update_snapshot_failure,
} from './integrity_alarm.ts';
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
  verified_snapshots,
  missing,
} from './evidence.ts';

/** Tells the owner that a copy failed SQLite's integrity check. Never throws. */
export type IntegrityAlarm = (sourceDatabase: string) => Promise<void>;

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
  options: { integrity_alarm?: IntegrityAlarm } = {},
): Promise<string> {
  const source = await resolved_path(sourceDatabase);
  const root = await resolved_path(backupDirectory);
  if (!(await is_file(source))) throw new Error(source);
  await fs.mkdir(root, { recursive: true });
  const temporary = await fs.mkdtemp(join(root, STAGED_PREFIX));
  try {
    const image = join(temporary, 'database.sqlite');
    await onlineBackup(source, image);
    try {
      verifyDatabaseIntegrity(image);
    } catch (error) {
      await (options.integrity_alarm ?? alarm_backup_integrity_failure)(source);
      throw error;
    }
    return temporary;
  } catch (error) {
    await remove_temporary(temporary);
    throw error;
  }
}

/**
 * One update prepares at a time, so a staged copy that is already here belongs to an earlier
 * attempt that ended without its installer. The update removes it before it measures space.
 */
export async function remove_staged_copies(backupDirectory: string): Promise<void> {
  let names: string[];
  try {
    names = await fs.readdir(backupDirectory);
  } catch (error) {
    if (missing(error)) return;
    throw error;
  }
  for (const name of names)
    if (name.startsWith(STAGED_PREFIX)) await remove_temporary(join(backupDirectory, name));
}

/** A staged copy is a temporary folder of the backup directory, under its own prefix. */
export async function is_staged_copy(path: string, backupDirectory: string): Promise<boolean> {
  const resolved = await resolved_path(path);
  return (
    dirname(resolved) === (await resolved_path(backupDirectory)) &&
    basename(resolved).startsWith(STAGED_PREFIX)
  );
}

/**
 * Make the staged image byte-identical to the stopped database. Only blocks that differ are
 * written. Returns the sha256 of the stopped database's bytes, read in the same pass.
 */
async function catch_up_staged_image(source: string, image: string): Promise<string> {
  return readStoppedDatabaseFile(source, () => copy_differing_blocks(source, image));
}
async function copy_differing_blocks(source: string, image: string): Promise<string> {
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
  options: { integrity_alarm?: IntegrityAlarm; staged_copy?: string } = {},
): Promise<string> {
  const stagedCopy = options.staged_copy;
  const source = await resolved_path(sourceDatabase);
  const root = await resolved_path(backupDirectory);
  if (!(await is_file(source))) throw new Error(source);
  await fs.mkdir(root, { recursive: true });
  let temporary: string;
  if (stagedCopy === undefined) temporary = await fs.mkdtemp(join(root, TEMPORARY_PREFIX));
  else {
    if (!(await is_staged_copy(stagedCopy, root)))
      throw new Error('the staged copy is not a temporary folder of the backup directory');
    temporary = await resolved_path(stagedCopy);
  }
  try {
    const image = join(temporary, 'database.sqlite');
    let sha256: string;
    if (stagedCopy === undefined) {
      await onlineBackup(source, image);
      try {
        verifyDatabaseIntegrity(image);
      } catch (error) {
        await (options.integrity_alarm ?? alarm_backup_integrity_failure)(source);
        throw error;
      }
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

/** The update snapshot's image failed its late check. */
export class UpdateSnapshotCheckFailure extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'UpdateSnapshotCheckFailure';
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
  options: {
    /** The live database, which holds the owner's push devices. */
    readonly database: string;
    readonly timed?: (part: 'late_check' | 'retention', seconds: number) => void;
    readonly integrity_alarm?: (database: string, restore_point: string | null) => Promise<void>;
  },
): Promise<void> {
  const timed = options.timed ?? (() => {});
  const started = performance.now();
  const path = await resolved_path(snapshot);
  const root = await resolved_path(backupDirectory);
  try {
    if (!(await is_verified_snapshot(path)))
      throw new UpdateSnapshotCheckFailure('the update snapshot no longer matches its sha256');
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
      throw new UpdateSnapshotCheckFailure(
        `the update snapshot failed SQLite's integrity check: ${String(error)}`,
        { cause: error },
      );
    }
  } catch (error) {
    if (error instanceof UpdateSnapshotCheckFailure) {
      const restore_point = (await verified_snapshots(root))[0];
      await (options.integrity_alarm ?? alarm_update_snapshot_failure)(
        options.database,
        restore_point === undefined ? null : basename(restore_point),
      );
    }
    throw error;
  } finally {
    timed('late_check', (performance.now() - started) / 1000);
  }
  const retention = performance.now();
  await apply_verified_snapshot_retention(root, await verified_snapshot_retention_plan(root));
  timed('retention', (performance.now() - retention) / 1000);
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
