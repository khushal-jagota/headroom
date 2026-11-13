/** Command effects and their durable settlement. The event/lifecycle owner supplies shared state. */
import type {
  AskReference,
  ChildCommand,
  ChildReference,
  CommandOutcome,
  QueuedEnvelope,
  WireInteger,
} from '../../wire.generated.ts';
import type { AsyncCommandJournal, CanonicalReader } from '../storage/journal_contracts.ts';
import type { MacNativeChildrenContract } from './contracts.ts';

export type OwedOutput = Pick<
  QueuedEnvelope,
  'conversation_id' | 'kind' | 'payload' | 'submission_id'
>;
export type MacOutputEntry = Pick<QueuedEnvelope, 'conversation_id' | 'kind' | 'payload'> &
  Partial<Pick<QueuedEnvelope, 'submission_id'>>;
export interface MacOutputPublication {
  add(entry: MacOutputEntry, options?: { ticket_id?: string }): Promise<void>;
}
export interface MacCommandRecordView {
  readonly command: ChildCommand;
  /** Only currently unsettled actual work. Null after success or failure, independently of dedupe. */
  readonly task: Promise<CommandOutcome> | null;
  /** A finished outcome can still have a non-null task inside journal/output settlement. */
  readonly phase: 'preparing' | 'invoking' | 'finished';
  readonly outcome: CommandOutcome | null;
  readonly has_outputs: boolean;
  readonly outputs_published: boolean;
}
/** Exact retirement uses these same records and cannot invent successful command joins. */
export interface MacCommandWork {
  records(reference?: ChildReference): readonly MacCommandRecordView[];
  withdraw(command_id: string): boolean;
  settle_known(reference?: ChildReference): Promise<void>;
}
export interface MacCommandDependencies {
  readonly incarnation: string;
  readonly native_children: MacNativeChildrenContract;
  readonly journal: AsyncCommandJournal;
  readonly publication: MacOutputPublication;
  readonly state: {
    readonly admission_stopped: boolean;
    is_retiring(child_id: string): boolean;
    is_retired(child_id: string): boolean;
  };
  readonly asks: {
    get(child: ChildReference, ask_id: string): AskReference | null;
    /** Read and remove the current ask after native acceptance, as Python does. */
    take(child: ChildReference, ask_id: string): AskReference | null;
  };
  readonly inputs: {
    download_inputs(command: ChildCommand, signal: AbortSignal): Promise<void>;
  };
  /** The lifecycle owner joins command/source work and marks invocation under the native lock.
   * Failed source work or unpublished output must prevent a successful stop certificate. */
  readonly stop_exact: (
    command: ChildCommand,
    work: MacCommandWork,
    invoking: () => void,
  ) => Promise<CommandOutcome>;
  now_ms(): WireInteger;
}
export interface MacCommandServiceContract extends MacCommandWork {
  run(command: ChildCommand): Promise<CommandOutcome>;
  /** Maintenance supplies a known fate after actual joins, through this same settlement record. */
  adopt_unjournaled_outcome(
    command: ChildCommand,
    outcome: CommandOutcome,
  ): Promise<CommandOutcome>;
  /** Actual replay I/O is owned separately from newly invoked child commands. */
  settled_replays(): Promise<void>;
  /** Readers remain owned by the mailbox caller. */
  publish_owed_outputs(command: CanonicalReader, result: CanonicalReader): Promise<void>;
}
/** One actual ask/admission owner serves commands now and the event lifecycle composition later. */
export type MacChildStateContract = MacCommandDependencies['state'] &
  MacCommandDependencies['asks'] & {
    seal_admission(): void;
    resume_admission(): void;
    begin_retirement(child_id: string): void;
    /** Preserve the tombstone and discard only this exact child's callbacks. */
    mark_retired(child: ChildReference): void;
    install(ask: AskReference): void;
    clear(child: ChildReference): void;
    entries(child: ChildReference): readonly AskReference[];
    count(child?: ChildReference): number;
  };
