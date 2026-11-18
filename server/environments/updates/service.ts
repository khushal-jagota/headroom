/** The server launches the installer at once. The installer owns the stop and the replacement. */
import { randomUUID } from 'node:crypto';
import { lstatSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { resolved_path } from '../paths.ts';
import type { UpdateBarrier } from '../update_barrier.ts';
import type { ChangeSignal } from '../../core/changes/index.ts';
import type { StopOptions } from '../../lifecycle/contracts.ts';
import { observe_shutdown } from '../../conversation/shutdown.ts';
import { build_worker_runtime_definitions } from '../../worker_types/configuration.ts';
import { read_definitions } from '../../worker_types/store.ts';
import { audit_ticket_registry_integrity } from '../../tickets/data/index.ts';
import { FileUpdateAttemptStore, UpdateConflict, utc_now } from './attempts.ts';
import { add_seconds, instant, resolve, settle } from './logic/experience.ts';
import {
  ATTEMPT_OBSERVER_SECONDS,
  MAC_UPDATE_REPORT_SECONDS,
  STAGED_APP_PREFIX,
  UPDATE_AVAILABILITY_REFRESH_SECONDS,
  UPDATE_HEALTH_TIMEOUT_SECONDS,
} from './configuration.ts';
import { install_deadline_seconds } from './logic/install_deadline.ts';
import type {
  InstallerOperations,
  MacUpdateOperations,
  PublishedUpdate,
  ReleaseStaging,
  UpdateAttempt,
  UpdateAttemptState,
  UpdateProvider,
  UpdateRuntimeContext,
  UpdateStatus,
} from './contracts.ts';

export const RESTARTED_DETAIL = 'Panels restarted before the update began, so nothing changed.';
const states = (...values: UpdateAttemptState[]): ReadonlySet<UpdateAttemptState> =>
  new Set(values);
const epoch = instant('1970-01-01T00:00:00+00:00');
const timestamp = (seconds: number): string => add_seconds(epoch, seconds).iso;

export interface UpdateServiceOptions {
  provider: UpdateProvider | null;
  attempts: FileUpdateAttemptStore;
  runtime: UpdateRuntimeContext;
  changes: Pick<ChangeSignal, 'emit'>;
  current_root: string;
  database: string;
  backup_directory: string;
  current_app_sha: string | null;
  health_url: string;
  service_unit: string;
  staging: ReleaseStaging;
  installer: InstallerOperations;
  mac: MacUpdateOperations;
  update_barrier?: UpdateBarrier;
  /** Tells the owner about a failed update. It must not throw. */
  notify_failure?: (attempt: UpdateAttempt) => void;
  can_refresh?: () => boolean;
  now?: () => number;
  record_now?: () => string;
}
export function staged_app_path(current_root: string, app_sha: string): string {
  return join(current_root, STAGED_APP_PREFIX + app_sha);
}
/** Credentials alone never let a source checkout install an update. */
export async function installed_update_root(
  app_sha: string | null,
  app_root: string,
  database: string,
  own_service_unit: string | null,
): Promise<string | null> {
  if (!app_sha || !own_service_unit) return null;
  const [canonical_database, canonical_app] = await Promise.all([
    resolved_path(database),
    resolved_path(app_root),
  ]);
  const current_root = dirname(dirname(canonical_database));
  return canonical_app === join(current_root, 'app') ? current_root : null;
}
export function validate_release(release: PublishedUpdate): void {
  if (release.manifest.runtime_contract === null)
    throw new UpdateConflict('This publication lacks a runtime contract. Republish the app.');
  // Publication retention is a real-world boundary, independent of a service test clock.
  let expired = false;
  try {
    const expiry = instant(release.expires_at);
    expired = expiry.epochMicroseconds <= BigInt(Date.now()) * 1000n;
  } catch (error) {
    if (!(error instanceof Error) || error.message !== 'update timestamp requires a time zone')
      throw error;
    expired = true;
  }
  if (expired)
    throw new UpdateConflict('The pinned publication expired. Select a retained publication.');
}
/** The database file and its WAL, which a backup or a restore copies. */
function database_file_bytes(path: string): number {
  let total = 0;
  for (const file of [path, path + '-wal'])
    total += statSync(file, { throwIfNoEntry: false })?.size ?? 0;
  return total;
}
function signature(path: string): string | null {
  try {
    const entry = lstatSync(path, { bigint: true });
    return [entry.dev, entry.ino, entry.size, entry.mtimeNs].join(':');
  } catch (error) {
    if (
      error instanceof Error &&
      'code' in error &&
      ['ENOENT', 'ENOTDIR'].includes(String(error.code))
    )
      return null;
    return '-1';
  }
}
export class UpdateService {
  readonly attempts: FileUpdateAttemptStore;
  readonly current_app_sha: string | null;
  private readonly options: UpdateServiceOptions;
  private readonly now: () => number;
  private readonly record_now: () => string;
  private ready: PublishedUpdate | null = null;
  private last_checked = 0;
  private last_state: string | null = null;
  private running = false;
  private incarnation = 0;
  private prefetching: Promise<void> | null = null;
  private advancing: Promise<void> | null = null;
  private readonly work = new Set<Promise<void>>();
  private stopping: Promise<void> | undefined;
  private shutdown_settlement: Promise<void> | undefined;
  private observer: NodeJS.Timeout | null = null;
  private preparation_attempt_id: string | null = null;
  private installer_starting: { attempt_id: string; until: number } | null = null;
  constructor(options: UpdateServiceOptions) {
    this.options = options;
    this.attempts = options.attempts;
    this.current_app_sha = options.current_app_sha;
    this.now = options.now ?? (() => Date.now() / 1000);
    this.record_now = options.record_now ?? utc_now;
  }
  owns_preparation(attempt_id: string): boolean {
    return (
      (this.preparation_attempt_id === attempt_id && this.advancing !== null) ||
      (this.installer_starting?.attempt_id === attempt_id &&
        this.now() < this.installer_starting.until)
    );
  }
  installer_observed(attempt_id: string): void {
    if (this.installer_starting?.attempt_id === attempt_id) this.installer_starting = null;
  }
  recover_orphaned_attempt(attempt_id: string, generation: number): number | null {
    const barrier = this.options.update_barrier;
    const current = this.attempts.read();
    if (
      barrier === undefined ||
      barrier.generation !== generation ||
      (current?.attempt_id ?? '') !== attempt_id ||
      this.owns_preparation(attempt_id)
    )
      return null;
    if (current !== null && !states('done', 'failed').has(current.state)) {
      this.attempts.change(attempt_id, states(current.state), (record) => ({
        ...record,
        state: 'failed',
        detail: states('waiting', 'preparing').has(record.state)
          ? RESTARTED_DETAIL
          : 'The previous update did not complete. Try again.',
      }));
    }
    const released = barrier.reopen_if_current(generation);
    if (released !== null) this.reopen_trial_work(attempt_id);
    return released;
  }
  reopen_trial_work(attempt_id: string): void {
    if (
      (this.attempts.read()?.attempt_id ?? '') !== attempt_id ||
      this.options.update_barrier?.frozen
    )
      return;
    this.options.runtime.conversation()?.reopen_after_update();
  }
  ready_release(): PublishedUpdate | null {
    if (this.ready === null || this.ready.manifest.app_sha === this.current_app_sha) return null;
    try {
      validate_release(this.ready);
    } catch (error) {
      if (error instanceof UpdateConflict) return null;
      throw error;
    }
    return this.ready;
  }
  refresh_available(options: { force?: boolean } = {}): void {
    if (
      !this.running ||
      this.options.provider === null ||
      !(this.options.can_refresh?.() ?? true) ||
      this.prefetching !== null
    )
      return;
    const current = this.attempts.read();
    if (current !== null && !states('done', 'failed').has(current.state)) return;
    if (!options.force && this.now() - this.last_checked < UPDATE_AVAILABILITY_REFRESH_SECONDS)
      return;
    this.last_checked = this.now();
    // Reserve the flight before a provider or commit callback can re-enter this door.
    void this.prefetch().catch((error: unknown) => {
      console.warn('Update prefetch did not complete; it retries later', error);
    });
  }
  prefetch(): Promise<void> {
    if (!this.running) return Promise.resolve();
    if (this.prefetching !== null) return this.prefetching;
    const incarnation = this.incarnation;
    const flight = Promise.resolve()
      .then(() => this.prefetch_release(incarnation))
      .finally(() => {
        if (this.prefetching === flight) this.prefetching = null;
        this.work.delete(flight);
      });
    this.prefetching = flight;
    this.work.add(flight);
    return flight;
  }
  private active(incarnation: number): boolean {
    return this.running && this.incarnation === incarnation;
  }
  private async prefetch_release(incarnation: number): Promise<void> {
    const provider = this.options.provider;
    if (!this.active(incarnation) || provider === null) return;
    const release = await provider.available();
    if (
      !this.active(incarnation) ||
      release === null ||
      release.manifest.app_sha === this.current_app_sha
    )
      return;
    validate_release(release);
    if (JSON.stringify(release) === JSON.stringify(this.ready)) return;
    const staged = await this.options.staging.stage(provider, release, this.options.current_root);
    if (!this.active(incarnation)) return;
    // Admission only. The install decides on the backup when it starts.
    await this.options.installer.node_schema_steps_pending(this.options.database, staged);
    if (!this.active(incarnation)) return;
    await this.options.mac.prepare(release);
    if (!this.active(incarnation)) return;
    this.ready = release;
    this.options.changes.emit();
  }
  private settled_attempt(): UpdateAttempt | null {
    const attempt = this.attempts.read();
    if (attempt === null) return null;
    if (this.options.update_barrier?.frozen) return attempt;
    const ended = settle(attempt, this.current_app_sha, timestamp(this.now()));
    if (ended === null) return attempt;
    this.options.runtime.assert_database_owned();
    try {
      return this.attempts.change(attempt.attempt_id, states('installing'), (current) => ({
        ...current,
        state: ended.state,
        detail: ended.detail,
        steps: { ...current.steps, settled: this.record_now() },
      }));
    } catch (error) {
      if (!(error instanceof UpdateConflict)) throw error;
      return this.attempts.read();
    }
  }
  status(): UpdateStatus {
    this.refresh_available();
    const attempt = this.running ? this.settled_attempt() : this.attempts.read();
    const system = this.options.runtime.conversation();
    const activity = system?.workspace_activity() ?? 'unknown';
    const available = this.ready_release();
    const reported = attempt?.mac_attempt_id
      ? this.options.mac.reported_at(attempt.mac_attempt_id)
      : null;
    const [state, allowed_actions, resolved_detail, deadline_at] = resolve(
      attempt,
      available,
      this.current_app_sha,
      {
        mac_applies: attempt?.release.mac != null && this.options.mac.configured(),
        mac_phase: attempt?.mac_attempt_id ? this.options.mac.phase(attempt.mac_attempt_id) : null,
        mac_reported_at: reported === null ? null : timestamp(reported),
        mac_report_seconds: MAC_UPDATE_REPORT_SECONDS,
        now: timestamp(this.now()),
      },
    );
    let detail = resolved_detail;
    // Information only. Running work never refuses or delays an update.
    const interrupted = state === 'available' ? (system?.update_interrupted_work() ?? []) : [];
    if (interrupted.length)
      detail = `The update interrupts this work, and Panels resumes it afterwards: ${interrupted.join(', ')}.`;
    return {
      state,
      allowed_actions,
      activity,
      available,
      attempt,
      app_sha: this.current_app_sha,
      detail,
      deadline_at,
    };
  }
  private release_barrier(): void {
    const barrier = this.options.update_barrier;
    if (barrier === undefined) return;
    barrier.remove(barrier.reopen());
  }
  private owns_attempt(attempt_id: string, state: UpdateAttemptState): boolean {
    const current = this.attempts.read();
    return current?.attempt_id === attempt_id && current.state === state;
  }
  private deadline(seconds: number): string {
    return timestamp(this.now() + seconds);
  }
  /** A press always starts the update. Running work is never asked. */
  install(app_sha: string): UpdateAttempt {
    this.require_running();
    const release = this.ready_release();
    if (release === null || release.manifest.app_sha !== app_sha)
      throw new UpdateConflict('This update is no longer ready. Refresh and try again.');
    this.options.runtime.assert_database_owned();
    const now = this.record_now();
    const attempt = this.attempts.create({
      attempt_id: randomUUID().replaceAll('-', ''),
      from_sha: this.current_app_sha ?? '',
      to_sha: app_sha,
      // Earlier releases read these two fields from the shared record.
      mode: 'now',
      confirm_activity: true,
      state: 'preparing',
      created_at: now,
      updated_at: now,
      release,
      detail: null,
      deadline_at: null,
      backup: null,
      snapshot_required: false,
      steps: { pressed: now },
      mac_attempt_id:
        release.mac === null && release.linux_executor == null
          ? null
          : randomUUID().replaceAll('-', ''),
      extra: {},
    });
    this.launch();
    return attempt;
  }
  retry(attempt_id: string): UpdateAttempt {
    this.require_running();
    const attempt = this.attempts.read();
    if (attempt === null || attempt.attempt_id !== attempt_id || attempt.state !== 'failed')
      throw new UpdateConflict('The update changed or already moved on.');
    return this.install(attempt.to_sha);
  }
  retry_mac(attempt_id: string): UpdateAttempt {
    this.require_running();
    this.options.runtime.assert_database_owned();
    return this.attempts.change(attempt_id, states('done'), (current) => ({
      ...current,
      mac_attempt_id: randomUUID().replaceAll('-', ''),
      steps: { ...current.steps, retry_mac: this.record_now() },
    }));
  }
  start(): void {
    if (this.running) return;
    this.stopping = this.shutdown_settlement = undefined;
    this.options.runtime.assert_database_owned();
    this.running = true;
    // An attempt that never reached its installer ended with the process that held it.
    const attempt = this.attempts.read();
    if (attempt !== null && states('waiting', 'preparing').has(attempt.state))
      this.fail(attempt.attempt_id, states('waiting', 'preparing'), RESTARTED_DETAIL);
    this.report_failure();
    let previous = signature(this.attempts.path);
    this.observer = setInterval(() => {
      if (!this.running) return;
      const current = signature(this.attempts.path);
      if (current !== previous) {
        previous = current;
        this.report_failure();
        this.options.changes.emit();
      }
    }, ATTEMPT_OBSERVER_SECONDS * 1000);
    this.observer.unref();
  }
  settled(): Promise<void> {
    return this.shutdown_settlement ?? Promise.allSettled([...this.work]).then(() => {});
  }
  stop(options: StopOptions): Promise<void> {
    if (this.stopping) return this.stopping;
    const deadline = options.deadline;
    this.running = false;
    this.incarnation++;
    if (this.observer !== null) clearInterval(this.observer);
    this.observer = null;
    this.shutdown_settlement = Promise.allSettled([...this.work]).then(() => {});
    this.prefetching = this.advancing = null;
    this.stopping = observe_shutdown(
      this.shutdown_settlement,
      { deadline },
      'Shutdown deadline expired; ownership retained: updates',
      ['updates'],
    );
    return this.stopping;
  }
  poll(): boolean {
    if (!this.running) return false;
    const status = this.status();
    if (status.state !== this.last_state) {
      this.last_state = status.state;
      this.options.changes.emit();
    }
    const attempt = status.attempt;
    return attempt !== null && states('preparing', 'installing').has(attempt.state);
  }
  private launch(): void {
    const incarnation = this.incarnation;
    const flight = Promise.resolve()
      .then(() => this.launch_installer(incarnation))
      .catch((error: unknown) => console.error('Update launch did not complete', error))
      .finally(() => {
        if (this.advancing === flight) this.advancing = null;
        this.work.delete(flight);
      });
    this.advancing = flight;
    this.work.add(flight);
  }
  private async launch_installer(incarnation: number): Promise<void> {
    if (!this.active(incarnation)) return;
    const attempt = this.attempts.read();
    if (attempt?.state !== 'preparing') return;
    const attempt_id = (this.preparation_attempt_id = attempt.attempt_id);
    let barrier_closed = false;
    try {
      this.options.runtime.assert_database_owned();
      try {
        this.options.runtime.database.write((tx) => {
          build_worker_runtime_definitions(
            read_definitions(tx),
            new Set(
              tx
                .all<{ skill_name: string }>(
                  'SELECT skill_name FROM managed_skills ORDER BY skill_name',
                )
                .map((row) => row.skill_name),
            ),
          );
          audit_ticket_registry_integrity(tx);
        });
      } catch (error) {
        this.fail(
          attempt_id,
          states('preparing'),
          'The Ticket integrity check refused the update before Panels stopped. Resolve the saved Ticket or Worker type error, then try again: ' +
            String(error instanceof Error ? error.message : error),
        );
        return;
      }
      const staged = staged_app_path(this.options.current_root, attempt.to_sha);
      if (!statSync(staged, { throwIfNoEntry: false })?.isDirectory())
        throw new Error('the staged release is missing');
      const database_bytes = database_file_bytes(this.options.database);
      const steps_pending = await this.options.installer.node_schema_steps_pending(
        this.options.database,
        staged,
      );
      if (!this.active(incarnation)) return;
      // Pending steps need a snapshot. A Mac home takes one on every trial.
      // The installer takes it after it stops the service, when no writer remains.
      const snapshot_required = steps_pending || this.options.installer.requires_snapshot === true;
      const command = this.options.installer.prepare({
        current_root: this.options.current_root,
        database: this.options.database,
        data_directory: dirname(this.options.database),
        backup_directory: this.options.backup_directory,
        attempt_id,
        service_unit: this.options.service_unit,
        health_url: this.options.health_url,
        health_timeout_seconds: UPDATE_HEALTH_TIMEOUT_SECONDS,
      });
      if (!this.active(incarnation) || !this.owns_attempt(attempt_id, 'preparing')) return;
      // Everything that can refuse ran first, so running work meets closed writes only
      // for the moment before the stop. The trial app starts behind this marker, and
      // the installer removes it at its end.
      const barrier = this.options.update_barrier;
      if (barrier !== undefined) {
        barrier_closed = true;
        barrier.persist(attempt_id, barrier.close());
      }
      this.attempts.change(attempt_id, states('preparing'), (current) => ({
        ...current,
        state: 'installing',
        detail: null,
        snapshot_required,
        deadline_at: this.deadline(
          install_deadline_seconds({
            backup: snapshot_required,
            steps: steps_pending,
            database_bytes,
          }),
        ),
        steps: { ...current.steps, installing: this.record_now() },
      }));
      await this.options.installer.launch(attempt_id, command);
      this.installer_starting = { attempt_id, until: this.now() + 5 };
    } catch (error) {
      if (
        !this.owns_attempt(attempt_id, 'installing') &&
        !this.owns_attempt(attempt_id, 'preparing')
      )
        return;
      console.error('Update did not start its installer', error);
      this.fail(
        attempt_id,
        states('preparing', 'installing'),
        `The update could not start, so nothing changed: ${error instanceof Error ? error.message : String(error)}`,
      );
      if (barrier_closed) this.release_barrier();
    }
  }
  /** Every failed update reaches the journal and the owner once. */
  private report_failure(): void {
    try {
      const attempt = this.attempts.read();
      if (attempt?.state !== 'failed' || attempt.steps.failure_reported !== undefined) return;
      this.attempts.change(attempt.attempt_id, states('failed'), (current) => ({
        ...current,
        steps: { ...current.steps, failure_reported: this.record_now() },
      }));
      console.error(
        `Update to ${attempt.to_sha} failed: ${attempt.detail ?? 'no reason was recorded'}`,
      );
      this.options.notify_failure?.(attempt);
    } catch (error) {
      // The next change to the record tries again. A report never stops the server.
      if (!(error instanceof UpdateConflict))
        console.error('The update failure could not be reported', error);
    }
  }
  private fail(
    attempt_id: string,
    expected: ReadonlySet<UpdateAttemptState>,
    detail: string,
  ): void {
    this.options.runtime.assert_database_owned();
    try {
      this.attempts.change(attempt_id, expected, (current) => ({
        ...current,
        state: 'failed',
        detail,
      }));
    } catch (error) {
      if (!(error instanceof UpdateConflict)) throw error;
    }
    if (this.running) this.report_failure();
  }
  private require_running(): void {
    if (!this.running)
      throw new UpdateConflict('Panels is shutting down. Retry after it restarts.');
  }
}
