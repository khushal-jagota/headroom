/** Admitted CLI publication appends through the existing spool owner. */
import { as_object } from '../../protocol.ts';
import type { AcceptanceReceipt, CliWrite, QueuedEnvelope } from '../../wire.generated.ts';
import type { OutboxStore } from '../storage/outbox.ts';
import { row_envelope, row_receipt } from '../storage/envelopes.ts';

export class GatewayOutbox {
  readonly store: OutboxStore;
  private readonly waiters = new Map<string, Set<(receipt: AcceptanceReceipt) => void>>();
  constructor(store: OutboxStore) {
    this.store = store;
  }
  async add(
    write: CliWrite,
    submission_id: string,
    ticket_id: string | null,
  ): Promise<QueuedEnvelope> {
    const existing = this.store.lookup(submission_id);
    if (existing !== null) return row_envelope(this.store.sender_id, existing);
    const encoded = Buffer.from(
      JSON.stringify({ payload: as_object(write, 'CliWrite'), files: [] }),
    );
    const row = this.store.append(
      {
        submission_id,
        conversation_id: write.child.conversation_id,
        child_id: write.child.child_id,
        kind: 'cli_write',
      },
      encoded,
      (row) => {
        row_envelope(this.store.sender_id, row);
      },
    );
    // The write is queued. A Ticket's files follow separately and never delay it.
    if (ticket_id !== null) this.store.note_ticket_activity(ticket_id);
    return row_envelope(this.store.sender_id, row);
  }
  lookup(submission_id: string) {
    const row = this.store.lookup(submission_id);
    return row === null
      ? null
      : { envelope: row_envelope(this.store.sender_id, row), receipt: row_receipt(row) };
  }
  /** The HTTP sender calls this only from OutboxStore.acknowledge's committed callback. */
  notify_confirmed(receipt: AcceptanceReceipt): void {
    for (const notify of this.waiters.get(receipt.submission_id) ?? []) notify(receipt);
  }
  wait_confirmed(
    submission_id: string,
    seconds: number,
    signal: AbortSignal,
  ): Promise<AcceptanceReceipt | null> {
    return new Promise((resolve, reject) => {
      let timer: ReturnType<typeof setTimeout> | undefined;
      let waiting = this.waiters.get(submission_id);
      if (!waiting) this.waiters.set(submission_id, (waiting = new Set()));
      const finish = (receipt: AcceptanceReceipt | null, error?: unknown) => {
        clearTimeout(timer);
        signal.removeEventListener('abort', aborted);
        waiting!.delete(notify);
        if (!waiting!.size) this.waiters.delete(submission_id);
        if (error) reject(error);
        else resolve(receipt);
      };
      const notify = (receipt: AcceptanceReceipt) => finish(receipt);
      const aborted = () => finish(null);
      waiting.add(notify);
      signal.addEventListener('abort', aborted, { once: true });
      try {
        const receipt = this.lookup(submission_id)?.receipt;
        if (receipt != null || signal.aborted) {
          finish(receipt ?? null);
          return;
        }
        timer = setTimeout(() => finish(null), Math.max(0, seconds) * 1000);
      } catch (error) {
        finish(null, error);
      }
    });
  }
}
