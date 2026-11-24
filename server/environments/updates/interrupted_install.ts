/** Startup fallback when the detached installer died and the OS restarted the home first. */
import { renameSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { restore_database_snapshot } from '../backup/restore.ts';
import { resolved_path } from '../paths.ts';
import { release_installer_barrier, with_installer_lock } from '../update_barrier.ts';
import { missing } from './attempts.ts';
import { is_object } from './codec.ts';
import { PREVIOUS_APP_PREFIX, STAGED_APP_PREFIX } from './configuration.ts';
import { assert_installer_path } from './installer/configuration.ts';
import { release_path, select_release, selected_release } from './installer/selection.ts';
import { InstallationRecord } from './installer/record.ts';

interface SwappedAttempt {
  readonly attempt_id: string;
  readonly from_sha: string;
  readonly to_sha: string;
  readonly backup: string | null;
}
/** An attempt still installing this app, with the swap recorded and no terminal state. */
function swapped_attempt(data_directory: string, app_sha: string): SwappedAttempt | null {
  let value: Record<string, unknown>;
  try {
    // The installer's own strict reader. Reading needs no attempt id.
    value = new InstallationRecord({ data_directory, attempt_id: '' }).read();
  } catch (error) {
    if (missing(error)) return null;
    throw error;
  }
  if (
    value.state !== 'installing' ||
    value.to_sha !== app_sha ||
    !is_object(value.steps) ||
    !(Object.hasOwn(value.steps, 'app_swapped') || Object.hasOwn(value.steps, 'selecting')) ||
    typeof value.attempt_id !== 'string' ||
    typeof value.from_sha !== 'string' ||
    !(value.backup === null || value.backup === undefined || typeof value.backup === 'string')
  )
    return null;
  return {
    attempt_id: value.attempt_id,
    from_sha: value.from_sha,
    to_sha: app_sha,
    backup: value.backup ?? null,
  };
}

export interface InterruptedInstallInputs {
  readonly database: string;
  /** The app this process runs from. Recovery acts only when it is the installed `app`. */
  readonly app_root: string;
  readonly app_sha: string | null;
}
/** Recovery never promotes a trial. A live installer retains its lock and decides. */
export async function recover_interrupted_install(
  inputs: InterruptedInstallInputs,
): Promise<'none' | 'rolled_back'> {
  const app_sha = inputs.app_sha;
  if (!app_sha) return 'none';
  const database = await resolved_path(inputs.database);
  const data_directory = dirname(database),
    current_root = dirname(data_directory),
    app = join(current_root, 'app');
  if ((await resolved_path(inputs.app_root)) !== selected_release(current_root)) return 'none';
  const seen = swapped_attempt(data_directory, app_sha);
  if (seen === null) return 'none';
  const owned = await with_installer_lock(data_directory, seen.attempt_id, async () => {
    const attempt = swapped_attempt(data_directory, app_sha);
    if (attempt?.attempt_id !== seen.attempt_id) return 'none';
    // A schema step can commit before the trial starts. Only the installer's
    // durable terminal record admits new work, so an uncommitted candidate is undone.
    const record = new InstallationRecord({ data_directory, attempt_id: attempt.attempt_id });
    const pinned = selected_release(current_root) === release_path(current_root, app_sha);
    const previous = pinned ? release_path(current_root, attempt.from_sha)
      : join(current_root, PREVIOUS_APP_PREFIX + attempt.from_sha);
    const staged = join(current_root, STAGED_APP_PREFIX + attempt.to_sha);
    for (const path of [app, previous, staged]) assert_installer_path(path);
    if (!statSync(previous, { throwIfNoEntry: false })?.isDirectory() ||
        statSync(staged, { throwIfNoEntry: false }) !== undefined)
      throw new Error('Interrupted update cannot restore its retained previous release');
    if (attempt.backup !== null) {
      await restore_database_snapshot(attempt.backup, database, { live_stopped: true });
      record.record('rollback_database_restored');
    }
    if (pinned) {
      select_release(current_root, attempt.from_sha);
      renameSync(release_path(current_root, app_sha), staged);
    } else {
      // Pre-bridge attempts used physical directories.
      renameSync(app, staged);
      renameSync(previous, app);
    }
    record.record('rollback_app_restored');
    record.record(
      'failed',
      'failed',
      `The update stopped before the new version could start, so Panels went back to ${attempt.backup === null ? 'the previous version' : 'the previous version and its data'}.`,
    );
    release_installer_barrier(data_directory, attempt.attempt_id);
    return 'rolled_back';
  });
  return owned.acquired ? owned.result : 'none';
}
