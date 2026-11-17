import type { EffortLevel, Options, SDKUserMessage } from '@anthropic-ai/claude-agent-sdk';
import type {
  OwnedChildProcesses,
  OwnedOneShotChildren,
  StopOptions,
} from '../../../lifecycle/contracts.ts';

import type { WorkerSupervisionRuntime } from '../../../worker_supervision/runtime.ts';
import type { LocalChildIdentity } from '../../../conversation/local_child_contracts.ts';
import type { BackendEventSink } from '../../../conversation/backends/contracts.ts';

/** Public SDK types omit native command lifecycle and some consumed result fields. */
export type ClaudeSdkMessage = Record<string, unknown> & { type: string };
export type ClaudeSdkOptions = Options & {
  pathToClaudeCodeExecutable: string;
  child_processes: OwnedChildProcesses;
  one_shot_children: OwnedOneShotChildren;
  worker_supervision: WorkerSupervisionRuntime;
  /** Every launch is supervised under the conversation it serves. */
  conversation_id: string;
  /** From `worker_ticket_id(resolved_start)`. Null for every conversation but a Worker's. */
  worker_ticket_id: string | null;
  custody_sink?: BackendEventSink;
};
export interface ClaudeSdkClient {
  connect(): Promise<void>;
  get_server_info(): Promise<Record<string, unknown> | null>;
  query(prompt: string | AsyncIterable<SDKUserMessage>): Promise<void>;
  receive_messages(): AsyncIterable<ClaudeSdkMessage>;
  watch_user_message(user_message_uuid: string): void;
  forget_user_message(user_message_uuid: string): void;
  wait_for_user_message_admission(user_message_uuid: string): Promise<boolean | null>;
  user_message_uuids_for_result(result_uuid: string | null): ReadonlySet<string>;
  user_message_is_settled(user_message_uuid: string): boolean;
  wait_for_user_message_settlement(user_message_uuid: string): Promise<void>;
  interrupt_and_cancel_queued(): Promise<readonly [ReadonlySet<string>, ReadonlySet<string>]>;
  /** Change the live session's effort without a restart. */
  apply_reasoning_effort(level: EffortLevel): Promise<void>;
  disconnect(options?: StopOptions): Promise<void>;
  local_child_identity?(): Promise<LocalChildIdentity | null>;
  settled?(): Promise<void>;
}
export interface ClaudeSdkClientFactory {
  (options: ClaudeSdkOptions): ClaudeSdkClient;
}
