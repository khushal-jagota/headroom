/** Durable record primitives. Event append is completed with its projection in PR2. */
import { randomUUID } from 'node:crypto';
import type { Database, SqlValue, TransactionSql } from '../../../core/database/contracts.ts';
import { ComposerCatalogEntry, ConversationAlreadyStarted } from '../../contracts.ts';
import type { ResolvedConversationStart } from '../../contracts.ts';
import { conversation_event_payload_from_canonical_json } from '../codec.ts';
import {
    ConversationRecord,
} from '../contracts.ts';
import type {
    ConversationRecordValues, SessionUpdate, StoredConversationEvent,
} from '../contracts.ts';
import { LIFECYCLE_CONTRACT_VERSION, LifecycleKind as K, LifecycleOperation as O, LifecycleStatus as S } from '../lifecycle.ts';
import type { ConversationTransactionParticipant, LifecycleFact } from '../lifecycle.ts';
import { apply_facts, participate, read_facts } from './lifecycle.ts';

interface ConversationRow extends Omit<ConversationRecordValues, 'identity_environment_variables' | 'composer_catalog'> {
    identity_environment_variables: string;
    composer_catalog: string;
}
interface EventRow extends Omit<StoredConversationEvent, 'payload'> { payload: string }
const EVENT_COLUMNS = 'conversation_id,sequence,kind,payload,created_at';
function record(row: ConversationRow): ConversationRecord {
    return new ConversationRecord({
        ...row,
        identity_environment_variables: JSON.parse(row.identity_environment_variables) as [string, string][],
        composer_catalog: (JSON.parse(row.composer_catalog) as ConstructorParameters<typeof ComposerCatalogEntry>[0][])
            .map(entry => new ComposerCatalogEntry(entry)),
    });
}
function stored_event(row: EventRow): StoredConversationEvent {
    return { ...row, payload: conversation_event_payload_from_canonical_json(row.kind, row.payload) };
}
function record_from_start(start: ResolvedConversationStart, created_at: number): ConversationRecord {
    return new ConversationRecord({
        conversation_id: start.conversation_id, backend_key: start.backend_key, model: start.model,
        reasoning_effort: start.reasoning_effort, workspace_folder: start.workspace_folder,
        workspace_path_policy: start.workspace_path_policy,
        role_text: start.role_materials?.role_text ?? null,
        identity_environment_variables: start.role_materials?.identity_environment_variables ?? [],
        access: start.access, vendor_session_cursor: null, composer_catalog: [], latest_sequence: 0,
        latest_agent_activity_at: null, latest_agent_activity_sequence: 0,
        automatically_compacted_through_sequence: 0, automatic_compaction_attempted_through_sequence: 0,
        owner_read_through_sequence: 0, created_at,
    });
}
function composer_catalog_to_json(entries: readonly ComposerCatalogEntry[]): string {
    return JSON.stringify(entries.map(entry => ({
        kind: entry.kind, display_text: entry.display_text, insertion_text: entry.insertion_text,
        description: entry.description, argument_hint: entry.argument_hint,
    })));
}

