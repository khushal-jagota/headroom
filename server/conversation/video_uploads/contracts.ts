/** Canonical video admission values. Upload intake endpoints remain in Unit 19. */
export type DestinationKind = 'chief' | 'manager' | 'ticket' | 'conversation';
export type UploadState =
  'reserved' | 'transferring' | 'ready' | 'admitted' | 'linked' | 'failed' | 'cancelled';
export interface UploadDestination {
  readonly kind: DestinationKind;
  readonly id: string;
  readonly expected_conversation_id: string | null;
}
export interface VideoUploadStatus {
  upload_id: string;
  destination: UploadDestination;
  filename: string;
  byte_count: number;
  received_bytes: number;
  state: UploadState;
  media_type: string | null;
  expires_at: number;
  error: string | null;
}
export interface CheckedVideoUpload {
  readonly upload_id: string;
  readonly destination: UploadDestination;
  readonly sender_message_id: string;
  readonly conversation_id: string | null;
}
export type VideoUploadOutcome = 'accepted' | 'refused' | 'uncertain';
export class VideoUploadError extends Error {
  readonly status_code: number;
  constructor(message: string, status_code = 400) {
    super(message);
    this.name = 'VideoUploadError';
    this.status_code = status_code;
  }
}
/** Pydantic model_dump_json emits declaration order with compact separators. */
export function destination_to_json(destination: UploadDestination): string {
  return JSON.stringify({
    kind: destination.kind,
    id: destination.id,
    expected_conversation_id: destination.expected_conversation_id,
  });
}
export function destination_from_json(raw: string): UploadDestination {
  const value: unknown = JSON.parse(raw);
  if (value === null || typeof value !== 'object' || Array.isArray(value))
    throw new TypeError('invalid upload destination');
  const fields = value as Record<string, unknown>;
  if (
    Object.keys(fields).sort().join(',') !== 'expected_conversation_id,id,kind' ||
    typeof fields.kind !== 'string' ||
    !['chief', 'manager', 'ticket', 'conversation'].includes(fields.kind) ||
    typeof fields.id !== 'string' ||
    !(
      fields.expected_conversation_id === null ||
      typeof fields.expected_conversation_id === 'string'
    )
  ) {
    throw new TypeError('invalid upload destination');
  }
  return {
    kind: fields.kind as DestinationKind,
    id: fields.id,
    expected_conversation_id: fields.expected_conversation_id,
  };
}
