import type { AutomaticStartAdmission } from './start_admission/index.ts';
import type { StartRequest } from './start_admission/contracts.ts';
/** The Python conversation boundary, with caller-owned conversation identities. */
import { homedir } from 'node:os';
import { join } from 'node:path';
import { PlannerError } from '../core/contracts.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';
import type { Principal } from '../core/contracts.ts';
import type { TransactionSql } from '../core/database/contracts.ts';
import { KNOWN_BACKEND_INSTANCE_IDS } from '../drivers/built_in_manifests.ts';
import type { BackendInstanceId } from '../drivers/core/identity.ts';
import type { DriverRegistry } from '../drivers/core/registry.ts';
import type { MessageContent } from './message_content.ts';
import type { WorkspaceAdmission } from './workspace_admission.ts';
export const ConversationAccess = { full: 'full' } as const;
export type ConversationAccess = (typeof ConversationAccess)[keyof typeof ConversationAccess];
export const ComposerCatalogEntryKind = {
  command: 'command',
  skill: 'skill',
  app: 'app',
  plugin: 'plugin',
} as const;
export type ComposerCatalogEntryKind =
  (typeof ComposerCatalogEntryKind)[keyof typeof ComposerCatalogEntryKind];
export const WorkspacePathPolicy = {
  home_relative: 'home_relative',
  absolute: 'absolute',
  executor_default: 'executor_default',
} as const;
export type WorkspacePathPolicy = (typeof WorkspacePathPolicy)[keyof typeof WorkspacePathPolicy];
export const AnswerFate = { applied: 'applied', saved: 'saved', refused: 'refused' } as const;
export type AnswerFate = (typeof AnswerFate)[keyof typeof AnswerFate];
export const PromptDeliveryMode = { queue: 'queue', send_now: 'send_now', steer: 'steer' } as const;
export type PromptDeliveryMode = (typeof PromptDeliveryMode)[keyof typeof PromptDeliveryMode];
export const HeldPromptPromotionMode = { send_now: 'send_now', steer: 'steer' } as const;
export type HeldPromptPromotionMode =
  (typeof HeldPromptPromotionMode)[keyof typeof HeldPromptPromotionMode];
export const PromptQueueReason = {
  requested: 'requested',
  attachment: 'attachment',
  run_change: 'run_change',
  steer_refused: 'steer_refused',
  command_needs_its_own_turn: 'command_needs_its_own_turn',
} as const;
export type PromptQueueReason = (typeof PromptQueueReason)[keyof typeof PromptQueueReason];
export const PromptDeliveryRefusalReason = {
  no_such_conversation: 'no_such_conversation',
  backend_did_not_start: 'backend_did_not_start',
  model_resolution_failed: 'model_resolution_failed',
  session_did_not_load: 'session_did_not_load',
  write_to_backend_failed: 'write_to_backend_failed',
  no_running_turn_to_steer_into: 'no_running_turn_to_steer_into',
  backend_cannot_steer: 'backend_cannot_steer',
  command_cannot_join_running_turn: 'command_cannot_join_running_turn',
  remote_steer_not_delivered: 'remote_steer_not_delivered',
  running_turn_changed_before_steer: 'running_turn_changed_before_steer',
  running_turn_cannot_accept_steer: 'running_turn_cannot_accept_steer',
  message_cannot_be_steered: 'message_cannot_be_steered',
  backend_rejected_steer: 'backend_rejected_steer',
  backend_rejected_prompt: 'backend_rejected_prompt',
} as const;
export type PromptDeliveryRefusalReason =
  (typeof PromptDeliveryRefusalReason)[keyof typeof PromptDeliveryRefusalReason];
