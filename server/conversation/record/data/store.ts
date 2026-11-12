/** Durable record primitives. Event append is completed with its projection in PR2. */
import { randomUUID } from 'node:crypto';
import type {
  Database,
  ReadSql,
  SqlValue,
  TransactionSql,
  WriteOptions,
} from '../../../core/database/contracts.ts';
import {
  ComposerCatalogEntry,
  ConversationAlreadyStarted,
  PromptDeliveryStarted,
  PromptDispatchDecision,
} from '../../contracts.ts';
import type {
  ResolvedConversationStart,
  NativeMessageOperation,
  NativeMessageAdmission,
} from '../../contracts.ts';
import {
  conversation_event_payload_from_canonical_json,
  conversation_event_payload_to_canonical_json,
} from '../codec.ts';
import { ConversationRecord, ConversationRecordMissing } from '../contracts.ts';
import type {
  ConversationRecordValues,
  SessionUpdate,
  StoredConversationEvent,
  ConversationStoreOptions,
  AppendOptions,
  EventMutation,
  EventTransactionResult,
  PreparedAttention,
  DeliveredPromptOptions,
  OpenRunTurn,
  ConversationRecordStore,
  AcceptedRecordEvent,
  RecordCommitParticipant,
  ConversationAttentionFacts,
} from '../contracts.ts';
import {
  LIFECYCLE_CONTRACT_VERSION,
  LifecycleKind as K,
  LifecycleOperation as O,
  LifecycleStatus as S,
} from '../lifecycle.ts';
import type { ConversationTransactionParticipant, LifecycleFact } from '../lifecycle.ts';
import { apply_facts, participate, read_facts } from './lifecycle.ts';
import { RecordSettlement } from '../settlement.ts';
import type { RecordingSettlementOptions } from '../settlement.ts';
import { facts_after_restart } from '../../logic/retained_turn.ts';
import { append_projection } from '../../views/data/writer.ts';
import {
  conversation_event_kinds_need_the_change_signal,
  PromptDiscardedEventPayload,
} from '../../events.ts';
import type { ConversationEventPayload } from '../../events.ts';

interface ConversationRow extends Omit<
  ConversationRecordValues,
  'identity_environment_variables' | 'composer_catalog'
> {
  identity_environment_variables: string;
  composer_catalog: string;
}
interface EventRow extends Omit<StoredConversationEvent, 'payload'> {
  payload: string;
}
const EVENT_COLUMNS = 'conversation_id,sequence,kind,payload,created_at';
function record(row: ConversationRow): ConversationRecord {
  return new ConversationRecord({
    ...row,
    identity_environment_variables: JSON.parse(row.identity_environment_variables) as [
      string,
      string,
    ][],
    composer_catalog: (
      JSON.parse(row.composer_catalog) as ConstructorParameters<typeof ComposerCatalogEntry>[0][]
    ).map((entry) => new ComposerCatalogEntry(entry)),
  });
}
function stored_event(row: EventRow): StoredConversationEvent {
  return { ...row, payload: conversation_event_payload_from_canonical_json(row.kind, row.payload) };
}
function record_from_start(
  start: ResolvedConversationStart,
  created_at: number,
): ConversationRecord {
  return new ConversationRecord({
    conversation_id: start.conversation_id,
    backend_key: start.backend_key,
    model: start.model,
    reasoning_effort: start.reasoning_effort,
    workspace_folder: start.workspace_folder,
    workspace_path_policy: start.workspace_path_policy,
    role_text: start.role_materials?.role_text ?? null,
    identity_environment_variables: start.role_materials?.identity_environment_variables ?? [],
    access: start.access,
    vendor_session_cursor: null,
    composer_catalog: [],
    latest_sequence: 0,
    latest_agent_activity_at: null,
    latest_agent_activity_sequence: 0,
    automatically_compacted_through_sequence: 0,
    automatic_compaction_attempted_through_sequence: 0,
    owner_read_through_sequence: 0,
    created_at,
  });
}
function composer_catalog_to_json(entries: readonly ComposerCatalogEntry[]): string {
  return JSON.stringify(
    entries.map((entry) => ({
      kind: entry.kind,
      display_text: entry.display_text,
      insertion_text: entry.insertion_text,
      description: entry.description,
      argument_hint: entry.argument_hint,
    })),
  );
}

