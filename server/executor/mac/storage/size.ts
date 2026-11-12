/** Size-only projection of a valid local wrapper through the shared generated wire contract. */
import { parse_numeric_json, stringify_pydantic_json } from '../../../core/http/numeric_json.ts';
import type { NumericJSON } from '../../../core/http/numeric_json.ts';
import type {
  ItemSubmission,
  QueuedEnvelope,
  FileManifest,
  EnvelopeKind,
} from '../../wire.generated.ts';
import { MAX_ENVELOPE_BYTES } from '../configuration.ts';
import { file_error } from '../file_metadata.ts';
import type { ItemColumns } from './contracts.ts';
export function item_size_guard(
  sender_id: string,
  columns: ItemColumns,
  submission_id: string,
  encoded: Buffer,
): (sequence: bigint) => void {
  const value = parse_numeric_json(new TextDecoder('utf-8', { fatal: true }).decode(encoded));
  if (
    value === null ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    !Object.hasOwn(value, 'payload') ||
    !Object.hasOwn(value, 'files')
  )
    throw file_error('invalid stored outbox payload');
  const parts = value as Record<string, unknown>;
  const projection: NumericJSON<ItemSubmission> = {
    envelope: {
      sender_id,
      conversation_id: columns.conversation_id,
      sequence: 0,
      submission_id,
      kind: columns.kind as EnvelopeKind,
      payload: parts.payload as NumericJSON<QueuedEnvelope['payload']>,
      file_dependencies: parts.files as NumericJSON<FileManifest[]>,
    },
    sender_refusal: null,
  };
  // Serialize outside SQLite's writer. Only the allocated sequence width remains.
  const size = Buffer.byteLength(stringify_pydantic_json(projection));
  return (sequence) => {
    if (size + String(sequence).length - 1 > MAX_ENVELOPE_BYTES)
      throw Object.assign(new Error('value exceeds its encoded size limit'), {
        name: 'ProtocolRefused',
      });
  };
}
