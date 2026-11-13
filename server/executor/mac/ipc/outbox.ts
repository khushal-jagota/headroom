/** Admitted CLI publication captures immutable files through the existing spool owner. */
import { stringify_python_json } from '../../../core/http/numeric_json.ts';
import { as_object } from '../../protocol.ts';
import { normalize_integer } from '../../integers.ts';
import type { AcceptanceReceipt, CliWrite, QueuedEnvelope } from '../../wire.generated.ts';
import type { OutboxStore } from '../storage/outbox.ts';
import { row_envelope, row_receipt } from '../storage/envelopes.ts';

export class GatewayOutbox {
  readonly store: OutboxStore;
  private readonly waiters = new Map<string, Set<(receipt: AcceptanceReceipt) => void>>();
  constructor(store: OutboxStore) {
    this.store = store;
  }
  async add(write: CliWrite, submission_id: string, ticket_id: string): Promise<QueuedEnvelope> {
    const existing = this.store.lookup(submission_id);
    if (existing !== null) return row_envelope(this.store.sender_id, existing);
    return this.store.with_capture(ticket_id, (capture) => {
      const payload = as_object(write, 'CliWrite');
      const files = capture.files.map((file) => ({
        scope: 'ticket',
        owner_id: ticket_id,
        relative_path: file.relative_path,
        sha256: file.sha256,
        byte_count: normalize_integer(file.byte_count),
      }));
      const encoded = Buffer.from(
        stringify_python_json(
          { payload, files },
          {
            sort_keys: true,
            compact: true,
            ensure_ascii: true,
          },
        ),
      );
      return row_envelope(
        this.store.sender_id,
        this.store.append(
          {
            submission_id,
            conversation_id: write.child.conversation_id,
            child_id: write.child.child_id,
            kind: 'cli_write',
          },
          encoded,
          capture.files,
          capture.updates,
          (row) => {
            row_envelope(this.store.sender_id, row);
          },
        ),
      );
    });
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