export class ComposerCatalogEntry {
  readonly kind: ComposerCatalogEntryKind;
  readonly display_text: string;
  readonly insertion_text: string;
  readonly description: string;
  readonly argument_hint: string | null;
  constructor(values: {
    kind: ComposerCatalogEntryKind;
    display_text: string;
    insertion_text: string;
    description: string;
    argument_hint?: string | null;
  }) {
    this.kind = values.kind;
    this.display_text = values.display_text;
    this.insertion_text = values.insertion_text;
    this.description = values.description;
    this.argument_hint = values.argument_hint === undefined ? null : values.argument_hint;
  }
}
export class ConversationRoleMaterials {
  readonly role_text: string;
  readonly identity_environment_variables: readonly (readonly [string, string])[];
  constructor(values: {
    role_text: string;
    identity_environment_variables?: readonly (readonly [string, string])[];
  }) {
    this.role_text = values.role_text;
    this.identity_environment_variables =
      values.identity_environment_variables === undefined
        ? []
        : values.identity_environment_variables;
  }
}
export class ConversationStartRequest {
  readonly conversation_id: string;
  readonly model: string;
  readonly backend_key: BackendInstanceId | null;
  readonly reasoning_effort: string | null;
  readonly role_materials: ConversationRoleMaterials | null;
  readonly workspace_folder: string | null;
  readonly access: ConversationAccess | null;
  readonly workspace_path_policy: WorkspacePathPolicy;
  /** The machine that runs this conversation, fixed at its start like the backend. */
  readonly machine_id: string;
  constructor(values: {
    conversation_id: string;
    model: string;
    backend_key?: BackendInstanceId | null;
    reasoning_effort?: string | null;
    role_materials?: ConversationRoleMaterials | null;
    workspace_folder?: string | null;
    access?: ConversationAccess | null;
    workspace_path_policy?: WorkspacePathPolicy;
    machine_id?: string;
  }) {
    this.conversation_id = values.conversation_id;
    this.machine_id = values.machine_id ?? HOME_MACHINE_ID;
    this.model = values.model;
    this.backend_key = values.backend_key === undefined ? null : values.backend_key;
    this.reasoning_effort = values.reasoning_effort === undefined ? null : values.reasoning_effort;
    this.role_materials = values.role_materials === undefined ? null : values.role_materials;
    this.workspace_folder = values.workspace_folder === undefined ? null : values.workspace_folder;
    this.access = values.access === undefined ? null : values.access;
    this.workspace_path_policy =
      values.workspace_path_policy === undefined
        ? WorkspacePathPolicy.home_relative
        : values.workspace_path_policy;
  }
}
export class ResolvedConversationStart {
  readonly conversation_id: string;
  readonly backend_key: BackendInstanceId;
  readonly model: string;
  readonly reasoning_effort: string | null;
  readonly role_materials: ConversationRoleMaterials | null;
  readonly workspace_folder: string;
  readonly access: ConversationAccess;
  readonly workspace_path_policy: WorkspacePathPolicy;
  constructor(values: {
    conversation_id: string;
    backend_key: BackendInstanceId;
    model: string;
    reasoning_effort: string | null;
    role_materials: ConversationRoleMaterials | null;
    workspace_folder: string;
    access: ConversationAccess;
    workspace_path_policy?: WorkspacePathPolicy;
  }) {
    this.conversation_id = values.conversation_id;
    this.backend_key = values.backend_key;
    this.model = values.model;
    this.reasoning_effort = values.reasoning_effort;
    this.role_materials = values.role_materials;
    this.workspace_folder = values.workspace_folder;
    this.access = values.access;
    this.workspace_path_policy =
      values.workspace_path_policy === undefined
        ? WorkspacePathPolicy.home_relative
        : values.workspace_path_policy;
  }
}
export class DeploymentRecoveryReference {
  readonly interruption_id: string;
  readonly expected_stage: string | null;
  constructor(values: { interruption_id: string; expected_stage?: string | null }) {
    this.interruption_id = values.interruption_id;
    this.expected_stage = values.expected_stage === undefined ? null : values.expected_stage;
  }
}
export class PromptDispatchDecision {
  readonly sender_message_id: string | null;
  readonly suppression_reason: string | null;
  readonly is_manager_notice: boolean;
  readonly is_memory_notice: boolean;
  constructor(values: {
    sender_message_id: string | null;
    suppression_reason?: string | null;
    is_manager_notice?: boolean;
    is_memory_notice?: boolean;
  }) {
    this.sender_message_id = values.sender_message_id;
    this.suppression_reason =
      values.suppression_reason === undefined ? null : values.suppression_reason;
    this.is_memory_notice = values.is_memory_notice ?? false;
    this.is_manager_notice =
      values.is_manager_notice === undefined ? false : values.is_manager_notice;
  }
}
export class PromptDeliverySuppressed {
  readonly reason: string;
  constructor(values: { reason: string }) {
    this.reason = values.reason;
  }
}
export class PromptDeliveryStarted {
  declare private readonly _PromptDeliveryStarted: void;
}
export class PromptDeliveryQueued {
  readonly queue_position: number;
  constructor(values: { queue_position: number }) {
    this.queue_position = values.queue_position;
  }
}
export class PromptDeliveryInjected {
  declare private readonly _PromptDeliveryInjected: void;
}
export class PromptDeliveryRefused {
  readonly refusal_reason: PromptDeliveryRefusalReason;
  constructor(values: { refusal_reason: PromptDeliveryRefusalReason }) {
    this.refusal_reason = values.refusal_reason;
  }
}
export class PromptDeliveryUncertain {
  declare private readonly _PromptDeliveryUncertain: void;
}
export class HeldPrompt {
  readonly requested_mode: PromptDeliveryMode;
  readonly held_prompt_id: string;
  readonly content: MessageContent;
  readonly sender_label: string;
  readonly sender_message_id: string | null;
  readonly sent_at_unix_milliseconds: number;
  readonly sender: Principal | null;
  readonly recipient: Principal | null;
  readonly queue_reason: PromptQueueReason;
  readonly delivery_uncertain: boolean;
  constructor(values: {
    requested_mode?: PromptDeliveryMode;
    held_prompt_id: string;
    content: MessageContent;
    sender_label: string;
    sender_message_id: string | null;
    sent_at_unix_milliseconds: number;
    sender?: Principal | null;
    recipient?: Principal | null;
    queue_reason?: PromptQueueReason;
    delivery_uncertain?: boolean;
  }) {
    this.requested_mode = values.requested_mode ?? PromptDeliveryMode.queue;
    this.held_prompt_id = values.held_prompt_id;
    this.content = values.content;
    this.sender_label = values.sender_label;
    this.sender_message_id = values.sender_message_id;
    this.sent_at_unix_milliseconds = values.sent_at_unix_milliseconds;
    this.sender = values.sender === undefined ? null : values.sender;
    this.recipient = values.recipient === undefined ? null : values.recipient;
    this.queue_reason =
      values.queue_reason === undefined ? PromptQueueReason.requested : values.queue_reason;
    this.delivery_uncertain =
      values.delivery_uncertain === undefined ? false : values.delivery_uncertain;
  }
}

