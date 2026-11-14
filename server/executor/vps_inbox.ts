/** Ordered Mac intake: each effect and its receipt share the canonical write transaction. */
import type { Clock } from '../core/clock.ts';
import type { Principal } from '../core/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type { TransactionSql } from '../core/database/contracts.ts';
import { is_sqlite_error } from '../core/database/index.ts';
import { is_os_error } from '../core/system_errors.ts';
import type { ExecutorIdentity } from '../execution_identity.ts';
import type {
  AcceptedRecordEvent,
  RecordCommitParticipant,
} from '../conversation/record/contracts.ts';
import { FifoLock } from '../conversation/runtime/state.ts';
import { observe_shutdown, is_shutdown_business_cancellation } from '../conversation/shutdown.ts';
import { ServerLifecycleError } from '../lifecycle/contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import { WAITS } from '../stalls/registry.ts';
import type { DecodedBackendEvent } from './protocol.ts';
import {
  as_object,
  decode_value,
  decode_event_arguments,
  integer_value,
  ValueError,
  KeyError,
} from './protocol.ts';
import { inspect } from 'node:util';
import type { WireValue, FileOperationOptions, HeldFileLocation } from './contracts.ts';
import { FileCleanupError } from './files.ts';
import { InboxStore, MissingFiles } from './data/inbox.ts';
import type { PublishedFiles } from './data/inbox.ts';
import { open_child_assignment } from './data/child_custody.ts';
import { AgentWrites } from './vps_agent_writes.ts';
import { now_ms } from './clock.ts';
import { observe_promise } from '../core/promise_observer.ts';
import { is_json_object } from './protocol_json.ts';
import { MAC_ITEM_RETRY_LATER_SECONDS, MAX_ITEM_BATCH_ITEMS } from './configuration.ts';

