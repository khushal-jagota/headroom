import type { PermissionResult, PermissionUpdate } from '@anthropic-ai/claude-agent-sdk';
import type { TurnToken } from '../contracts.ts';
import type { ClaudeSdkMessage } from './query_contracts.ts';
import { Pending } from './wire.ts';
export { Pending } from './wire.ts';

export interface UserQuestion {
  text: string;
  header: string;
  choices: { label: string; description: string }[];
  multi_select: boolean;
}
export interface ParkedPermission {
  answer: Pending<PermissionResult>;
  handed_over: Pending<void>;
  tool_input: Record<string, unknown>;
  suggestions: readonly PermissionUpdate[];
}
export interface ParkedUserInput {
  answer: Pending<PermissionResult>;
  handed_over: Pending<void>;
  tool_input: Record<string, unknown>;
  questions: readonly UserQuestion[];
}
export class TurnInFlight {
  readonly token: TurnToken;
  readonly automatic_compaction: boolean;
  cancel_requested = false;
  command_replies: string[] = [];
  readonly owned_steer_uuids = new Set<string>();
  held_result: ClaudeSdkMessage | null = null;
  steer_settlement_watch: { cancelled: Pending<void>; task: Promise<void> } | null = null;
  readonly ended = new Pending<void>();
  readonly parked_asks = new Map<string, ParkedPermission>();
  readonly parked_user_inputs = new Map<string, ParkedUserInput>();
  readonly user_input_tool_use_ids = new Set<string>();
  constructor(token: TurnToken, automatic_compaction = false) {
    this.token = token;
    this.automatic_compaction = automatic_compaction;
  }
}
export function stop_waiting_for_steers(turn: TurnInFlight): void {
  const watch = turn.steer_settlement_watch;
  turn.steer_settlement_watch = null;
  turn.held_result = null;
  watch?.cancelled.resolve();
}
export function settle_parked_asks(turn: TurnInFlight): void {
  for (const parked of [...turn.parked_asks.values(), ...turn.parked_user_inputs.values()]) {
    parked.answer.resolve({
      behavior: 'deny',
      message: 'The turn ended before this was answered.',
    });
  }
  turn.parked_asks.clear();
  turn.parked_user_inputs.clear();
}
export function same_turn(left: TurnToken, right: TurnToken): boolean {
  return left.conversation_id === right.conversation_id && left.turn_number === right.turn_number;
}
