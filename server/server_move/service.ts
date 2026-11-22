/**
 * The old server's side of a move: it leads the run, serves the copy, pauses, decides the
 * commit and becomes the address responder. The rules are in logic/run.ts.
 */
import { randomUUID } from 'node:crypto';
import { mkdir, rm } from 'node:fs/promises';
import { basename, join } from 'node:path';
import { PlannerError } from '../core/contracts.ts';
import type { Database } from '../core/database/contracts.ts';
import { onlineBackup, verifyDatabaseIntegrity } from '../core/database/index.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import type { UpdateBarrier } from '../environments/update_barrier.ts';
import type { ChecksumManifest, ServerStatePaths } from '../environments/server_state/contracts.ts';
import { server_state_manifest } from '../environments/server_state/inventory.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';
import { now_ms } from '../executor/clock.ts';
import { read_presence } from '../executor/vps_presence_store.ts';
import type { ServerMoveNotice } from '../executor/wire.generated.ts';
import { read_server_location } from '../server_link/data.ts';
import { parse_server_location, validate_server_address } from '../server_link/logic.ts';
import type { ServerLocation } from '../server_link/contracts.ts';
import { mac_is_present } from '../tickets/locale_configuration.ts';
import type {
  ServerMoveAttempt,
  ServerMoveFiles,
  ServerMoveIngress,
  ServerMoveInstruction,
  ServerMovePlatform,
  ServerMoveReportReply,
  ServerMoveView,
} from './contracts.ts';
import { SERVER_MOVE_ACTIVE_PHASES, SERVER_MOVE_DIRECTORY } from './contracts.ts';
import {
  SERVER_MOVE_DRAIN_SECONDS,
  SERVER_MOVE_PAUSE_CHECK_SECONDS,
  SERVER_MOVE_PROBE_INTERVAL_SECONDS,
} from './configuration.ts';
import { ServerMoveAttemptStore } from './data/attempt_store.ts';
import type { ServerPackageCache } from './packages/cache.ts';
import {
  MOVE_BLOCKS_UPDATE,
  PAUSE_TOO_LONG,
  RESTARTED_DURING_MOVE,
  SERVER_MOVE_FAILABLE_PHASES,
  SERVER_MOVE_HOLDING_PHASES,
  instruction_applies,
  instruction_of,
  new_attempt,
  new_server_took_over,
  notice_applies,
  parse_move_report,
  parse_move_request,
  pause_expired,
  report_decision,
  start_refusal,
  startup_recovery,
  transition,
  view_of,
} from './logic/run.ts';

export interface ServerMoveCoordinatorOptions {
  readonly database: Database;
  /** The server folder: the database's parent directory. */
  readonly data_directory: string;
  /** The resolved path of the live database. */
  readonly database_path: string;
  readonly state_paths: () => ServerStatePaths;
  readonly revision: string | null;
  readonly trusted_ingress: ServerMoveIngress | null;
  readonly update_barrier: UpdateBarrier;
  /** An update attempt that is not settled, a live installer lock, or a frozen barrier. */
  readonly update_running: () => boolean;
  readonly conversation: () => ConversationSystem | null;
  readonly packages: ServerPackageCache;
  /** One HTTPS read of `<origin>/api/server-link`. */
  readonly probe: (origin: string) => Promise<unknown>;
  readonly emit_change: () => void;
  readonly now?: () => number;
}

