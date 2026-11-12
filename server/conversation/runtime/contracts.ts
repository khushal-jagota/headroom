/** Process-local state and injected effects for the local conversation core. */
import type { Principal } from '../../core/contracts.ts';
import type {
  ConversationBackendKey,
  DeploymentRecoveryReference,
  NativeMessageOperation,
  PromptDeliveryMode,
  PromptDeliveryRefusalReason,
  PromptQueueReason,
} from '../contracts.ts';
import type { BackendChildFactory, TurnToken } from '../backends/contracts.ts';
import type { ConversationLiveTailFrame } from '../events.ts';
import type { MessageContent } from '../message_content.ts';
import type { ConversationMessageFiles } from '../message_files.ts';
import type { ConversationRecordStore, StoredConversationEvent } from '../record/contracts.ts';
import type { Deferred } from './state.ts';

export type ConversationPhase = 'idle' | 'starting' | 'running' | 'ending' | 'draining';
export interface Message {
  readonly held_prompt_id: string;
  readonly content: MessageContent;
  readonly sender_label: string;
  readonly sender: Principal | null;
  readonly recipient: Principal | null;
  readonly sender_message_id: string | null;
  readonly sent_at_unix_milliseconds: number | null;
  readonly snapshot_sent_at_unix_milliseconds: number;
  readonly model_change: string | null;
  readonly reasoning_effort_change: string | null;
  readonly reasoning_effort_is_set: boolean;
  readonly owner_read_through_sequence: number | null;
  readonly acknowledge_owner_on_failure: boolean;
  readonly recovery_references: readonly DeploymentRecoveryReference[];
  readonly recording_receipt: NativeMessageOperation | null;
  readonly queue_reason: PromptQueueReason;
  readonly delivery_uncertain: boolean;
  readonly system_generated: boolean;
}
export interface SenderAdmission {
  readonly message: Message;
  readonly settled: Deferred<void>;
}
export interface ReservedTurn {
  readonly token: TurnToken;
  readonly resolved: Deferred<void>;
  readonly automatic_compaction: boolean;
  automatic_notice: boolean;
  prompt_accepted: boolean;
  messages: readonly Message[];
  mode: PromptDeliveryMode;
}
export interface DeliveryAttempt {
  readonly refusal_reason: PromptDeliveryRefusalReason | null;
  readonly refusal_detail: string | null;
  readonly record_refusal: boolean;
  readonly suppression_reason: string | null;
  readonly composed_content_delivered: boolean;
  readonly messages: readonly Message[];
  readonly automatic_notice: boolean;
}
/** Applying a field returns its rollback operation for this synchronous attempt. */
export interface RuntimeMutation {
  apply(): () => void;
}
export type SaveAttempt = <T>(
  conversation_id: string,
  work: () => T,
  mutations: readonly RuntimeMutation[],
) => T;
export interface TrackedSaveRetry {
  set_detail(detail: string): void;
  close(): void;
}
/** Structural slice of the stalls unit's registry. The owner injects its clock. */
export interface SaveWaitRegistry {
  track(
    kind: 'vps_save_retrying',
    wait_key: string,
    options: { detail: string; conversation_id?: string | null },
  ): TrackedSaveRetry;
}
export interface ConversationCoreOptions {
  readonly store: ConversationRecordStore;
  readonly wait_registry?: SaveWaitRegistry;
  readonly backend_child_factories: Readonly<Record<ConversationBackendKey, BackendChildFactory>>;
  readonly message_files: ConversationMessageFiles;
  readonly publish_event?: (event: StoredConversationEvent) => void;
  readonly publish_frame?: (conversation_id: string, frame: ConversationLiveTailFrame) => void;
  readonly publish_status_changed?: (conversation_id: string) => void;
  readonly emit_change?: () => void;
  readonly save_attempt?: SaveAttempt;
  readonly require_runtime_ownership?: () => void;
  readonly unix_time_now?: () => number;
  readonly monotonic_now?: () => number;
  readonly idle_child_stop_after_seconds?: number;
  readonly idle_child_sweep_interval_seconds?: number;
  readonly model_is_enabled?: (backend: ConversationBackendKey, model: string) => boolean;
  readonly on_held_model_change?: (
    conversation_id: string,
    model: string,
    reasoning_effort: string | null,
  ) => void | Promise<void>;
}

/** Only synchronous persistence effects enter a retry. Provider calls never do. */
export interface DeliveryServices {
  readonly options: ConversationCoreOptions;
  readonly shutting_down: boolean;
  make_sink(
    state: import('./state.ts').ConversationState,
  ): import('../backends/contracts.ts').BackendEventSink;
  persist<T>(
    state: import('./state.ts').ConversationState,
    work: () => T,
    mutations?: readonly RuntimeMutation[],
  ): Promise<T>;
  native_operation<T>(operation: () => Promise<T>): Promise<T>;
  discard_child(
    state: import('./state.ts').ConversationState,
    child: import('../backends/contracts.ts').BackendChild,
  ): Promise<boolean>;
  note_written(
    state: import('./state.ts').ConversationState,
    rows: readonly StoredConversationEvent[],
  ): void;
}
