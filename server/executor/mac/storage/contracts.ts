/** Frozen local SQLite rows. No executor wire or manifest type belongs in this layer. */
export interface ItemRow {
  sequence: number;
  submission_id: string;
  conversation_id: string;
  child_id: string;
  kind: string;
  payload_json: Buffer;
  receipt_json: Buffer | null;
}
export interface TicketFileRow {
  ticket_id: string;
  relative_path: string;
  byte_count: number;
  modified_ns: bigint;
  sha256: string;
}
export interface ItemColumns {
  submission_id?: string;
  conversation_id: string;
  child_id: string;
  kind: string;
}
export interface OutboxClock {
  monotonic(): number;
  now_ns(): bigint;
}
/** Local delivery evidence, outside the submitted event and file manifests. */
export interface OutboxFileRefusal {
  relative_path: string;
  sha256: string;
  reason: string;
}
export interface OutboxDeliveryState {
  started_at_ms: number | null;
  file_refusals: OutboxFileRefusal[];
  failure: string | null;
}
export interface OutboxDeliveryStatus {
  failed_count: number;
  refused_file_count: number;
  last_error: string | null;
}