/** PR1 exposes no append callers and installs no runtime or HTTP routes. */
export class ConversationStorePrimitives {
    protected readonly database: Database;
    protected readonly integer_now: () => number;
    protected readonly transaction_participant: ConversationTransactionParticipant | null;
    constructor(database: Database, options: {
        integer_now?: () => number;
        transaction_participant?: ConversationTransactionParticipant | null;
    } = {}) {
        this.database = database;
        this.integer_now = options.integer_now ?? (() => Math.floor(Date.now() / 1000));
        this.transaction_participant = options.transaction_participant ?? null;
    }
    create_conversation(start: ResolvedConversationStart): ConversationRecord {
        const value = record_from_start(start, this.integer_now());
        try {
            this.database.write(tx => tx.run(
                'INSERT INTO conversations (conversation_id,backend_key,model,reasoning_effort,workspace_folder,'
                + 'workspace_path_policy,role_text,identity_environment_variables,access,vendor_session_cursor,'
                + 'composer_catalog,latest_sequence,latest_agent_activity_at,latest_agent_activity_sequence,'
                + 'automatically_compacted_through_sequence,automatic_compaction_attempted_through_sequence,'
                + 'owner_read_through_sequence,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                [value.conversation_id, value.backend_key, value.model, value.reasoning_effort, value.workspace_folder,
                    value.workspace_path_policy, value.role_text, JSON.stringify(value.identity_environment_variables),
                    value.access, value.vendor_session_cursor, composer_catalog_to_json(value.composer_catalog),
                    value.latest_sequence, value.latest_agent_activity_at, value.latest_agent_activity_sequence,
                    value.automatically_compacted_through_sequence, value.automatic_compaction_attempted_through_sequence,
                    value.owner_read_through_sequence, value.created_at]));
        } catch (error) {
            if (error !== null && typeof error === 'object' && 'code' in error
                && typeof error.code === 'string' && error.code.startsWith('SQLITE_CONSTRAINT')) {
                throw new ConversationAlreadyStarted(value.conversation_id, { cause: error });
            }
            throw error;
        }
        return value;
    }
    read_conversation(conversation_id: string): ConversationRecord | null {
        return this.database.read(sql => {
            const row = sql.get<ConversationRow>('SELECT * FROM conversations WHERE conversation_id=?', [conversation_id]);
            return row === undefined ? null : record(row);
        });
    }
    read_conversation_records(conversation_ids: readonly string[]): readonly ConversationRecord[] {
        const ids = [...new Set(conversation_ids)];
        if (!ids.length) return [];
        return this.database.read(sql => sql.all<ConversationRow>(
            `SELECT * FROM conversations WHERE conversation_id IN (${ids.map(() => '?').join(',')}) ORDER BY conversation_id`, ids).map(record));
    }
    reserve_turn(conversation_id: string): number {
        return this.database.write(tx => {
            const facts = tx.get<{ maximum: number }>('SELECT COALESCE(MAX(turn_number),0) AS maximum '
                + 'FROM conversation_lifecycle_facts WHERE conversation_id=?', [conversation_id])!;
            const occurrence = tx.get<{ maximum: number }>('SELECT COALESCE(MAX(turn_number),0) AS maximum '
                + 'FROM conversation_turn_occurrences WHERE conversation_id=?', [conversation_id])!;
            const number = Math.max(facts.maximum, occurrence.maximum) + 1;
            const epoch = tx.get<{ epoch: string }>('SELECT epoch FROM conversation_runtime_epoch WHERE singleton=1');
            if (epoch === undefined) throw new Error('conversation runtime epoch is absent');
            tx.run('INSERT INTO conversation_turn_occurrences VALUES (?,?,?,?)',
                [conversation_id, number, randomUUID().replaceAll('-', ''), epoch.epoch]);
            apply_facts(tx, conversation_id, [
                { kind: K.turn, identity: String(number), turn_number: number, operation: O.turn, status: S.intent, provider_identity: null },
                { kind: K.attempt, identity: `prompt:${number}`, turn_number: number, operation: O.prompt, status: S.intent, provider_identity: null },
            ]);
            participate(tx, this.transaction_participant, {
                conversation_id, facts: read_facts(tx, conversation_id), receipt_sequences: [], contract_version: LIFECYCLE_CONTRACT_VERSION,
            });
            return number;
        }, { changeSignal: 'silent' });
    }
    lifecycle_facts(conversation_id: string): readonly LifecycleFact[] {
        return this.database.read(sql => read_facts(sql, conversation_id));
    }
    read_events_after(conversation_id: string, after_sequence: number): readonly StoredConversationEvent[] {
        return this.database.read(sql => sql.all<EventRow>(`SELECT ${EVENT_COLUMNS} FROM conversation_events `
            + 'WHERE conversation_id=? AND sequence>? ORDER BY sequence', [conversation_id, after_sequence]).map(stored_event));
    }
    read_latest_events(conversation_id: string, options: { before_sequence: number | null; limit: number }): readonly StoredConversationEvent[] {
        const parameters: SqlValue[] = [conversation_id];
        const before = options.before_sequence === null ? '' : ' AND sequence<?';
        if (options.before_sequence !== null) parameters.push(options.before_sequence);
        parameters.push(options.limit);
        return this.database.read(sql => sql.all<EventRow>(`SELECT * FROM (SELECT ${EVENT_COLUMNS} FROM conversation_events `
            + `WHERE conversation_id=?${before} ORDER BY sequence DESC LIMIT ?) ORDER BY sequence`, parameters).map(stored_event));
    }
    read_event(conversation_id: string, sequence: number): StoredConversationEvent | null {
        return this.database.read(sql => {
            const row = sql.get<EventRow>(`SELECT ${EVENT_COLUMNS} FROM conversation_events WHERE conversation_id=? AND sequence=?`,
                [conversation_id, sequence]);
            return row === undefined ? null : stored_event(row);
        });
    }
    sender_message_outcome(conversation_id: string, sender_message_id: string): StoredConversationEvent | null {
        return this.database.read(sql => {
            const row = sql.get<EventRow>(`SELECT ${EVENT_COLUMNS} FROM conversation_events `
                + "WHERE conversation_id=? AND json_extract(payload,'$.sender_message_id')=? "
                + "AND kind IN ('prompt','prompt_delivery_refused','prompt_delivery_uncertain','prompt_discarded','message_to_owner') "
                + 'ORDER BY sequence DESC LIMIT 1', [conversation_id, sender_message_id]);
            return row === undefined ? null : stored_event(row);
        });
    }
    latest_turn_ended_sequences(conversation_ids: readonly string[]): ReadonlyMap<string, number> {
        const ids = [...new Set(conversation_ids)];
        if (!ids.length) return new Map();
        return this.database.read(sql => new Map(sql.all<{ conversation_id: string; latest: number }>(
            'SELECT conversation_id,MAX(sequence) AS latest FROM conversation_events '
            + `WHERE kind='turn_ended' AND conversation_id IN (${ids.map(() => '?').join(',')}) GROUP BY conversation_id ORDER BY conversation_id`,
            ids).map(row => [row.conversation_id, row.latest])));
    }
    owner_read_through_sequences(conversation_ids: readonly string[]): ReadonlyMap<string, number> {
        const ids = [...new Set(conversation_ids)];
        if (!ids.length) return new Map();
        return this.database.read(sql => new Map(sql.all<{ conversation_id: string; owner_read_through_sequence: number }>(
            'SELECT conversation_id,owner_read_through_sequence FROM conversations '
            + `WHERE conversation_id IN (${ids.map(() => '?').join(',')}) ORDER BY conversation_id`, ids)
            .map(row => [row.conversation_id, row.owner_read_through_sequence])));
    }
    has_delivered_prompt(conversation_id: string): boolean {
        return this.database.read(sql => sql.get("SELECT 1 FROM conversation_events WHERE conversation_id=? AND kind='prompt' LIMIT 1", [conversation_id]) !== undefined);
    }
    update_session_in_transaction(tx: TransactionSql, conversation_id: string, update: SessionUpdate): void {
        if (update.vendor_session_cursor != null) {
            tx.run('UPDATE conversations SET vendor_session_cursor=? WHERE conversation_id=?', [update.vendor_session_cursor, conversation_id]);
        }
        if (update.composer_catalog != null) {
            tx.run('UPDATE conversations SET composer_catalog=? WHERE conversation_id=?', [composer_catalog_to_json(update.composer_catalog), conversation_id]);
        }
    }
}

