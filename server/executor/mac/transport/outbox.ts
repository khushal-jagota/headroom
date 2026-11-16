/** Durable ordered delivery. Each conversation owns its retries and file negotiation. */
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import {
  observe_shutdown,
  is_shutdown_business_cancellation,
} from '../../../conversation/shutdown.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { decode_value, encode_bounded } from '../../protocol.ts';
import { file_manifest_key as manifest_key } from '../../file_manifest.ts';
import { is_record as is_json_object } from '../../../core/records.ts';
import type { AcceptanceReceipt, ItemBatch, ItemBatchReply } from '../../wire.generated.ts';
import type { OutboxStore } from '../storage/outbox.ts';
import { row_envelope } from '../storage/envelopes.ts';
import {
  MAX_ITEM_BATCH_BYTES,
  OUTBOX_RETRY_INITIAL_SECONDS,
  OUTBOX_RETRY_MAX_SECONDS,
} from '../configuration.ts';
import type { MacHttpClient } from './http.ts';
import type { MacFileTransport } from './files.ts';
import { ExecutorHttpError } from './errors.ts';

interface ConversationSending {
  readonly refusals: Map<string, string>;
  readonly uploaded: Map<string, Set<string>>;
}
export interface OutboxSenderOptions {
  readonly store: Pick<
    OutboxStore,
    'root' | 'sender_id' | 'pending' | 'pending_conversations' | 'acknowledge' | 'pending_events'
  >;
  readonly client: Pick<MacHttpClient, 'post_items'>;
  readonly files: Pick<MacFileTransport, 'upload_file'>;
  readonly notify_confirmed: (receipt: AcceptanceReceipt) => void;
  readonly warn: (message: string) => void;
  readonly on_failure: (error: unknown) => void;
}
class RetryLater extends Error {}

