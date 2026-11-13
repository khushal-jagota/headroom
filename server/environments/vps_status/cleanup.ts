import { rename, rm, stat, lstat } from 'node:fs/promises';
import { basename, dirname } from 'node:path';
import {
  DEFAULT_DEPENDENCIES,
  bounded_children,
  contained,
  modified_microseconds,
  safe_under,
} from './probes.ts';
import { DEFAULT_VPS_STATUS_POLICY } from './configuration.ts';
import {
  expand_path,
  is_directory,
  is_symlink,
  is_verified_snapshot,
  missing,
  resolved_path,
  verified_snapshot_retention_plan,
} from '../backup/evidence.ts';
import { instant, utc_iso } from './logic/status.ts';
import type {
  CleanupCandidate,
  CleanupInventory,
  CleanupResult,
  StatusConfig,
  StatusOptions,
  VpsStatusPolicy,
} from './contracts.ts';
export function candidate_body(candidate: CleanupCandidate) {
  return { kind: candidate.kind, path: candidate.path, expires_at: candidate.expires_at };
}
export function inventory_body(inventory: CleanupInventory) {
  return { candidates: inventory.candidates.map(candidate_body) };
}
async function entry_exists(path: string): Promise<boolean> {
  try {
    await lstat(path);
    return true;
  } catch (error) {
    if (missing(error)) return false;
    throw error;
  }
}
async function expired(path: string, now: bigint, policy: VpsStatusPolicy): Promise<boolean> {
  try {
    return (await modified_microseconds(path)) + BigInt(policy.temporary_expiry * 1_000_000) <= now;
  } catch (error) {
    if (error instanceof Error && 'code' in error) return false;
    throw error;
  }
}
export async function collect_cleanup_inventory(
  config: StatusConfig,
  options: StatusOptions = {},
): Promise<CleanupInventory> {
  const dependencies = { ...DEFAULT_DEPENDENCIES, ...options.dependencies };
  const policy = options.policy ?? DEFAULT_VPS_STATUS_POLICY;
  const now = instant(dependencies.now());
  const backupRoot = expand_path(config.backup_dir);
  const logsRoot = expand_path(config.logs_dir);
  const candidates: CleanupCandidate[] = [];
  if ((await is_directory(backupRoot)) && !(await is_symlink(backupRoot))) {
    for (const path of await verified_snapshot_retention_plan(backupRoot)) {
      candidates.push({ kind: 'backup_retention', path, root: backupRoot, expires_at: null });
    }
    let children: string[];
    try {
      children = await bounded_children(backupRoot);
    } catch {
      children = [];
    }
    for (const path of children) {
      if (!basename(path).startsWith('.backup-') || !(await expired(path, now, policy))) continue;
      candidates.push({
        kind: 'temporary_backup',
        path,
        root: backupRoot,
        expires_at: utc_iso(
          (await modified_microseconds(path)) + BigInt(policy.temporary_expiry * 1_000_000),
        ),
      });
    }
  }
  if ((await is_directory(logsRoot)) && !(await is_symlink(logsRoot))) {
    let children: string[];
    try {
      children = await bounded_children(logsRoot);
    } catch {
      children = [];
    }
    for (const path of children) {
      if (
        (await safe_under(logsRoot, path, false)) &&
        (await stat(path)).size >= policy.log_warning_bytes
      ) {
        candidates.push({ kind: 'log_rotation', path, root: logsRoot, expires_at: null });
      }
    }
  }
  return { candidates, policy, dependencies };
}
async function temporary_still_safe(
  candidate: CleanupCandidate,
  inventory: CleanupInventory,
): Promise<boolean> {
  const now = instant(inventory.dependencies.now());
  if (
    !(await safe_under(candidate.root, candidate.path, true)) ||
    !basename(candidate.path).startsWith('.backup-') ||
    !(await expired(candidate.path, now, inventory.policy))
  )
    return false;
  const references = new Set(
    await Promise.all(
      (await inventory.dependencies.live_reference_paths()).map((path) => resolved_path(path)),
    ),
  );
  return !references.has(await resolved_path(candidate.path));
}
async function apply_retention(
  root: string,
  paths: readonly string[],
  signal?: AbortSignal,
  admit_path?: (path: string) => string,
): Promise<void> {
  signal?.throwIfAborted();
  const admitted_root = admit_path?.(root) ?? root;
  if ((await is_symlink(root)) || !(await is_directory(admitted_root)))
    throw new Error('backup retention root is no longer a regular directory');
  const canonical = await resolved_path(admitted_root);
  const current = new Set(
    await Promise.all(
      (await verified_snapshot_retention_plan(admitted_root)).map((path) => resolved_path(path)),
    ),
  );
  for (const candidate of paths) {
    signal?.throwIfAborted();
    const resolved = admit_path?.(candidate) ?? (await resolved_path(candidate));
    if (
      (await is_symlink(candidate)) ||
      dirname(resolved) !== canonical ||
      !current.has(resolved) ||
      !(await is_verified_snapshot(resolved))
    ) {
      throw new Error('backup retention target is no longer verified');
    }
    signal?.throwIfAborted();
    await rm(resolved, { recursive: true });
  }
}
export async function apply_cleanup_inventory(
  inventory: CleanupInventory,
  signal?: AbortSignal,
  admit_path?: (path: string) => string,
): Promise<CleanupResult> {
  signal?.throwIfAborted();
  const removed: string[] = [],
    rotated: string[] = [],
    review_needed: string[] = [];
  const backups: CleanupCandidate[] = [];
  for (const candidate of inventory.candidates) {
    signal?.throwIfAborted();
    const path = admit_path?.(candidate.path) ?? candidate.path;
    if (admit_path) admit_path(candidate.root);
    if (candidate.kind === 'backup_retention') {
      backups.push(candidate);
      continue;
    }
    if (candidate.kind === 'temporary_backup') {
      if (!(await temporary_still_safe(candidate, inventory))) {
        review_needed.push(candidate.path);
        continue;
      }
      signal?.throwIfAborted();
      await rm(path, { recursive: true });
      removed.push(candidate.path);
      continue;
    }
    if (!(await safe_under(candidate.root, candidate.path, false))) {
      review_needed.push(candidate.path);
      continue;
    }
    const rotatedPath = candidate.path + '.1';
    if ((await entry_exists(rotatedPath)) || !(await contained(candidate.root, rotatedPath))) {
      review_needed.push(candidate.path);
      continue;
    }
    signal?.throwIfAborted();
    await rename(path, admit_path?.(rotatedPath) ?? rotatedPath);
    rotated.push(candidate.path);
  }
  if (backups.length) {
    const root = backups[0].root;
    const paths = backups.map((candidate) => candidate.path);
    if (backups.some((candidate) => candidate.root !== root)) review_needed.push(...paths);
    else {
      try {
        await apply_retention(root, paths, signal, admit_path);
      } catch {
        signal?.throwIfAborted();
        review_needed.push(...paths);
        return { removed, rotated, review_needed };
      }
      removed.push(...paths);
    }
  }
  signal?.throwIfAborted();
  return { removed, rotated, review_needed };
}