/** Fill the fake/system representation gap before the Ticket's guarded ownership write. */
export function ensure_started_conversation_record(tx: TransactionSql, start: ResolvedConversationStart, options: { created_at: number }): void {
    const identity = JSON.stringify(start.role_materials?.identity_environment_variables ?? []);
    const expected = [start.backend_key, start.workspace_folder, start.workspace_path_policy,
        start.role_materials?.role_text ?? null, identity, start.access];
    tx.run('INSERT OR IGNORE INTO conversations (conversation_id,backend_key,model,reasoning_effort,'
        + 'workspace_folder,workspace_path_policy,role_text,identity_environment_variables,access,created_at) '
        + 'VALUES (?,?,?,?,?,?,?,?,?,?)', [start.conversation_id, start.backend_key, start.model, start.reasoning_effort,
        start.workspace_folder, start.workspace_path_policy, start.role_materials?.role_text ?? null, identity, start.access, options.created_at]);
    const row = tx.get<Pick<ConversationRow, 'backend_key' | 'workspace_folder' | 'workspace_path_policy' | 'role_text' | 'identity_environment_variables' | 'access'>>(
        'SELECT backend_key,workspace_folder,workspace_path_policy,role_text,identity_environment_variables,access '
        + 'FROM conversations WHERE conversation_id=?', [start.conversation_id]);
    const actual = row === undefined ? null : [row.backend_key, row.workspace_folder, row.workspace_path_policy,
        row.role_text, row.identity_environment_variables, row.access];
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
        throw new Error(`conversation ${start.conversation_id} started with values that differ from its durable record`);
    }
}
