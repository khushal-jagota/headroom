/** The server owns waiting, admission and launch. The installer owns replacement. */
import { randomUUID } from 'node:crypto';
import { lstatSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { resolved_path } from '../paths.ts';
import type { UpdateBarrier } from '../update_barrier.ts';
import { observe_promise } from '../../core/promise_observer.ts';
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
  SUPPORTED_UPDATE_SCHEMA_FLOOR,
  UPDATE_AVAILABILITY_REFRESH_SECONDS,
  UPDATE_HEALTH_TIMEOUT_SECONDS,
  UPDATE_DRAIN_SECONDS,
} from './configuration.ts';
import { backup_detail, install_deadline_seconds } from './logic/install_deadline.ts';
import type {
  InstallerOperations,
  MacUpdateOperations,
  PublishedUpdate,
  ReleaseStaging,
  UpdateAttempt,
  UpdateAttemptState,
  UpdateMode,
  UpdateProvider,
  UpdateRuntimeContext,
  UpdateStatus,
} from './contracts.ts';

export const ACTIVITY_CHANGED_DETAIL =
  'Work started before the update began. Confirm active work to update now.';
export const WAIT_DETAIL =
  'Work still waits for a safe point after five minutes. ' +
  'Leave the update pending, cancel it, or choose Update now.';
