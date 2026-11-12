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
