/** Ordered durable delivery with bounded attempts and fair conversation rotation. */
import {
  observe_shutdown,
  is_shutdown_business_cancellation,
} from '../../../conversation/shutdown.ts';
import { observe_promise } from '../../../core/promise_observer.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { encode_bounded } from '../../protocol.ts';
import type {
  AcceptanceReceipt,
  ItemBatch,
  ItemBatchReply,
  QueuedEnvelope,
} from '../../wire.generated.ts';
import type { OutboxStore } from '../storage/outbox.ts';
import { row_envelope } from '../storage/envelopes.ts';
import {
  MAX_ITEM_BATCH_BYTES,
  MAX_OUTBOX_SENDERS,
  OUTBOX_ITEM_DELIVERY_SECONDS,
  OUTBOX_RETRY_INITIAL_SECONDS,
  OUTBOX_RETRY_MAX_SECONDS,
} from '../configuration.ts';
import type { MacHttpClient } from './http.ts';
import { ExecutorHttpError } from './errors.ts';

interface ConversationSending {
  retry_at: number;
  delay: number;
  attempt: number;
  last_error: string | null;
}
export interface OutboxSenderOptions {
  readonly store: Pick<
    OutboxStore,
    | 'root'
    | 'sender_id'
    | 'pending'
    | 'pending_conversations'
    | 'acknowledge'
    | 'pending_events'
    | 'delivery_started'
    | 'fail_delivery'
  >;
  readonly client: Pick<MacHttpClient, 'post_items'>;
  readonly notify_confirmed: (receipt: AcceptanceReceipt) => void;
  readonly warn: (message: string) => void;
  readonly on_failure: (error: unknown) => void;
}
class RetryLater extends Error {}

function diagnostic(error: unknown): string {
  if (error instanceof ExecutorHttpError)
    return `HTTP ${error.status} ${error.code}: ${error.message}`;
  if (error instanceof RetryLater) return `retry_later: ${error.message}`;
  return error instanceof Error ? `${error.name}: ${error.message}` : String(error);
}

