/** The durable record boundary used by the conversation core. */
import type { TransactionSql } from '../../core/database/contracts.ts';
import { ConversationRoleMaterials, ResolvedConversationStart } from '../contracts.ts';
import type {
    ComposerCatalogEntry, ConversationAccess, ConversationBackendKey, NativeMessageAdmission,
    NativeMessageOperation, PromptDeliveryMode, WorkspacePathPolicy,
} from '../contracts.ts';
import { BackendSpawnFailed } from '../backends/contracts.ts';
import type { TurnToken } from '../backends/contracts.ts';
import type { AutomaticCompactionResult, ConversationEventKind, ConversationEventPayload, UserInputQuestion } from '../events.ts';
import type { MessageContent } from '../message_content.ts';
import { bind_conversation_origin } from '../logic/conversation_start_resolution.ts';
import type { LifecycleFact, LifecycleTransition } from './lifecycle.ts';

export class ConversationRecordMissing extends Error {}
export class ConversationRecordNamesNoModel extends BackendSpawnFailed {}

export interface ConversationRecordValues {
    readonly conversation_id: string;
    readonly backend_key: ConversationBackendKey;
    readonly model: string | null;
    readonly reasoning_effort: string | null;
    readonly workspace_folder: string;
    readonly workspace_path_policy: WorkspacePathPolicy;
    readonly role_text: string | null;
    readonly identity_environment_variables: readonly (readonly [string, string])[];
    readonly access: ConversationAccess;
    readonly vendor_session_cursor: string | null;
    readonly composer_catalog: readonly ComposerCatalogEntry[];
    readonly latest_sequence: number;
    readonly latest_agent_activity_at: number | null;
    readonly latest_agent_activity_sequence: number;
    readonly automatically_compacted_through_sequence: number;
    readonly automatic_compaction_attempted_through_sequence: number;
    readonly owner_read_through_sequence: number;
    readonly created_at: number;
}
export class ConversationRecord implements ConversationRecordValues {
    declare readonly conversation_id: string;
    declare readonly backend_key: ConversationBackendKey;
    declare readonly model: string | null;
    declare readonly reasoning_effort: string | null;
    declare readonly workspace_folder: string;
    declare readonly workspace_path_policy: WorkspacePathPolicy;
    declare readonly role_text: string | null;
    declare readonly identity_environment_variables: readonly (readonly [string, string])[];
    declare readonly access: ConversationAccess;
    declare readonly vendor_session_cursor: string | null;
    declare readonly composer_catalog: readonly ComposerCatalogEntry[];
    declare readonly latest_sequence: number;
    declare readonly latest_agent_activity_at: number | null;
    declare readonly latest_agent_activity_sequence: number;
    declare readonly automatically_compacted_through_sequence: number;
    declare readonly automatic_compaction_attempted_through_sequence: number;
    declare readonly owner_read_through_sequence: number;
    declare readonly created_at: number;
    constructor(values: ConversationRecordValues) { Object.assign(this, values); }
    resolved_start(): ResolvedConversationStart {
        const role_materials = this.role_text === null ? null : new ConversationRoleMaterials({
            role_text: this.role_text, identity_environment_variables: this.identity_environment_variables,
        });
        if (this.model === null) throw new ConversationRecordNamesNoModel(this.conversation_id);
        return new ResolvedConversationStart({
            conversation_id: this.conversation_id, backend_key: this.backend_key, model: this.model,
            reasoning_effort: this.reasoning_effort, role_materials: bind_conversation_origin(role_materials, this.conversation_id),
            workspace_folder: this.workspace_folder, workspace_path_policy: this.workspace_path_policy, access: this.access,
        });
    }
}
export interface StoredConversationEvent {
    readonly conversation_id: string;
    readonly sequence: number;
    readonly kind: ConversationEventKind;
    readonly payload: ConversationEventPayload;
    readonly created_at: number;
}
export interface ConversationAttentionFacts {
    readonly unread_message_to_owner: boolean;
    readonly last_turn_failed: boolean;
    readonly has_live_background_task: boolean;
}
export interface OpenRunTurn {
    readonly turn_number: number;
    readonly executor_process_current: boolean;
}
export interface UndeliveredTurnPrompt {
    readonly turn_number: number;
    readonly content: MessageContent;
    readonly sender_content: MessageContent;
    readonly sender_label: string;
    readonly sender_message_count: number;
    readonly mode: PromptDeliveryMode;
    readonly model_change: string | null;
    readonly reasoning_effort_change: string | null;
    readonly reasoning_effort_is_set: boolean;
    readonly automatic_compaction: boolean;
}
/** Mutable runtime value reconstructed by the retained-turn pure rule. */
export interface RunningTurn {
    token: TurnToken;
    automatic_compaction: boolean;
    compaction_confirmed: boolean;
    pending_permission_ask_ids: Set<string>;
    permission_provider_ids: Map<string, string>;
    user_input_provider_ids: Map<string, string>;
    pending_user_input_questions: Map<string, readonly UserInputQuestion[]>;
    automatic_notice: boolean;
    ended: boolean;
    callbacks_revoked: boolean;
    ending_is_the_cores: boolean;
}
export interface AppendOptions {
    readonly agent_activity?: boolean;
    readonly automatic_compaction_confirmed?: boolean;
    readonly automatic_compaction_result?: AutomaticCompactionResult | null;
    readonly create_failure_notice?: boolean;
    readonly owner_read_through_sequence?: number | null;
    readonly lifecycle?: readonly LifecycleFact[];
    readonly recording_receipt?: NativeMessageOperation | null;
    readonly native_message_admission?: NativeMessageAdmission | null;
}
export interface SessionUpdate {
    readonly vendor_session_cursor?: string | null;
    readonly composer_catalog?: readonly ComposerCatalogEntry[] | null;
}
export interface EventMutation<T> {
    readonly value: T;
    readonly payloads: readonly ConversationEventPayload[];
}
export interface EventTransactionResult<T> {
    readonly value: T;
    readonly written: readonly StoredConversationEvent[];
}
export type LifecycleParticipant = (tx: TransactionSql, candidate: LifecycleTransition) => void;
export type EventParticipant = (tx: TransactionSql, conversation_id: string,
    payloads: readonly ConversationEventPayload[]) => readonly ConversationEventPayload[];

