/** The wire and mailbox keep exact integers; core counters use native numbers. */
import type { RetainedBackendRun, TurnToken } from '../backends/contracts.ts';
import { integer_value } from '../../executor/integers.ts';
import { ValueError } from '../../core/value_error.ts';
export function admit_remote_turn(turn: RetainedBackendRun['turn']): TurnToken {
  const value = integer_value(turn.turn_number);
  if (value > BigInt(Number.MAX_SAFE_INTEGER) || value < BigInt(Number.MIN_SAFE_INTEGER))
    throw new ValueError(
      'Remote turn number exceeds the safe integer range of the conversation core',
    );
  return { conversation_id: turn.conversation_id, turn_number: Number(value) };
}
