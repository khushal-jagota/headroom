/** Private custody ports. Native runners, catalogs, HTTP, and codecs have separate owners. */
import type { NumericJSON } from '../../../core/http/numeric_json.ts';
import type {
  ChildCommand,
  CommandOutcome,
  CommandOutcomeReply,
  ModelListRequest,
} from '../../wire.generated.ts';
import type {
  AsyncCommandJournal,
  CanonicalReader,
  RequiredJournalProtocol,
} from '../storage/journal_contracts.ts';
export interface MailboxRunner {
  readonly admission_stopped: boolean;
  /** Cancel only this waiter, never the runner's planned-replay gate. */
  planned_replay_finished(signal: AbortSignal): Promise<void>;
  /** The runner shields and owns native work and idempotent durable settlement. */
  run(command: NumericJSON<ChildCommand>): Promise<NumericJSON<CommandOutcome>>;
  publish_owed_outputs(command: CanonicalReader, result: CanonicalReader): Promise<void>;
}
export interface CanonicalCatalogResult {
  /** Every reader starts at byte zero of the same immutable complete CommandResult. */
  open(): Promise<CanonicalReader>;
  /** A failed close remains retained by the catalog owner until its actual cleanup. */
  close(): Promise<void>;
}
export interface MailboxCatalog {
  read_result(
    command_id: string,
    request: NumericJSON<ModelListRequest>,
  ): Promise<CanonicalCatalogResult>;
}
export interface MailboxClient {
  /** Own the actual request until settlement and enforce the poll response body cap. */
  poll_commands(after: bigint, hold_seconds: number, signal: AbortSignal): Promise<Uint8Array>;
  /** Refuse an oversized first report using byte_count. Never truncate it. */
  report_outcomes(body: CanonicalReader, signal: AbortSignal): Promise<CommandOutcomeReply>;
}
export interface QueueDependencies {
  runner: MailboxRunner;
  catalog: MailboxCatalog;
  journal: AsyncCommandJournal;
  protocol: RequiredJournalProtocol;
  result_finished(): void;
  on_failure(error: Error): void;
}
export interface ReporterDependencies {
  client: MailboxClient;
  journal: AsyncCommandJournal;
  on_failure(error: Error): void;
}
export interface PollDependencies extends ReporterDependencies {
  runner: MailboxRunner;
  protocol: RequiredJournalProtocol;
  incarnation: string;
  result_finished(): void;
}
export interface MailboxDependencies extends Omit<QueueDependencies, 'result_finished'> {
  client: MailboxClient;
  incarnation: string;
}