/** A file the target may fetch: its path on disk and the digest its answer carries. */
export interface ServerMoveFileSource {
  readonly path: string;
  /** Null before the pause: the route digests the live bytes it sends. */
  readonly sha256: string | null;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
function conflict(text: string): PlannerError {
  return new PlannerError('revision_conflict', text);
}
async function admission_finished(
  conversation: ConversationSystem | null,
  signal: AbortSignal,
): Promise<void> {
  const admission = conversation?.workspace_admission;
  if (!admission?.active || signal.aborted) return;
  const { promise, resolve } = Promise.withResolvers<void>();
  const unsubscribe = admission.subscribe_operations_finished(resolve);
  signal.addEventListener('abort', () => resolve(), { once: true });
  try {
    await promise;
  } finally {
    unsubscribe();
  }
}

export class ServerMoveCoordinator {
  private readonly options: ServerMoveCoordinatorOptions;
  private readonly store: ServerMoveAttemptStore;
  private readonly now: () => number;
  private current: ServerMoveAttempt | null;
  /** The pre-pause listing that the file route admits, and the complete list from the pause. */
  private listed: ChecksumManifest | null = null;
  private complete: ServerMoveFiles | null = null;
  private pausing: Promise<ServerMoveFiles> | null = null;
  /** The conversation admission this run claimed, so only this run reopens it. */
  private admission_claimed = false;
  private run_lifetime = new AbortController();
  private pause_timer: ReturnType<typeof setInterval> | undefined;
  private probe_timer: ReturnType<typeof setInterval> | undefined;
  private probing = false;
  private readonly work = new Set<Promise<unknown>>();
  private stopped = false;

  constructor(options: ServerMoveCoordinatorOptions) {
    this.options = options;
    this.now = options.now ?? now_ms;
    this.store = new ServerMoveAttemptStore(options.data_directory);
    this.current = this.store.read();
  }

  /**
   * Startup: a run before the point of no return failed, and nothing holds this server for it.
   * A committing run stays held by `startup_move_hold` and goes on asking the new address.
   */
  recover(): void {
    const attempt = this.current;
    const action = startup_recovery(attempt);
    if (action === 'fail') {
      this.write(transition(attempt!, 'failed', this.now(), { failure: RESTARTED_DURING_MOVE }));
      this.track(this.remove_copy(attempt!.move_id));
    } else if (action === 'probe') this.start_probe();
  }

  view(): ServerMoveView | null {
    return this.current === null ? null : view_of(this.current);
  }
  notice(machine_id: string): ServerMoveNotice | null {
    return notice_applies(this.current, machine_id) ? { move_id: this.current!.move_id } : null;
  }
  /** Normal work waits from the pause on, the same as during an update trial. */
  holds_work(): boolean {
    return (
      this.pausing !== null ||
      (this.current !== null && SERVER_MOVE_HOLDING_PHASES.has(this.current.phase))
    );
  }
  update_refusal(): string | null {
    const phase = this.current?.phase;
    return phase !== undefined && (SERVER_MOVE_ACTIVE_PHASES.has(phase) || phase === 'moved')
      ? MOVE_BLOCKS_UPDATE
      : null;
  }

  start(target_machine_id: string, body: unknown): ServerMoveView {
    if (this.stopped) throw conflict('Panels is shutting down. Try again after it restarts.');
    let request;
    try {
      request = parse_move_request(body, validate_server_address);
    } catch (error) {
      throw new PlannerError('validation', message(error));
    }
    const facts = this.options.database.read((sql) => {
      const machine = sql.get<{ is_home: number }>(
        'SELECT is_home FROM machines WHERE machine_id=?',
        [target_machine_id],
      );
      const platform = sql.get<{ platform: string }>(
        'SELECT platform FROM machine_executor_credentials WHERE machine_id=?',
        [target_machine_id],
      );
      const presence = machine ? read_presence(sql, target_machine_id) : null;
      return {
        location: read_server_location(sql),
        machine,
        platform: platform?.platform ?? null,
        present: machine ? mac_is_present(sql, this.now(), target_machine_id) : false,
        target_revision: presence?.identity?.application_revision ?? null,
      };
    });
    const refusal = start_refusal({
      update_running: this.options.update_running(),
      move_running: this.current !== null && SERVER_MOVE_ACTIVE_PHASES.has(this.current.phase),
      server_active: facts.location?.state === 'active',
      target_known: facts.machine !== undefined,
      target_runs_server: target_machine_id === HOME_MACHINE_ID || facts.machine?.is_home === 1,
      target_present: facts.present,
      target_revision: facts.target_revision,
      target_platform: facts.platform,
      server_revision: this.options.revision,
    });
    if (refusal !== null) throw new PlannerError('validation', refusal);
    const attempt = this.write(
      new_attempt({
        move_id: randomUUID(),
        target_machine_id,
        request,
        platform: facts.platform as ServerMovePlatform,
        revision: this.options.revision!,
        location: facts.location!,
        trusted_ingress: this.options.trusted_ingress,
        now_ms: this.now(),
      }),
    );
    this.listed = this.complete = null;
    this.run_lifetime = new AbortController();
    this.track(this.prepare(attempt));
    return view_of(attempt);
  }

