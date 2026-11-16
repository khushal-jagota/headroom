import type { Options, SDKUserMessage } from '@anthropic-ai/claude-agent-sdk';
import type {
  OwnedChildProcesses,
  OwnedOneShotChildren,
  StopOptions,
} from '../../../lifecycle/contracts.ts';

import type { WorkerSupervisionRuntime } from '../../../worker_supervision/runtime.ts';
import type { LocalChildIdentity } from '../../local_child_contracts.ts';
import type { BackendEventSink } from '../contracts.ts';

/** Public SDK types omit native command lifecycle and some consumed result fields. */
export type ClaudeSdkMessage = Record<string, unknown> & { type: string };
export type ClaudeSdkOptions = Options & {
  pathToClaudeCodeExecutable: string;
  child_processes: OwnedChildProcesses;
  one_shot_children: OwnedOneShotChildren;
  worker_supervision?: WorkerSupervisionRuntime;
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
  disconnect(options?: StopOptions): Promise<void>;
  local_child_identity?(): Promise<LocalChildIdentity | null>;
  settled?(): Promise<void>;
}
export interface ClaudeSdkClientFactory {
  (options: ClaudeSdkOptions): ClaudeSdkClient;
}
