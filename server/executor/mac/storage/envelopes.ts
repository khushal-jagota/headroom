/** One generated-codec projection of durable spool rows for IPC and HTTP senders. */
import { decode_json, decode_value } from '../../protocol.ts';
import type { AcceptanceReceipt, QueuedEnvelope } from '../../wire.generated.ts';
import type { ItemRow } from './contracts.ts';

export function row_envelope(sender_id: string, row: ItemRow): QueuedEnvelope {
  const value = decode_json(row.payload_json, 'JsonObject');
  return decode_value(
    {
      sender_id,
      conversation_id: row.conversation_id,
      sequence: row.sequence,
      submission_id: row.submission_id,
      kind: row.kind,
      payload: value.payload,
      file_dependencies: value.files,
    },
    'QueuedEnvelope',
  );
}
export function row_receipt(row: ItemRow): AcceptanceReceipt | null {
  return row.receipt_json === null ? null : decode_json(row.receipt_json, 'AcceptanceReceipt');
}