  private async prepare(attempt: ServerMoveAttempt): Promise<void> {
    try {
      const prepared = await this.options.packages.get(
        attempt.platform,
        attempt.revision,
        this.run_lifetime.signal,
      );
      if (!this.is_phase(attempt.move_id, 'preparing')) return;
      this.write(transition(this.current!, 'copying', this.now(), { package: prepared }));
    } catch (error) {
      await this.fail(attempt.move_id, `The server package could not be had: ${message(error)}`);
    }
  }

  /** The executor routes admit only the target of the run, and only for its move. */
  private admitted(machine_id: string, move_id: string): ServerMoveAttempt {
    const attempt = this.current;
    if (
      attempt === null ||
      attempt.move_id !== move_id ||
      attempt.target_machine_id !== machine_id ||
      !SERVER_MOVE_ACTIVE_PHASES.has(attempt.phase)
    )
      throw new PlannerError('not_found', 'No active server move for this machine.');
    return attempt;
  }

  instruction(machine_id: string): ServerMoveInstruction {
    if (!instruction_applies(this.current, machine_id))
      throw new PlannerError('not_found', 'No server move for this machine.');
    return instruction_of(this.current!);
  }

  package_path(machine_id: string, move_id: string): string {
    const attempt = this.admitted(machine_id, move_id);
    if (attempt.package === null) throw conflict('The server package is not ready yet.');
    return this.options.packages.archive_path(attempt.platform, attempt.revision);
  }

  async files(machine_id: string, move_id: string): Promise<ServerMoveFiles> {
    const attempt = this.admitted(machine_id, move_id);
    if (this.pausing !== null) return this.pausing;
    if (this.complete !== null) return this.complete;
    if (attempt.phase !== 'copying') throw conflict('The move is not copying yet.');
    let manifest: ChecksumManifest;
    try {
      manifest = await server_state_manifest(this.options.state_paths(), { database_copy: null });
    } catch (error) {
      throw conflict(`The state files could not be listed: ${message(error)}`);
    }
    if (this.is_phase(move_id, 'copying') && this.pausing === null) this.listed = manifest;
    return { complete: false, manifest };
  }

  /** Only listed paths are served. The database name gives the checked copy. */
  file(machine_id: string, move_id: string, path: string): ServerMoveFileSource {
    this.admitted(machine_id, move_id);
    if (this.complete !== null) {
      if (!Object.hasOwn(this.complete.manifest, path))
        throw new PlannerError('not_found', 'This file is not listed.');
      const sha256 = this.complete.manifest[path]!;
      return {
        path:
          path === basename(this.options.database_path)
            ? this.copy_path(move_id)
            : join(this.options.data_directory, path),
        sha256,
      };
    }
    if (this.listed === null || !Object.hasOwn(this.listed, path))
      throw new PlannerError('not_found', 'This file is not listed.');
    return { path: join(this.options.data_directory, path), sha256: null };
  }

  pause(machine_id: string, move_id: string): Promise<ServerMoveFiles> {
    const attempt = this.admitted(machine_id, move_id);
    if (this.pausing !== null) return this.pausing;
    if (this.complete !== null) return Promise.resolve(this.complete);
    if (attempt.phase !== 'copying') return Promise.reject(conflict('The move is not copying.'));
    const pausing = this.make_pause(attempt).finally(() => {
      if (this.pausing === pausing) this.pausing = null;
    });
    this.pausing = pausing;
    this.track(pausing.catch(() => {}));
    return pausing;
  }

  private copy_directory(move_id: string): string {
    return join(this.options.data_directory, SERVER_MOVE_DIRECTORY, move_id);
  }
  private copy_path(move_id: string): string {
    return join(this.copy_directory(move_id), basename(this.options.database_path));
  }