export const FLOOR_DEFAULT_WORKSPACE_FOLDER = join(homedir(), 'projects');
export const FLOOR_DEFAULT_ACCESS = ConversationAccess.full;
/** A live instance decides. Without one on this home (a retained remote child's backend),
 * every known backend steers, as each does on the side that runs it. */
export function backend_supports_active_steering(
  registry: DriverRegistry,
  key: BackendInstanceId,
): boolean {
  const entry = registry.get(key);
  return entry?.kind === 'live'
    ? entry.instance.capabilities.turns.supportsActiveSteering
    : KNOWN_BACKEND_INSTANCE_IDS.includes(key);
}
/** The built-in backend `value` names, or undefined. A stored key may name one this build lacks. */
export function known_conversation_backend_key(value: unknown): BackendInstanceId | undefined {
  return KNOWN_BACKEND_INSTANCE_IDS.find((id) => id === value);
}
/** A backend to launch on or to choose. Reads of a stored key do not need one. */
export function require_conversation_backend_key(value: unknown): BackendInstanceId {
  const known = known_conversation_backend_key(value);
  if (known !== undefined) return known;
  throw new PlannerError('validation', 'unknown agent backend', {
    backend_key: typeof value === 'string' ? value : null,
    backend_keys: [...KNOWN_BACKEND_INSTANCE_IDS],
  });
}
export class ConversationAlreadyStarted extends Error {}
export type ConversationMessageContent =
  MessageContent | ((conversation_id: string) => Promise<MessageContent>);
export type PromptDeliveryFate =
  | PromptDeliveryStarted
  | PromptDeliveryQueued
  | PromptDeliveryInjected
  | PromptDeliveryRefused
  | PromptDeliveryUncertain
  | PromptDeliverySuppressed;
