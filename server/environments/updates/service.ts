import { has_home_credential } from '../../machines/executor_credentials.ts';
import { require_snapshot_space } from './installer/snapshot_space.ts';
/** The server launches the installer at once. The installer owns the stop and the replacement. */
import { with_installer_lock } from '../update_barrier.ts';
import { selected_release } from './installer/selection.ts';
import { HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID } from '../../execution_identity.ts';
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
import { add_seconds, instant, resolve } from './logic/experience.ts';
import {
  ATTEMPT_OBSERVER_SECONDS,
  STAGED_APP_PREFIX,
  UPDATE_AVAILABILITY_REFRESH_SECONDS,
  UPDATE_HEALTH_TIMEOUT_SECONDS,
} from './configuration.ts';
import { install_deadline_seconds } from './logic/install_deadline.ts';
import type {
  InstallerOperations,
  MachineUpdateState,
  UpdateInterruption,
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
  /** A reason no update may start now, such as a running server move, or null. */
  install_refusal?: () => string | null;
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
  return canonical_app === selected_release(current_root) ? current_root : null;
}
export function validate_release(release: PublishedUpdate): void {
  if (release.manifest.runtime_contract === null)
    throw new UpdateConflict('This publication lacks a runtime contract. Republish the app.');
  if (release.expires_at === null) {
    if (release.artifact_source !== 'github-release')
      throw new UpdateConflict('Only GitHub Releases can have no expiry.');
    return;
  }
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
  private checked = false;
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
      this.owns_preparation(attempt_id) ||
      (current?.state === 'installing' &&
        (current.steps.installer_started !== undefined ||
          current.steps.coordinator_registered !== undefined)) ||
      current?.steps.rollback_failed !== undefined
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
  ready_screens_root(): string | null {
    const release = this.ready_release();
    return release === null
      ? null
      : staged_app_path(this.options.current_root, release.manifest.app_sha);
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
      .catch((error: unknown) => {
        if (this.active(incarnation)) {
          this.checked = false;
          this.ready = null;
          this.options.changes.emit();
        }
        throw error;
      })
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
    if (!this.active(incarnation)) return;
    const applied = this.attempts.read();
    // Earlier Darwin homes recorded the home archive but discarded its signed machine package.
    if (
      applied?.state === 'done' &&
      applied.to_sha === this.current_app_sha &&
      applied.release.manifest.runtime_contract?.system === 'darwin' &&
      applied.release.mac === null &&
      applied.release.parts?.machines['darwin-arm64']
    ) {
      const saved = applied.release;
      const published =
        release?.proof_artifact_id === saved.proof_artifact_id
          ? release
          : await provider.available(saved.proof_artifact_id);
      if (!this.active(incarnation)) return;
      if (
        published?.mac &&
        published.proof_artifact_id === saved.proof_artifact_id &&
        published.publication_run_id === saved.publication_run_id &&
        published.manifest.app_sha === applied.to_sha &&
        published.archive_sha256 === saved.archive_sha256 &&
        published.mac.archive_sha256 === saved.parts!.machines['darwin-arm64']!.archive_sha256 &&
        this.attempts.read()?.attempt_id === applied.attempt_id
      ) {
        const repaired = this.attempts.change(applied.attempt_id, states('done'), (current) => ({
          ...current,
          mac_attempt_id: current.mac_attempt_id ?? current.attempt_id,
          release: { ...current.release, mac: published.mac },
        }));
        await this.options.mac.prepare(repaired.release);
        if (!this.active(incarnation)) return;
      }
    }
    if (release === null || release.manifest.app_sha === this.current_app_sha) {
      this.ready = null;
      this.checked = true;
      this.options.changes.emit();
      return;
    }
    validate_release(release);
    if (JSON.stringify(release) === JSON.stringify(this.ready)) return;
    const staged = await this.options.staging.stage(provider, release, this.options.current_root);
    if (!this.active(incarnation)) return;
    // Admission only. The install decides on the backup when it starts.
    await this.options.installer.node_schema_steps_pending(this.options.database, staged);
    if (!this.active(incarnation)) return;
    this.checked = true;
    this.ready = release;
    this.options.changes.emit();
    try {
      await this.options.mac.prepare(release);
    } catch (error) {
      if (this.active(incarnation))
        console.warn('Machine archive preparation did not complete', error);
    }
  }
  status(): UpdateStatus {
    this.refresh_available();
    const attempt = this.attempts.read();
    const system = this.options.runtime.conversation();
    const activity = system?.workspace_activity() ?? 'unknown';
    const available = this.ready_release();
    const machines = this.options.mac.machine_states();
    const available_machines = available === null ? [] : this.options.mac.machine_states(available);
    const interruption =
      available === null
        ? { stops_agents: false, agents: [], machine_ids: [] }
        : this.interruption(available_machines);
    const [state, allowed_actions, resolved_detail, deadline_at] = resolve(attempt, available);
    let detail = resolved_detail;
    // Information only. Running work never refuses or delays an update.
    const interrupted = state === 'available' ? interruption.agents : [];
    if (interrupted.length)
      detail = `The update interrupts this work, and Panels resumes it afterwards: ${interrupted.join(', ')}.`;
    return {
      machines,
      available_machines,
      interruption,
      state: state === 'up_to_date' && !this.checked ? 'check_unavailable' : state,
      allowed_actions,
      activity,
      available,
      attempt,
      app_sha: this.current_app_sha,
      detail,
      deadline_at,
    };
  }
  private interruption(machines: readonly MachineUpdateState[]): UpdateInterruption {
    // Until the server and its machine run separately, a server restart stops home work.
    const machine_ids = [
      ...new Set([
        HOME_MACHINE_ID,
        ...machines.filter((machine) => machine.changed).map((machine) => machine.machine_id),
      ]),
    ];
    const agents = this.options.runtime.conversation()?.update_interrupted_work(machine_ids) ?? [];
    return { stops_agents: agents.length > 0, agents, machine_ids };
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
    const pending = this.attempts.read();
    if (pending?.to_sha === app_sha && states('preparing', 'installing').has(pending.state))
      return pending;
    if (pending?.steps.rollback_failed !== undefined && this.options.update_barrier?.frozen)
      throw new UpdateConflict(
        'Finish recovering the interrupted installation before starting another update.',
      );
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
      extra: { interruption: this.interruption(this.options.mac.machine_states(release)) },
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
  retry_machine(
    machine_id: string,
    attempt_id: string,
  ): { attempt: UpdateAttempt; machine: MachineUpdateState } {
    this.require_running();
    this.options.runtime.assert_database_owned();
    this.options.mac.retry_machine(machine_id, attempt_id);
    const attempt = this.attempts.read();
    const machine = this.options.mac
      .machine_states()
      .find((value) => value.machine_id === machine_id);
    if (attempt === null || machine === undefined)
      throw new UpdateConflict('The machine update changed or already moved on.');
    return { attempt, machine };
  }
  retry_mac(attempt_id: string): UpdateAttempt {
    return this.retry_machine(ORIGINAL_MAC_MACHINE_ID, attempt_id).attempt;
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
    if (
      attempt?.state === 'preparing' &&
      this.advancing === null &&
      !this.options.runtime.conversation()?.has_pending_update_sends()
    )
      this.launch();
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
      await this.options.mac.prepare_home?.(attempt.release);
      if (!this.active(incarnation)) return;
      const database_bytes = database_file_bytes(this.options.database);
      const steps_pending = await this.options.installer.node_schema_steps_pending(
        this.options.database,
        staged,
      );
      if (!this.active(incarnation)) return;
      // Pending steps need a snapshot. A Mac home takes one on every trial.
      // The installer takes it after it stops the service, when no writer remains.
      const snapshot_required =
        steps_pending ||
        this.options.installer.requires_snapshot === true ||
        !this.options.runtime.database.read(has_home_credential);
      if (snapshot_required)
        await require_snapshot_space(this.options.database, this.options.backup_directory);
      if (!this.active(incarnation) || !this.owns_attempt(attempt_id, 'preparing')) return;
      if (this.options.runtime.conversation()?.has_pending_update_sends()) return;
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
      const fail_before_start = () => {
        if (this.attempts.read()?.steps.installer_started !== undefined) return;
        console.error('Update did not start its installer', error);
        this.fail(
          attempt_id,
          states('preparing', 'installing'),
          `The update could not start, so nothing changed: ${error instanceof Error ? error.message : String(error)}`,
        );
        if (barrier_closed) this.release_barrier();
      };
      // The OS can start the unit before its client disconnects or times out.
      // Serialize the last check with the installer's claim before reopening.
      if (barrier_closed)
        await with_installer_lock(dirname(this.options.database), attempt_id, async () =>
          fail_before_start(),
        );
      else fail_before_start();
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
    const refusal = this.options.install_refusal?.() ?? null;
    if (refusal !== null) throw new UpdateConflict(refusal);
  }
}
