import type { ExecutorIdentity } from '../execution_identity.ts';
import type { ChangeSignal } from '../core/changes/index.ts';
import type { FileHandle } from 'node:fs/promises';
import type { Principal } from '../core/contracts.ts';
import type { WireInteger, WireRootName, WireTypes } from './wire.generated.ts';
export interface ExecutorRefusal extends Omit<
  WireTypes['ExecutorRefusal'],
  'server_identity' | 'peer_identity'
> {
  server_identity: ExecutorIdentity;
  peer_identity: ExecutorIdentity | null;
}
export interface PresenceRecord {
  executor_incarnation: string | null;
  identity: ExecutorIdentity | null;
  last_contact_at_ms: number | null;
  last_lease_at_ms: number | null;
  continuous_since_ms: number | null;
  going_to_sleep_at_ms: number | null;
  lease: WireTypes['LeaseRequest'] | null;
  refusal: ExecutorRefusal | null;
}
export interface PresenceJudgement {
  presence: 'never' | 'refused' | 'asleep' | 'present' | 'not_heard_from';
  since_ms: WireInteger | null;
}

export interface AcceptedLease {
  previous: PresenceRecord | null;
  current: PresenceRecord;
  new_incarnation: boolean;
  replaced_incarnation: string | null;
}
export interface ExecutorHealthRead extends Omit<
  WireTypes['ExecutorHealthRead'],
  'identity' | 'refusal' | 'last_contact_at_ms'
> {
  identity: ExecutorIdentity | null;
  refusal: ExecutorRefusal | null;
  last_contact_at_ms: number | null;
}
export type StallLease = WireTypes['LeaseRequest'];
export type StallPresenceRecord = PresenceRecord;
export type UpdateCheck = () => WireTypes['MacUpdateCheck'] | Promise<WireTypes['MacUpdateCheck']>;
export type IncarnationReplaced = (replaced: string, current: string) => Promise<void>;
export interface MacPresenceOptions {
  changes: ChangeSignal;
  application_revision?: string;
  clock?: () => number;
}

/** Caller-owned Linux descriptor capabilities. Paths are diagnostic only. */
export type DirectoryHandle = FileHandle;
export interface HeldFileLocation {
  readonly parent: DirectoryHandle;
  readonly leaf: string;
  readonly path: string;
}
export interface FileOperationOptions {
  readonly signal?: AbortSignal;
}
export interface InputFile {
  readonly path: string;
  readonly sha256: string;
  readonly byte_count: WireInteger;
  readonly chunks: AsyncIterable<Uint8Array>;
  close(): Promise<void>;
}
/** An owned upload stream: destruction cancels its outstanding iterator read. */
export interface UploadSource extends AsyncIterable<Uint8Array> {
  destroy(error?: Error): void;
}
export interface VpsFileInbox {
  authorize_upload(upload: WireTypes['FileUploadRequest']): void | Promise<void>;
  store_upload(
    upload: WireTypes['FileUploadRequest'],
    source: HeldFileLocation,
    options?: FileOperationOptions,
  ): Promise<void>;
  readonly runs: {
    authority(child: WireTypes['ChildReference']): Principal | Promise<Principal>;
  };
}
export interface VpsFilesOptions {
  readonly signal: AbortSignal;
}

/** Runtime wire values preserve Python integer custody. */
export type WireValue<K extends WireRootName> = WireTypes[K];
export type MailboxCounter = WireInteger;
export const MailboxCommandState = {
  waiting: 'waiting',
  delivered: 'delivered',
  finished: 'finished',
  withdrawn: 'withdrawn',
  expired: 'expired',
} as const;
export type MailboxCommandState = (typeof MailboxCommandState)[keyof typeof MailboxCommandState];
export const CommandSettlementReason = {
  executor_process_replaced: 'executor_process_replaced',
  executor_process_restarted: 'executor_process_restarted',
  steer_turn_not_running: 'steer_turn_not_running',
  steer_delivery_limit_passed: 'steer_delivery_limit_passed',
  answered_request_not_open: 'answered_request_not_open',
  model_list_limit_passed: 'model_list_limit_passed',
  delivery_withdrawn: 'delivery_withdrawn',
} as const;
export type CommandSettlementReason =
  (typeof CommandSettlementReason)[keyof typeof CommandSettlementReason];
export interface MailboxRow {
  command: WireValue<'MailboxCommand'>;
  executor_incarnation: string;
  state: MailboxCommandState;
  result: WireValue<'CommandResult'> | null;
}
export type MailboxSettlement = readonly [WireValue<'MailboxCommand'>, WireValue<'CommandResult'>];
export type MailboxSettlements = readonly MailboxSettlement[];
export type MailboxSettledListener = (
  command: WireValue<'MailboxCommand'>,
  result: WireValue<'CommandResult'>,
) => void;
export interface MailboxFetchResult {
  reply: WireValue<'MailboxPollReply'> | null;
  settled: MailboxSettlements;
}
export interface MailboxRecordResult {
  reply: WireValue<'CommandOutcomeReply'>;
  settled: MailboxSettlements;
}

/** A rewritten prompt's exact turn and the two committed-delivery facts. */
export type PromptWrittenAgain = readonly [
  WireValue<'ChildReference'>,
  MailboxCounter,
  WireValue<'WritePrompt'>,
  boolean,
  boolean,
];
/** A prior uncertain record's exact sequence, command, and eventual delivery proof. */
export type LateSteerAfterRestart = readonly [string, MailboxCounter, string, boolean | null];