/** Common record reads and reservation primitives shared by the complete store. */
export class ConversationStorePrimitives {
  protected readonly database: Database;
  protected readonly integer_now: () => number;
  protected readonly transaction_participant: ConversationTransactionParticipant | null;
  constructor(
    database: Database,
    options: {
      integer_now?: () => number;
      transaction_participant?: ConversationTransactionParticipant | null;
    } = {},
  ) {
    this.database = database;
    this.integer_now = options.integer_now ?? (() => Math.floor(Date.now() / 1000));
    this.transaction_participant = options.transaction_participant ?? null;
  }
  create_conversation(start: ResolvedConversationStart): ConversationRecord {
    const value = record_from_start(start, this.integer_now());
    try {
      this.database.write((tx) =>
        tx.run(
          'INSERT INTO conversations (conversation_id,backend_key,model,reasoning_effort,workspace_folder,' +
            'workspace_path_policy,role_text,identity_environment_variables,access,vendor_session_cursor,' +
            'composer_catalog,latest_sequence,latest_agent_activity_at,latest_agent_activity_sequence,' +
            'automatically_compacted_through_sequence,automatic_compaction_attempted_through_sequence,' +
            'owner_read_through_sequence,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
          [
            value.conversation_id,
            value.backend_key,
            value.model,
            value.reasoning_effort,
            value.workspace_folder,
            value.workspace_path_policy,
            value.role_text,
            JSON.stringify(value.identity_environment_variables),
            value.access,
            value.vendor_session_cursor,
            composer_catalog_to_json(value.composer_catalog),
            value.latest_sequence,
            value.latest_agent_activity_at,
            value.latest_agent_activity_sequence,
            value.automatically_compacted_through_sequence,
            value.automatic_compaction_attempted_through_sequence,
            value.owner_read_through_sequence,
            value.created_at,
          ],
        ),
      );
    } catch (error) {
      if (
        error !== null &&
        typeof error === 'object' &&
        'code' in error &&
        typeof error.code === 'string' &&
        error.code.startsWith('SQLITE_CONSTRAINT')
      ) {
        throw new ConversationAlreadyStarted(value.conversation_id, { cause: error });
      }
      throw error;
    }
    return value;
  }
  read_conversation(conversation_id: string): ConversationRecord | null {
    return this.database.read((sql) => {
      const row = sql.get<ConversationRow>('SELECT * FROM conversations WHERE conversation_id=?', [
        conversation_id,
      ]);
      return row === undefined ? null : record(row);
    });
  }
  read_conversation_records(conversation_ids: readonly string[]): readonly ConversationRecord[] {
    const ids = [...new Set(conversation_ids)];
    if (!ids.length) {
      return [];
    }
    return this.database.read((sql) =>
      sql
        .all<ConversationRow>(
          `SELECT * FROM conversations WHERE conversation_id IN (${ids.map(() => '?').join(',')}) ORDER BY conversation_id`,
          ids,
        )
        .map(record),
    );
  }
  reserve_turn(conversation_id: string): number {
    return this.database.write(
      (tx) => {
        const facts = tx.get<{ maximum: number }>(
          'SELECT COALESCE(MAX(turn_number),0) AS maximum ' +
            'FROM conversation_lifecycle_facts WHERE conversation_id=?',
          [conversation_id],
        )!;
        const occurrence = tx.get<{ maximum: number }>(
          'SELECT COALESCE(MAX(turn_number),0) AS maximum ' +
            'FROM conversation_turn_occurrences WHERE conversation_id=?',
          [conversation_id],
        )!;
        const number = Math.max(facts.maximum, occurrence.maximum) + 1;
        const epoch = tx.get<{ epoch: string }>(
          'SELECT epoch FROM conversation_runtime_epoch WHERE singleton=1',
        );
        if (epoch === undefined) {
          throw new Error('conversation runtime epoch is absent');
        }
        tx.run('INSERT INTO conversation_turn_occurrences VALUES (?,?,?,?)', [
          conversation_id,
          number,
          randomUUID().replaceAll('-', ''),
          epoch.epoch,
        ]);
        apply_facts(tx, conversation_id, [
          {
            kind: K.turn,
            identity: String(number),
            turn_number: number,
            operation: O.turn,
            status: S.intent,
            provider_identity: null,
          },
          {
            kind: K.attempt,
            identity: `prompt:${number}`,
            turn_number: number,
            operation: O.prompt,
            status: S.intent,
            provider_identity: null,
          },
        ]);
        participate(tx, this.transaction_participant, {
          conversation_id,
          facts: read_facts(tx, conversation_id),
          receipt_sequences: [],
          contract_version: LIFECYCLE_CONTRACT_VERSION,
        });
        return number;
      },
      { changeSignal: 'silent' },
    );
  }
  lifecycle_facts(conversation_id: string): readonly LifecycleFact[] {
    return this.database.read((sql) => read_facts(sql, conversation_id));
  }
  read_events_after(
    conversation_id: string,
    after_sequence: number,
  ): readonly StoredConversationEvent[] {
    return this.database.read((sql) =>
      sql
        .all<EventRow>(
          `SELECT ${EVENT_COLUMNS} FROM conversation_events ` +
            'WHERE conversation_id=? AND sequence>? ORDER BY sequence',
          [conversation_id, after_sequence],
        )
        .map(stored_event),
    );
  }
  read_latest_events(
    conversation_id: string,
    options: { before_sequence: number | null; limit: number },
  ): readonly StoredConversationEvent[] {
    const parameters: SqlValue[] = [conversation_id];
    const before = options.before_sequence === null ? '' : ' AND sequence<?';
    if (options.before_sequence !== null) {
      parameters.push(options.before_sequence);
    }
    parameters.push(options.limit);
    return this.database.read((sql) =>
      sql
        .all<EventRow>(
          `SELECT * FROM (SELECT ${EVENT_COLUMNS} FROM conversation_events ` +
            `WHERE conversation_id=?${before} ORDER BY sequence DESC LIMIT ?) ORDER BY sequence`,
          parameters,
        )
        .map(stored_event),
    );
  }
  read_event(conversation_id: string, sequence: number): StoredConversationEvent | null {
    return this.database.read((sql) => {
      const row = sql.get<EventRow>(
        `SELECT ${EVENT_COLUMNS} FROM conversation_events WHERE conversation_id=? AND sequence=?`,
        [conversation_id, sequence],
      );
      return row === undefined ? null : stored_event(row);
    });
  }
  sender_message_outcome(
    conversation_id: string,
    sender_message_id: string,
  ): StoredConversationEvent | null {
    return this.database.read((sql) => {
      const row = sql.get<EventRow>(
        `SELECT ${EVENT_COLUMNS} FROM conversation_events ` +
          "WHERE conversation_id=? AND json_extract(payload,'$.sender_message_id')=? " +
          "AND kind IN ('prompt','prompt_delivery_refused','prompt_delivery_uncertain','prompt_discarded','message_to_owner') " +
          'ORDER BY sequence DESC LIMIT 1',
        [conversation_id, sender_message_id],
      );
      return row === undefined ? null : stored_event(row);
    });
  }
  latest_turn_ended_sequences(conversation_ids: readonly string[]): ReadonlyMap<string, number> {
    const ids = [...new Set(conversation_ids)];
    if (!ids.length) {
      return new Map();
    }
    return this.database.read(
      (sql) =>
        new Map(
          sql
            .all<{ conversation_id: string; latest: number }>(
              'SELECT conversation_id,MAX(sequence) AS latest FROM conversation_events ' +
                `WHERE kind='turn_ended' AND conversation_id IN (${ids.map(() => '?').join(',')}) GROUP BY conversation_id ORDER BY conversation_id`,
              ids,
            )
            .map((row) => [row.conversation_id, row.latest]),
        ),
    );
  }
  owner_read_through_sequences(conversation_ids: readonly string[]): ReadonlyMap<string, number> {
    const ids = [...new Set(conversation_ids)];
    if (!ids.length) {
      return new Map();
    }
    return this.database.read(
      (sql) =>
        new Map(
          sql
            .all<{ conversation_id: string; owner_read_through_sequence: number }>(
              'SELECT conversation_id,owner_read_through_sequence FROM conversations ' +
                `WHERE conversation_id IN (${ids.map(() => '?').join(',')}) ORDER BY conversation_id`,
              ids,
            )
            .map((row) => [row.conversation_id, row.owner_read_through_sequence]),
        ),
    );
  }
  has_delivered_prompt(conversation_id: string): boolean {
    return this.database.read(
      (sql) =>
        sql.get(
          "SELECT 1 FROM conversation_events WHERE conversation_id=? AND kind='prompt' LIMIT 1",
          [conversation_id],
        ) !== undefined,
    );
  }
  update_session_in_transaction(
    tx: TransactionSql,
    conversation_id: string,
    update: SessionUpdate,
  ): void {
    if (update.vendor_session_cursor != null) {
      tx.run('UPDATE conversations SET vendor_session_cursor=? WHERE conversation_id=?', [
        update.vendor_session_cursor,
        conversation_id,
      ]);
    }
    if (update.composer_catalog != null) {
      tx.run('UPDATE conversations SET composer_catalog=? WHERE conversation_id=?', [
        composer_catalog_to_json(update.composer_catalog),
        conversation_id,
      ]);
    }
  }
}