export type HeldPromptPromotionFate = PromptDeliveryFate;
export interface NativeMessageOperation {
  in_admission?: (transaction: TransactionSql, conversation_id: string) => void;
  admitted?: () => void;
  sender_id: string;
  submission_id: string;
  in_commit?:
    | ((
        connection: TransactionSql,
        conversation_id: string,
        event: unknown,
        admission: NativeMessageAdmission | null,
      ) => void)
    | null;
}
export interface NativeMessageAdmission {
  readonly fate: PromptDeliveryFate;
}
export interface SendOptions {
  /** Preserve the original reply policy during durable recipient recovery. */
  acknowledge_owner_on_failure?: boolean;
  sender_label: string;
  owner_read_through_sequence?: number | null;
  mode?: PromptDeliveryMode;
  model_change?: string | null;
  reasoning_effort_change?: string | null;
  reasoning_effort_is_set?: boolean;
  sender_message_id?: string | null;
  sent_at_unix_milliseconds?: number | null;
  /** A resend of a refused (or unconfirmed) message names that row's sequence; the
   *  recorded prompt carries it, and the view folds the earlier row away. */
  reconciles_sequence?: number | null;
  sender?: Principal | null;
  recipient?: Principal | null;
  recovery_references?: readonly DeploymentRecoveryReference[];
  record_refusal?: boolean;
  recording_receipt?: NativeMessageOperation | null;
}
export interface RecordMessageToOwnerOptions {
  sender_label: string;
  sender: Principal;
  recipient: Principal;
  sender_message_id?: string | null;
  sent_at_unix_milliseconds?: number | null;
  recording_receipt?: NativeMessageOperation | null;
}
export type OwnerSendProgress =
  { state: 'queued'; position: number } | { state: 'pending' } | { state: 'uncertain' };

export interface ConversationSystem {
  configure_worker_cleanup_recheck?(
    callback: (
      source: import('./backends/contracts.ts').ChildReference,
      ticket_id: string,
    ) => Promise<import('../worker_supervision/contracts.ts').CleanupResult>,
  ): void;
  recheck_worker_cleanup?(
    ticket_id: string,
  ): Promise<import('../worker_supervision/contracts.ts').CleanupResult>;
  readonly retire_local_workspace?: import('../ticket_workspaces/contracts.ts').WorkspaceRetirementOperation;
  readonly start_admission?: AutomaticStartAdmission;
  automatic_start_request?(identity: string, conversation_id: string): Promise<StartRequest>;
  readonly workspace_admission: WorkspaceAdmission;
  /** Accepted sends that still depend on this process for delivery. */
  has_pending_update_sends(): boolean;
  workspace_activity(): 'idle' | 'busy' | 'unknown';
  /** Work that an owner update interrupts. Information only. */
  update_interrupted_work(machine_ids?: readonly string[]): readonly string[];
  /** A trial app starts with admission closed. This opens it when the installer ends. */
  reopen_after_update(): void;
  run_with_workspace_admission(
    operation: () => void,
    options: { require_idle: boolean },
  ): Promise<boolean>;
  start_conversation(request: ConversationStartRequest): Promise<void>;
  send(
    conversation_id: string,
    content: MessageContent,
    options: SendOptions,
  ): Promise<PromptDeliveryFate>;
  record_message_to_owner(
    conversation_id: string,
    content: MessageContent,
    options: RecordMessageToOwnerOptions,
  ): Promise<void>;
  interrupt(conversation_id: string): Promise<void>;
  held_prompts(conversation_id: string): Promise<readonly HeldPrompt[]>;
  owner_send_progress(
    conversation_id: string,
    sender_message_id: string,
    recipient: Principal,
  ): Promise<OwnerSendProgress | null>;

  sender_message_ids_awaiting_outcome(conversation_id: string): Promise<ReadonlySet<string>>;
  promote_held_prompt(
    conversation_id: string,
    held_prompt_id: string,
    mode: HeldPromptPromotionMode,
  ): Promise<HeldPromptPromotionFate | null>;
  /** ``withdrawn_for_edit``: the person took the message back to edit it (no transcript row). */
  discard_held_prompt(
    conversation_id: string,
    held_prompt_id: string,
    withdrawn_for_edit?: boolean,
  ): Promise<boolean>;
  kill(conversation_id: string): Promise<void>;
  kill_for_restart(
    conversation_id: string,
    ticket_id: string,
    finish: (check_cleanup: ((sql: TransactionSql) => void) | null) => Promise<void>,
  ): Promise<void>;
  is_running(conversation_id: string): Promise<boolean>;
  has_pending_permission_ask(conversation_id: string): Promise<boolean>;
  has_pending_user_input(conversation_id: string): Promise<boolean>;
}

