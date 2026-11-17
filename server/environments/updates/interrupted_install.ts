/**
 * Before admission: settle an install whose installer stopped after the app swap.
 * The database's schema revision decides, because the attempt file cannot say whether
 * the last step committed.
 */
import { renameSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { application_schema_seed, read_schema_revisions } from '../../core/database/admission.ts';
import { PYTHON_BASELINE_SEED } from '../../core/database/schema_steps/baseline.ts';
import { SCHEMA_STEPS, type SchemaStep } from '../../core/database/schema_steps/contracts.ts';
import { restore_database_snapshot } from '../backup/restore.ts';
import { resolved_path } from '../paths.ts';
import { release_installer_barrier, with_installer_lock } from '../update_barrier.ts';
import { missing } from './attempts.ts';
import { is_object } from './codec.ts';
import { PREVIOUS_APP_PREFIX, STAGED_APP_PREFIX } from './configuration.ts';
import { assert_installer_path } from './installer/configuration.ts';
import { InstallationRecord } from './installer/record.ts';

/** The revisions this app's steps start from, and the one it admits. */
export interface SchemaChain {
  readonly head_revision: string;
  readonly earlier_revisions: ReadonlySet<string>;
}
export function schema_chain(
  steps: readonly SchemaStep[],
  baseline_revision: string,
  head_revision: string,
): SchemaChain {
  const earlier = new Set([baseline_revision, ...steps.map((step) => step.previous_revision)]);
  earlier.delete(head_revision);
  return { head_revision, earlier_revisions: earlier };
}
const APPLICATION_CHAIN = schema_chain(
  SCHEMA_STEPS,
  PYTHON_BASELINE_SEED.alembicRevision,
  application_schema_seed.alembicRevision,
);

/**
 * The one rule. At this app's head the steps committed, so the new app stays. Earlier on
 * its chain they did not all commit, so the previous app and the backup come back. Any
 * other revision is left for admission to refuse.
 */
export function interrupted_install_decision(
  revisions: readonly string[] | null,
  chain: SchemaChain,
): 'keep_new_app' | 'roll_back' | 'leave' {
  if (revisions?.length !== 1) return 'leave';
  const revision = revisions[0]!;
  if (revision === chain.head_revision) return 'keep_new_app';
  return chain.earlier_revisions.has(revision) ? 'roll_back' : 'leave';
}

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
    !Object.hasOwn(value.steps, 'app_swapped') ||
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
  readonly chain?: SchemaChain;
}
/**
 * Runs in the new app before admission, the one place every start of it passes. Holds the
 * installer lock, so it never races a live installer. A rollback repeats safely: the
 * restore comes first, then the two renames, then the terminal record. Keeping the new
 * app settles the attempt as done, as the deadline would.
 */
export async function recover_interrupted_install(
  inputs: InterruptedInstallInputs,
): Promise<'none' | 'kept_new_app' | 'rolled_back'> {
  const app_sha = inputs.app_sha;
  if (!app_sha) return 'none';
  const database = await resolved_path(inputs.database);
  const data_directory = dirname(database),
    current_root = dirname(data_directory),
    app = join(current_root, 'app');
  if ((await resolved_path(inputs.app_root)) !== app) return 'none';
  const seen = swapped_attempt(data_directory, app_sha);
  if (seen === null) return 'none';
  const owned = await with_installer_lock(data_directory, seen.attempt_id, async () => {
    const attempt = swapped_attempt(data_directory, app_sha);
    if (attempt?.attempt_id !== seen.attempt_id) return 'none';
    let revisions: string[] | null | undefined;
    try {
      revisions = read_schema_revisions(database);
    } catch {
      revisions = undefined; // Admission reports an unreadable database.
    }
    const decision =
      revisions === undefined
        ? 'leave'
        : interrupted_install_decision(revisions, inputs.chain ?? APPLICATION_CHAIN);
    // The journal's record of why this start kept, undid, or left an update.
    console.warn(
      `Interrupted update ${attempt.attempt_id}: database revision ${revisions === undefined ? '<unreadable>' : JSON.stringify(revisions)}; decision ${decision}`,
    );
    if (decision === 'leave') return 'none';
    const record = new InstallationRecord({ data_directory, attempt_id: attempt.attempt_id });
    if (decision === 'keep_new_app') {
      // As settle() would at the deadline: this version is the one installed.
      record.record(
        'done',
        'done',
        'The installer stopped after the new version was in place; Panels kept it.',
      );
      release_installer_barrier(data_directory, attempt.attempt_id);
      return 'kept_new_app';
    }
    const previous = join(current_root, PREVIOUS_APP_PREFIX + attempt.from_sha),
      staged = join(current_root, STAGED_APP_PREFIX + attempt.to_sha);
    for (const path of [app, previous, staged]) assert_installer_path(path);
    if (
      statSync(previous, { throwIfNoEntry: false })?.isDirectory() !== true ||
      statSync(staged, { throwIfNoEntry: false }) !== undefined
    )
      throw new Error(
        `An interrupted update left the database behind this version, but ${previous} is missing or ${staged} is in the way`,
      );
    if (attempt.backup !== null) {
      await restore_database_snapshot(attempt.backup, database, { live_stopped: true });
      record.record('rollback_database_restored');
    }
    renameSync(app, staged);
    renameSync(previous, app);
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
