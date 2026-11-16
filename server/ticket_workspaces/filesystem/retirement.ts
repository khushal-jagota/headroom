import { constants } from 'node:fs';
import { constants as os_constants } from 'node:os';
import { setTimeout } from 'node:timers/promises';
import { open, mkdir, readdir, rename, unlink } from 'node:fs/promises';
import { basename, dirname, join } from 'node:path';
import { flock } from '../../core/locks/flock.ts';
import {
  snapshot_ticket_scratch,
  prune_ticket_scratch,
} from '../../conversation/worker_scratch.ts';
import { RETENTION_SECONDS } from '../contracts.ts';
import type {
  CleanupResult,
  RetirementOptions,
  RetirementRecord,
  RetirementTarget,
  WorkspaceCleanupOptions,
  WorkspaceOptions,
  WorkspaceState,
} from '../contracts.ts';
import {
  admit,
  checkouts,
  entry_stat,
  git,
  json,
  read_workspace_identity,
  ticket_match,
  token,
  workspace_identity,
  write_json,
} from './shared.ts';
import { cleanup_ticket_folder, reason_to_keep } from './safety.ts';

export async function prepare_workspace_retirement(
  path: string,
  state: WorkspaceState,
  options: RetirementOptions = {},
): Promise<RetirementTarget> {
  path = admit(path, options);
  if (
    (await entry_stat(path))?.isSymbolicLink() ||
    (await entry_stat(dirname(path)))?.isSymbolicLink()
  )
    return { reason: 'workspace path is a symlink' };
  const identity = options.identity ?? read_workspace_identity(path),
    actual = read_workspace_identity(path);
  if (actual.machine_id !== identity.machine_id || actual.workspace_id !== identity.workspace_id)
    return { reason: 'physical workspace identity changed' };
  const retired = admit(
    options.retired_root ?? join(dirname(dirname(path)), '.ticket-retired'),
    options,
  );
  if ((await entry_stat(retired))?.isSymbolicLink())
    return { reason: 'retirement directory is a symlink' };
  await mkdir(retired, { recursive: true });
  const id = token(),
    target = join(retired, `${state.ticket_id}-${identity.workspace_id}-${id}`);
  const scratch_names = await (options.snapshot_ticket_scratch ?? snapshot_ticket_scratch)(
    state.ticket_id,
  );
  await write_json(join(retired, id + '.json'), {
    source: path,
    target,
    ...state,
    ...identity,
    scratch_names,
  });
  return { path: target };
}
export async function retire_workspace(
  path: string,
  state: WorkspaceState,
  options: RetirementOptions = {},
): Promise<RetirementTarget> {
  const target = await prepare_workspace_retirement(path, state, options);
  if ('path' in target) await rename(path, target.path);
  return target;
}
async function remove_retired(path: string, options: WorkspaceOptions): Promise<string | null> {
  if (process.platform !== 'linux' && process.platform !== 'darwin')
    return 'safe removal is not supported on this platform yet';
  for (const checkout of await checkouts(path))
    if ((await entry_stat(join(checkout, '.git')))?.isFile()) {
      const result = await git(checkout, ['worktree', 'repair', checkout], options);
      if (result.returncode) return `cannot repair retired worktree: ${result.stderr.trim()}`;
    }
  const id = basename(path).split('-').at(-1)!,
    record = await json<RetirementRecord>(join(dirname(path), id + '.json'));
  if (record.target !== path) return 'retirement target differs from its record';
  const names = record.scratch_names ?? [];
  if (!Array.isArray(names) || names.some((name) => typeof name !== 'string'))
    return 'invalid retirement scratch snapshot';
  if (!(await (options.prune_ticket_scratch ?? prune_ticket_scratch)(record.ticket_id, names)))
    return 'Worker scratch path is not an owned real directory';
  return cleanup_ticket_folder(path, {
    signal: options.signal,
    admit_path: options.admit_path,
    git_execution: options.git_execution,
  });
}
export async function retry_retired_workspaces(
  retired: string,
  options: WorkspaceCleanupOptions = {},
): Promise<CleanupResult> {
  retired = admit(retired, options);
  const result: CleanupResult = { removed: [], skipped: [] },
    info = await entry_stat(retired);
  if (!info?.isDirectory() || info.isSymbolicLink()) return result;
  for (const name of (await readdir(retired)).filter((name) => name.endsWith('.json')).sort()) {
    const record_path = join(retired, name);
    try {
      const record = await json<RetirementRecord>(record_path),
        target = admit(record.target, options);
      if (dirname(target) !== retired || (await entry_stat(target))?.isSymbolicLink())
        throw new Error('invalid retirement target');
      if (!(await entry_stat(target))) {
        await unlink(record_path);
        continue;
      }
      if (options.authorize_retirement && !(await options.authorize_retirement(record, record))) {
        result.skipped.push({
          path: target,
          reason: 'retirement grant is not committed; owner review required',
        });
        continue;
      }
      const reason = await remove_retired(target, options);
      if (reason === null) {
        result.removed.push(target);
        await unlink(record_path);
      } else result.skipped.push({ path: target, reason });
    } catch (error) {
      result.skipped.push({ path: record_path, reason: String(error) });
    }
  }
  return result;
}
async function allocated_bytes(root: string): Promise<number> {
  const info = await entry_stat(root);
  if (!info) return 0;
  if (!info.isDirectory()) return info.blocks * 512;
  let total = 0;
  for (const name of await readdir(root))
    try {
      total += await allocated_bytes(join(root, name));
    } catch {}
  return total;
}
export async function cleanup_local_workspaces(
  tickets_root: string,
  states: ReadonlyMap<string, WorkspaceState>,
  now: number,
  options: WorkspaceCleanupOptions = {},
): Promise<CleanupResult> {
  tickets_root = admit(tickets_root, options);
  await mkdir(dirname(tickets_root), { recursive: true });
  const descriptor = await open(
    join(dirname(tickets_root), '.ticket-workspace-cleanup.lock'),
    constants.O_RDWR | constants.O_CREAT | constants.O_NOFOLLOW,
    0o600,
  );
  try {
    for (;;) {
      options.signal?.throwIfAborted();
      const error = flock(descriptor.fd, 'try-exclusive');
      if (!error) break;
      if (error !== os_constants.errno.EAGAIN && error !== os_constants.errno.EWOULDBLOCK)
        throw new Error('Cannot lock workspace cleanup');
      await setTimeout(20, undefined, { signal: options.signal });
    }
    return await cleanup_pass(tickets_root, states, now, options);
  } finally {
    await descriptor.close();
  }
}
async function cleanup_pass(
  tickets_root: string,
  states: ReadonlyMap<string, WorkspaceState>,
  now: number,
  options: WorkspaceCleanupOptions,
): Promise<CleanupResult> {
  const result: CleanupResult = { removed: [], skipped: [] },
    manifest: unknown[] = [],
    eligible: string[] = [];
  const active = options.active_ticket_ids ?? new Set<string>();
  for (let root of [tickets_root, ...(options.extra_roots ?? [])]) {
    root = admit(root, options);
    const info = await entry_stat(root);
    if (!info?.isDirectory() || info.isSymbolicLink()) continue;
    for (const name of (await readdir(root)).sort()) {
      const path = admit(join(root, name), options);
      if ((await entry_stat(path))?.isSymbolicLink()) continue;
      const ticket_id = ticket_match.exec(name)?.[1] ?? null,
        state = states.get(ticket_id ?? '');
      let reason: string | null;
      if (root !== tickets_root)
        reason = 'legacy scratch outside Ticket files requires one-time owner review';
      else if (!state) reason = 'Ticket owner or end date is unknown; owner review required';
      else if (active.has(ticket_id!) || state.ended_at === null) reason = 'Ticket is active';
      else if (now < state.ended_at + RETENTION_SECONDS)
        reason = 'seven-day retention period has not ended';
      else reason = await reason_to_keep(path, states, active, options);
      manifest.push({
        path,
        ticket_id,
        ended_at: state?.ended_at ?? null,
        eligible_at: state?.ended_at == null ? null : state.ended_at + RETENTION_SECONDS,
        allocated_bytes: await allocated_bytes(path),
        preservation_reason: reason,
      });
      if (reason) result.skipped.push({ path, reason });
      else eligible.push(path);
    }
  }
  await write_json(join(dirname(tickets_root), 'ticket-workspaces-cleanup.json'), {
    at: now,
    folders: manifest,
  });
  if (options.apply === false) return result;
  const retried = await retry_retired_workspaces(join(dirname(tickets_root), '.ticket-retired'), {
    ...options,
    authorize_retirement: options.retirement_is_committed ?? options.authorize_retirement,
  });
  result.removed.push(...retried.removed);
  result.skipped.push(...retried.skipped);
  for (const path of eligible)
    try {
      const state = states.get(ticket_match.exec(basename(path))![1]!)!,
        identity = workspace_identity(path, { create: false });
      const before = await options.before_remove?.(path);
      if (before) {
        result.skipped.push({ path, reason: before });
        continue;
      }
      if (options.authorize_retirement && !(await options.authorize_retirement(state, identity))) {
        result.skipped.push({ path, reason: 'retirement authorization refused' });
        continue;
      }
      const target = options.retire
        ? await options.retire(path, state, identity)
        : await retire_workspace(path, state, {
            ...options,
            identity,
            retired_root: join(dirname(tickets_root), '.ticket-retired'),
          });
      if ('reason' in target) {
        result.skipped.push({ path, reason: target.reason });
        continue;
      }
      const reason = await remove_retired(target.path, options);
      if (reason) result.skipped.push({ path: target.path, reason });
      else result.removed.push(path);
    } catch (error) {
      result.skipped.push({ path, reason: `cleanup failed: ${String(error)}` });
    }
  return result;
}