export interface ConversationSignal {
  working: boolean;
  message_for_owner: boolean;
}
export interface ConversationAttention {
  readonly signal: ConversationSignal;
  readonly awaiting_answer: boolean;
  readonly last_turn_failed: boolean;
}
export class ConversationSignalPublicationV1 {
  readonly conversation_id: string;
  readonly runtime_epoch: string;
  readonly conversation_revision: number;
  readonly attention: ConversationAttention;
  constructor(value: {
    conversation_id: string;
    runtime_epoch: string;
    conversation_revision: number;
    attention: ConversationAttention;
  }) {
    this.conversation_id = value.conversation_id;
    this.runtime_epoch = value.runtime_epoch;
    this.conversation_revision = value.conversation_revision;
    this.attention = { ...value.attention, signal: { ...value.attention.signal } };
  }
}
export interface SignalCursor {
  readonly scope: string;
  readonly runtime_epoch: string;
  readonly sequence: number;
}
export class SignalSnapshot {
  readonly installation_id: string;
  readonly cursor: SignalCursor;
  readonly values: readonly ConversationSignalPublicationV1[];
  constructor(value: {
    installation_id: string;
    cursor: SignalCursor;
    values: readonly ConversationSignalPublicationV1[];
  }) {
    this.installation_id = value.installation_id;
    this.cursor = value.cursor;
    this.values = value.values.map((item) => new ConversationSignalPublicationV1(item));
  }
}
export interface SignalDeletion {
  readonly conversation_id: string;
  readonly runtime_epoch: string;
  readonly conversation_revision: number;
}
export interface SignalAssociationEffect {
  readonly owner_kind: 'ticket' | 'manager' | 'chief';
  readonly owner_id: string;
  readonly relation: 'current' | 'historical';
  readonly before_conversation_id: string | null;
  readonly after_conversation_id: string | null;
}
export class SignalBatch {
  readonly base_cursor: SignalCursor;
  readonly cursor: SignalCursor;
  readonly replacements: readonly ConversationSignalPublicationV1[];
  readonly deletions: readonly SignalDeletion[];
  readonly associations: readonly SignalAssociationEffect[];
  constructor(value: {
    base_cursor: SignalCursor;
    cursor: SignalCursor;
    replacements: readonly ConversationSignalPublicationV1[];
    deletions: readonly SignalDeletion[];
    associations: readonly SignalAssociationEffect[];
  }) {
    this.base_cursor = value.base_cursor;
    this.cursor = value.cursor;
    this.replacements = value.replacements.map((item) => new ConversationSignalPublicationV1(item));
    this.deletions = value.deletions;
    this.associations = value.associations;
  }
}
export type SignalInstaller = (connection: TransactionSql, snapshot: SignalSnapshot) => void;
export type SignalParticipant = (connection: TransactionSql, batch: SignalBatch) => void;
export interface SignalReadPermit {
  readonly scope: string;
  readonly runtime_epoch: string;
  readonly installation_id: string;
  readonly generation: number;
}
export interface SignalInvalidation {
  readonly generation: number;
  readonly reason: 'initializing' | 'authority_lost' | 'detached' | 'shutdown';
}
export interface SignalDurableFacts {
  readonly message_for_owner: boolean;
  readonly last_turn_failed: boolean;
  readonly has_live_background_task: boolean;
}
export interface SignalAvailability {
  readonly generation: number;
  readonly state: 'initializing' | 'current' | 'unavailable';
  readonly reason: string | null;
}
export class SignalUnavailable extends Error {}
export interface SignalAttachment {
  capture_permit(): SignalReadPermit;
  validate(permit: SignalReadPermit): void;
  close(): void;
}
export interface SignalPublicationSource {
  signal_availability(): SignalAvailability;
  attach_signal_publication(options: {
    install: SignalInstaller;
    participate: SignalParticipant;
    unavailable: (invalidation: SignalInvalidation) => void;
  }): SignalAttachment;
}