export const STOP_TIMEOUT_DETAIL =
  'Work did not stop safely in five minutes. The update remains paused.';
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
  if (release.schema_floor !== SUPPORTED_UPDATE_SCHEMA_FLOOR)
    throw new UpdateConflict('This publication requires an unsupported schema floor.');
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
  private paused_attempt_id: string | null = null;
  private running = false;
  private incarnation = 0;
  private prefetching: Promise<void> | null = null;
  private advancing: Promise<void> | null = null;
  private readonly work = new Set<Promise<void>>();
  private stopping: Promise<void> | undefined;
  private shutdown_settlement: Promise<void> | undefined;
  private observer: NodeJS.Timeout | null = null;
  private waiting_for_work = false;
  private advance_requested = false;
  private unsubscribe_work: (() => void) | null = null;
  private preparation_generation = 0;
  private update_wait: AbortController | null = null;
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
    if (
      (this.preparation_attempt_id === attempt_id && this.advancing !== null) ||
      (this.installer_starting?.attempt_id === attempt_id &&
        this.now() < this.installer_starting.until)
    )
      return true;
    if (this.paused_attempt_id === attempt_id && this.options.update_barrier?.frozen) {
      const current = this.attempts.read();
      return current?.attempt_id === attempt_id && current.state === 'waiting';
    }
    return false;
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
      (this.paused_attempt_id !== null && this.paused_attempt_id !== attempt_id)
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
      (this.paused_attempt_id !== null && this.paused_attempt_id !== attempt_id) ||
      this.options.update_barrier?.frozen
    )
      return;
    this.options.runtime.conversation()?.cancel_update();
    this.paused_attempt_id = null;
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
    if (release.manifest.runtime_contract?.version === 2)
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
    if (this.running) this.reopen_after_unstarted(attempt);
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
    const waiting_work =
      state === 'available' || state === 'waiting' ? (system?.update_waiting_work() ?? []) : [];
    if (state === 'waiting' && detail === null)
      detail = 'New work waits. Panels saves admitted work before the update.';
    if (waiting_work.length) {
      const names = waiting_work.join(', ');
      detail =
        state === 'available'
          ? `Update now stops this work: ${names}.`
          : `${detail ?? 'The update waits for a safe point.'} Work before the update: ${names}.`;
    }
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
  private reopen_after_unstarted(attempt: UpdateAttempt | null): void {
    if (
      this.paused_attempt_id === null ||
      (attempt?.attempt_id === this.paused_attempt_id &&
        states('waiting', 'preparing', 'installing').has(attempt.state))
    )
      return;
    this.paused_attempt_id = null;
    const system = this.options.runtime.conversation();
    if (system !== null) {
      system.cancel_update();
      this.options.changes.emit();
    }
    this.release_barrier();
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
  private drain_deadline(): string {
    return timestamp(this.now() + UPDATE_DRAIN_SECONDS);
  }
  private remaining_drain(attempt: UpdateAttempt): number {
    return attempt.deadline_at === null
      ? UPDATE_DRAIN_SECONDS * 1000
      : Math.max(
          1,
          Number(instant(attempt.deadline_at).epochMicroseconds) / 1000 - this.now() * 1000,
        );
  }
  private async observe_wait<T>(operation: (signal: AbortSignal) => Promise<T>, timeout: number) {
    const controller = new AbortController();
    this.update_wait = controller;
    try {
      return await observe_promise(operation(controller.signal), timeout);
    } finally {
      // End this waiter, not the retirement or save that its original owner retains.
      controller.abort();
      if (this.update_wait === controller) this.update_wait = null;
    }
  }
  private deadline(seconds: number): string {
    return timestamp(this.now() + seconds);
  }
  install(
    app_sha: string,
    mode: UpdateMode,
    options: { confirm_activity: boolean },
  ): UpdateAttempt {
    this.require_running();
    const release = this.ready_release();
    if (release === null || release.manifest.app_sha !== app_sha)
      throw new UpdateConflict('This update is no longer ready. Refresh and try again.');
    const activity = this.options.runtime.conversation()?.workspace_activity() ?? 'unknown';
    if (mode === 'now' && activity !== 'idle' && !options.confirm_activity)
      throw new UpdateConflict('Active or unknown work requires explicit confirmation.');
    this.options.runtime.assert_database_owned();
    const now = this.record_now();
    const attempt = this.attempts.create({
      attempt_id: randomUUID().replaceAll('-', ''),
      from_sha: this.current_app_sha ?? '',
      to_sha: app_sha,
      mode,
      confirm_activity: options.confirm_activity,
      state: 'waiting',
      created_at: now,
      updated_at: now,
      release,
      detail: null,
      deadline_at: this.drain_deadline(),
      backup: null,
      steps: { pressed: now },
      mac_attempt_id:
        release.mac === null && release.linux_executor == null
          ? null
          : randomUUID().replaceAll('-', ''),
      extra: {},
    });
    this.schedule_advance();
    return attempt;
  }
  cancel(attempt_id: string): void {
    this.require_running();
    this.options.runtime.assert_database_owned();
    this.attempts.remove(attempt_id, states('waiting', 'preparing'));
    this.preparation_generation++;
    this.update_wait?.abort();
    this.options.runtime.conversation()?.cancel_update();
    this.paused_attempt_id = null;
    this.release_barrier();
    this.waiting_for_work = false;
    this.advance_requested = false;
    this.advancing = null;
  }
  update_now(attempt_id: string, options: { confirm_activity: boolean }): UpdateAttempt {
    this.require_running();
    if (!options.confirm_activity)
      throw new UpdateConflict('Confirm active work before Update now.');
    this.options.runtime.assert_database_owned();
    const attempt = this.attempts.change(attempt_id, states('waiting'), (current) => ({
      ...current,
      mode: 'now',
      confirm_activity: true,
      detail: null,
      deadline_at: this.drain_deadline(),
    }));
    this.schedule_advance();
    return attempt;
  }
  retry(attempt_id: string): UpdateAttempt {
    this.require_running();
    const attempt = this.attempts.read();
    if (attempt === null || attempt.attempt_id !== attempt_id || attempt.state !== 'failed')
      throw new UpdateConflict('The update changed or already moved on.');
    return this.install(attempt.to_sha, 'now', { confirm_activity: true });
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
    this.unsubscribe_work =
      this.options.runtime.conversation()?.workspace_admission.subscribe_operations_finished(() => {
        if (!this.running || !this.waiting_for_work) return;
        this.waiting_for_work = false;
        queueMicrotask(() => {
          if (this.running) this.schedule_advance();
        });
      }) ?? null;
    const attempt = this.attempts.read();
    if (attempt?.state === 'preparing')
      this.fail(attempt.attempt_id, states('preparing'), RESTARTED_DETAIL);
    let previous = signature(this.attempts.path);
    this.observer = setInterval(() => {
      if (!this.running) return;
      const current = signature(this.attempts.path);
      if (current !== previous) {
        previous = current;
        this.options.changes.emit();
      }
    }, ATTEMPT_OBSERVER_SECONDS * 1000);
    this.observer.unref();
    this.schedule_advance();
  }
  settled(): Promise<void> {
    return this.shutdown_settlement ?? Promise.allSettled([...this.work]).then(() => {});
  }
  stop(options: StopOptions): Promise<void> {
    if (this.stopping) return this.stopping;
    const deadline = options.deadline;
    this.running = false;
    this.incarnation++;
    this.update_wait?.abort();
    this.waiting_for_work = false;
    this.advance_requested = false;
    this.unsubscribe_work?.();
    this.unsubscribe_work = null;
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
    if (attempt === null) return false;
    if (states('preparing', 'installing').has(attempt.state)) return true;
    if (attempt.state !== 'waiting') return false;
    this.schedule_advance();
    return true;
  }
  schedule_advance(): void {
    if (this.advancing !== null) {
      this.advance_requested = true;
      return;
    }
    void this.advance().catch((error: unknown) =>
      console.error('Update advance did not complete', error),
    );
  }
  advance(): Promise<void> {
    if (!this.running) return Promise.resolve();
    if (this.advancing !== null) return this.advancing;
    const incarnation = this.incarnation;
    const generation = this.preparation_generation;
    const flight = Promise.resolve()
      .then(() => this.advance_waiting(incarnation, generation))
      .finally(() => {
        if (this.advancing === flight) {
          this.advancing = null;
          if (this.advance_requested && this.active(incarnation)) {
            this.advance_requested = false;
            queueMicrotask(() => this.schedule_advance());
          }
        }
        this.work.delete(flight);
      });
    this.advancing = flight;
    this.work.add(flight);
    return flight;
  }
  private async advance_waiting(incarnation: number, generation: number): Promise<void> {
    const active = () => this.active(incarnation) && this.preparation_generation === generation;
    if (!active()) return;
    const attempt = this.attempts.read();
    const system = this.options.runtime.conversation();
    this.waiting_for_work = false;
    if (attempt?.state !== 'waiting' || system === null) return;
    const attempt_id = attempt.attempt_id;
    if (
      attempt.detail &&
      !(attempt.mode === 'idle' && attempt.deadline_at === null && attempt.detail === WAIT_DETAIL)
    )
      return;
    if (
      attempt.deadline_at !== null &&
      BigInt(Math.floor(this.now() * 1_000_000)) >= instant(attempt.deadline_at).epochMicroseconds
    ) {
      this.attempts.change(attempt_id, states('waiting'), (current) => ({
        ...current,
        detail: current.mode === 'idle' ? WAIT_DETAIL : STOP_TIMEOUT_DETAIL,
        deadline_at: null,
      }));
      return;
    }
    if (
      attempt.mode === 'now' &&
      !attempt.confirm_activity &&
      this.paused_attempt_id === null &&
      system.workspace_activity() !== 'idle'
    ) {
      this.fail(attempt_id, states('waiting'), ACTIVITY_CHANGED_DETAIL);
      return;
    }
    this.paused_attempt_id = this.preparation_attempt_id = attempt_id;
    this.waiting_for_work = true;
    try {
      // Enter synchronously: Cancel must not race a separately queued admission close.
      const prepared = await this.observe_wait(
        (signal) => system.prepare_update(attempt.confirm_activity ? attempt.mode : 'idle', signal),
        this.remaining_drain(attempt),
      );
      if (!active() || !this.owns_attempt(attempt_id, 'waiting')) return;
      if (!prepared.completed) {
        this.attempts.change(attempt_id, states('waiting'), (current) => ({
          ...current,
          detail: STOP_TIMEOUT_DETAIL,
        }));
        return;
      }
      if (!prepared.value) return;
    } catch (error) {
      if (!active() || !this.owns_attempt(attempt_id, 'waiting')) return;
      console.warn('Update safe-point check stopped', error);
      this.attempts.change(attempt_id, states('waiting'), (current) => ({
        ...current,
        detail: `Work could not reach a safe point: ${error instanceof Error ? error.message : String(error)}. The update remains paused.`,
      }));
      return;
    }
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
          states('waiting'),
          'The Ticket integrity check refused the update before Panels stopped. Resolve the saved Ticket or Worker type error, then try again: ' +
            String(error instanceof Error ? error.message : error),
        );
        throw new UpdateConflict('Ticket integrity refused the update.');
      }
      if (!active()) return;
      this.attempts.change(attempt_id, states('waiting'), (current) => ({
        ...current,
        state: 'preparing',
        steps: { ...current.steps, work_paused: this.record_now() },
      }));
    } catch (error) {
      if (!(error instanceof UpdateConflict)) throw error;
      this.waiting_for_work = false;
      this.reopen_after_unstarted(this.attempts.read());
      return;
    }
    this.waiting_for_work = false;
    try {
      const barrier = this.options.update_barrier;
      if (barrier !== undefined) {
        const barrier_generation = barrier.close();
        barrier.persist(attempt_id, barrier_generation);
        if (!active() || !this.owns_attempt(attempt_id, 'preparing')) return;
        const drained = await this.observe_wait(
          (signal) => barrier.drain(signal),
          this.remaining_drain(attempt),
        );
        if (!active() || !this.owns_attempt(attempt_id, 'preparing')) return;
        if (!drained.completed) {
          this.attempts.change(attempt_id, states('preparing'), (current) => ({
            ...current,
            state: 'waiting',
            detail:
              'Accepted result saves still wait. ' +
              'Choose Update now to retry after they finish, or cancel the update.',
          }));
          return;
        }
      }
      if (!active() || !this.owns_attempt(attempt_id, 'preparing')) return;
      // This claim seals Cancel before the first snapshot or installer effect.
      this.attempts.change(attempt_id, states('preparing'), (current) => ({
        ...current,
        state: 'installing',
        detail: null,
      }));
      await this.start_installer(attempt_id, incarnation);
    } catch (error) {
      if (
        !active() ||
        (!this.owns_attempt(attempt_id, 'installing') &&
          !this.owns_attempt(attempt_id, 'preparing'))
      )
        return;
      console.error('Update did not start its installer', error);
      this.fail(
        attempt_id,
        states('preparing', 'installing'),
        `The update could not start, so nothing changed: ${error instanceof Error ? error.message : String(error)}`,
      );
      this.reopen_after_unstarted(this.attempts.read());
    }
  }
  private async start_installer(attempt_id: string, incarnation: number): Promise<void> {
    if (!this.active(incarnation)) return;
    const attempt = this.attempts.read();
    if (attempt?.attempt_id !== attempt_id) return;
    const staged = staged_app_path(this.options.current_root, attempt.to_sha);
    if (!statSync(staged, { throwIfNoEntry: false })?.isDirectory())
      throw new Error('the staged release is missing');
    let backup: string | null = null,
      steps_pending = false;
    const database_bytes = database_file_bytes(this.options.database);
    if (attempt.release.manifest.runtime_contract?.version === 2) {
      steps_pending = await this.options.installer.node_schema_steps_pending(
        this.options.database,
        staged,
      );
      if (!this.active(incarnation)) return;
      // The deadline covers the backup before it starts, so neither settle() nor the
      // Updating card gives up while it runs.
      this.options.runtime.assert_database_owned();
      this.attempts.change(attempt_id, states('installing'), (current) => ({
        ...current,
        deadline_at: this.deadline(
          install_deadline_seconds({
            backup: steps_pending,
            steps: steps_pending,
            database_bytes,
          }),
        ),
        ...(steps_pending ? { detail: backup_detail(database_bytes) } : {}),
      }));
      if (steps_pending) {
        backup = await this.options.installer.create_backup(
          this.options.database,
          this.options.backup_directory,
          attempt.from_sha,
        );
        if (!this.active(incarnation)) return;
      }
    } else {
      const pending = await this.options.installer.schema_change_pending(
        this.options.database,
        staged,
      );
      if (!this.active(incarnation)) return;
      if (pending) {
        backup = await this.options.installer.create_backup(
          this.options.database,
          this.options.backup_directory,
          attempt.from_sha,
        );
        if (!this.active(incarnation)) return;
      }
    }
    if (backup === null && this.options.installer.requires_snapshot) {
      backup = await this.options.installer.create_backup(
        this.options.database,
        this.options.backup_directory,
        attempt.from_sha,
      );
      if (!this.active(incarnation)) return;
    }
    if (!this.active(incarnation)) return;
    this.options.runtime.assert_database_owned();
    const command = this.options.installer.prepare({
      current_root: this.options.current_root,
      database: this.options.database,
      data_directory: dirname(this.options.database),
      attempt_id,
      service_unit: this.options.service_unit,
      health_url: this.options.health_url,
      health_timeout_seconds: UPDATE_HEALTH_TIMEOUT_SECONDS,
    });
    if (!this.active(incarnation)) return;
    this.attempts.change(attempt_id, states('installing'), (current) => ({
      ...current,
      state: 'installing',
      backup,
      detail: null,
      deadline_at: this.deadline(
        install_deadline_seconds({ backup: false, steps: steps_pending, database_bytes }),
      ),
      steps: {
        ...current.steps,
        ...(backup === null ? {} : { backup: this.record_now() }),
        installing: this.record_now(),
      },
    }));
    if (!this.active(incarnation)) return;
    await this.options.installer.launch(attempt_id, command);
    this.installer_starting = { attempt_id, until: this.now() + 5 };
  }
  private fail(
    attempt_id: string,
    expected: ReadonlySet<UpdateAttemptState>,
    detail: string,
  ): void {
    this.waiting_for_work = false;
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
  }
  private require_running(): void {
    if (!this.running)
      throw new UpdateConflict('Panels is shutting down. Retry after it restarts.');
  }
}
