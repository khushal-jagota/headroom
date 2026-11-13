/** Native events share the command owner's asks and one durable publication door. */
import {
  ToolCallStarted,
  ToolCallProgress,
  ToolCallFinished,
  UserInputFailed,
  PermissionAskRaised,
  UserInputRequested,
  TurnEnded,
  BackgroundTaskStarted,
  BackgroundTaskEnded,
  BackendStreamEnded,
} from '../../../conversation/backends/contracts.ts';
import type {
  BackendEventSink,
  BackendEventValue,
} from '../../../conversation/backends/contracts.ts';
import { as_object, encode_backend_event, ValueError } from '../../protocol.ts';
import type { ChildReference, TurnToken } from '../../wire.generated.ts';
import type { MacEventsDependencies, MacRunnerEventsContract } from './lifecycle_contracts.ts';
import { bounded_detail, same_reference, same_turn } from './logic/outcomes.ts';

function bounded_event(event: BackendEventValue): BackendEventValue {
  if (
    (event instanceof ToolCallStarted ||
      event instanceof ToolCallProgress ||
      event instanceof ToolCallFinished ||
      event instanceof UserInputFailed) &&
    event.detail !== null
  )
    return Reflect.construct(Object.getPrototypeOf(event).constructor, [
      { ...event, detail: bounded_detail(event.detail) },
    ]) as BackendEventValue;
  return event;
}
export class MacRunnerEvents implements MacRunnerEventsContract {
  private readonly dependencies: MacEventsDependencies;
  constructor(dependencies: MacEventsDependencies) {
    this.dependencies = dependencies;
  }
  event_sink(reference: ChildReference): BackendEventSink {
    return {
      report: async (turn, input) => {
        const event = bounded_event(input);
        const [method, arguments_] = encode_backend_event(event);
        if (
          method === 'agent_message_delta' ||
          method === 'model_thinking_happened' ||
          method === 'tool_call_progress'
        ) {
          this.dependencies.live?.send({ child: reference, method, turn, arguments: arguments_ });
          return;
        }
        await this.report_event(reference, turn, event);
      },
    };
  }
  report_event(
    reference: ChildReference,
    turn: TurnToken | null,
    event: BackendEventValue,
  ): Promise<void> {
    return this.dependencies.sources.source_work(
      reference,
      () => this.publish(reference, turn, event),
      { final_event: true },
    );
  }
  private async publish(
    reference: ChildReference,
    turn: TurnToken | null,
    event: BackendEventValue,
  ): Promise<void> {
    const child = this.dependencies.native_children.get(reference.child_id);
    if (child === null || !same_reference(child.reference, reference))
      throw new ValueError('event from unknown child');
    const state = this.dependencies.state;
    if (event instanceof PermissionAskRaised) {
      if (turn === null) throw new ValueError('permission ask lacks turn');
      state.install({ child: reference, turn, ask_id: event.ask.ask_id, kind: 'permission' });
    } else if (event instanceof UserInputRequested) {
      if (turn === null) throw new ValueError('user input request lacks turn');
      state.install({
        child: reference,
        turn,
        ask_id: event.request.request_id,
        kind: 'user_input',
      });
    } else if (event instanceof ToolCallStarted) child.open_tool_calls.add(event.tool_call_id);
    else if (event instanceof ToolCallFinished) child.open_tool_calls.delete(event.tool_call_id);
    else if (event instanceof TurnEnded) {
      if (
        child.active_turn === null
          ? turn === null
          : turn !== null && same_turn(child.active_turn, turn)
      ) {
        child.active_turn = null;
        state.clear(reference);
        child.open_tool_calls.clear();
      }
    } else if (event instanceof BackgroundTaskStarted) child.background_task_ids.add(event.task_id);
    else if (event instanceof BackgroundTaskEnded) child.background_task_ids.delete(event.task_id);
    else if (event instanceof BackendStreamEnded) child.background_task_ids.clear();
    const [method, arguments_] = encode_backend_event(event);
    const ticket_id =
      method === 'agent_message_completed' ||
      method === 'tool_call_finished' ||
      method === 'turn_ended'
        ? child.ticket_id
        : undefined;
    await this.dependencies.publication.add(
      {
        conversation_id: reference.conversation_id,
        kind: 'backend_event',
        payload: as_object(
          { child: reference, method, turn, arguments: arguments_ },
          'BackendEvent',
        ),
      },
      ticket_id === undefined ? undefined : { ticket_id },
    );
  }
}
