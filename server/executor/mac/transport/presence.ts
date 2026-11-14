/** Local presence and the independently scheduled lease to the VPS. */
import { setTimeout as sleep } from 'node:timers/promises';
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
} from '../../wire.generated.ts';
import type { AsyncCommandJournal } from '../storage/journal_contracts.ts';
import type { OutboxStore } from '../storage/outbox.ts';
import { LEASE_INTERVAL_SECONDS, MAX_LEASE_WAITING_CONVERSATIONS } from '../configuration.ts';
import type { MacHttpClient } from './http.ts';
import { failure_name } from './errors.ts';
import { ConnectionDiagnostics } from './diagnostics.ts';

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
  readonly client: Pick<MacHttpClient, 'lease'>;
  readonly presence: MacPresence;
  readonly outbox: Pick<OutboxStore, 'pending_count' | 'pending_conversations' | 'oldest_age_ms'>;
  readonly journal: Pick<AsyncCommandJournal, 'poll_cursor' | 'lower_poll_cursor'>;
  readonly runner: {
    readonly admission_stopped: boolean;
    working_turns(): readonly WorkingTurn[];
    seal(): void;
  };
  readonly updates: { offer(update: MacUpdateCheck): void };
  readonly record_children: () => Promise<void>;
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
    try {
      if (cursor !== null) await journal.lower_poll_cursor(reply.mailbox_high_sequence, cursor);
    } catch (error) {
      warn(`Lease cursor update failed: ${failure_name(error)}`);
    }
    return true;
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
        try {
          await this.options.record_children();
        } catch (error) {
          this.options.warn(`Agent process record failed: ${failure_name(error)}`);
        }
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