  private async make_pause(attempt: ServerMoveAttempt): Promise<ServerMoveFiles> {
    const { update_barrier: barrier } = this.options;
    const move_id = attempt.move_id;
    const paused_at_ms = this.now();
    // Writes, machine receipts and uploads are refused from here, and mailbox polls are held.
    barrier.move_hold = move_id;
    barrier.close();
    // New conversation starts and workspace operations stop, as at a frozen startup.
    this.admission_claimed =
      this.options.conversation()?.workspace_admission.claim(() => {}, {
        idle: () => true,
        require_idle: false,
      }) ?? false;
    try {
      const drain = AbortSignal.any([
        this.run_lifetime.signal,
        AbortSignal.timeout(SERVER_MOVE_DRAIN_SECONDS * 1000),
      ]);
      await barrier.drain(drain);
      await admission_finished(this.options.conversation(), drain);
      this.run_lifetime.signal.throwIfAborted();
      if (!barrier.drained || this.options.conversation()?.workspace_admission.active)
        throw new Error(
          `running operations did not finish within ${SERVER_MOVE_DRAIN_SECONDS} seconds`,
        );
      const copy = this.copy_path(move_id);
      await rm(this.copy_directory(move_id), { recursive: true, force: true });
      await mkdir(this.copy_directory(move_id), { recursive: true, mode: 0o700 });
      await onlineBackup(this.options.database_path, copy);
      verifyDatabaseIntegrity(copy);
      const manifest = await server_state_manifest(this.options.state_paths(), {
        database_copy: copy,
      });
      if (!this.is_phase(move_id, 'copying')) throw new Error('the move ended during the pause');
      const files: ServerMoveFiles = { complete: true, manifest };
      this.write(transition(this.current!, 'paused', this.now(), { paused_at_ms }));
      this.complete = files;
      this.start_pause_timer();
      return files;
    } catch (error) {
      await this.fail(move_id, `The pause did not complete: ${message(error)}`);
      // A copy that finished after another failure removed the folder goes too.
      await this.remove_copy(move_id);
      throw conflict(`The pause did not complete: ${message(error)}`);
    }
  }

  async report(machine_id: string, move_id: string, body: unknown): Promise<ServerMoveReportReply> {
    let report;
    try {
      report = parse_move_report(body);
    } catch (error) {
      throw new PlannerError('validation', message(error));
    }
    const attempt = this.admitted(machine_id, move_id);
    if (attempt.phase === 'committing') return { commit: true };
    if (report.state === 'failed') {
      await this.fail(move_id, `The target machine stopped the move: ${report.reason}`);
      return { commit: false };
    }
    if (attempt.phase !== 'paused') {
      await this.fail(move_id, 'The target machine reported a check before the pause.');
      return { commit: false };
    }
    let answer: ServerLocation | null = null;
    try {
      answer = parse_server_location(await this.options.probe(attempt.server_url));
    } catch (error) {
      console.warn('The new server address did not answer the move check', error);
    }
    const settled = this.current;
    if (settled?.move_id !== move_id) return { commit: false };
    if (settled.phase === 'committing') return { commit: true };
    const decision = report_decision(settled, answer, this.now());
    if (!decision.commit) {
      await this.fail(move_id, decision.reason);
      return { commit: false };
    }
    // The point of no return is durable before the target hears it.
    this.write(transition(settled, 'committing', this.now()));
    this.stop_pause_timer();
    this.start_probe();
    return { commit: true };
  }

  async switched(machine_id: string, move_id: string): Promise<Record<string, never>> {
    const attempt = this.admitted(machine_id, move_id);
    if (attempt.phase !== 'committing') throw conflict('The move has not committed.');
    this.become_moved();
    return {};
  }

