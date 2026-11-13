/** The existing core operations owed to retained Mac runs, before route composition. */
import type {
  BackendChild,
  ChildReference,
  TurnToken,
} from '../conversation/backends/contracts.ts';
import type { UndeliveredTurnPrompt } from '../conversation/record/contracts.ts';
import type { WireValue } from './contracts.ts';
export interface MacRunConversations {
  emit_answer(turn: TurnToken, emit: () => Promise<unknown>): Promise<void>;
  retire_replaced_remote_child(
    reference: ChildReference,
    options: { replaced_by: string; undelivered_prompt?: UndeliveredTurnPrompt | null },
  ): Promise<void>;
  resume_undelivered_prompt(
    reference: ChildReference,
    prompt: UndeliveredTurnPrompt,
    options: { start_needed: boolean },
  ): Promise<void>;
  await_late_steer(
    conversation_id: string,
    uncertain_sequence: WireValue<'TurnToken'>['turn_number'],
    not_delivered: Promise<boolean>,
  ): Promise<void>;
  confirm_remote_child_termination(reference: ChildReference): Promise<void>;
  stop_interrupted_remote_child(reference: ChildReference, child: BackendChild): Promise<void>;
}
