/** Native effects stay under the retained child lock. Exact retirement belongs to its owner. */
import { same_reference } from '../../child_reference.ts';
import {
  BackendPromptRejected,
  BackendSpawnFailed,
  BackendSteerAccepted,
  BackendSteerRefused,
} from '../../../conversation/backends/contracts.ts';
import type { TurnToken as NativeTurn } from '../../../conversation/backends/contracts.ts';
import { prefix_message_content_text } from '../../../conversation/message_content.ts';
import type { MessageContent as NativeContent } from '../../../conversation/message_content.ts';
import { UserInputAnswer } from '../../../conversation/events.ts';
import { as_object, ValueError } from '../../protocol.ts';
import { normalize_integer } from '../../integers.ts';
import { class_name } from '../../protocol_errors.ts';
import { wire_classes } from '../../protocol_classes.ts';
import type {
  AskReference,
  ChildCommand,
  CommandOutcome,
  DurableAnswer,
  JsonObject,
  MessageContent,
  TurnToken,
} from '../../wire.generated.ts';
import { NATIVE_WORKER_CONTEXT } from '../configuration.ts';
import { MacChildAdmissionRefused } from './contracts.ts';
import type { MacRunningChild } from './contracts.ts';
import type { MacCommandDependencies, MacCommandWork } from './command_contracts.ts';
import {
  CHILD_START_FAILED,
  outcome,
  outcome_detail,
  same_wire,
} from './logic/outcomes.ts';

function native_content(content: MessageContent): NativeContent {
  // Shared validation chose each union member. Retain its exact numeric values.
  return content.map((piece) =>
    Reflect.construct(
      wire_classes[class_name(piece) as 'MessageText' | 'MessageFile' | 'MessageImage'],
      [piece],
    ),
  ) as NativeContent;
}
function native_turn(child: MacRunningChild, token: TurnToken): NativeTurn {
  // Native adapters retain this accepted token and compare its integer with ===.
  // Reuse that exact marker for a semantically equal steer, without a numeric conversion.
  return (
    child.active_turn !== null && same_wire(child.active_turn, token, 'TurnToken')
      ? child.active_turn
      : token
  ) as NativeTurn;
}
function check_answer(
  dependencies: MacCommandDependencies,
  child: MacRunningChild,
  answer: DurableAnswer | null | undefined,
): void {
  if (answer == null) return;
  if (child.active_turn !== null && !same_wire(child.active_turn, answer.ask.turn, 'TurnToken'))
    throw new MacChildAdmissionRefused('answer turn is no longer active');
  const current = dependencies.asks.get(child.reference, answer.ask.ask_id);
  if (current === null || !same_wire(current, answer.ask, 'AskReference'))
    throw new MacChildAdmissionRefused(
      answer.ask.kind === 'permission'
        ? 'permission callback is gone'
        : 'user input callback is gone',
    );
}
function answer_application(
  dependencies: MacCommandDependencies,
  command: ChildCommand,
  ask: AskReference | null,
): JsonObject {
  return ask === null
    ? {}
    : {
        answer_application: as_object(
          {
            answer_id: command.command_id,
            ask,
            applied_at_mac_ms: normalize_integer(dependencies.now_ms()),
          },
          'AnswerApplication',
        ),
      };
}
async function invoke(
  dependencies: MacCommandDependencies,
  child: MacRunningChild,
  command: ChildCommand,
  invoking: () => void,
): Promise<JsonObject> {
  const args = command.arguments;
  switch (args.kind) {
    case 'write_prompt': {
      if (dependencies.state.admission_stopped)
        throw new MacChildAdmissionRefused('executor is quiescing');
      const model = args.model_change == null ? child.model : args.model_change;
      const effort =
        args.reasoning_effort_change == null && !args.reasoning_effort_is_set
          ? child.reasoning_effort
          : (args.reasoning_effort_change ?? null);
      const previous = child.active_turn;
      const turn = command.turn!;
      invoking();
      child.active_turn = turn;
      try {
        const result = await child.native.write_prompt(
          turn as NativeTurn,
          prefix_message_content_text(native_content(args.content), NATIVE_WORKER_CONTEXT, '\n\n'),
          {
            sender_label: args.sender_label,
            sender_content: native_content(args.sender_content),
            sender_message_count: args.sender_message_count as number,
            mode: args.mode,
            model_change: args.model_change ?? null,
            reasoning_effort_change: args.reasoning_effort_change ?? null,
            reasoning_effort_is_set: args.reasoning_effort_is_set,
            automatic_compaction: args.automatic_compaction,
          },
        );
        child.model = model;
        child.reasoning_effort = effort;
        return {
          kind: 'prompt_accepted',
          composed_content_delivered: result.composed_content_delivered,
        };
      } catch (error) {
        if (error instanceof BackendPromptRejected) child.active_turn = previous;
        throw error;
      }
    }
    case 'steer': {
      if (dependencies.state.admission_stopped)
        throw new MacChildAdmissionRefused('executor is quiescing');
      invoking();
      const result = await child.native.steer(
        native_turn(child, command.turn!),
        prefix_message_content_text(native_content(args.content), NATIVE_WORKER_CONTEXT, '\n\n'),
        {
          sender_content: args.sender_content == null ? null : native_content(args.sender_content),
          sender_label: args.sender_label,
        },
      );
      if (result instanceof BackendSteerRefused)
        return { kind: 'steer_refused', refusal_reason: result.refusal_reason };
      return {
        kind: result instanceof BackendSteerAccepted ? 'steer_accepted' : 'steer_uncertain',
        composed_content_delivered: result.composed_content_delivered,
      };
    }
    case 'cancel_running_turn':
      invoking();
      await child.native.cancel_running_turn();
      return {};
    case 'answer_permission_ask':
      check_answer(dependencies, child, args.answer);
      invoking();
      await child.native.answer_permission_ask(args.ask_id, args.option_id);
      return answer_application(
        dependencies,
        command,
        dependencies.asks.take(child.reference, args.ask_id),
      );
    case 'answer_user_input':
      check_answer(dependencies, child, args.answer);
      invoking();
      await child.native.answer_user_input(
        args.request_id,
        args.answers.map((answer) => new UserInputAnswer(answer)),
      );
      return answer_application(
        dependencies,
        command,
        dependencies.asks.take(child.reference, args.request_id),
      );
    default:
      throw new ValueError('unsupported child command');
  }
}