/** The append implementation arrives with the atomic projection in PR2. */
export interface ConversationRecordStore {
    create_conversation(start: ResolvedConversationStart): ConversationRecord;
    read_conversation(conversation_id: string): ConversationRecord | null;
    read_conversation_records(conversation_ids: readonly string[]): readonly ConversationRecord[];
    read_events_after(conversation_id: string, after_sequence: number): readonly StoredConversationEvent[];
    read_latest_events(conversation_id: string, options: { before_sequence: number | null; limit: number }): readonly StoredConversationEvent[];
    read_event(conversation_id: string, sequence: number): StoredConversationEvent | null;
    sender_message_outcome(conversation_id: string, sender_message_id: string): StoredConversationEvent | null;
    latest_turn_ended_sequences(conversation_ids: readonly string[]): ReadonlyMap<string, number>;
    owner_read_through_sequences(conversation_ids: readonly string[]): ReadonlyMap<string, number>;
    has_delivered_prompt(conversation_id: string): boolean;
    reserve_turn(conversation_id: string): number;
    lifecycle_facts(conversation_id: string): readonly LifecycleFact[];
    append_events(conversation_id: string, payloads: readonly ConversationEventPayload[], options?: AppendOptions): readonly StoredConversationEvent[];
    transact_events<T>(conversation_id: string, work: (tx: TransactionSql) => EventMutation<T>): EventTransactionResult<T>;
    append_in_transaction(tx: TransactionSql, conversation_id: string, payloads: readonly ConversationEventPayload[], options?: AppendOptions): readonly StoredConversationEvent[];
    transition_lifecycle(conversation_id: string, facts: readonly LifecycleFact[]): void;
    update_session_in_transaction(tx: TransactionSql, conversation_id: string, update: SessionUpdate): void;
    reconcile_in_transaction(tx: TransactionSql, open_runs: ReadonlyMap<string, OpenRunTurn>): void;
}