  /**
   * The database first: a restart after this write starts the address responder. The run
   * record follows. The running app answers as the responder from this write on, because
   * the server link reads the location on every request.
   */
  private become_moved(): void {
    const attempt = this.current;
    if (attempt?.phase !== 'committing') return;
    this.options.database.write((tx) => {
      const location = read_server_location(tx);
      if (location?.state === 'moved') return;
      if (
        location === null ||
        location.server_id !== attempt.server_id ||
        location.move_number !== attempt.move_number
      )
        throw new Error('The server record changed during the move.');
      tx.run("UPDATE server_link SET state='moved', server_url=? WHERE singleton=1", [
        attempt.server_url,
      ]);
    });
    this.write(transition(attempt, 'moved', this.now()));
    this.stop_probe();
  }

  /** Asks at once, then every few seconds, until the new server shows the next number. */
  private start_probe(): void {
    if (this.probe_timer !== undefined || this.stopped) return;
    const ask = () => {
      const attempt = this.current;
      if (this.probing || attempt?.phase !== 'committing') return;
      this.probing = true;
      const probe = Promise.resolve()
        .then(() => this.options.probe(attempt.server_url))
        .then((answer) => {
          if (new_server_took_over(attempt, parse_server_location(answer))) this.become_moved();
        })
        .catch(() => {
          // The new server can be slow to switch. The next probe asks again.
        })
        .finally(() => {
          this.probing = false;
        });
      this.track(probe);
    };
    this.probe_timer = setInterval(ask, SERVER_MOVE_PROBE_INTERVAL_SECONDS * 1000);
    this.probe_timer.unref();
    ask();
  }
  private stop_probe(): void {
    clearInterval(this.probe_timer);
    this.probe_timer = undefined;
  }
  private start_pause_timer(): void {
    this.stop_pause_timer();
    this.pause_timer = setInterval(() => {
      const attempt = this.current;
      if (attempt !== null && pause_expired(attempt, this.now()))
        this.track(this.fail(attempt.move_id, PAUSE_TOO_LONG));
    }, SERVER_MOVE_PAUSE_CHECK_SECONDS * 1000);
    this.pause_timer.unref();
  }
  private stop_pause_timer(): void {
    clearInterval(this.pause_timer);
    this.pause_timer = undefined;
  }

  /** Ends a run before the point of no return. The pause lifts and the server continues. */
  async fail(move_id: string, failure: string): Promise<boolean> {
    const attempt = this.current;
    if (attempt?.move_id !== move_id || !SERVER_MOVE_FAILABLE_PHASES.has(attempt.phase))
      return false;
    this.write(transition(attempt, 'failed', this.now(), { failure }));
    console.warn(`The server move ${move_id} failed: ${failure}`);
    this.run_lifetime.abort();
    this.stop_pause_timer();
    // A pause still copying ends on its own; a next run never waits for it.
    this.pausing = null;
    this.listed = this.complete = null;
    // A stopping server keeps its admission sealed. Its restart starts without the pause.
    if (!this.stopped) this.lift(move_id);
    await this.remove_copy(move_id);
    return true;
  }
  private lift(move_id: string): void {
    const barrier = this.options.update_barrier;
    if (barrier.move_hold !== move_id) return;
    barrier.move_hold = null;
    barrier.reopen();
    if (this.admission_claimed) this.options.conversation()?.reopen_after_update();
    this.admission_claimed = false;
  }
  private async remove_copy(move_id: string): Promise<void> {
    try {
      await rm(this.copy_directory(move_id), { recursive: true, force: true });
    } catch (error) {
      console.warn('The database copy of a failed move could not be removed', error);
    }
  }

  private is_phase(move_id: string, phase: ServerMoveAttempt['phase']): boolean {
    return this.current?.move_id === move_id && this.current.phase === phase;
  }
  private write(attempt: ServerMoveAttempt): ServerMoveAttempt {
    this.current = this.store.write(attempt);
    this.options.emit_change();
    return attempt;
  }
  private track(work: Promise<unknown>): void {
    this.work.add(work);
    void work.finally(() => this.work.delete(work));
  }

  stop(): Promise<void> {
    this.stopped = true;
    this.stop_probe();
    this.stop_pause_timer();
    this.run_lifetime.abort();
    return this.settled();
  }
  settled(): Promise<void> {
    return Promise.allSettled([...this.work]).then(() => {});
  }
}
