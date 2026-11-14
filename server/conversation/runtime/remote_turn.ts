/** The wire and mailbox keep exact integers; core counters use native numbers. */
import type { RetainedBackendRun, TurnToken } from '../backends/contracts.ts';
import type { WireInteger } from '../../executor/wire.generated.ts';
import { integer_value } from '../../executor/integers.ts';
export function admit_remote_integer(integer: WireInteger, field: string): number {
  const value = integer_value(integer);
  if (value > BigInt(Number.MAX_SAFE_INTEGER) || value < BigInt(Number.MIN_SAFE_INTEGER))
    throw new TypeError(`${field} exceeds the safe integer range of the conversation core`);
  return Number(value);
}
export function admit_remote_turn(turn: RetainedBackendRun['turn']): TurnToken {
  return {
    conversation_id: turn.conversation_id,
    turn_number: admit_remote_integer(turn.turn_number, 'Remote turn number'),
  };
}
