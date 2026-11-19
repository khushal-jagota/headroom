import { constants, readFileSync, renameSync } from 'node:fs';
import { constants as os_constants } from 'node:os';
import { setTimeout } from 'node:timers/promises';
import { open, mkdir, readdir, rename, rmdir, unlink } from 'node:fs/promises';
import { LOGS_DIRECTORY } from '../../agent_homes/agent_home.ts';
import { basename, dirname, join } from 'node:path';
import { clean_mirrors } from './mirror.ts';
import { flock } from '../../core/locks/flock.ts';
import {
  snapshot_ticket_scratch,
  prune_ticket_scratch,
} from '../../conversation/worker_scratch.ts';
import { OWNERSHIP_FILE, RETENTION_SECONDS, WORKTREE_DIRECTORY } from '../contracts.ts';
import type {
  CleanupResult,
  RetirementOptions,
  RetirementRecord,
  RetirementTarget,
  WorkspaceCleanupOptions,
  WorkspaceOptions,
  WorkspaceState,
  WorkspaceMarker,
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
  write_json_sync,
} from './shared.ts';
import {
  cleanup_ticket_folder,
  reason_to_keep,
  recorded_checkout_problem,
  workspace_ticket_owners,
} from './safety.ts';

function clear_checkout(path: string): void {
  const marker_path = join(path, OWNERSHIP_FILE);
  const marker = JSON.parse(readFileSync(marker_path, 'utf8')) as WorkspaceMarker;
  for (const key of [
    'worktree',
    'repository',
    'branch',
    'preparing',
    'initial_commit',
    'checkout_kind',
    'temporary',
  ] as const)
    delete marker[key];
  write_json_sync(marker_path, { ...marker, workspace_id: token() });
}
export function detach_workspace(path: string, target: string, checkout_only: boolean): void {
  if (!checkout_only) {
    renameSync(path, target);
    return;
  }
  renameSync(join(path, WORKTREE_DIRECTORY), join(target, WORKTREE_DIRECTORY));
  try {
    clear_checkout(path);
  } catch (error) {
    renameSync(join(target, WORKTREE_DIRECTORY), join(path, WORKTREE_DIRECTORY));
    throw error;
  }
}
async function discard_preparation(target: string, checkout_only: boolean): Promise<void> {
  if (
    checkout_only &&
    (await entry_stat(target))?.isDirectory() &&
    !(await entry_stat(join(target, WORKTREE_DIRECTORY)))
  ) {
    await unlink(join(target, OWNERSHIP_FILE)).catch((error: NodeJS.ErrnoException) => {
      if (error.code !== 'ENOENT') throw error;
    });
    await rmdir(target);
  }
  const id = basename(target).split('-').at(-1)!;
  await unlink(join(dirname(target), id + '.json')).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== 'ENOENT') throw error;
  });
}

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
  if (options.checkout_only) {
    await mkdir(target);
    await write_json(join(target, OWNERSHIP_FILE), await json(join(path, OWNERSHIP_FILE)));
  }
  await write_json(join(retired, id + '.json'), {
    source: path,
    target,
    ...state,
    ...identity,
    scratch_names,
    checkout_only: options.checkout_only ?? false,
  });
  return { path: target };
}
export async function retire_workspace(
  path: string,
  state: WorkspaceState,
  options: RetirementOptions = {},
): Promise<RetirementTarget> {
  const target = await prepare_workspace_retirement(path, state, options);
  if ('path' in target) {
    try {
      detach_workspace(path, target.path, options.checkout_only ?? false);
    } catch (error) {
      await discard_preparation(target.path, options.checkout_only ?? false);
      throw error;
    }
  }
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
  if (
    record.checkout_only &&
    (await entry_stat(record.source))?.isDirectory() &&
    !(await entry_stat(join(record.source, WORKTREE_DIRECTORY)))
  ) {
    try {
      const identity = read_workspace_identity(record.source);
      if (
        identity.machine_id === record.machine_id &&
        identity.workspace_id === record.workspace_id
      )
        clear_checkout(record.source);
    } catch (error) {
      return `cannot recover retained Ticket folder ownership: ${String(error)}`;
    }
  }
  // The seven-day removal keeps the agent's logs in the Ticket folder. A retry
  // after an interruption returns them the same way. If a reopened Ticket has
  // a file with the same name, that newer file stays.
  const logs = join(path, LOGS_DIRECTORY);
  if (!record.checkout_only && (await entry_stat(logs))?.isDirectory()) {
    const kept = join(record.source, LOGS_DIRECTORY);
    await mkdir(kept, { recursive: true });
    for (const name of await readdir(logs))
      if (!(await entry_stat(join(kept, name)))) await rename(join(logs, name), join(kept, name));
  }
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
    } catch (error) {
      console.warn('retired workspace size could not be measured', join(root, name), error);
    }
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
    const result = await cleanup_pass(tickets_root, states, now, options);
    if (options.apply !== false) await clean_mirrors(now, options);
    return result;
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
    eligible: { path: string; checkout_only: boolean }[] = [];
  const active = options.active_ticket_ids ?? new Set<string>();
  for (let root of [tickets_root, ...(options.extra_roots ?? [])]) {
    root = admit(root, options);
    const info = await entry_stat(root);
    if (!info?.isDirectory() || info.isSymbolicLink()) continue;
    for (const name of (await readdir(root)).sort()) {
      const path = admit(join(root, name), options);
      const entry = await entry_stat(path);
      if (entry?.isSymbolicLink()) continue;
      const ticket_id = ticket_match.exec(name)?.[1] ?? null,
        state = states.get(ticket_id ?? '');
      let reason: string | null;
      if (root !== tickets_root)
        reason = 'legacy scratch outside Ticket files requires one-time owner review';
      else if (entry?.isDirectory() && (await readdir(path)).join('/') === LOGS_DIRECTORY)
        reason = 'only logs remain';
      else if (!state) reason = 'Ticket owner or end date is unknown; owner review required';
      else if (active.has(ticket_id!) || state.ended_at === null) reason = 'Ticket is active';
      else {
        const checkout = join(path, WORKTREE_DIRECTORY);
        const checkout_only = now < state.ended_at + RETENTION_SECONDS;
        reason = await recorded_checkout_problem(path, options);
        if (!reason && checkout_only && !(await entry_stat(join(checkout, '.git'))))
          reason = 'Ticket artifacts remain for seven days';
        if (
          !reason &&
          checkout_only &&
          (await workspace_ticket_owners(checkout, options)).some((owner) => owner !== ticket_id)
        )
          reason = 'mixed Ticket ownership requires owner review';
        if (!reason)
          reason = await reason_to_keep(checkout_only ? checkout : path, states, active, options);
      }
      manifest.push({
        path,
        ticket_id,
        ended_at: state?.ended_at ?? null,
        eligible_at: state?.ended_at == null ? null : state.ended_at + RETENTION_SECONDS,
        allocated_bytes: options.measure_size ? await allocated_bytes(path) : null,
        preservation_reason: reason,
      });
      if (reason) result.skipped.push({ path, reason });
      else eligible.push({ path, checkout_only: now < state!.ended_at! + RETENTION_SECONDS });
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
  for (const { path, checkout_only } of eligible)
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
        ? await options.retire(path, state, identity, checkout_only)
        : await retire_workspace(path, state, {
            ...options,
            identity,
            retired_root: join(dirname(tickets_root), '.ticket-retired'),
            checkout_only,
          });
      if ('reason' in target) {
        result.skipped.push({ path, reason: target.reason });
        continue;
      }
      const reason = await remove_retired(target.path, options);
      if (reason) result.skipped.push({ path: target.path, reason });
      else result.removed.push(checkout_only ? join(path, WORKTREE_DIRECTORY) : path);
    } catch (error) {
      result.skipped.push({ path, reason: `cleanup failed: ${String(error)}` });
    }
  return result;
}