export class MacOutboxSender {
  private readonly options: OutboxSenderOptions;
  private readonly cancellation = new AbortController();
  private readonly senders = new Map<string, Promise<void>>();
  private readonly states = new Map<string, ConversationSending>();
  private readonly failures = new Set<unknown>();
  private task: Promise<void> | undefined;
  private closed = false;
  private pending_wake: (() => void) | undefined;
  private wake_requested = false;
  readonly wake = (): void => {
    this.wake_requested = true;
    this.pending_wake?.();
  };
  constructor(options: OutboxSenderOptions) {
    this.options = options;
  }
  private failed(error: unknown): void {
    if (this.closed && is_shutdown_business_cancellation(error)) return;
    const first = this.failures.size === 0;
    this.failures.add(error);
    this.closed = true;
    this.cancellation.abort();
    this.wake();
    if (first) this.options.on_failure(error);
  }
  start(): void {
    if (this.closed) throw new ServerLifecycleError('Mac outbox sender is stopped.');
    if (this.task) return;
    this.options.store.pending_events.on('pending', this.wake);
    this.task = this.run();
    void this.task.catch((error: unknown) => this.failed(error));
  }
  private batch(conversation_id: string): ItemBatch | null {
    const store = this.options.store;
    const batch: ItemBatch = { sender_id: store.sender_id, items: [] };
    let size = encode_bounded(batch, 'ItemBatch').byteLength;
    for (const row of store.pending(conversation_id)) {
      const envelope = row_envelope(store.sender_id, row);
      const item = { envelope, sender_refusal: null };
      const bytes =
        encode_bounded(item, 'ItemSubmission', { limit: MAX_ITEM_BATCH_BYTES }).byteLength + 1;
      if (batch.items.length && size + bytes > MAX_ITEM_BATCH_BYTES) break;
      batch.items.push(item);
      size += bytes;
      if (envelope.kind !== 'backend_event') break;
    }
    return batch.items.length ? batch : null;
  }
  private async settle_failure(
    envelope: QueuedEnvelope,
    state: ConversationSending,
    reason: string,
    outcome: 'unconfirmed' | 'refused',
  ): Promise<void> {
    const receipt: AcceptanceReceipt = {
      sender_id: envelope.sender_id,
      submission_id: envelope.submission_id,
      disposition: 'refused',
      accepted_at_vps_ms: 0, // This is a local receipt, not a VPS acceptance timestamp.
      result: {
        local_delivery_failure: true,
        delivery_outcome: outcome,
        error: { code: 'outbox_delivery_failed', message: reason },
      },
    };
    await this.options.store.fail_delivery(
      envelope.submission_id,
      Buffer.from(encode_bounded(receipt, 'AcceptanceReceipt', { limit: MAX_ITEM_BATCH_BYTES })),
      reason,
      () => this.options.notify_confirmed(receipt),
    );
    this.options.warn(
      `Outbox item failed conversation=${envelope.conversation_id} sequence=${envelope.sequence} attempt=${state.attempt} outcome=${outcome}: ${reason}`,
    );
    state.delay = OUTBOX_RETRY_INITIAL_SECONDS;
    state.retry_at = 0;
    state.attempt = 0;
    state.last_error = null;
  }
  private async apply_reply(batch: ItemBatch, reply: ItemBatchReply): Promise<void> {
    const { store, notify_confirmed } = this.options;
    if (reply.receipts.length > batch.items.length) throw new Error('Too many outbox receipts.');
    for (let index = 0; index < reply.receipts.length; index++) {
      const receipt = reply.receipts[index]!,
        envelope = batch.items[index]!.envelope;
      if (receipt.submission_id !== envelope.submission_id)
        throw new Error('Outbox receipt names a different item.');
      await store.acknowledge(
        receipt.submission_id,
        receipt.sender_id,
        Buffer.from(encode_bounded(receipt, 'AcceptanceReceipt', { limit: MAX_ITEM_BATCH_BYTES })),
        () => notify_confirmed(receipt),
      );
    }
    const stop = reply.stop;
    if (stop == null) {
      if (reply.receipts.length !== batch.items.length)
        throw new Error('Partial outbox reply has no stop.');
      return;
    }
    const envelope = batch.items[reply.receipts.length]?.envelope;
    if (!envelope || envelope.sequence !== stop.sequence)
      throw new Error('Outbox stop is outside this batch.');
    if (stop.reason === 'retry_later') throw new RetryLater(stop.message);
    // Items carry no files, so the home has nothing to request from this sender.
    throw new Error('Outbox stop requests files that no item carries.');
  }
  private async send_batch(conversation_id: string, state: ConversationSending): Promise<void> {
    const batch = this.batch(conversation_id);
    if (batch === null) return;
    const head = batch.items[0]!.envelope;
    const started = batch.items.map((item) =>
      this.options.store.delivery_started(item.envelope.submission_id),
    );
    const deadline = Math.min(...started) + OUTBOX_ITEM_DELIVERY_SECONDS * 1000;
    if (Date.now() >= deadline) {
      await this.settle_failure(
        head,
        state,
        `No VPS receipt within ${OUTBOX_ITEM_DELIVERY_SECONDS} seconds. Outcome unknown; the VPS may already have accepted the item. Last error: ${state.last_error ?? 'no receipt'}`,
        'unconfirmed',
      );
      return;
    }
    const signal = AbortSignal.any([
      this.cancellation.signal,
      AbortSignal.timeout(Math.max(1, deadline - Date.now())),
    ]);
    state.attempt++;
    try {
      const observed = await observe_promise(
        this.options.client.post_items(conversation_id, batch, signal),
        signal,
      );
      if (!observed.completed) signal.throwIfAborted();
      else await this.apply_reply(batch, observed.value);
      state.delay = OUTBOX_RETRY_INITIAL_SECONDS;
      state.retry_at = 0;
      state.attempt = 0;
      state.last_error = null;
    } catch (error) {
      if (this.closed) {
        if (is_shutdown_business_cancellation(error) || this.cancellation.signal.aborted) return;
        throw error;
      }
      const pending = this.batch(conversation_id)?.items[0]?.envelope;
      if (!pending) return;
      const reason = diagnostic(error);
      state.last_error = reason;
      // A receipted prefix can advance the head during this attempt. Its successor
      // retains its own budget rather than inheriting an earlier item's deadline.
      const pending_deadline =
        this.options.store.delivery_started(pending.submission_id) +
        OUTBOX_ITEM_DELIVERY_SECONDS * 1000;
      if (Date.now() >= pending_deadline) {
        await this.settle_failure(
          pending,
          state,
          `No VPS receipt within ${OUTBOX_ITEM_DELIVERY_SECONDS} seconds. Outcome unknown; the VPS may already have accepted the item. Last error: ${reason}`,
          'unconfirmed',
        );
      } else if (error instanceof ExecutorHttpError && error.status === 404) {
        await this.settle_failure(pending, state, reason, 'refused');
      } else {
        const retry_ms = Math.min(state.delay * 1000, Math.max(0, pending_deadline - Date.now()));
        this.options.warn(
          `Outbox delivery failed conversation=${conversation_id} sequence=${pending.sequence} attempt=${state.attempt} retry_in=${retry_ms / 1000}s: ${reason}`,
        );
        state.retry_at = Date.now() + retry_ms;
        state.delay =
          error instanceof RetryLater
            ? OUTBOX_RETRY_INITIAL_SECONDS
            : Math.min(state.delay * 2, OUTBOX_RETRY_MAX_SECONDS);
      }
    }
  }
  private async run(): Promise<void> {
    while (!this.closed) {
      this.wake_requested = false;
      let waiting: readonly (readonly [string, number])[];
      try {
        waiting = this.options.store.pending_conversations(null);
      } catch (error) {
        this.options.warn(`Outbox scan failed: ${diagnostic(error)}`);
        await new Promise<void>((resolve) => {
          const timer = setTimeout(resolve, OUTBOX_RETRY_INITIAL_SECONDS * 1000);
          this.pending_wake = () => {
            clearTimeout(timer);
            resolve();
          };
        });
        this.pending_wake = undefined;
        continue;
      }
      const waiting_set = new Set(waiting.map(([id]) => id));
      for (const id of this.states.keys())
        if (!waiting_set.has(id) && !this.senders.has(id)) this.states.delete(id);
      for (const [id] of waiting)
        if (!this.states.has(id))
          this.states.set(id, {
            retry_at: 0,
            delay: OUTBOX_RETRY_INITIAL_SECONDS,
            attempt: 0,
            last_error: null,
          });
      const now = Date.now();
      for (const [id, state] of [...this.states]) {
        if (this.closed || this.senders.size >= MAX_OUTBOX_SENDERS) break;
        if (this.senders.has(id) || state.retry_at > now || !waiting_set.has(id)) continue;
        this.states.delete(id);
        this.states.set(id, state);
        const task = Promise.resolve()
          .then(() => this.send_batch(id, state))
          .finally(() => {
            this.senders.delete(id);
            this.wake();
          });
        this.senders.set(id, task);
        void task.catch((error: unknown) => this.failed(error));
      }
      if (!this.wake_requested)
        await new Promise<void>((resolve) => {
          const deadlines = [...this.states]
            .filter(([id, state]) => !this.senders.has(id) && state.retry_at > now)
            .map(([, state]) => state.retry_at);
          const timer = deadlines.length
            ? setTimeout(resolve, Math.max(0, Math.min(...deadlines) - Date.now()))
            : undefined;
          this.pending_wake = () => {
            clearTimeout(timer);
            resolve();
          };
        });
      this.pending_wake = undefined;
    }
  }
  stop(options: StopOptions): Promise<void> {
    this.closed = true;
    this.options.store.pending_events.off('pending', this.wake);
    this.cancellation.abort();
    this.wake();
    return observe_shutdown(this.settled(), options, 'Mac outbox delivery remains unfinished.');
  }
  async settled(): Promise<void> {
    const results = await Promise.allSettled([this.task, ...this.senders.values()]);
    for (const result of results)
      if (
        result.status === 'rejected' &&
        !(this.closed && is_shutdown_business_cancellation(result.reason))
      )
        this.failures.add(result.reason);
    if (this.failures.size === 1) throw [...this.failures][0];
    if (this.failures.size) throw new AggregateError(this.failures, 'Mac outbox delivery failed.');
  }
}
