import type { ExecutorIdentity } from '../execution_identity.ts';
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