export { InboxStore, MissingFiles, PublishedFiles } from './data/inbox.ts';
export type PublishFiles = () => Promise<PublishedFiles | null>;
type Json = WireValue<'JsonObject'>;
type Receipt = WireValue<'AcceptanceReceipt'>;
type Envelope = WireValue<'QueuedEnvelope'>;
type Participant = RecordCommitParticipant<Json>;
/** Source-shaped core admission. The real core binds these methods in the retained-run slice. */
export interface MacInboxConversations {
  take_backend_event(
    turn: WireValue<'TurnToken'> | null,
    arguments_: DecodedBackendEvent,
    options: { in_commit: Participant; conversation_id: string; retry_later_at: number },
  ): Promise<AcceptedRecordEvent<Json>>;
  fence_replaced_remote_child(
    reference: WireValue<'ChildReference'>,
    options: { retry_later_at: number },
  ): Promise<void>;
  take_answer_application(
    turn: WireValue<'TurnToken'>,
    answer_id: string,
    provider_id: string,
    kind: WireValue<'AskReference'>['kind'],
    in_commit: Participant,
    options: { retry_later_at: number },
  ): Promise<AcceptedRecordEvent<Json>>;
}
export interface InboxRuns {
  authority(child: WireValue<'ChildReference'>): Principal | Promise<Principal>;
  validate_stop(outcome: WireValue<'CommandOutcome'>): void | Promise<void>;
  accept_stop_receipt(outcome: WireValue<'CommandOutcome'>, receipt: Receipt): Promise<void>;
}
function storage_error(error: unknown): boolean {
  if (is_os_error(error)) return true;
  // sqlite3.OperationalError excludes integrity/programming/data exceptions.
  if (!is_sqlite_error(error)) return false;
  return !/^SQLITE_(CONSTRAINT|MISMATCH|MISUSE|RANGE|TOOBIG)(_|$)/.test(
    (error as { code: string }).code,
  );
}
function retry(message: string): PlannerError {
  return new PlannerError('retry_later', message);
}
function value_error(error: unknown): error is Error {
  return error instanceof ValueError || error instanceof KeyError || error instanceof TypeError;
}
async function lock_by(
  lock: FifoLock,
  deadline: number,
  message: string,
  after_release?: () => void,
): Promise<() => void> {
  let release: () => void;
  try {
    release = await lock.acquire(deadline);
  } catch (error) {
    after_release?.();
    if (error instanceof PlannerError && error.code === 'retry_later') throw retry(message);
    throw error;
  }
  return () => {
    release();
    after_release?.();
  };
}
export class MacInbox {
  readonly store: InboxStore;
  readonly clock: Clock;
  readonly runs: InboxRuns;
  readonly agent_writes: AgentWrites;
  conversations!: MacInboxConversations;
  private readonly conversation_locks = new Map<string, FifoLock>();
  private readonly publication_guards = new Map<string, FifoLock>();
  private readonly still_settling = new Map<string, Promise<Receipt>>();
  private readonly operations = new Set<Promise<unknown>>();
  private closed = false;
  private resolve_completion!: () => void;
  private reject_completion!: (error: unknown) => void;
  private readonly completion = new Promise<void>((resolve, reject) => {
    this.resolve_completion = resolve;
    this.reject_completion = reject;
  });
  constructor(store: InboxStore, clock: Clock, runs: InboxRuns) {
    this.store = store;
    this.clock = clock;
    this.runs = runs;
    this.agent_writes = new AgentWrites(store, clock, runs);
    void this.completion.catch(() => {});
  }
  private admit(): void {
    if (this.closed) throw new ServerLifecycleError('Executor inbox is closed.');
  }
  private own<T>(promise: Promise<T>): Promise<T> {
    this.operations.add(promise);
    void promise.then(
      () => this.operations.delete(promise),
      () => this.operations.delete(promise),
    );
    return promise;
  }
  async accept_batch(
    conversation_id: string,
    batch: WireValue<'ItemBatch'>,
    identity: ExecutorIdentity,
  ): Promise<WireValue<'ItemBatchReply'>> {
    this.admit();
    validate_batch(conversation_id, batch);
    return this.own(this.take_batch(batch, identity));
  }
  private async take_batch(
    batch: WireValue<'ItemBatch'>,
    identity: ExecutorIdentity,
  ): Promise<WireValue<'ItemBatchReply'>> {
    const deadline = performance.now() / 1000 + MAC_ITEM_RETRY_LATER_SECONDS;
    const receipts: Receipt[] = [];
    for (const item of batch.items) {
      const envelope = item.envelope;
      try {
        receipts.push(
          item.sender_refusal != null
            ? await this.refuse_item(envelope, identity, item.sender_refusal, {
                retry_later_at: deadline,
              })
            : await this.accept(envelope, identity, { retry_later_at: deadline }),
        );
      } catch (error) {
        if (error instanceof MissingFiles)
          return {
            receipts,
            stop: {
              sequence: envelope.sequence,
              reason: 'missing_files',
              message: error.message,
              missing_files: [...error.manifests],
            },
          };
        if (storage_error(error))
          return {
            receipts,
            stop: {
              sequence: envelope.sequence,
              reason: 'retry_later',
              message: `The VPS could not save this yet: ${(error as Error).message}`,
              missing_files: [],
            },
          };
        if (!(error instanceof PlannerError)) throw error;
        if (
          error.code !== 'retry_later' &&
          !(error.code === 'executor_incarnation_not_current' && receipts.length)
        )
          throw error;
        return {
          receipts,
          stop: {
            sequence: envelope.sequence,
            reason: 'retry_later',
            message: error.message,
            missing_files: [],
          },
        };
      }
    }
    return { receipts, stop: null };
  }
  async authorize_upload(upload: WireValue<'FileUploadRequest'>): Promise<void> {
    this.admit();
    const principal = await this.runs.authority(upload.child);
    await this.store.staging.file_target(upload.manifest);
    if (principal.kind !== 'ticket' || principal.id !== upload.manifest.owner_id)
      throw new PlannerError('agent_forbidden', 'artifact belongs to another Ticket');
  }
  async store_upload(
    upload: WireValue<'FileUploadRequest'>,
    source: HeldFileLocation,
    options: FileOperationOptions = {},
  ): Promise<void> {
    this.admit();
    return this.own(
      (async () => {
        await this.authorize_upload(upload);
        await this.store.store_upload(upload.manifest, source, options);
      })(),
    );
  }
  accept(
    envelope: Envelope,
    identity: ExecutorIdentity,
    options: { retry_later_at?: number } = {},
  ): Promise<Receipt> {
    this.admit();
    const deadline =
      options.retry_later_at ?? performance.now() / 1000 + MAC_ITEM_RETRY_LATER_SECONDS;
    return this.own(
      this.answer_item(envelope, deadline, () =>
        this.accept_envelope(envelope, identity, deadline),
      ),
    );
  }
  private async answer_item(
    envelope: Envelope,
    deadline: number,
    work: () => Promise<Receipt>,
  ): Promise<Receipt> {
    const id = envelope.conversation_id;
    let lock = this.conversation_locks.get(id);
    if (!lock) {
      lock = new FifoLock();
      this.conversation_locks.set(id, lock);
    }
    const release = await lock_by(
      lock,
      deadline,
      'An earlier item of this conversation is still being saved',
    );
    try {
      this.admit();
      const earlier = this.still_settling.get(id);
      if (earlier) {
        // An earlier rejection is already observed. It does not poison the next item.
        const settled = await observe_promise(
          earlier.then(
            () => undefined,
            () => undefined,
          ),
          Math.max(deadline * 1000 - performance.now(), 0),
        );
        if (!settled.completed)
          throw retry("An earlier attempt at this conversation's item is still settling");
        this.still_settling.delete(id);
      }
      this.admit();
      const wait = WAITS.track(
        'mac_item_still_saving',
        `${id}:${envelope.sender_id}:${integer_value(envelope.sequence)}`,
        {
          detail: `Saving Mac item ${integer_value(envelope.sequence)}`,
          conversation_id: id,
        },
      );
      const task = this.own(
        Promise.resolve()
          .then(() => {
            this.admit();
            return work();
          })
          .finally(() => wait.close()),
      );
      void task.catch(() => {});
      let result;
      try {
        result = await observe_promise(task, Math.max(deadline * 1000 - performance.now(), 0));
      } catch (error) {
        if (storage_error(error))
          throw retry(`The VPS could not save this item yet: ${(error as Error).message}`);
        throw error;
      }
      if (result.completed) return result.value;
      this.still_settling.set(id, task);
      const clear = () => {
        if (this.still_settling.get(id) === task) this.still_settling.delete(id);
      };
      void task.then(clear, clear);
      throw retry('This item is still being saved; send it again');
    } finally {
      release();
    }
  }
  async refuse_item(
    envelope: Envelope,
    identity: ExecutorIdentity,
    message: string,
    options: { retry_later_at?: number } = {},
  ): Promise<Receipt> {
    this.admit();
    const payload = envelope.payload;
    const raw = payload.child;
    const ask = payload.ask;
    const child = decode_value(
      is_json_object(raw) ? raw : is_json_object(ask) ? ask.child : null,
      'ChildReference',
    );
    let refusal = new PlannerError('validation', message);
    try {
      if (child.conversation_id !== envelope.conversation_id)
        throw new PlannerError('validation', 'item refusal belongs to another child');
      const principal = await this.runs.authority(child);
      for (const manifest of envelope.file_dependencies ?? []) {
        await this.store.staging.file_target(manifest);
        if (principal.kind !== 'ticket' || principal.id !== manifest.owner_id)
          throw new PlannerError('agent_forbidden', 'upload refusal belongs to another Ticket');
      }
    } catch (error) {
      if (!(error instanceof PlannerError || error instanceof ValueError)) throw error;
      refusal =
        error instanceof PlannerError ? error : new PlannerError('validation', error.message);
    }
    return this.own(
      this.answer_item(
        envelope,
        options.retry_later_at ?? performance.now() / 1000 + MAC_ITEM_RETRY_LATER_SECONDS,
        () => this.record_refusal(envelope, identity, refusal),
      ),
    );
  }
  private async publication_locks(envelope: Envelope, deadline: number): Promise<() => void> {
    const releases: (() => void)[] = [];
    try {
      for (const owner of [
        ...new Set((envelope.file_dependencies ?? []).map((manifest) => manifest.owner_id)),
      ].sort()) {
        const guard = this.publication_guards.get(owner) ?? new FifoLock();
        this.publication_guards.set(owner, guard);
        releases.push(
          await lock_by(guard, deadline, "Another item is still saving this Ticket's files", () => {
            // FifoLock stays locked while it hands ownership to a queued waiter.
            if (!guard.locked && this.publication_guards.get(owner) === guard)
              this.publication_guards.delete(owner);
          }),
        );
      }
      return () => {
        for (const release of releases.reverse()) release();
      };
    } catch (error) {
      for (const release of releases.reverse()) release();
      throw error;
    }
  }
  private async accept_envelope(
    envelope: Envelope,
    identity: ExecutorIdentity,
    deadline: number,
  ): Promise<Receipt> {
    let published: PublishedFiles | null = null;
    const close_published = async () => {
      await published?.close();
      published = null;
    };
    try {
      try {
        const previous = this.store.preflight(envelope, { identity });
        if (previous !== null) {
          if (
            envelope.kind === 'child_stopped' &&
            previous.disposition === 'accepted' &&
            previous.result.recorded === true
          )
            await this.runs.accept_stop_receipt(
              decode_value(envelope.payload, 'CommandOutcome'),
              previous,
            );
          return previous;
        }
        const release = await this.publication_locks(envelope, deadline);
        try {
          const publish: PublishFiles = async () => {
            if (!envelope.file_dependencies?.length) return null;
            published = await this.store.publish_file_dependencies(envelope, identity);
            return published;
          };
          switch (envelope.kind) {
            case 'backend_event':
              return await this.apply_event(envelope, identity, deadline, publish);
            case 'answer_application':
              return await this.apply_answer(envelope, identity, deadline, publish);
            case 'cli_write':
              return await this.agent_writes.accept(envelope, identity, publish);
            case 'child_stopped':
              return await this.apply_stop(envelope, identity, publish);
            default:
              throw new KeyError(envelope.kind);
          }
        } finally {
          try {
            await close_published();
          } finally {
            release();
          }
          published = null;
        }
      } catch (error) {
        if (
          error instanceof PlannerError &&
          ['retry_later', 'executor_incarnation_not_current'].includes(error.code)
        )
          throw error;
        if (
          error instanceof MissingFiles ||
          error instanceof FileCleanupError ||
          storage_error(error) ||
          is_shutdown_business_cancellation(error) ||
          error instanceof ServerLifecycleError
        )
          throw error;
        let refusal: PlannerError;
        if (error instanceof PlannerError) refusal = error;
        else if (value_error(error)) refusal = new PlannerError('validation', error.message);
        else {
          console.error(
            `Saving a Mac item failed, so it is refused unless already recorded: conversation=${envelope.conversation_id} sender=${envelope.sender_id} sequence=${integer_value(envelope.sequence)} submission=${envelope.submission_id} kind=${envelope.kind}:`,
            error,
          );
          refusal = new PlannerError(
            'save_failed',
            `The VPS could not save this item: ${error instanceof Error ? error.message : inspect(error)}`,
          );
        }
        return await this.record_refusal(envelope, identity, refusal);
      }
    } finally {
      // A partially prepared group retains cleanup in its own owner. A successful set belongs here.
      await close_published();
    }
  }
  private async record_refusal(
    envelope: Envelope,
    identity: ExecutorIdentity,
    refusal: PlannerError,
  ): Promise<Receipt> {
    return this.store.accept(
      envelope,
      () => {
        throw refusal;
      },
      now_ms(),
      { identity, check_files: false },
    );
  }
  private record_event(
    envelope: Envelope,
    identity: ExecutorIdentity,
    published: PublishedFiles | null,
    tx: TransactionSql,
    take: () => Json,
  ): AcceptedRecordEvent<Json> {
    const [receipt, taken] = this.store.accept_in_transaction(
      tx,
      envelope,
      () => take(),
      now_ms(),
      { identity, published },
    );
    return { result: as_object(receipt, 'AcceptanceReceipt'), taken, written: [] };
  }
  private async apply_event(
    envelope: Envelope,
    identity: ExecutorIdentity,
    deadline: number,
    publish: PublishFiles,
  ): Promise<Receipt> {
    const event = decode_value(envelope.payload, 'BackendEvent');
    await this.runs.authority(event.child);
    if (event.child.conversation_id !== envelope.conversation_id)
      throw new ValueError('event conversation conflicts with envelope');
    if (event.child.executor_incarnation !== identity.executor_incarnation)
      await this.conversations.fence_replaced_remote_child(event.child, {
        retry_later_at: deadline,
      });
    const decoded = decode_event_arguments(event);
    const published = await publish();
    const accepted = await this.conversations.take_backend_event(event.turn, decoded, {
      in_commit: (tx, take) => this.record_event(envelope, identity, published, tx, take),
      conversation_id: event.child.conversation_id,
      retry_later_at: deadline,
    });
    return decode_value(accepted.result, 'AcceptanceReceipt');
  }
  private async apply_stop(
    envelope: Envelope,
    identity: ExecutorIdentity,
    publish: PublishFiles,
  ): Promise<Receipt> {
    const outcome = decode_value(envelope.payload, 'CommandOutcome');
    if (outcome.child.conversation_id !== envelope.conversation_id)
      throw new ValueError('command outcome conversation conflicts with envelope');
    await this.runs.validate_stop(outcome);
    const published = await publish();
    const receipt = this.store.accept(
      envelope,
      () => ({
        recorded: true,
        stop_command_id: outcome.command_id,
        stopped_child: as_object(outcome.child, 'ChildReference'),
      }),
      now_ms(),
      { identity, published },
    );
    if (receipt.disposition === 'accepted') await this.runs.accept_stop_receipt(outcome, receipt);
    return receipt;
  }
  private async apply_answer(
    envelope: Envelope,
    identity: ExecutorIdentity,
    deadline: number,
    publish: PublishFiles,
  ): Promise<Receipt> {
    const application = decode_value(envelope.payload, 'AnswerApplication');
    if (
      application.ask.child.conversation_id !== envelope.conversation_id ||
      this.store.database.read((sql) => open_child_assignment(sql, application.ask.child)) === null
    )
      throw new ValueError('application lacks the original authenticated child assignment');
    const published = await publish();
    const accepted = await this.conversations.take_answer_application(
      application.ask.turn,
      application.answer_id,
      application.ask.ask_id,
      application.ask.kind,
      (tx, take) => this.record_event(envelope, identity, published, tx, take),
      { retry_later_at: deadline },
    );
    return decode_value(accepted.result, 'AcceptanceReceipt');
  }
  stop(options: StopOptions): Promise<void> {
    if (!this.closed) {
      this.closed = true;
      const pending = [...this.operations];
      const drain = Promise.allSettled(pending).then((results) => {
        const failures = results.filter(
          (result): result is PromiseRejectedResult =>
            result.status === 'rejected' &&
            !(result.reason instanceof PlannerError) &&
            !is_shutdown_business_cancellation(result.reason) &&
            !(result.reason instanceof ServerLifecycleError),
        );
        if (failures.length)
          throw new AggregateError(
            failures.map((result) => result.reason),
            'Executor inbox shutdown failed.',
          );
      });
      void drain.then(this.resolve_completion, this.reject_completion);
    }
    return observe_shutdown(
      this.completion,
      options,
      'Executor inbox saves remain unfinished at the shutdown deadline.',
    );
  }
  settled(): Promise<void> {
    return this.completion;
  }
}
export function validate_batch(conversation_id: string, batch: WireValue<'ItemBatch'>): void {
  if (batch.items.length < 1 || batch.items.length > MAX_ITEM_BATCH_ITEMS)
    throw new PlannerError('validation', 'an item batch holds 1 to 32 items');
  let previous: bigint | null = null;
  for (const [index, item] of batch.items.entries()) {
    const envelope = item.envelope;
    if (envelope.conversation_id !== conversation_id || envelope.sender_id !== batch.sender_id)
      throw new PlannerError('validation', 'a batch item belongs to another sender');
    const sequence = integer_value(envelope.sequence);
    if (previous !== null && sequence <= previous)
      throw new PlannerError('validation', 'batch items must ascend by sequence');
    previous = sequence;
    if (
      index < batch.items.length - 1 &&
      (envelope.kind !== 'backend_event' || envelope.file_dependencies?.length)
    )
      throw new PlannerError(
        'validation',
        'a batch ends after its first item that is not a plain event',
      );
  }
}
