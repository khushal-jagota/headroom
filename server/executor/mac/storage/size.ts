/** Size-only projection of a valid local wrapper through the shared generated wire contract. */
import { ProtocolRefused } from '../../../execution_identity.ts';
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
): (sequence: number) => void {
  const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(encoded));
  if (
    value === null ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    !Object.hasOwn(value, 'payload') ||
    !Object.hasOwn(value, 'files')
  )
    throw file_error('invalid stored outbox payload');
  const parts = value as Record<string, unknown>;
  const projection: ItemSubmission = {
    envelope: {
      sender_id,
      conversation_id: columns.conversation_id,
      sequence: 0,
      submission_id,
      kind: columns.kind as EnvelopeKind,
      payload: parts.payload as QueuedEnvelope['payload'],
      file_dependencies: parts.files as FileManifest[],
    },
    sender_refusal: null,
  };
  // Serialize outside SQLite's writer. Only the allocated sequence width remains.
  const size = Buffer.byteLength(JSON.stringify(projection));
  return (sequence) => {
    if (size + String(sequence).length - 1 > MAX_ENVELOPE_BYTES)
      throw new ProtocolRefused('value exceeds its encoded size limit');
  };
}
