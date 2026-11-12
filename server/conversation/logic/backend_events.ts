/** Translate backend facts without provider execution or database access. */
import {
  CommandReplyReported,
  ContextCompacted,
  PermissionAskRaised,
  PlanUpdated,
  TokenUsageReported,
  ToolCallFinished,
  ToolCallStarted,
  TurnEnded,
  UserInputFailed,
  UserInputRequested,
} from '../backends/contracts.ts';
import type { BackendEventValue, TurnToken } from '../backends/contracts.ts';
import {
  AutomaticCompactionResult,
  CommandReplyEventPayload,
  ContextCompactedEventPayload,
  PermissionAskedEventPayload,
  PlanUpdatedEventPayload,
  TokenUsageEventPayload,
  ToolCallFinishedEventPayload,
  ToolCallStartedEventPayload,
  TurnEndedEventPayload,
  UserInputFailedEventPayload,
  UserInputRequestedEventPayload,
} from '../events.ts';
import type { ConversationEventPayload } from '../events.ts';
import type { RunningTurn } from '../record/contracts.ts';
import {
  LifecycleKind as K,
  LifecycleOperation as O,
  LifecycleStatus as S,
} from '../record/lifecycle.ts';
import type { LifecycleFact } from '../record/lifecycle.ts';

export interface InterpretedBackendEvent {
  readonly payloads: readonly ConversationEventPayload[];
  readonly lifecycle: readonly LifecycleFact[];
  readonly predicted: RunningTurn | null;
  readonly compacted: boolean;
  readonly compaction_result: AutomaticCompactionResult | null;
  readonly failure_notice: boolean;
}
export function normalized_failure_summary(detail: string | null): string {
  // Python str.split includes these controls and excludes the JavaScript BOM whitespace.
  let text = (detail ?? '').replace(/[\p{White_Space}\u001c-\u001f]+/gu, ' ').replace(/^ | $/g, '');
  // Python \b uses Unicode letters/numbers. Its IGNORECASE also includes dotted/dotless I.
  // After str.split-equivalent collapse, Python whitespace is exactly the ASCII space.
  const assignment =
    /(["']?(?<![\p{L}\p{N}_])(?:author[iİı]zat[iİı]on|password|ap[iİı][ -]key|(?:[a-z0-9İı]+[_-])*(?:ap[iİı][_-]?key|author[iİı]zat[iİı]on|password|secret|token)(?:[_-][a-z0-9İı]+)*)(?![\p{L}\p{N}_])["']? *[:=] *)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:bearer +)?[^,;}]+)/giu;
  text = text.replace(assignment, '$1[redacted]');
  text = text.replace(
    /(?<![\p{L}\p{N}_])bearer +(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^ ]+)/giu,
    'Bearer [redacted]',
  );
  if (!text) return 'the turn failed for an unknown reason';
  const points = Array.from(text);
  return points.length > 240 ? points.slice(0, 239).join('').replace(/ +$/u, '') + '…' : text;
}
function copy_turn(running: RunningTurn): RunningTurn {
  return {
    ...running,
    pending_permission_ask_ids: new Set(running.pending_permission_ask_ids),
    pending_user_input_questions: new Map(running.pending_user_input_questions),
    permission_provider_ids: new Map(running.permission_provider_ids),
    user_input_provider_ids: new Map(running.user_input_provider_ids),
  };
}
export function interpret_backend_event(
  token: TurnToken | null,
  event: BackendEventValue,
  running: RunningTurn | null,
  known_lifecycle: readonly LifecycleFact[],
): InterpretedBackendEvent {
  let payloads: readonly ConversationEventPayload[] = [];
  let lifecycle: readonly LifecycleFact[] = [];
  const compacted = event instanceof ContextCompacted;
  let compaction_result: AutomaticCompactionResult | null = null;
  let failure_notice = false;
  const predicted =
    running !== null &&
    (compacted ||
      event instanceof PermissionAskRaised ||
      event instanceof UserInputRequested ||
      event instanceof TurnEnded)
      ? copy_turn(running)
      : null;
  if (event instanceof ToolCallStarted) payloads = [new ToolCallStartedEventPayload(event)];
  else if (event instanceof ToolCallFinished) payloads = [new ToolCallFinishedEventPayload(event)];
  else if (event instanceof PlanUpdated) payloads = [new PlanUpdatedEventPayload(event)];
  else if (event instanceof TokenUsageReported) payloads = [new TokenUsageEventPayload(event)];
  else if (event instanceof CommandReplyReported) payloads = [new CommandReplyEventPayload(event)];
  else if (compacted) {
    payloads = [new ContextCompactedEventPayload()];
    if (predicted) predicted.compaction_confirmed = true;
  } else if (event instanceof PermissionAskRaised && token) {
    const identity = `${token.turn_number}:permission:${event.ask.ask_id}`;
    payloads = [new PermissionAskedEventPayload({ ...event.ask, ask_id: identity })];
    lifecycle = [
      {
        kind: K.request,
        identity,
        turn_number: token.turn_number,
        operation: O.permission,
        status: S.pending,
        provider_identity: event.ask.ask_id,
      },
    ];
    predicted?.pending_permission_ask_ids.add(identity);
    predicted?.permission_provider_ids.set(identity, event.ask.ask_id);
  } else if (event instanceof UserInputRequested && token) {
    const identity = `${token.turn_number}:user_input:${event.request.request_id}`;
    payloads = [new UserInputRequestedEventPayload({ ...event.request, request_id: identity })];
    lifecycle = [
      {
        kind: K.request,
        identity,
        turn_number: token.turn_number,
        operation: O.user_input,
        status: S.pending,
        provider_identity: event.request.request_id,
      },
    ];
    predicted?.pending_user_input_questions.set(identity, event.request.questions);
    predicted?.user_input_provider_ids.set(identity, event.request.request_id);
  } else if (event instanceof UserInputFailed && token) {
    payloads = [
      new UserInputFailedEventPayload({
        ...event,
        request_id: `${token.turn_number}:user_input:${event.request_id}`,
      }),
    ];
  } else if (event instanceof TurnEnded && running) {
    compaction_result =
      running.automatic_compaction && event.ending === 'completed' && !running.compaction_confirmed
        ? AutomaticCompactionResult.not_compacted
        : null;
    payloads = [
      new TurnEndedEventPayload({
        ending: event.ending,
        error_summary:
          event.ending === 'failed' ? normalized_failure_summary(event.error_summary) : null,
        automatic_compaction_result: compaction_result,
      }),
    ];
    lifecycle = [
      {
        kind: K.turn,
        identity: String(running.token.turn_number),
        turn_number: running.token.turn_number,
        operation: O.turn,
        status: event.ending,
        provider_identity: null,
      },
      ...known_lifecycle
        .filter(
          (fact) =>
            fact.kind === K.request &&
            fact.turn_number === running.token.turn_number &&
            (fact.status === S.pending || fact.status === S.answering),
        )
        .map((fact) => ({ ...fact, status: S.unavailable })),
    ];
    failure_notice = event.ending === 'failed' && !running.automatic_notice;
    if (predicted) {
      predicted.ended = true;
      predicted.pending_permission_ask_ids.clear();
      predicted.pending_user_input_questions.clear();
      predicted.permission_provider_ids.clear();
      predicted.user_input_provider_ids.clear();
    }
  }
  return { payloads, lifecycle, predicted, compacted, compaction_result, failure_notice };
}