interface WriteResult<T> {
  readonly value: T;
  readonly payloads: readonly ConversationEventPayload[];
  readonly always_announce?: boolean;
}
interface InsertOptions {
  readonly lifecycle?: readonly LifecycleFact[];
  readonly recording_receipt?: NativeMessageOperation | null;
  readonly extra_recording_receipts?: readonly NativeMessageOperation[];
  readonly native_message_admission?: NativeMessageAdmission | null;
}
function synchronously<T>(operation: () => T): T {
  const result = operation();
  if (
    result !== null &&
    (typeof result === 'object' || typeof result === 'function') &&
    typeof (result as { then?: unknown }).then === 'function'
  ) {
    if (result instanceof Promise) {
      void result.catch(() => {});
    }
    throw new TypeError('conversation transaction participants must be synchronous');
  }
  return result;
}

/** The record owns every append. The core publishes returned rows after this call commits.
 * Each synchronous method attempts one transaction. The async core uses settle_recording
 * around complete writes for Python's contention and retained-save retry behavior.
 */
export class ConversationStore
  extends ConversationStorePrimitives
  implements ConversationRecordStore
{
  readonly #participants: ConversationStoreOptions;
  readonly #settlement = new RecordSettlement();

  constructor(database: Database, options: ConversationStoreOptions = {}) {
    super(database, options);
    this.#participants = options;
  }

  settle_recording<T>(
    conversation_id: string,
    operation: () => T | Promise<T>,
    options: RecordingSettlementOptions = {},
  ): Promise<T> {
    return this.#settlement.settle_recording(conversation_id, operation, options);
  }

  finish_writes(): Promise<void> {
    return this.#settlement.finish_writes();
  }

  #write<T>(operation: (tx: TransactionSql) => WriteResult<T>): T {
    const options: WriteOptions = { changeSignal: 'silent' };
    return this.database.write((tx) => {
      const result = operation(tx);
      if (
        result.always_announce ||
        conversation_event_kinds_need_the_change_signal(
          result.payloads.map((payload) => payload.kind),
        )
      ) {
        options.changeSignal = 'emit';
      }
      return result.value;
    }, options);
  }

  #require_record(
    tx: ReadSql,
    conversation_id: string,
  ): { latest_sequence: number; owner_read_through_sequence: number } {
    const row = tx.get<{ latest_sequence: number; owner_read_through_sequence: number }>(
      'SELECT latest_sequence,owner_read_through_sequence FROM conversations WHERE conversation_id=?',
      [conversation_id],
    );
    if (row === undefined) {
      throw new ConversationRecordMissing(conversation_id);
    }
    return row;
  }

  #prepare_attention(tx: TransactionSql, conversation_id: string): PreparedAttention | null {
    const prepare = this.#participants.prepare_attention;
    return prepare === undefined ? null : synchronously(() => prepare(tx, conversation_id));
  }

  #finish_attention(
    tx: TransactionSql,
    conversation_id: string,
    prepared: PreparedAttention | null,
    written: readonly StoredConversationEvent[],
    occurred_at: number,
  ): void {
    if (prepared !== null) {
      const current = this.#require_record(tx, conversation_id);
      synchronously(() =>
        prepared(tx, {
          written,
          owner_read_through_sequence: current.owner_read_through_sequence,
          occurred_at,
        }),
      );
    }
  }

  #participate(
    tx: TransactionSql,
    conversation_id: string,
    written: readonly StoredConversationEvent[],
  ): void {
    participate(tx, this.transaction_participant, {
      conversation_id,
      facts: read_facts(tx, conversation_id),
      receipt_sequences: written.map((event) => event.sequence),
      contract_version: LIFECYCLE_CONTRACT_VERSION,
    });
  }

  #claim_revisions(sql: ReadSql, conversation_id: string): string {
    const read = this.#participants.recovery_claim_revisions;
    return JSON.stringify(
      read === undefined ? [] : synchronously(() => read(sql, conversation_id)),
    );
  }

  #settle_delivery(
    tx: TransactionSql,
    conversation_id: string,
    facts: readonly LifecycleFact[],
    composed_content_delivered?: boolean,
  ): void {
    const settle = this.#participants.settle_delivery;
    if (settle !== undefined) {
      synchronously(() =>
        settle(tx, conversation_id, {
          facts,
          now: this.integer_now(),
          ...(composed_content_delivered === undefined ? {} : { composed_content_delivered }),
        }),
      );
    }
  }

  #insert_rows(
    tx: TransactionSql,
    conversation_id: string,
    payloads: readonly ConversationEventPayload[],
    options: InsertOptions = {},
  ): readonly StoredConversationEvent[] {
    const expand = this.#participants.event_participant;
    const all_payloads =
      expand === undefined
        ? payloads
        : [...payloads, ...synchronously(() => expand(tx, conversation_id, payloads))];
    const created_at = this.integer_now();
    const latest = this.#require_record(tx, conversation_id).latest_sequence;
    const operations = [
      ...(options.recording_receipt == null ? [] : [options.recording_receipt]),
      ...(options.extra_recording_receipts ?? []),
    ];
    const written = all_payloads.map((payload, offset): StoredConversationEvent => {
      const sequence = latest + offset + 1;
      tx.run(
        'INSERT INTO conversation_events (conversation_id,sequence,kind,payload,created_at) VALUES (?,?,?,?,?)',
        [
          conversation_id,
          sequence,
          payload.kind,
          conversation_event_payload_to_canonical_json(payload),
          created_at,
        ],
      );
      for (const operation of operations) {
        if (
          'sender_message_id' in payload &&
          payload.sender_message_id === operation.submission_id &&
          operation.in_commit != null
        ) {
          synchronously(() =>
            operation.in_commit!(
              tx,
              conversation_id,
              payload,
              options.native_message_admission ?? null,
            ),
          );
        }
      }
      return { conversation_id, sequence, kind: payload.kind, payload, created_at };
    });
    tx.run('UPDATE conversations SET latest_sequence=? WHERE conversation_id=?', [
      latest + all_payloads.length,
      conversation_id,
    ]);
    const lifecycle = options.lifecycle ?? [];
    apply_facts(tx, conversation_id, lifecycle);
    this.#settle_delivery(
      tx,
      conversation_id,
      lifecycle.filter(
        (fact) =>
          fact.status === S.refused || fact.status === S.unknown || fact.status === S.suppressed,
      ),
    );
    append_projection(tx, written);
    return written;
  }

  append_in_transaction(
    tx: TransactionSql,
    conversation_id: string,
    payloads: readonly ConversationEventPayload[],
    options: AppendOptions = {},
  ): readonly StoredConversationEvent[] {
    if (payloads.length === 0 && (options.lifecycle?.length ?? 0) === 0) {
      return [];
    }
    const prepared = this.#prepare_attention(tx, conversation_id);
    const written = this.#insert_rows(tx, conversation_id, payloads, options);
    if (written.length === 0) {
      this.#participate(tx, conversation_id, written);
      return written;
    }
    const last = written[written.length - 1];
    if (options.agent_activity) {
      tx.run(
        'UPDATE conversations SET latest_agent_activity_at=?,latest_agent_activity_sequence=? WHERE conversation_id=?',
        [last.created_at, last.sequence, conversation_id],
      );
    }
    if (options.automatic_compaction_confirmed) {
      tx.run(
        'UPDATE conversations SET automatically_compacted_through_sequence=latest_agent_activity_sequence,' +
          'automatic_compaction_attempted_through_sequence=latest_agent_activity_sequence WHERE conversation_id=?',
        [conversation_id],
      );
    } else if (options.automatic_compaction_result === 'not_compacted') {
      tx.run(
        'UPDATE conversations SET automatic_compaction_attempted_through_sequence=latest_agent_activity_sequence ' +
          'WHERE conversation_id=?',
        [conversation_id],
      );
    }
    const failure_notice = this.#participants.failure_notice;
    if (options.create_failure_notice && failure_notice !== undefined) {
      const error_summary =
        'error_summary' in last.payload && last.payload.error_summary !== null
          ? last.payload.error_summary
          : 'None';
      synchronously(() =>
        failure_notice(tx, {
          conversation_id,
          event_sequence: last.sequence,
          error_summary,
          now: last.created_at,
        }),
      );
    }
    if (options.owner_read_through_sequence != null) {
      tx.run(
        'UPDATE conversations SET owner_read_through_sequence=MAX(owner_read_through_sequence,?) WHERE conversation_id=?',
        [options.owner_read_through_sequence, conversation_id],
      );
    }
    this.#finish_attention(tx, conversation_id, prepared, written, last.created_at);
    this.#participate(tx, conversation_id, written);
    return written;
  }

  append_events(
    conversation_id: string,
    payloads: readonly ConversationEventPayload[],
    options: AppendOptions = {},
  ): readonly StoredConversationEvent[] {
    return this.#write((tx) => ({
      value: this.append_in_transaction(tx, conversation_id, payloads, options),
      payloads,
    }));
  }

  append_event(
    conversation_id: string,
    payload: ConversationEventPayload,
    options: AppendOptions = {},
  ): StoredConversationEvent {
    return this.append_events(conversation_id, [payload], options)[0];
  }

  append_turn_ending(
    conversation_id: string,
    payloads: readonly ConversationEventPayload[],
    options: AppendOptions,
  ): readonly StoredConversationEvent[] {
    return this.append_events(conversation_id, payloads, options);
  }

  transact_events<T>(
    conversation_id: string,
    work: (tx: TransactionSql) => EventMutation<T>,
  ): EventTransactionResult<T> {
    return this.#write((tx) => {
      this.#require_record(tx, conversation_id);
      const result = synchronously(() => work(tx));
      const written = this.append_in_transaction(tx, conversation_id, result.payloads);
      return {
        value: { value: result.value, written },
        payloads: written.map((row) => row.payload),
      };
    });
  }

  append_delivered_prompt(
    conversation_id: string,
    options: DeliveredPromptOptions,
  ): readonly StoredConversationEvent[] {
    const prompts = [options.prompt, ...(options.extra_prompts ?? [])];
    const payloads = options.model_change === null ? prompts : [options.model_change, ...prompts];
    return this.#write((tx) => {
      const prepared = this.#prepare_attention(tx, conversation_id);
      const written = this.#insert_rows(tx, conversation_id, payloads, {
        lifecycle: options.lifecycle,
        recording_receipt: options.recording_receipt,
        extra_recording_receipts: options.extra_recording_receipts,
        native_message_admission: { fate: new PromptDeliveryStarted() },
      });
      this.#settle_delivery(
        tx,
        conversation_id,
        options.lifecycle ?? [],
        options.composed_content_delivered ?? true,
      );
      if (options.model_change !== null) {
        tx.run('UPDATE conversations SET model=?,reasoning_effort=? WHERE conversation_id=?', [
          options.model_change.model,
          options.model_change.reasoning_effort,
          conversation_id,
        ]);
      }
      const owner_sequences = written
        .filter((row) => row.payload.kind === 'prompt' && row.payload.sender?.kind === 'owner')
        .map((row) => row.sequence);
      if (owner_sequences.length > 0) {
        const through = options.owner_read_through_sequence ?? Math.max(...owner_sequences);
        tx.run(
          'UPDATE conversations SET owner_read_through_sequence=MAX(owner_read_through_sequence,?) WHERE conversation_id=?',
          [through, conversation_id],
        );
      }
      this.#finish_attention(
        tx,
        conversation_id,
        prepared,
        written,
        written[written.length - 1].created_at,
      );
      this.#participate(tx, conversation_id, written);
      return { value: written, payloads };
    });
  }

  transition_lifecycle(conversation_id: string, facts: readonly LifecycleFact[]): void {
    this.#write((tx) => {
      const revisions = this.#claim_revisions(tx, conversation_id);
      apply_facts(tx, conversation_id, facts);
      this.#settle_delivery(tx, conversation_id, facts);
      this.#participate(tx, conversation_id, []);
      return {
        value: undefined,
        payloads: [],
        always_announce: revisions !== this.#claim_revisions(tx, conversation_id),
      };
    });
  }

  advance_owner_read_through_sequence(
    conversation_id: string,
    through_sequence: number,
  ): ConversationRecord | null {
    if (through_sequence < 0) {
      throw new Error('through_sequence must be non-negative');
    }
    return this.#write((tx) => {
      const prepared = this.#prepare_attention(tx, conversation_id);
      tx.run(
        'UPDATE conversations SET owner_read_through_sequence=MAX(owner_read_through_sequence,MIN(?,latest_sequence)) ' +
          'WHERE conversation_id=?',
        [through_sequence, conversation_id],
      );
      const row = tx.get<ConversationRow>('SELECT * FROM conversations WHERE conversation_id=?', [
        conversation_id,
      ]);
      if (row !== undefined) {
        this.#finish_attention(tx, conversation_id, prepared, [], this.integer_now());
      }
      return { value: row === undefined ? null : record(row), payloads: [], always_announce: true };
    });
  }

  conversations_due_for_automatic_compaction(options: {
    due_at_or_before: number;
    activity_after: number;
  }): readonly string[] {
    return this.database.read((sql) =>
      sql
        .all<{ conversation_id: string }>(
          'SELECT conversation_id FROM conversations WHERE latest_agent_activity_at IS NOT NULL ' +
            'AND latest_agent_activity_at<=? AND latest_agent_activity_at>? ' +
            'AND latest_agent_activity_sequence>automatic_compaction_attempted_through_sequence ' +
            'ORDER BY latest_agent_activity_at,conversation_id',
          [options.due_at_or_before, options.activity_after],
        )
        .map((row) => row.conversation_id),
    );
  }

  admit_sender_messages(
    conversation_id: string,
    messages: readonly PromptDiscardedEventPayload[],
    options: {
      lifecycle_if_all_suppressed?: readonly LifecycleFact[];
    } = {},
  ): readonly PromptDispatchDecision[] {
    return this.#write((tx) => {
      const prepared = this.#prepare_attention(tx, conversation_id);
      const revisions = this.#claim_revisions(tx, conversation_id);
      const discarded: PromptDiscardedEventPayload[] = [];
      const decisions = messages.map((message) => {
        const admit = this.#participants.admit_notice;
        const decision =
          admit === undefined
            ? new PromptDispatchDecision({ sender_message_id: message.sender_message_id })
            : synchronously(() =>
                admit(tx, conversation_id, message.sender_message_id, this.integer_now()),
              );
        const recover = this.#participants.admit_recovery;
        let reason = decision.suppression_reason;
        if (reason === null && message.recovery_references.length > 0 && recover !== undefined) {
          reason = synchronously(() =>
            recover(tx, conversation_id, message.recovery_references, this.integer_now()),
          );
        }
        if (reason !== null) {
          const previous = tx.get<{ payload: string }>(
            "SELECT payload FROM conversation_events WHERE conversation_id=? AND kind='prompt_discarded' " +
              "AND json_extract(payload,'$.sender_message_id')=? ORDER BY sequence LIMIT 1",
            [conversation_id, message.sender_message_id],
          );
          if (previous === undefined) {
            discarded.push(
              new PromptDiscardedEventPayload({ ...message, suppression_reason: reason }),
            );
          } else {
            reason =
              (JSON.parse(previous.payload) as { suppression_reason?: string })
                .suppression_reason || reason;
          }
        }
        return new PromptDispatchDecision({
          sender_message_id: message.sender_message_id,
          suppression_reason: reason,
          is_manager_notice: decision.is_manager_notice,
        });
      });
      const lifecycle =
        decisions.length > 0 && decisions.every((value) => value.suppression_reason !== null)
          ? (options.lifecycle_if_all_suppressed ?? [])
          : [];
      if (discarded.length > 0 || lifecycle.length > 0) {
        const written = this.#insert_rows(tx, conversation_id, discarded, { lifecycle });
        if (discarded.length > 0) {
          this.#finish_attention(tx, conversation_id, prepared, written, this.integer_now());
        }
        this.#participate(tx, conversation_id, written);
      }
      return {
        value: decisions,
        payloads: discarded,
        always_announce: revisions !== this.#claim_revisions(tx, conversation_id),
      };
    });
  }

  mark_held_sender_messages_leaving_queue(
    conversation_id: string,
    sender_message_ids: readonly string[],
  ): number {
    const ids = [...new Set(sender_message_ids)];
    if (ids.length === 0) {
      return 0;
    }
    return this.database.write((tx) => {
      const mark = this.#participants.mark_notices_leaving_queue;
      return mark === undefined
        ? 0
        : synchronously(() => mark(tx, conversation_id, ids, this.integer_now()));
    });
  }

  all_sender_messages_are_manager_notices(
    conversation_id: string,
    sender_message_ids: readonly (string | null)[],
  ): boolean {
    if (sender_message_ids.length === 0 || sender_message_ids.some((id) => id === null)) {
      return false;
    }
    const ids = [...new Set(sender_message_ids as readonly string[])];
    return this.database.read((sql) => {
      const read = this.#participants.all_are_notices;
      return read === undefined ? false : synchronously(() => read(sql, conversation_id, ids));
    });
  }

  record_background_task_started_in_transaction(
    tx: TransactionSql,
    conversation_id: string,
    task_id: string,
    task_description: string,
  ): void {
    tx.run(
      'INSERT OR REPLACE INTO conversation_background_tasks (conversation_id,task_id,task_description) VALUES (?,?,?)',
      [conversation_id, task_id, task_description],
    );
  }

  record_background_task_ended_in_transaction(
    tx: TransactionSql,
    conversation_id: string,
    task_id: string,
  ): string | null {
    return (
      tx.get<{ task_description: string }>(
        'DELETE FROM conversation_background_tasks WHERE conversation_id=? AND task_id=? RETURNING task_description',
        [conversation_id, task_id],
      )?.task_description ?? null
    );
  }

  take_background_tasks_in_transaction(
    tx: TransactionSql,
    conversation_id: string,
    options: { task_ids?: readonly string[] | null } = {},
  ): readonly string[] {
    const rows = tx.all<{ task_id: string; task_description: string }>(
      'SELECT task_id,task_description FROM conversation_background_tasks WHERE conversation_id=? ORDER BY rowid',
      [conversation_id],
    );
    const taken = rows.filter(
      (row) => options.task_ids == null || options.task_ids.includes(row.task_id),
    );
    for (const row of taken) {
      tx.run('DELETE FROM conversation_background_tasks WHERE conversation_id=? AND task_id=?', [
        conversation_id,
        row.task_id,
      ]);
    }
    return taken.map((row) => row.task_description);
  }

  take_background_tasks(
    conversation_id: string,
    options: { task_ids?: readonly string[] | null } = {},
  ): readonly string[] {
    return this.#write((tx) => {
      const taken = this.take_background_tasks_in_transaction(tx, conversation_id, options);
      return { value: taken, payloads: [], always_announce: taken.length > 0 };
    });
  }

  read_live_background_task_ids(): ReadonlyMap<string, readonly string[]> {
    return this.database.read((sql) => {
      const result = new Map<string, string[]>();
      for (const row of sql.all<{ conversation_id: string; task_id: string }>(
        'SELECT conversation_id,task_id FROM conversation_background_tasks ORDER BY conversation_id,rowid',
      )) {
        const ids = result.get(row.conversation_id) ?? [];
        ids.push(row.task_id);
        result.set(row.conversation_id, ids);
      }
      return result;
    });
  }

  conversation_ids_with_live_background_tasks(): ReadonlySet<string> {
    return new Set(this.read_live_background_task_ids().keys());
  }

  attention_facts(
    conversation_ids: readonly string[],
  ): ReadonlyMap<string, ConversationAttentionFacts> {
    const ids = [...new Set(conversation_ids)];
    if (ids.length === 0) {
      return new Map();
    }
    return this.database.read(
      (sql) =>
        new Map(
          sql
            .all<{
              conversation_id: string;
              owner_read_through_sequence: number;
              latest_owner_message: number | null;
              has_live_background_task: number;
              last_turn_ending: string | null;
            }>(
              'SELECT c.conversation_id,c.owner_read_through_sequence,e.latest_owner_message,' +
                'EXISTS(SELECT 1 FROM conversation_background_tasks b WHERE b.conversation_id=c.conversation_id) AS has_live_background_task,' +
                "(SELECT CASE WHEN te.kind='turn_ended' THEN json_extract(te.payload,'$.ending') ELSE NULL END " +
                "FROM conversation_events te WHERE te.conversation_id=c.conversation_id AND te.kind IN ('prompt','turn_ended') " +
                'AND te.sequence>COALESCE((SELECT a.through_sequence FROM conversation_error_acknowledgements a ' +
                'WHERE a.conversation_id=c.conversation_id),0) ORDER BY te.sequence DESC LIMIT 1) AS last_turn_ending ' +
                'FROM conversations c LEFT JOIN (SELECT conversation_id,MAX(sequence) AS latest_owner_message ' +
                "FROM conversation_events WHERE kind='message_to_owner' GROUP BY conversation_id) e ON e.conversation_id=c.conversation_id " +
                `WHERE c.conversation_id IN (${ids.map(() => '?').join(',')}) ORDER BY c.conversation_id`,
              ids,
            )
            .map((row) => [
              row.conversation_id,
              {
                unread_message_to_owner:
                  row.latest_owner_message !== null &&
                  row.latest_owner_message > row.owner_read_through_sequence,
                last_turn_failed: row.last_turn_ending === 'failed',
                has_live_background_task: Boolean(row.has_live_background_task),
              },
            ]),
        ),
    );
  }

  read_answer_events_in_transaction(
    tx: TransactionSql,
    conversation_id: string,
    answer_id: string,
  ): readonly StoredConversationEvent[] {
    return tx
      .all<EventRow>(
        `SELECT ${EVENT_COLUMNS} FROM conversation_events WHERE conversation_id=? ` +
          "AND kind IN ('permission_answered','user_input_answered','answer_applied') " +
          "AND json_extract(payload,'$.answer_id')=? ORDER BY sequence",
        [conversation_id, answer_id],
      )
      .map(stored_event);
  }

  commit_backend_event<T>(
    writer: (tx: TransactionSql) => { result: T; written: readonly StoredConversationEvent[] },
    in_commit: RecordCommitParticipant<T> | null = null,
    options: { always_announce?: boolean } = {},
  ): AcceptedRecordEvent<T> {
    return this.#write((tx) => {
      let written: readonly StoredConversationEvent[] = [];
      let taken = false;
      const take = (): T => {
        const accepted = synchronously(() => writer(tx));
        taken = true;
        written = accepted.written;
        return accepted.result;
      };
      const outcome =
        in_commit === null
          ? { result: take(), taken: true, written }
          : synchronously(() => in_commit(tx, take));
      if (taken && !outcome.taken) {
        // Unit 22 must settle its savepoint acceptance route against the write door.
        // Until then a refusal cannot commit an event that take already wrote.
        throw new Error('commit participant refused after taking the conversation event');
      }
      const result = { ...outcome, written: outcome.taken ? written : [] };
      return {
        value: result,
        payloads: result.written.map((row) => row.payload),
        always_announce: in_commit !== null || options.always_announce,
      };
    });
  }

  diagnostics_expired(conversation_id: string): boolean {
    return this.database.read(
      (sql) =>
        sql.get('SELECT 1 FROM conversation_expired_ranges WHERE conversation_id=? LIMIT 1', [
          conversation_id,
        ]) !== undefined,
    );
  }

  sequence_expired(conversation_id: string, sequence: number): boolean {
    return this.database.read((sql) => {
      const row = sql.get<{ last_sequence: number }>(
        'SELECT last_sequence FROM conversation_expired_ranges ' +
          'WHERE conversation_id=? AND first_sequence<=? ORDER BY first_sequence DESC LIMIT 1',
        [conversation_id, sequence],
      );
      return row !== undefined && sequence <= row.last_sequence;
    });
  }

  reconcile_in_transaction(tx: TransactionSql, open_runs: ReadonlyMap<string, OpenRunTurn>): void {
    const reconcile = this.#participants.reconcile_deployment;
    if (reconcile !== undefined) {
      synchronously(() => reconcile(tx, this.integer_now()));
    }
    const ids = tx.all<{ conversation_id: string }>(
      'SELECT DISTINCT conversation_id FROM conversation_lifecycle_facts ' +
        "WHERE status IN ('intent','running','pending','answering') ORDER BY conversation_id",
    );
    for (const { conversation_id } of ids) {
      const facts = facts_after_restart(
        read_facts(tx, conversation_id),
        open_runs.get(conversation_id) ?? null,
      );
      if (facts.length === 0) {
        continue;
      }
      apply_facts(tx, conversation_id, facts);
      this.#settle_delivery(tx, conversation_id, facts);
      this.#participate(tx, conversation_id, []);
    }
    const startup = this.#participants.startup_participant;
    if (startup !== undefined) {
      for (const [conversation_id, payloads] of synchronously(() => startup(tx))) {
        this.#insert_rows(tx, conversation_id, payloads);
      }
    }
  }
}

