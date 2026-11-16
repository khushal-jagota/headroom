/** The record keeps one transaction and one epoch rotation for every composition. */
import type { CapturedTurn } from './contracts.ts';
import type { RecordParticipants } from '../record/contracts.ts';
import { read_manifests } from './files.ts';
import {
  reconcile,
  import_captured_turns,
  bind_delivery,
  fail_recovery_claims,
  settle_delivery,
  admit_recovery,
  recovery_claim_revisions,
} from './data.ts';
export function deployment_participants(db_path: string): RecordParticipants {
  const manifests = read_manifests(db_path);
  return {
    reconcile_deployment: (tx, now, context) => {
      if (context === undefined) {
        reconcile(tx, manifests, now);
        return;
      }
      for (const turn of context.proven) {
        if (
          manifests.some((manifest) =>
            manifest.turns.some((captured) => same_source(turn, captured)),
          )
        )
          continue;
        if (
          tx.get(
            'SELECT 1 FROM deploy_interruptions WHERE source_conversation_id=? AND turn_number=? AND occurrence_id IS ?',
            [turn.conversation_id, turn.turn_number, turn.occurrence_id],
          ) !== undefined
        )
          continue;
        const identity = `crash:${turn.runtime_epoch}:${turn.conversation_id}:${turn.turn_number}`;
        import_captured_turns(
          tx,
          {
            version: 1,
            deployment_id: identity,
            capture_id: identity,
            captured_at: now,
            state: 'captured',
            detail: null,
            turns: [turn],
          },
          now,
          {
            claim_error_reason: 'interrupted by a home crash',
            ended_by: 'The home process interruption',
          },
        );
      }
      reconcile(
        tx,
        manifests.map((manifest) => ({
          ...manifest,
          turns: manifest.turns.filter(
            (captured) => !context.blocked.some((source) => same_source(source, captured)),
          ),
        })),
        now,
      );
    },
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

function same_source(source: CapturedTurn, captured: CapturedTurn): boolean {
  return (
    source.conversation_id === captured.conversation_id &&
    source.turn_number === captured.turn_number &&
    (captured.occurrence_id !== null
      ? source.occurrence_id === captured.occurrence_id
      : source.runtime_epoch === captured.runtime_epoch)
  );
}
