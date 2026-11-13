/** Required dependencies and asynchronous journal custody, not another wire model. */
import type { MailboxCommand, CommandResult } from '../../wire.generated.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import type { OutboxCleanup, OutboxSidecar } from './resources.ts';
export interface JournalOutputPolicy {
  command_owes_outputs(command: MailboxCommand, result: CommandResult): boolean;
}
export interface JournalDependencies {
  outputs_module: URL;
  now_ms(): bigint;
  /** The host must initiate shutdown. This notification occurs once per service fault. */
  on_failure(error: Error): void;
}
export interface SidecarOpenScope {
  readonly root: string;
  readonly health_fence: SharedArrayBuffer;
  assert_admission(): void;
  assert_healthy(): void;
  register(service: OutboxSidecar): void;
  flush_spool(): Promise<void>;
}
export interface JournalSpoolOwner {
  readonly root: string;
  readonly health_fence: SharedArrayBuffer;
  assert_admission(): void;
  assert_healthy(): void;
  with_sidecar_open<T>(work: (scope: SidecarOpenScope) => Promise<T>): Promise<T>;
}
export interface CanonicalReader extends AsyncIterable<Uint8Array> {
  readonly byte_count: number;
  close(): Promise<void>;
}
export interface JournalCursor<T> extends AsyncIterable<T> {
  close(): Promise<void>;
}
export interface FreshJournalCommand {
  command_id: string;
  command: CanonicalReader;
}
export interface FreshCommandCursor extends JournalCursor<FreshJournalCommand> {
  /** Original reply command count, including duplicates. */
  readonly command_count: number;
}
export interface UnpublishedJournalCommand extends FreshJournalCommand {
  result: CanonicalReader;
}
export interface UnreportedJournalResult {
  command_id: string;
  result: CanonicalReader;
}
export interface AsyncCommandJournal extends OutboxCleanup {
  poll_cursor(): Promise<bigint>;
  lower_poll_cursor(high: bigint, cursor_at_lease: bigint): Promise<boolean>;
  record_poll_reply(
    bytes: Uint8Array | string,
    incarnation: string,
    polled_after: bigint,
  ): Promise<FreshCommandCursor>;
  result_of(command_id: string): Promise<CanonicalReader | null>;
  finish(command_id: string, result: Uint8Array | string, outputs_owed: boolean): Promise<void>;
  mark_outputs_published(command_id: string): Promise<void>;
  unpublished(): Promise<JournalCursor<UnpublishedJournalCommand>>;
  unreported(limit: number): Promise<JournalCursor<UnreportedJournalResult>>;
  mark_reported(command_ids: readonly string[]): Promise<void>;
  shrink_to_tombstones(): Promise<void>;
  stop(options: StopOptions): Promise<void>;
  settled(): Promise<void>;
}
