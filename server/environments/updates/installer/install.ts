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
import { assert_installer_path, PREVIOUS_APP_PREFIX, STAGED_APP_PREFIX } from './configuration.ts';
import { error_detail } from './commands.ts';
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
): Promise<1> {
  signal.throwIfAborted();
  const from_sha = field(attempt, 'from_sha'),
    to_sha = field(attempt, 'to_sha');
  const app = join(request.current_root, 'app'),
    staged = join(request.current_root, STAGED_APP_PREFIX + to_sha),
    previous = join(request.current_root, PREVIOUS_APP_PREFIX + from_sha);
  const backup = attempt.backup,
    restore_data = new_app_moved && typeof backup === 'string';
  try {
    if (previous_moved || new_app_moved) {
      signal.throwIfAborted();
      await effects.service('stop');
      signal.throwIfAborted();
      record.record('rollback_service_stopped');
    }
    if (new_app_moved) move(app, staged, signal);
    if (previous_moved) move(previous, app, signal);
    record.record('rollback_app_restored');
    if (restore_data) {
      signal.throwIfAborted();
      await effects.restore(backup);
      signal.throwIfAborted();
      record.record('rollback_database_restored');
    }
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
      'failed',
      'failed',
      `${reason}. Panels could not go back to the previous version: ${problem(error)}.`,
    );
    return 1;
  }
  record.record('rollback_healthy');
  record.record(
    'failed',
    'failed',
    `${reason}, so Panels went back to ${restore_data ? 'the previous version and its data' : 'the previous version'}.`,
  );
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
  const record = new InstallationRecord(request),
    attempt = record.claim();
  if (attempt === null) {
    io.stderr('This update attempt is not waiting to install; nothing was changed.\n');
    return 2;
  }
  const to_sha = field(attempt, 'to_sha'),
    from_sha = field(attempt, 'from_sha');
  const app = join(request.current_root, 'app'),
    staged = join(request.current_root, STAGED_APP_PREFIX + to_sha),
    previous = join(request.current_root, PREVIOUS_APP_PREFIX + from_sha);
  if (!directory(staged)) {
    record.record(
      'failed',
      'failed',
      'The update could not start because the downloaded version is missing. The current version kept running.',
    );
    return 1;
  }
  if (installed_sha(app) !== from_sha) {
    record.record(
      'failed',
      'failed',
      'The update could not start because the installed version is not the one it expected. The current version kept running.',
    );
    return 1;
  }
  assert_installer_path(previous);
  if (existsSync(previous)) rmtree(previous);
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
    move(app, previous, signal);
    previous_moved = true;
    move(staged, app, signal);
    new_app_moved = true;
    record.record('app_swapped');
    signal.throwIfAborted();
    await effects.service('start');
    signal.throwIfAborted();
    record.record('service_started');
    signal.throwIfAborted();
    const healthy = await effects.healthy(to_sha);
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
    );
  }
  record.record('healthy');
  record.record('done', 'done');
  assert_installer_path(request.current_root);
  for (const folder of readdirSync(request.current_root)) {
    const path = join(request.current_root, folder);
    if (folder.startsWith(PREVIOUS_APP_PREFIX) && path !== previous) rmtree(path, true);
  }
  rmtree(join(request.current_root, 'app-previous'), true);
  return 0;
}
