import type { ExecutorIdentity } from '../execution_identity.ts';
import type { WireInteger, WireRootName, WireTypes } from './wire.generated.ts';
export interface ExecutorRefusal {
  reason: string;
  server_identity: ExecutorIdentity;
  peer_identity: ExecutorIdentity | null;
  refused_at_ms: number;
}
export interface PresenceRecord {
  executor_incarnation: string | null;
  identity: ExecutorIdentity | null;
  last_contact_at_ms: number | null;
  last_lease_at_ms: number | null;
  continuous_since_ms: number | null;
  going_to_sleep_at_ms: number | null;
  refusal: ExecutorRefusal | null;
}
export interface PresenceJudgement {
  presence: 'never' | 'refused' | 'asleep' | 'present' | 'not_heard_from';
  since_ms: number | null;
}

/** Only the lease fields used by the stall rule. Temporary Unit 10 projection. Unit 22 supplies full LeaseRequest validation. */
export interface StallLease {
  waiting_conversations: readonly { conversation_id: string; oldest_sequence: number }[];
  working_turns: readonly { child: { conversation_id: string }; tool_running: boolean }[];
}
export interface StallPresenceRecord extends PresenceRecord {
  lease: StallLease | null;
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
