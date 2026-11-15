/** Local presence and the independently scheduled lease to the VPS. */
import { setTimeout as sleep } from 'node:timers/promises';
import { lstat, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import {
  observe_shutdown,
  is_shutdown_business_cancellation,
} from '../../../conversation/shutdown.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import type {
  ExecutorIdentity,
  ExecutorRefusal,
  LeaseRequest,
  MacLocalStatus,
  MacUpdateCheck,
  WorkingTurn,
  WorkerCleanupUncertainty,
  WorkspaceState,
} from '../../wire.generated.ts';
import type { AsyncCommandJournal } from '../storage/journal_contracts.ts';
import type { OutboxStore } from '../storage/outbox.ts';
import {
  LEASE_INTERVAL_SECONDS,
  MAX_LEASE_WAITING_CONVERSATIONS,
  MAX_WORKSPACE_STATE_IDS,
  WORKSPACE_CLEANUP_INTERVAL_SECONDS,
} from '../configuration.ts';
import { TICKET_ID_PATTERN } from '../../../ticket_workspaces/contracts.ts';
import type { MacHttpClient } from './http.ts';
import { failure_name } from './errors.ts';
import { ConnectionDiagnostics } from './diagnostics.ts';

async function local_workspace_ticket_ids(root: string): Promise<string[]> {
  let names: string[];
  try {
    const status = await lstat(root);
    if (!status.isDirectory() || status.isSymbolicLink()) return [];
    names = await readdir(root);
  } catch (error) {
    if (['ENOENT', 'ENOTDIR'].includes((error as NodeJS.ErrnoException).code ?? '')) return [];
    throw error;
  }
  const owners = new Set<string>();
  const pattern = new RegExp(`^(${TICKET_ID_PATTERN})(?:-|$)`);
  for (const name of names) {
    const match = pattern.exec(name);
    if (!match) continue;
    try {
      const status = await lstat(join(root, name));
      if (status.isDirectory() && !status.isSymbolicLink()) owners.add(match[1]!);
    } catch (error) {
      if (!['ENOENT', 'ENOTDIR'].includes((error as NodeJS.ErrnoException).code ?? '')) throw error;
    }
  }
  return [...owners].sort();
}

