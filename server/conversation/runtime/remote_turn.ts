/** Remote counters share the native safe-integer contract with the conversation core. */
import type { RetainedBackendRun, TurnToken } from '../backends/contracts.ts';
export class RemoteTurnRejected extends TypeError {}

export function admit_remote_integer(value: number, field: string): number {
  if (!Number.isSafeInteger(value))
    throw new RemoteTurnRejected(
      `${field} exceeds the safe integer range of the conversation core`,
    );
  return value;
}
export function admit_remote_turn(turn: RetainedBackendRun['turn']): TurnToken {
  return {
    conversation_id: turn.conversation_id,
    turn_number: admit_remote_integer(turn.turn_number, 'Remote turn number'),
  };
}
