/** Startup fallback when the detached installer died and the OS restarted the home first. */
import { readFileSync, renameSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { restore_database_snapshot } from '../backup/restore.ts';
import { resolved_path } from '../paths.ts';
import { release_installer_barrier, with_installer_lock } from '../update_barrier.ts';
import { missing, read_record_json } from './attempts.ts';
import { is_object } from './codec.ts';
import { PREVIOUS_APP_PREFIX, STAGED_APP_PREFIX } from './configuration.ts';
import { assert_installer_path } from './installer/configuration.ts';
import { release_path, select_release, selected_release } from './installer/selection.ts';
import { InstallationRecord } from './installer/record.ts';

interface InterruptedAttempt {
  readonly attempt_id: string;
  readonly from_sha: string;
  readonly to_sha: string;
  readonly backup: string | null;
  readonly registered: boolean;
  readonly restored: boolean;
  readonly swapped: boolean;
  readonly data_restored: boolean;
  readonly snapshot_required: boolean;
}
/** An attempt still installing this app, with the swap recorded and no terminal state. */
function interrupted_attempt(data_directory: string, app_sha: string): InterruptedAttempt | null {
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
    (value.to_sha !== app_sha && value.from_sha !== app_sha) ||
    typeof value.to_sha !== 'string' ||
    !is_object(value.steps) ||
    !(
      Object.hasOwn(value.steps, 'installer_started') || Object.hasOwn(value.steps, 'app_swapped')
    ) ||
    typeof value.attempt_id !== 'string' ||
    typeof value.from_sha !== 'string' ||
    !(value.backup === null || value.backup === undefined || typeof value.backup === 'string')
  )
    return null;
  return {
    attempt_id: value.attempt_id,
    from_sha: value.from_sha,
    to_sha: value.to_sha,
    backup: value.backup ?? null,
    registered: Object.hasOwn(value.steps, 'coordinator_registered'),
    restored: Object.hasOwn(value.steps, 'rollback_app_restored'),
    swapped: Object.hasOwn(value.steps, 'app_swapped'),
    data_restored: Object.hasOwn(value.steps, 'rollback_database_restored'),
    snapshot_required: value.snapshot_required === true,
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
  const seen = interrupted_attempt(data_directory, app_sha);
  if (seen === null) return 'none';
  const owned = await with_installer_lock(data_directory, seen.attempt_id, async () => {
    const attempt = interrupted_attempt(data_directory, app_sha);
    if (attempt?.attempt_id !== seen.attempt_id) return 'none';
    // A schema step can commit before the trial starts. Only the installer's
    // durable terminal record admits new work, so an uncommitted candidate is undone.
    const record = new InstallationRecord({ data_directory, attempt_id: attempt.attempt_id });
    if (attempt.registered) {
      // A registered installer can resume late. Bind this fallback to its actual marker and selection.
      if (
        readFileSync(join(data_directory, 'update-barrier.json'), 'utf8').trim() !==
        attempt.attempt_id
      )
        throw new Error('Interrupted update marker belongs to a different attempt');
      if (selected_release(current_root) !== release_path(current_root, app_sha))
        throw new Error('Interrupted update cannot verify the selected home');
      const manifest = read_record_json(join(app, 'manifest.json'));
      if (!is_object(manifest) || manifest.app_sha !== app_sha)
        throw new Error('Interrupted update cannot verify the running release');
    }
    if (attempt.from_sha === app_sha) {
      // A snapshot never mutates the original database. On the return path, the
      // installer restores data before selecting this release.
      if (attempt.registered) {
        // The retained home must boot and pass its machine trial before ordinary writes resume.
        if (
          attempt.swapped &&
          (attempt.backup !== null || attempt.snapshot_required) &&
          !attempt.data_restored
        )
          throw new Error('The previous release is selected without its recorded database restore');
        if (!attempt.restored) record.record('rollback_app_restored');
        return 'none';
      }
      record.record(
        'failed',
        'failed',
        'The installer stopped before it committed the update. Panels retained the previous version.',
      );
      release_installer_barrier(data_directory, attempt.attempt_id);
      return 'none';
    }
    const pinned = selected_release(current_root) === release_path(current_root, app_sha);
    const previous = pinned
      ? release_path(current_root, attempt.from_sha)
      : join(current_root, PREVIOUS_APP_PREFIX + attempt.from_sha);
    const staged = join(current_root, STAGED_APP_PREFIX + attempt.to_sha);
    for (const path of [app, previous, staged]) assert_installer_path(path);
    if (
      !statSync(previous, { throwIfNoEntry: false })?.isDirectory() ||
      statSync(staged, { throwIfNoEntry: false }) !== undefined
    )
      throw new Error('Interrupted update cannot restore its retained previous release');
    if (attempt.registered) {
      const manifest = read_record_json(join(previous, 'manifest.json'));
      if (
        !is_object(manifest) ||
        manifest.app_sha !== attempt.from_sha ||
        (attempt.snapshot_required && !attempt.backup)
      )
        throw new Error('Interrupted update cannot verify its retained release and snapshot');
    }
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
    if (attempt.registered) return 'rolled_back';
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
