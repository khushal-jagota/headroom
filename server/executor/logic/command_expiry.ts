/** Fetch-time expiry judges waiting rows only, against facts from the same writer. */
import type { LifecycleFact } from '../../conversation/record/lifecycle.ts';
import type { CommandSettlementReason, WireValue } from '../contracts.ts';
import { MODEL_LIST_LIMIT_SECONDS, STEER_DELIVERY_LIMIT_SECONDS } from '../configuration.ts';
import { integer } from './counters.ts';

export function expiry_reason(command: WireValue<'MailboxCommand'>, facts: readonly LifecycleFact[], now_ms: number): CommandSettlementReason | null {
  const age = BigInt(now_ms) - integer(command.created_at_vps_ms);
  if (command.model_list_request != null)
    return age >= BigInt(MODEL_LIST_LIMIT_SECONDS * 1000) ? 'model_list_limit_passed' : null;
  const child = command.child_command!;
  const status = (kind: string, identity: string) => facts.find(fact => fact.kind === kind && fact.identity === identity)?.status;
  if (child.operation === 'steer') {
    if (!['intent','running'].includes(status('turn', String(integer(child.turn!.turn_number))) ?? '')) return 'steer_turn_not_running';
    return age >= BigInt(STEER_DELIVERY_LIMIT_SECONDS * 1000) ? 'steer_delivery_limit_passed' : null;
  }
  if (child.operation === 'answer_permission_ask' || child.operation === 'answer_user_input') {
    if (child.turn === null) return 'answered_request_not_open';
    const args = child.arguments;
    const identity = child.operation === 'answer_permission_ask'
      ? `${integer(child.turn.turn_number)}:permission:${(args as WireValue<'AnswerPermission'>).ask_id}`
      : `${integer(child.turn.turn_number)}:user_input:${(args as WireValue<'AnswerUserInput'>).request_id}`;
    if (!['pending','answering'].includes(status('request',identity) ?? '')) return 'answered_request_not_open';
  }
  return null;
}
