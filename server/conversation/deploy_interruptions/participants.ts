/** The record keeps one transaction and one epoch rotation for every composition. */
import type { RecordParticipants } from '../record/contracts.ts';
import { read_manifests } from './files.ts';
import {
  reconcile,
  bind_delivery,
  fail_recovery_claims,
  settle_delivery,
  admit_recovery,
  recovery_claim_revisions,
} from './data.ts';
export function deployment_participants(db_path: string): RecordParticipants {
  const manifests = read_manifests(db_path);
  return {
    reconcile_deployment: (tx, now) => reconcile(tx, manifests, now),
    bind_interruption_delivery: (tx, conversation_id, turn_number, references, identities) =>
      bind_delivery(tx, conversation_id, turn_number, references, identities ?? undefined),
    fail_recovery_claims: (tx, conversation_id, turn_number, now) =>
      fail_recovery_claims(tx, conversation_id, turn_number, {
        now,
        error_reason: 'recovery after a deploy failed',
      }),
    settle_delivery: (tx, conversation_id, settlement) =>
      settle_delivery(tx, conversation_id, settlement.facts, settlement),
    admit_recovery,
    recovery_claim_revisions,
  };
}
