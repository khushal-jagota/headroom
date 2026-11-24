import {
  existsSync,
  lstatSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  statSync,
} from 'node:fs';
import { join } from 'node:path';
import { is_object } from '../codec.ts';
import { release_installer_barrier, with_installer_lock } from '../../update_barrier.ts';
import { assert_installer_path, PREVIOUS_APP_PREFIX, STAGED_APP_PREFIX } from './configuration.ts';
import { error_detail } from './commands.ts';
import { pin_existing, release_path, select_release, selected_release } from './selection.ts';
import { InstallationRecord } from './record.ts';
import type { InstallerEffects, InstallerIO, InstallerRequest } from './contracts.ts';

export class InstallFailed extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'InstallFailed';
  }
}
function field(attempt: Record<string, unknown>, key: string): string {
  if (!Object.hasOwn(attempt, key)) throw new TypeError(`Update attempt is missing ${key}`);
  const value = attempt[key];
  if (typeof value !== 'string') throw new TypeError(`Update attempt ${key} must be a string`);
  return value;
}
function directory(path: string): boolean {
  assert_installer_path(path);
  try {
    return statSync(path).isDirectory();
  } catch (error) {
    if (
      error instanceof Error &&
      'code' in error &&
      ['ENOENT', 'ENOTDIR', 'ELOOP'].includes(String(error.code))
    )
      return false;
    throw error;
  }
}
function installed_sha(app: string): unknown {
  assert_installer_path(app);
  assert_installer_path(join(app, 'manifest.json'));
  try {
    const value = JSON.parse(
      new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(
        readFileSync(join(app, 'manifest.json')),
      ),
    );
    return is_object(value) ? (value.app_sha ?? null) : null;
  } catch (error) {
    if (
      (error instanceof Error && 'code' in error) ||
      error instanceof SyntaxError ||
      error instanceof TypeError
    )
      return null;
    throw error;
  }
}
/** Refuse links and files before recursive directory removal. */
function rmtree(path: string, ignore_errors = false): void {
  assert_installer_path(path);
  try {
    const entry = lstatSync(path);
    if (entry.isSymbolicLink())
      throw new Error(`Cannot remove a symbolic link as a directory: ${path}`);
    if (!entry.isDirectory())
      throw Object.assign(new Error('Not a directory'), { code: 'ENOTDIR', path });
    rmSync(path, { recursive: true });
  } catch (error) {
    if (!ignore_errors) throw error;
  }
}
function move(source: string, destination: string, signal: AbortSignal): void {
  signal.throwIfAborted();
  assert_installer_path(source);
  assert_installer_path(destination);
  renameSync(source, destination);
}
function problem(error: unknown): string {
  if (error instanceof InstallFailed) return error.message;
  const detail = error_detail(error);
  return `${detail.name}: ${detail.message}`;
}
async function roll_back(
  request: InstallerRequest,
  effects: InstallerEffects,
  record: InstallationRecord,
  attempt: Record<string, unknown>,
  reason: string,
  previous_moved: boolean,
  new_app_moved: boolean,
  signal: AbortSignal,
  lifetime_lock_fd: number,
): Promise<1> {
  signal.throwIfAborted();
  const from_sha = field(attempt, 'from_sha'),
    to_sha = field(attempt, 'to_sha');
  const staged = join(request.current_root, STAGED_APP_PREFIX + to_sha);
  const backup = attempt.backup,
    restore_data = new_app_moved && typeof backup === 'string';
  try {
    if (
      !previous_moved &&
      !new_app_moved &&
      effects.recover_unchanged &&
      (await effects.recover_unchanged(from_sha))
    ) {
      record.record(
        'failed',
        'failed',
        `${reason}. The previous app kept running. Its auxiliary jobs were restored.`,
      );
      release_installer_barrier(request.data_directory, request.attempt_id);
      return 1;
    }
    if (previous_moved || new_app_moved) {
      signal.throwIfAborted();
      await effects.service('stop');
      signal.throwIfAborted();
      record.record('rollback_service_stopped');
    }
    // Data before apps: a kill between them leaves the new app in place, which its
    // next start settles before admission (interrupted_install.ts).
    if (restore_data) {
      signal.throwIfAborted();
      await effects.restore(backup, lifetime_lock_fd);
      signal.throwIfAborted();
      record.record('rollback_database_restored');
    }
    if (new_app_moved) {
      select_release(request.current_root, from_sha);
      move(release_path(request.current_root, to_sha), staged, signal);
    }
    record.record('rollback_app_restored');
    signal.throwIfAborted();
    await effects.service('start');
    signal.throwIfAborted();
    record.record('rollback_service_started');
    signal.throwIfAborted();
    const healthy = await effects.healthy(from_sha);
    signal.throwIfAborted();
    if (!healthy)
      throw new InstallFailed('the previous version did not pass its health check either');
  } catch (error) {
    signal.throwIfAborted();
    record.record(
      'rollback_failed',
      'failed',
      `${reason}. Panels could not go back to the previous version: ${problem(error)}.`,
    );
    return 1;
  }
  record.record('rollback_healthy');
  let auxiliary_detail: string | null = null;
  try {
    auxiliary_detail = (await effects.finish_rollback?.()) ?? null;
  } catch (error) {
    auxiliary_detail = `Its auxiliary jobs did not restart: ${problem(error)}`;
  }
  if (auxiliary_detail) record.record('auxiliary_restart_failed', undefined, auxiliary_detail);
  record.record(
    'failed',
    'failed',
    `${reason}, so Panels went back to ${restore_data ? 'the previous version and its data' : 'the previous version'}.${auxiliary_detail ? ' ' + auxiliary_detail : ''}`,
  );
  release_installer_barrier(request.data_directory, request.attempt_id);
  return 1;
}
export async function install(
  request: InstallerRequest,
  effects: InstallerEffects,
  io: InstallerIO,
  signal: AbortSignal,
): Promise<0 | 1 | 2> {
  signal.throwIfAborted();
  assert_installer_path(request.current_root);
  assert_installer_path(request.database);
  assert_installer_path(request.data_directory);
  const owned = await with_installer_lock(
    request.data_directory,
    request.attempt_id,
    (descriptor) => install_locked(request, effects, io, signal, descriptor),
  );
  if (!owned.acquired) {
    io.stderr('Another installer still owns this update.\n');
    return 2;
  }
  return owned.result;
}
async function install_locked(
  request: InstallerRequest,
  effects: InstallerEffects,
  io: InstallerIO,
  signal: AbortSignal,
  lifetime_lock_fd: number,
): Promise<0 | 1 | 2> {
  signal.throwIfAborted();
  assert_installer_path(request.current_root);
  assert_installer_path(request.database);
  assert_installer_path(request.data_directory);
  const record = new InstallationRecord(request),
    attempt = record.claim();
  if (attempt === null) {
    io.stderr('This update attempt is not waiting to install; nothing was changed.\n');
    return 2;
  }
  const to_sha = field(attempt, 'to_sha'),
    from_sha = field(attempt, 'from_sha');
  const app = join(request.current_root, 'app'),
    staged = join(request.current_root, STAGED_APP_PREFIX + to_sha);
  if (is_object(attempt.steps) && Object.hasOwn(attempt.steps, 'installer_started')) {
    const active = selected_release(request.current_root);
    const candidate_selected = active === release_path(request.current_root, to_sha);
    if (!candidate_selected && installed_sha(app) !== from_sha)
      throw new InstallFailed(
        'Unable to identify the installed release after an interrupted update',
      );
    return roll_back(
      request,
      effects,
      record,
      attempt,
      'The installer restarted before it committed the update',
      true,
      candidate_selected,
      signal,
      lifetime_lock_fd,
    );
  }
  if (!directory(staged)) {
    record.record(
      'failed',
      'failed',
      'The update could not start because the downloaded version is missing. The current version kept running.',
    );
    release_installer_barrier(request.data_directory, request.attempt_id);
    return 1;
  }
  if (installed_sha(app) !== from_sha) {
    record.record(
      'failed',
      'failed',
      'The update could not start because the installed version is not the one it expected. The current version kept running.',
    );
    release_installer_barrier(request.data_directory, request.attempt_id);
    return 1;
  }
  try {
    await effects.validate_service?.();
  } catch (error) {
    signal.throwIfAborted();
    record.record('failed', 'failed', `Mac identity refused: ${problem(error)}`);
    release_installer_barrier(request.data_directory, request.attempt_id);
    return 1;
  }

  let previous_moved = false,
    new_app_moved = false;
  try {
    signal.throwIfAborted();
    await effects.service('stop');
    signal.throwIfAborted();
    record.record('service_stopped');
    signal.throwIfAborted();
    const capture = await effects.capture();
    signal.throwIfAborted();
    if (capture.kind === 'unavailable')
      io.stderr(`Interruption capture unavailable: ${capture.detail}\n`);
    signal.throwIfAborted();
    record.record('interruptions_captured');
    if (attempt.snapshot_required === true) {
      await effects.snapshot(from_sha, lifetime_lock_fd);
      signal.throwIfAborted();
      attempt.backup = record.read().backup;
      if (typeof attempt.backup !== 'string')
        throw new InstallFailed('The database snapshot was not recorded');
    }
    pin_existing(request.current_root, from_sha);
    previous_moved = true;
    const target = release_path(request.current_root, to_sha);
    if (existsSync(target)) rmtree(target);
    move(staged, target, signal);
    record.record('selecting');
    select_release(request.current_root, to_sha);
    new_app_moved = true;
    record.record('app_swapped');
    signal.throwIfAborted();
    await effects.schema_steps(lifetime_lock_fd, typeof attempt.backup === 'string');
    signal.throwIfAborted();
    await effects.service('start');
    signal.throwIfAborted();
    record.record('service_started');
    signal.throwIfAborted();
    const healthy = await effects.healthy(to_sha, request.attempt_id);
    signal.throwIfAborted();
    if (!healthy) throw new InstallFailed('The new version did not pass its health check');
  } catch (error) {
    signal.throwIfAborted();
    const reason =
      error instanceof InstallFailed
        ? error.message
        : `The new version could not be installed (${problem(error)})`;
    return roll_back(
      request,
      effects,
      record,
      attempt,
      reason,
      previous_moved,
      new_app_moved,
      signal,
      lifetime_lock_fd,
    );
  }
  record.record('healthy');
  let completion_detail: string | null = null;
  try {
    completion_detail = (await effects.finish_success?.()) ?? null;
  } catch (error) {
    completion_detail = `The release passed health, but its auxiliary jobs did not restart: ${problem(error)}`;
  }
  if (completion_detail) record.record('auxiliary_restart_failed', undefined, completion_detail);
  record.record('done', 'done', completion_detail);
  release_installer_barrier(request.data_directory, request.attempt_id);
  assert_installer_path(request.current_root);
  for (const folder of readdirSync(request.current_root)) {
    const path = join(request.current_root, folder);
    if (
      (folder.startsWith(PREVIOUS_APP_PREFIX) || /^\.release-[0-9a-f]{40}$/.test(folder)) &&
      path !== release_path(request.current_root, from_sha) &&
      path !== selected_release(request.current_root)
    )
      rmtree(path, true);
  }
  rmtree(join(request.current_root, 'app-previous'), true);
  return 0;
}
