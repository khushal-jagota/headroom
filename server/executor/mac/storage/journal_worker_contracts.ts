/** Private control and bounded chunk transport. No executor message shapes are cloned. */
import type { JournalReportFeedback } from './journal_contracts.ts';
export interface JournalWorkerData {
  root: string;
  incarnation: string;
  protocol_module: string;
  outputs_module: string;
  health_fence: SharedArrayBuffer;
}
export type JournalAction =
  | { operation: 'open' | 'poll_cursor' | 'unpublished' | 'shrink_to_tombstones' | 'select_report' }
  | { operation: 'report_feedback'; selection: number; settled_ids: JournalReportFeedback }
  | { operation: 'lower_poll_cursor'; high: number; cursor_at_lease: number }
  | {
      operation: 'record_poll_reply';
      input: number;
      incarnation: string;
      after: number;
      decoded?: true;
    }
  | { operation: 'result_of' | 'mark_outputs_published'; command_id: string }
  | { operation: 'finish'; command_id: string; input: number; owed: boolean }
  | { operation: 'unreported'; limit: number }
  | { operation: 'mark_reported'; command_ids: readonly string[] }
  | { operation: 'input_start'; text: boolean; limit: number }
  | { operation: 'input_push'; input: number; chunk: ArrayBuffer | string }
  | { operation: 'input_end' | 'input_drop'; input: number }
  | { operation: 'cursor_next' | 'cursor_close'; cursor: number }
  | { operation: 'reader_next' | 'reader_close'; reader: number };
export interface JournalErrorBody {
  name: string;
  message: string;
  properties: unknown;
}
export function journal_error_properties(error: JournalErrorBody): Record<string, unknown> {
  return error.properties as Record<string, unknown>;
}
export type JournalMainMessage =
  | { kind: 'request'; id: number; action: JournalAction }
  | { kind: 'stop'; id: number }
  | { kind: 'callback_result'; id: number; value?: number; error?: JournalErrorBody };
export interface ReaderDescriptor {
  reader: number;
  byte_count: number;
}
export interface CursorRowDescriptor {
  command_id: string;
  command?: ReaderDescriptor;
  result?: ReaderDescriptor;
  decoded?: unknown;
}
export type JournalWorkerMessage =
  | { kind: 'ready' }
  | { kind: 'cleanup_failure'; error: JournalErrorBody }
  | { kind: 'startup_error'; error: JournalErrorBody }
  | { kind: 'reply'; id: number; value?: unknown; error?: JournalErrorBody }
  | { kind: 'callback'; id: number; callback: 'permission' | 'flush' | 'clock' };
export function journal_error(error: unknown): JournalErrorBody {
  const value = error instanceof Error ? error : new Error(String(error));
  const properties = Object.fromEntries(
    Object.entries(value).filter(
      ([key, item]) =>
        !['database_cleanup', 'cleanup', 'cleanup_error'].includes(key) &&
        typeof item !== 'function',
    ),
  );
  return { name: value.name, message: value.message, properties };
}
