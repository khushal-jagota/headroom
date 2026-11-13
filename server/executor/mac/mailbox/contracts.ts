/** Private custody ports. Native runners, catalogs, HTTP, and codecs have separate owners. */
import type {
  ChildCommand,
  CommandOutcome,
  CommandOutcomeReply,
  ModelListRequest,
} from '../../wire.generated.ts';
import type { AsyncCommandJournal, CanonicalReader } from '../storage/journal_contracts.ts';
import { DeliveryCancelled } from '../../../conversation/backends/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
export class MailboxRunCancelled extends DeliveryCancelled {
  readonly stop_options: StopOptions;
  constructor(options: StopOptions) {
    super('Mac mailbox stopped.');
    this.stop_options = options;
  }
}
export interface MailboxRunner {
  readonly admission_stopped: boolean;
  /** Cancel only this waiter, never the runner's planned-replay gate. */
  planned_replay_finished(signal: AbortSignal): Promise<void>;
  /** Abort stops owned native work and joins its actual custody, not only this waiter.
   * MailboxRunCancelled supplies the host's original absolute shutdown deadline. */
  run(command: ChildCommand, signal: AbortSignal): Promise<CommandOutcome>;
  publish_owed_outputs(command: CanonicalReader, result: CanonicalReader): Promise<void>;
}
export interface CanonicalCatalogResult {
  /** Every reader starts at byte zero of the same immutable complete CommandResult. */
  open(): Promise<CanonicalReader>;
  /** A failed close remains retained by the catalog owner until its actual cleanup. */
  close(): Promise<void>;
}
export interface MailboxCatalog {
  read_result(command_id: string, request: ModelListRequest): Promise<CanonicalCatalogResult>;
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
  incarnation: string;
  result_finished(): void;
}
export interface MailboxDependencies extends Omit<QueueDependencies, 'result_finished'> {
  client: MailboxClient;
  incarnation: string;
}