export async function dispatch_command(
  dependencies: MacCommandDependencies,
  command: ChildCommand,
  start_failed: ReadonlyMap<string, CommandOutcome>,
  work: MacCommandWork,
  invoking: () => void,
) {
  if (command.child.executor_incarnation !== dependencies.incarnation)
    return outcome(command, 'refused', { reason: 'incarnation_gone' });
  if (command.operation === 'stop') return dependencies.stop_exact(command, work, invoking);
  if (
    dependencies.state.is_retiring(command.child.child_id) ||
    dependencies.state.is_retired(command.child.child_id)
  )
    throw new BackendSpawnFailed('source child identity was retired');
  let result: JsonObject;
  if (command.arguments.kind === 'start') {
    if (dependencies.state.admission_stopped)
      throw new MacChildAdmissionRefused('executor is quiescing');
    await dependencies.native_children.start(command.child, command.arguments, invoking);
    result = {};
  } else {
    const child = dependencies.native_children.get(command.child.child_id);
    if (child === null) {
      const failed = start_failed.get(command.child.child_id);
      return outcome(command, 'refused', {
        reason: CHILD_START_FAILED,
        error_message:
          failed && same_reference(failed.child, command.child)
            ? outcome_detail(failed, 'unknown')
            : 'the Mac has no agent for this conversation',
      });
    }
    if (!same_reference(child.reference, command.child))
      return outcome(command, 'uncertain', { reason: 'child_gone' });
    result = await child.lock.run(async () => {
      if (
        dependencies.state.is_retiring(command.child.child_id) ||
        dependencies.native_children.is_retiring(command.child.child_id)
      )
        throw new MacChildAdmissionRefused('source child identity is retiring');
      return invoke(dependencies, child, command, invoking);
    });
  }
  return outcome(command, result.kind === 'steer_uncertain' ? 'uncertain' : 'succeeded', result);
}