export class MacOutboxSender {
  private readonly options: OutboxSenderOptions;
  private readonly cancellation = new AbortController();
  private readonly senders = new Map<string, Promise<void>>();
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
  private batch(conversation_id: string, state: ConversationSending): ItemBatch | null {
    const store = this.options.store;
    const batch: ItemBatch = { sender_id: store.sender_id, items: [] };
    let size = encode_bounded(batch, 'ItemBatch').byteLength;
    for (const row of store.pending(conversation_id)) {
      const envelope = row_envelope(store.sender_id, row);
      const item = { envelope, sender_refusal: state.refusals.get(envelope.submission_id) ?? null };
      const bytes =
        encode_bounded(item, 'ItemSubmission', { limit: MAX_ITEM_BATCH_BYTES }).byteLength + 1;
      if (batch.items.length && size + bytes > MAX_ITEM_BATCH_BYTES) break;
      batch.items.push(item);
      size += bytes;
      if (envelope.kind !== 'backend_event' || envelope.file_dependencies?.length) break;
    }
    return batch.items.length ? batch : null;
  }
  private async apply_reply(
    batch: ItemBatch,
    reply: ItemBatchReply,
    state: ConversationSending,
  ): Promise<void> {
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
        receipt.disposition === 'accepted',
        (envelope.file_dependencies ?? []).map((file) => ({
          ticket_id: file.owner_id,
          relative_path: file.relative_path,
          sha256: file.sha256,
        })),
        () => notify_confirmed(receipt),
      );
      state.refusals.delete(receipt.submission_id);
      state.uploaded.delete(receipt.submission_id);
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
    const uploaded = state.uploaded.get(envelope.submission_id) ?? new Set<string>();
    state.uploaded.set(envelope.submission_id, uploaded);
    const missing = stop.missing_files ?? [];
    if (!missing.some((file) => !uploaded.has(manifest_key(file)))) {
      state.uploaded.delete(envelope.submission_id);
      throw new Error('Outbox file negotiation made no progress.');
    }
    const snapshots = new Map(
      (envelope.file_dependencies ?? []).map((file) => [manifest_key(file), file]),
    );
    const direct = envelope.payload.child;
    const child = decode_value(
      is_json_object(direct)
        ? direct
        : is_json_object(envelope.payload.ask)
          ? envelope.payload.ask.child
          : null,
      'ChildReference',
    );
    for (const file of missing) {
      this.cancellation.signal.throwIfAborted();
      const key = manifest_key(file);
      if (!snapshots.has(key)) throw new Error('VPS requested a file outside this item.');
      try {
        await this.options.files.upload_file(
          { manifest: file, child },
          join(store.root, 'blobs', file.sha256),
          this.cancellation.signal,
        );
      } catch (error) {
        if (error instanceof ExecutorHttpError && [400, 403].includes(error.status)) {
          state.refusals.set(
            envelope.submission_id,
            error.message || `upload refused: ${error.code}`,
          );
          return;
        }
        throw error;
      }
      uploaded.add(key);
    }
  }
  private async send(conversation_id: string): Promise<void> {
    let delay = OUTBOX_RETRY_INITIAL_SECONDS;
    const state: ConversationSending = { refusals: new Map(), uploaded: new Map() };
    while (!this.closed) {
      try {
        const batch = this.batch(conversation_id, state);
        if (batch === null) return;
        const reply = await this.options.client.post_items(
          conversation_id,
          batch,
          this.cancellation.signal,
        );
        await this.apply_reply(batch, reply, state);
        delay = OUTBOX_RETRY_INITIAL_SECONDS;
      } catch (error) {
        if (this.closed) {
          if (is_shutdown_business_cancellation(error)) return;
          throw error;
        }
        this.options.warn(
          `Outbox delivery failed for ${conversation_id}: ${error instanceof Error ? error.name : 'Error'}`,
        );
        await sleep(delay * 1000, undefined, { signal: this.cancellation.signal }).catch(
          (error: unknown) => {
            if (error instanceof Error && error.cause === this.cancellation.signal.reason)
              throw this.cancellation.signal.reason;
            throw error;
          },
        );
        delay =
          error instanceof RetryLater
            ? OUTBOX_RETRY_INITIAL_SECONDS
            : Math.min(delay * 2, OUTBOX_RETRY_MAX_SECONDS);
      }
    }
  }
  private async run(): Promise<void> {
    try {
      while (!this.closed) {
        this.wake_requested = false;
        let waiting;
        try {
          waiting = this.options.store.pending_conversations(null);
        } catch (error) {
          this.options.warn(`Outbox scan failed: ${error instanceof Error ? error.name : 'Error'}`);
          await sleep(OUTBOX_RETRY_INITIAL_SECONDS * 1000, undefined, {
            signal: this.cancellation.signal,
          }).catch((error: unknown) => {
            if (error instanceof Error && error.cause === this.cancellation.signal.reason)
              throw this.cancellation.signal.reason;
            throw error;
          });
          continue;
        }
        for (const [conversation_id] of waiting) {
          if (this.closed) return;
          if (this.senders.has(conversation_id)) continue;
          const task = Promise.resolve()
            .then(() => this.send(conversation_id))
            .finally(() => {
              this.senders.delete(conversation_id);
              this.wake();
            });
          this.senders.set(conversation_id, task);
          void task.catch((error: unknown) => this.failed(error));
        }
        if (!this.wake_requested)
          await new Promise<void>((resolve) => {
            this.pending_wake = resolve;
          });
        this.pending_wake = undefined;
      }
    } catch (error) {
      if (!this.closed || !is_shutdown_business_cancellation(error)) throw error;
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
    for (const result of results) {
      if (
        result.status === 'rejected' &&
        !(this.closed && is_shutdown_business_cancellation(result.reason))
      )
        this.failures.add(result.reason);
    }
    if (this.failures.size === 1) throw [...this.failures][0];
    if (this.failures.size) throw new AggregateError(this.failures, 'Mac outbox delivery failed.');
  }
}
