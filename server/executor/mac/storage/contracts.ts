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
export interface ItemFileRow {
  sequence: number;
  relative_path: string;
  sha256: string;
  byte_count: number;
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
export type ItemFileColumns = Omit<ItemFileRow, 'sequence'>;
export type TicketFileKey = Pick<TicketFileRow, 'ticket_id' | 'relative_path' | 'sha256'>;
export interface CaptureRows {
  files: readonly TicketFileRow[];
  updates: readonly TicketFileRow[];
}
export interface OutboxClock {
  monotonic(): number;
  now_ns(): bigint;
}