export class MacPresence {
  readonly identity: ExecutorIdentity;
  readonly process_id: number;
  readonly now: () => number;
  lease_accepted_at_mac_ms: number | null = null;
  last_lease_failure: string | null = null;
  refusal: ExecutorRefusal | null = null;
  going_to_sleep = false;
  private accepted_lease = false;
  constructor(identity: ExecutorIdentity, now = Date.now, process_id = process.pid) {
    this.identity = identity;
    this.now = now;
    this.process_id = process_id;
  }
  leased(): boolean {
    return this.accepted_lease;
  }
  accepted(): void {
    this.accepted_lease = true;
    this.lease_accepted_at_mac_ms = this.now();
    this.last_lease_failure = null;
    this.refusal = null;
  }
  failed(failure: string, refusal: ExecutorRefusal | null = null): void {
    this.accepted_lease = false;
    this.last_lease_failure = failure;
    if (refusal !== null) this.refusal = refusal;
  }
  status(admission_stopped: boolean): MacLocalStatus {
    return {
      identity: this.identity,
      process_id: this.process_id,
      lease_accepted_at_mac_ms: this.lease_accepted_at_mac_ms,
      last_lease_failure: this.last_lease_failure,
      refusal: this.refusal,
      admission_stopped,
      going_to_sleep: this.going_to_sleep,
    };
  }
}
export interface MacLeaseOptions {
  readonly client: Pick<MacHttpClient, 'lease' | 'workspace_states'>;
  readonly workspace_root: string;
  readonly presence: MacPresence;
  readonly outbox: Pick<OutboxStore, 'pending_count' | 'pending_conversations' | 'oldest_age_ms'>;
  readonly journal: Pick<AsyncCommandJournal, 'poll_cursor' | 'lower_poll_cursor'>;
  readonly runner: {
    readonly admission_stopped: boolean;
    readonly cleanup_uncertainties: readonly WorkerCleanupUncertainty[];
    readonly legacy_cleanup_notice: string | null;
    working_turns(): readonly WorkingTurn[];
    refresh_worker_cleanup(): Promise<void>;
    cleanup_workspaces(states: readonly WorkspaceState[], now: number): Promise<void>;
    seal(): void;
  };
  readonly updates: { offer(update: MacUpdateCheck): void };
  readonly replaced: () => void;
  readonly on_failure: (error: unknown) => void;
  readonly warn: (message: string) => void;
}
export class MacLeaseLoop {
  private readonly options: MacLeaseOptions;
  private readonly diagnostics: ConnectionDiagnostics;
  private readonly cancellation = new AbortController();
  private task: Promise<void> | undefined;
  private lease: Promise<boolean> | undefined;
  private workspace_cleanup: Promise<void> | undefined;
  private next_workspace_cleanup = 0;
  private closed = false;
  constructor(options: MacLeaseOptions) {
    this.options = options;
    this.diagnostics = new ConnectionDiagnostics(options.warn);
  }
  private request(): LeaseRequest {
    const { outbox, runner, presence } = this.options;
    const waiting = outbox.pending_conversations();
    return {
      identity: presence.identity,
      process_id: presence.process_id,
      mac_wall_clock_ms: presence.now(),
      queue_depth: outbox.pending_count(),
      oldest_item_age_ms: outbox.oldest_age_ms(),
      waiting_conversations: waiting
        .slice(0, MAX_LEASE_WAITING_CONVERSATIONS)
        .map(([conversation_id, oldest]) => ({
          conversation_id,
          oldest_sequence: oldest,
        })),
      waiting_conversations_truncated: waiting.length > MAX_LEASE_WAITING_CONVERSATIONS,
      working_turns: [...runner.working_turns()],
      admission_stopped: runner.admission_stopped,
      going_to_sleep: presence.going_to_sleep,
      cleanup_uncertainties: [...runner.cleanup_uncertainties],
      legacy_cleanup_notice: runner.legacy_cleanup_notice,
    };
  }
  lease_once(): Promise<boolean> {
    if (this.closed) return Promise.reject(new ServerLifecycleError('Mac lease loop is stopped.'));
    if (!this.lease) {
      this.lease = this.exchange();
      void this.lease.then(
        () => {
          this.lease = undefined;
        },
        () => {
          this.lease = undefined;
        },
      );
    }
    return this.lease;
  }
  private async exchange(): Promise<boolean> {
    const { client, journal, runner, presence, updates, warn } = this.options;
    let cursor: number | null = null;
    try {
      cursor = await journal.poll_cursor();
    } catch (error) {
      warn(`Command cursor unavailable: ${failure_name(error)}`);
    }
    this.cancellation.signal.throwIfAborted();
    let reply;
    try {
      reply = await client.lease(this.request(), this.cancellation.signal);
    } catch (error) {
      this.cancellation.signal.throwIfAborted();
      presence.failed(failure_name(error));
      this.diagnostics.report(error, 'lease');
      return true;
    }
    this.cancellation.signal.throwIfAborted();
    if ('error' in reply) {
      const code = typeof reply.error.code === 'string' ? reply.error.code : 'unknown';
      presence.failed(`Lease refused: ${code}`, reply.refusal);
      if (code === 'executor_incarnation_not_current') {
        runner.seal();
        this.options.replaced();
        return false;
      }
      if (reply.update !== null) updates.offer(reply.update);
      return true;
    }
    presence.accepted();
    updates.offer(reply.update);
    this.schedule_workspace_cleanup();
    try {
      if (cursor !== null) await journal.lower_poll_cursor(reply.mailbox_high_sequence, cursor);
    } catch (error) {
      warn(`Lease cursor update failed: ${failure_name(error)}`);
    }
    return true;
  }
  private schedule_workspace_cleanup(): void {
    const now = performance.now() / 1000;
    if (this.closed || this.workspace_cleanup !== undefined || now < this.next_workspace_cleanup)
      return;
    this.next_workspace_cleanup = now + WORKSPACE_CLEANUP_INTERVAL_SECONDS;
    const { promise, resolve, reject } = Promise.withResolvers<void>();
    this.workspace_cleanup = promise;
    void promise.then(
      () => {
        if (this.workspace_cleanup === promise) this.workspace_cleanup = undefined;
      },
      () => {
        if (this.workspace_cleanup === promise) this.workspace_cleanup = undefined;
      },
    );
    try {
      resolve(this.cleanup_workspaces());
    } catch (error) {
      reject(error);
    }
  }
  private async cleanup_workspaces(): Promise<void> {
    const { runner, client, workspace_root, warn } = this.options;
    const signal = this.cancellation.signal;
    try {
      await runner.refresh_worker_cleanup();
      signal.throwIfAborted();
      const ids = await local_workspace_ticket_ids(workspace_root);
      signal.throwIfAborted();
      const states: WorkspaceState[] = [];
      let at_vps_ms = Infinity;
      for (let offset = 0; offset < Math.max(ids.length, 1); offset += MAX_WORKSPACE_STATE_IDS) {
        const reply = await client.workspace_states(
          ids.slice(offset, offset + MAX_WORKSPACE_STATE_IDS),
          signal,
        );
        signal.throwIfAborted();
        states.push(...reply.states);
        at_vps_ms = Math.min(at_vps_ms, reply.at_vps_ms);
      }
      await runner.cleanup_workspaces(states, Math.floor(at_vps_ms / 1000));
    } catch (error) {
      if (!(signal.aborted && is_shutdown_business_cancellation(error)))
        warn(`Ticket workspace cleanup failed: ${failure_name(error)}`);
    }
  }
  start(): void {
    if (this.closed) throw new ServerLifecycleError('Mac lease loop is stopped.');
    if (this.task) return;
    this.task = this.run();
    void this.task.catch(this.options.on_failure);
  }
  private async run(): Promise<void> {
    try {
      while (!this.closed) {
        const started = performance.now();
        if (!(await this.lease_once())) return;
        if (this.closed) return;
        await sleep(
          Math.max(0, started + LEASE_INTERVAL_SECONDS * 1000 - performance.now()),
          undefined,
          { signal: this.cancellation.signal },
        ).catch((error: unknown) => {
          if (
            this.cancellation.signal.aborted &&
            error instanceof Error &&
            error.name === 'AbortError' &&
            error.cause === this.cancellation.signal.reason
          )
            throw this.cancellation.signal.reason;
          throw error;
        });
      }
    } catch (error) {
      if (!this.cancellation.signal.aborted || !is_shutdown_business_cancellation(error))
        throw error;
    }
  }
  stop(options: StopOptions): Promise<void> {
    this.closed = true;
    this.cancellation.abort();
    return observe_shutdown(this.settled(), options, 'Mac lease loop remains unfinished.');
  }
  async settled(): Promise<void> {
    const results = await Promise.allSettled([this.task, this.lease]);
    results.push(...(await Promise.allSettled([this.workspace_cleanup])));
    const failures = new Set<unknown>();
    for (const result of results) {
      if (result.status === 'fulfilled') continue;
      if (this.cancellation.signal.aborted && is_shutdown_business_cancellation(result.reason))
        continue;
      failures.add(result.reason);
    }
    if (failures.size === 1) throw [...failures][0];
    if (failures.size) throw new AggregateError(failures, 'Mac lease loop failed.');
  }
}
