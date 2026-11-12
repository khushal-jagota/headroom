/** Export-only synthetic storage driver. Python supplies exact opaque codec bytes. */
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { OutboxStore } from '../outbox.ts';
import type { ItemColumns, ItemRow, TicketFileKey, TicketFileRow } from '../contracts.ts';

interface Step {
  operation: 'append' | 'acknowledge' | 'recent' | 'orphan' | 'crash';
  columns?: ItemColumns;
  payload_hex?: string;
  capture?: boolean;
  submission_id?: string;
  receipt_hex?: string;
  accepted?: boolean;
  invalidated?: TicketFileKey[];
}
interface Input {
  root: string;
  opened_at: number;
  observed_at: number;
  now_ns: string;
  steps: Step[];
}
function row(value: ItemRow | null): unknown {
  return value === null
    ? null
    : {
        sequence: value.sequence.toString(),
        submission_id: value.submission_id,
        conversation_id: value.conversation_id,
        child_id: value.child_id,
        kind: value.kind,
        payload_json: value.payload_json.toString('hex'),
        receipt_json: value.receipt_json?.toString('hex') ?? null,
      };
}
function captured(files: readonly TicketFileRow[]): unknown {
  return files.map((file) => ({
    ...file,
    byte_count: file.byte_count.toString(),
    modified_ns: file.modified_ns.toString(),
  }));
}
export async function spool_parity(input: Input): Promise<unknown> {
  let monotonic = input.opened_at;
  const store = await OutboxStore.open(join(input.root, 'spool'), {
    monotonic: () => monotonic,
    now_ns: () => BigInt(input.now_ns),
  });
  const trace: unknown[] = [];
  try {
    for (const step of input.steps) {
      if (step.operation === 'recent') {
        const path = join(input.root, 'artifacts', 'tickets', 'ticket', 'recent.txt');
        writeFileSync(path, 'recent-B\n');
        // Keep the byte count unchanged. Recent files must remain marked -1 in the
        // index and must supply the replacement bytes to the next capture.
        continue;
      }
      if (step.operation === 'orphan') {
        writeFileSync(join(input.root, 'spool', 'blobs', '.tmp-orphan'), 'uncommitted');
        continue;
      }
      if (step.operation === 'acknowledge') {
        await store.acknowledge(
          step.submission_id!,
          store.sender_id,
          Buffer.from(step.receipt_hex!, 'hex'),
          step.accepted!,
          step.invalidated ?? [],
          () => {},
        );
        trace.push(row(store.lookup(step.submission_id!)));
        continue;
      }
      const append = (
        files: readonly TicketFileRow[] = [],
        updates: readonly TicketFileRow[] = [],
      ) =>
        store.append(
          step.columns!,
          Buffer.from(step.payload_hex!, 'hex'),
          files.map(({ relative_path, sha256, byte_count }) => ({
            relative_path,
            sha256,
            byte_count,
          })),
          [...updates],
          (inserted) => {
            if (step.operation === 'crash') process.exit(71);
            if (!inserted.payload_json.equals(Buffer.from(step.payload_hex!, 'hex')))
              throw new Error('opaque payload changed before validation');
          },
        );
      if (step.capture) {
        trace.push(
          await store.with_capture('ticket', ({ files, updates }) => ({
            row: row(append(files, updates)),
            captured: captured(files),
          })),
        );
      } else trace.push({ row: row(append()) });
    }
    monotonic = input.observed_at;
    return {
      sender: store.sender_id,
      trace,
      pending: store.pending().map(row),
      conversations: store
        .pending_conversations()
        .map(([id, sequence]) => [id, sequence.toString()]),
      count: store.pending_count().toString(),
      age: store.oldest_age_ms(),
    };
  } finally {
    await store.stop({ deadline: performance.now() / 1000 + 10 });
    await store.settled();
  }
}