/** Fill the fake/system representation gap before the Ticket's guarded ownership write. */
export function ensure_started_conversation_record(
  tx: TransactionSql,
  start: ResolvedConversationStart,
  options: { created_at: number },
): void {
  const identity = JSON.stringify(start.role_materials?.identity_environment_variables ?? []);
  const expected = [
    start.backend_key,
    start.workspace_folder,
    start.workspace_path_policy,
    start.role_materials?.role_text ?? null,
    identity,
    start.access,
  ];
  tx.run(
    'INSERT OR IGNORE INTO conversations (conversation_id,backend_key,model,reasoning_effort,' +
      'workspace_folder,workspace_path_policy,role_text,identity_environment_variables,access,created_at) ' +
      'VALUES (?,?,?,?,?,?,?,?,?,?)',
    [
      start.conversation_id,
      start.backend_key,
      start.model,
      start.reasoning_effort,
      start.workspace_folder,
      start.workspace_path_policy,
      start.role_materials?.role_text ?? null,
      identity,
      start.access,
      options.created_at,
    ],
  );
  const row = tx.get<
    Pick<
      ConversationRow,
      | 'backend_key'
      | 'workspace_folder'
      | 'workspace_path_policy'
      | 'role_text'
      | 'identity_environment_variables'
      | 'access'
    >
  >(
    'SELECT backend_key,workspace_folder,workspace_path_policy,role_text,identity_environment_variables,access ' +
      'FROM conversations WHERE conversation_id=?',
    [start.conversation_id],
  );
  const actual =
    row === undefined
      ? null
      : [
          row.backend_key,
          row.workspace_folder,
          row.workspace_path_policy,
          row.role_text,
          row.identity_environment_variables,
          row.access,
        ];
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(
      `conversation ${start.conversation_id} started with values that differ from its durable record`,
    );
  }
}
