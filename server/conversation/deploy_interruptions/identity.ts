import { createHash } from 'node:crypto';
import type { CaptureManifest, CapturedTurn } from './contracts.ts';
export function interruption_identity(manifest: CaptureManifest, turn: CapturedTurn): string {
  const occurrence =
    turn.occurrence_id || `${turn.runtime_epoch}:${turn.conversation_id}:${turn.turn_number}`;
  return createHash('sha256').update(`${manifest.deployment_id}:${occurrence}`).digest('hex');
}
export function recovery_message_identity(interruption_ids: readonly string[]): string {
  return (
    'deployment-recovery:' +
    createHash('sha256')
      .update([...interruption_ids].sort().join('\n'))
      .digest('hex')
  );
}
