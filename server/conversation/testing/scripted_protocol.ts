/** Test-only JSON-lines boundary shared by the TS and Python child adapters. */
import * as backend from '../backends/contracts.ts';
import type { BackendEventValue, TurnToken } from '../backends/contracts.ts';
import { ComposerCatalogEntry } from '../contracts.ts';
import { PermissionAskOption, PlanEntry, UserInputOption, UserInputQuestion } from '../events.ts';
import type { MessageContent } from '../message_content.ts';
import { message_content_from_stored, message_content_json_entries } from '../message_content.ts';

export interface ScriptedRequest {
  readonly id: number;
  readonly command: string;
  readonly arguments: Record<string, unknown>;
}
export interface ScriptedEvent {
  readonly kind: string;
  readonly values: Record<string, unknown>;
}
export type ScriptedFrame =
  | { readonly id: number; readonly result: unknown }
  | {
      readonly id: number;
      readonly error: ScriptedFailure;
    }
  | { readonly event: ScriptedEvent; readonly turn: TurnToken | null };
export interface ScriptedCall {
  readonly command: string;
  readonly arguments: Record<string, unknown>;
}
export interface ScriptedSnapshot {
  readonly token: TurnToken | null;
  readonly calls: readonly ScriptedCall[];
  readonly delivered_text: readonly string[];
  readonly answers: Readonly<Record<string, readonly { question_id: string; answers: string[] }[]>>;
  readonly barriers: readonly string[];
}
export interface ScriptedFailure {
  readonly name: string;
  readonly message: string;
  readonly failed_child_recovery?: boolean;
}
export interface ScriptedBehavior {
  readonly error?: ScriptedFailure;
  readonly barrier?: string;
  readonly events?: readonly { turn?: TurnToken | null; event: ScriptedEvent }[];
  readonly result?: unknown;
}

type Fields<T extends new (...args: never[]) => unknown> = ConstructorParameters<T>[0];

/** Keep the existing seam classes: core event dispatch relies on instanceof. */
export function decode_scripted_event(event: ScriptedEvent): BackendEventValue {
  const values = event.values;
  switch (event.kind) {
    case 'AgentMessageDelta':
      return new backend.AgentMessageDelta(values as Fields<typeof backend.AgentMessageDelta>);
    case 'ModelThinkingHappened':
      return new backend.ModelThinkingHappened();
    case 'AgentMessageCompleted':
      return new backend.AgentMessageCompleted({
        content: message_content_from_stored(values),
      });
    case 'CommandReplyReported':
      return new backend.CommandReplyReported(
        values as Fields<typeof backend.CommandReplyReported>,
      );
    case 'ToolCallStarted':
      return new backend.ToolCallStarted(values as Fields<typeof backend.ToolCallStarted>);
    case 'ToolCallProgress':
      return new backend.ToolCallProgress(values as Fields<typeof backend.ToolCallProgress>);
    case 'ToolCallFinished':
      return new backend.ToolCallFinished(values as Fields<typeof backend.ToolCallFinished>);
    case 'PlanUpdated':
      return new backend.PlanUpdated({
        entries: (values.entries as Fields<typeof PlanEntry>[]).map(
          (entry) => new PlanEntry(entry),
        ),
      });
    case 'TokenUsageReported':
      return new backend.TokenUsageReported(values as Fields<typeof backend.TokenUsageReported>);
    case 'ContextCompacted':
      return new backend.ContextCompacted();
    case 'PermissionAskRaised': {
      const ask = values.ask as Fields<typeof backend.BackendPermissionAsk>;
      return new backend.PermissionAskRaised({
        ask: new backend.BackendPermissionAsk({
          ...ask,
          options: ask.options.map((option) => new PermissionAskOption(option)),
        }),
      });
    }
    case 'UserInputRequested': {
      const request = values.request as Fields<typeof backend.BackendUserInputRequest>;
      return new backend.UserInputRequested({
        request: new backend.BackendUserInputRequest({
          ...request,
          questions: request.questions.map(
            (question) =>
              new UserInputQuestion({
                ...question,
                options: question.options.map((option) => new UserInputOption(option)),
              }),
          ),
        }),
      });
    }
    case 'UserInputFailed':
      return new backend.UserInputFailed(values as Fields<typeof backend.UserInputFailed>);
    case 'TurnEnded':
      return new backend.TurnEnded(values as Fields<typeof backend.TurnEnded>);
    case 'VendorSessionCursorRebound':
      return new backend.VendorSessionCursorRebound(
        values as Fields<typeof backend.VendorSessionCursorRebound>,
      );
    case 'ComposerCatalogReported':
      return new backend.ComposerCatalogReported({
        composer_catalog: (values.composer_catalog as Fields<typeof ComposerCatalogEntry>[]).map(
          (entry) => new ComposerCatalogEntry(entry),
        ),
      });
    case 'BackgroundTaskStarted':
      return new backend.BackgroundTaskStarted(
        values as Fields<typeof backend.BackgroundTaskStarted>,
      );
    case 'BackgroundTaskEnded':
      return new backend.BackgroundTaskEnded(values as Fields<typeof backend.BackgroundTaskEnded>);
    case 'BackendStreamEnded':
      return new backend.BackendStreamEnded();
    default:
      throw new Error(`unknown scripted backend event ${event.kind}`);
  }
}

export function encode_scripted_event(event: BackendEventValue): ScriptedEvent {
  if (event instanceof backend.AgentMessageCompleted) {
    return {
      kind: 'AgentMessageCompleted',
      values: message_content_json_entries(event.content),
    };
  }
  return { kind: event.constructor.name, values: { ...event } };
}

export function scripted_content(content: MessageContent): readonly Record<string, unknown>[] {
  const stored = message_content_json_entries(content);
  return (stored.content ?? [{ piece: 'text', text: stored.text }]) as Record<string, unknown>[];
}
