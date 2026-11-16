/** Explicit native constructors for executor schema names. No name-based inference. */
import * as Backend from '../conversation/backends/contracts.ts';
import {
  ComposerCatalogEntry,
  ConversationRoleMaterials,
  ResolvedConversationStart,
} from '../conversation/contracts.ts';
import {
  PermissionAskOption,
  PlanEntry,
  UserInputAnswer,
  UserInputOption,
  UserInputQuestion,
} from '../conversation/events.ts';
import { MessageFile, MessageImage, MessageText } from '../conversation/message_content.ts';

export const wire_classes = {
  AgentMessageDelta: Backend.AgentMessageDelta,
  ModelThinkingHappened: Backend.ModelThinkingHappened,
  AgentMessageCompleted: Backend.AgentMessageCompleted,
  CommandReplyReported: Backend.CommandReplyReported,
  ToolCallStarted: Backend.ToolCallStarted,
  ToolCallProgress: Backend.ToolCallProgress,
  ToolCallFinished: Backend.ToolCallFinished,
  PlanUpdated: Backend.PlanUpdated,
  TokenUsageReported: Backend.TokenUsageReported,
  ContextCompacted: Backend.ContextCompacted,
  PermissionAskRaised: Backend.PermissionAskRaised,
  UserInputRequested: Backend.UserInputRequested,
  UserInputFailed: Backend.UserInputFailed,
  TurnEnded: Backend.TurnEnded,
  VendorSessionCursorRebound: Backend.VendorSessionCursorRebound,
  ComposerCatalogReported: Backend.ComposerCatalogReported,
  BackgroundTaskStarted: Backend.BackgroundTaskStarted,
  BackgroundTaskEnded: Backend.BackgroundTaskEnded,
  BackendStreamEnded: Backend.BackendStreamEnded,
  BackendPermissionAsk: Backend.BackendPermissionAsk,
  BackendUserInputRequest: Backend.BackendUserInputRequest,
  ComposerCatalogEntry,
  ConversationRoleMaterials,
  ResolvedConversationStart,
  PermissionAskOption,
  PlanEntry,
  UserInputAnswer,
  UserInputOption,
  UserInputQuestion,
  MessageFile,
  MessageImage,
  MessageText,
} as const;
const roots = new Map<Function, string>(
  Object.entries(wire_classes).map(([root, constructor]) => [constructor, root]),
);
export function native_class_name(value: unknown): string | undefined {
  return value !== null && typeof value === 'object'
    ? roots.get(Object.getPrototypeOf(value)?.constructor)
    : undefined;
}
