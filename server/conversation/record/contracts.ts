/** The durable record boundary used by the conversation core. */
import type { ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import { ConversationRoleMaterials, ResolvedConversationStart } from '../contracts.ts';
import type {
    ComposerCatalogEntry, ConversationAccess, ConversationBackendKey, NativeMessageAdmission,
    NativeMessageOperation, PromptDeliveryMode, WorkspacePathPolicy, PromptDispatchDecision, DeploymentRecoveryReference,
} from '../contracts.ts';
import { BackendSpawnFailed } from '../backends/contracts.ts';
import type { TurnToken } from '../backends/contracts.ts';
import type { AutomaticCompactionResult, ConversationEventKind, ConversationEventPayload, UserInputQuestion, PromptEventPayload, ModelChangedEventPayload, PromptDiscardedEventPayload } from '../events.ts';
import type { MessageContent } from '../message_content.ts';
import { bind_conversation_origin } from '../logic/conversation_start_resolution.ts';
import type { RecordingSettlementOptions } from './settlement.ts';
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
    constructor(values: ConversationRecordValues) {
        Object.assign(this, values);
    }
    resolved_start(): ResolvedConversationStart {
        const role_materials = this.role_text === null ? null : new ConversationRoleMaterials({
            role_text: this.role_text, identity_environment_variables: this.identity_environment_variables,
        });
        if (this.model === null) {
            throw new ConversationRecordNamesNoModel(this.conversation_id);
        }
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

/** Changes to pass to an owner-prepared attention projection after the source write. */
export interface AttentionChange {
    readonly written: readonly StoredConversationEvent[];
    readonly owner_read_through_sequence: number;
    readonly occurred_at: number;
}
export type PreparedAttention = (tx: TransactionSql, change: AttentionChange) => void;
export interface DeliverySettlement {
    readonly facts: readonly LifecycleFact[];
    readonly now: number;
    readonly composed_content_delivered?: boolean;
}
/** Each callback uses the record's existing transaction and never commits or awaits. */
export interface RecordParticipants {
    readonly event_participant?: EventParticipant;
    readonly transaction_participant?: LifecycleParticipant;
    readonly prepare_attention?: (tx: TransactionSql, conversation_id: string) => PreparedAttention | null;
    readonly settle_delivery?: (tx: TransactionSql, conversation_id: string, settlement: DeliverySettlement) => void;
    readonly recovery_claim_revisions?: (sql: ReadSql, conversation_id: string) => readonly (readonly [string, number])[];
    readonly failure_notice?: (tx: TransactionSql, notice: {
        readonly conversation_id: string;
        readonly event_sequence: number;
        readonly error_summary: string;
        readonly now: number;
    }) => void;
    readonly admit_notice?: (tx: TransactionSql, conversation_id: string,
        sender_message_id: string | null, now: number) => PromptDispatchDecision;
    readonly admit_recovery?: (tx: TransactionSql, conversation_id: string,
        references: readonly DeploymentRecoveryReference[], now: number) => string | null;
    readonly mark_notices_leaving_queue?: (tx: TransactionSql, conversation_id: string,
        sender_message_ids: readonly string[], now: number) => number;
    readonly all_are_notices?: (sql: ReadSql, conversation_id: string, sender_message_ids: readonly string[]) => boolean;
    readonly startup_participant?: (tx: TransactionSql) => ReadonlyMap<string, readonly ConversationEventPayload[]>;
    readonly reconcile_deployment?: (tx: TransactionSql, now: number) => void;
}
export interface ConversationStoreOptions extends RecordParticipants {
    readonly integer_now?: () => number;
}
export interface DeliveredPromptOptions {
    readonly prompt: PromptEventPayload;
    readonly model_change: ModelChangedEventPayload | null;
    readonly extra_prompts?: readonly PromptEventPayload[];
    readonly owner_read_through_sequence?: number | null;
    readonly lifecycle?: readonly LifecycleFact[];
    readonly composed_content_delivered?: boolean;
    readonly recording_receipt?: NativeMessageOperation | null;
    readonly extra_recording_receipts?: readonly NativeMessageOperation[];
}
export interface AcceptedRecordEvent<T> {
    readonly result: T;
    readonly taken: boolean;
    readonly written: readonly StoredConversationEvent[];
}
export type RecordCommitParticipant<T> = (tx: TransactionSql, take: () => T) => AcceptedRecordEvent<T>;

/** One append owner, synchronous source effects, and publication after successful return. */
export interface ConversationRecordStore {
    settle_recording<T>(conversation_id: string, operation: () => T | Promise<T>, options?: RecordingSettlementOptions): Promise<T>;
    finish_writes(): Promise<void>;
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
    append_event(conversation_id: string, payload: ConversationEventPayload, options?: AppendOptions): StoredConversationEvent;
    append_turn_ending(conversation_id: string, payloads: readonly ConversationEventPayload[], options: AppendOptions): readonly StoredConversationEvent[];
    append_delivered_prompt(conversation_id: string, options: DeliveredPromptOptions): readonly StoredConversationEvent[];
    advance_owner_read_through_sequence(conversation_id: string, through_sequence: number): ConversationRecord | null;
    conversations_due_for_automatic_compaction(options: { due_at_or_before: number; activity_after: number }): readonly string[];
    admit_sender_messages(conversation_id: string, messages: readonly PromptDiscardedEventPayload[], options?: {
        lifecycle_if_all_suppressed?: readonly LifecycleFact[];
    }): readonly PromptDispatchDecision[];
    mark_held_sender_messages_leaving_queue(conversation_id: string, sender_message_ids: readonly string[]): number;
    all_sender_messages_are_manager_notices(conversation_id: string, sender_message_ids: readonly (string | null)[]): boolean;
    attention_facts(conversation_ids: readonly string[]): ReadonlyMap<string, ConversationAttentionFacts>;
    record_background_task_started_in_transaction(tx: TransactionSql, conversation_id: string, task_id: string, task_description: string): void;
    record_background_task_ended_in_transaction(tx: TransactionSql, conversation_id: string, task_id: string): string | null;
    take_background_tasks_in_transaction(tx: TransactionSql, conversation_id: string, options?: { task_ids?: readonly string[] | null }): readonly string[];
    take_background_tasks(conversation_id: string, options?: { task_ids?: readonly string[] | null }): readonly string[];
    read_live_background_task_ids(): ReadonlyMap<string, readonly string[]>;
    conversation_ids_with_live_background_tasks(): ReadonlySet<string>;
    read_answer_events_in_transaction(tx: TransactionSql, conversation_id: string, answer_id: string): readonly StoredConversationEvent[];
    commit_backend_event<T>(writer: (tx: TransactionSql) => { result: T; written: readonly StoredConversationEvent[] },
        in_commit?: RecordCommitParticipant<T> | null, options?: { always_announce?: boolean }): AcceptedRecordEvent<T>;
    diagnostics_expired(conversation_id: string): boolean;
    sequence_expired(conversation_id: string, sequence: number): boolean;
}
