import type { Options, SDKUserMessage } from '@anthropic-ai/claude-agent-sdk';

/** Public SDK types omit native command lifecycle and some consumed result fields. */
export type ClaudeSdkMessage = Record<string, unknown> & { type: string };
export type ClaudeSdkOptions = Options & { pathToClaudeCodeExecutable: string };
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
  disconnect(): Promise<void>;
}
export interface ClaudeSdkClientFactory {
  (options: ClaudeSdkOptions): ClaudeSdkClient;
}
