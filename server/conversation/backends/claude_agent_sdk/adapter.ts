/** One selected Claude CLI child, persistent session, and backend event stream. */
import { randomUUID } from 'node:crypto';
import type { Options, SDKUserMessage } from '@anthropic-ai/claude-agent-sdk';
import * as B from '../contracts.ts';
import type { ResolvedConversationStart, PromptDeliveryMode, AnswerFate } from '../../contracts.ts';
import {
  ConversationAccess,
  PromptDeliveryRefusalReason as R,
  WorkspacePathPolicy,
} from '../../contracts.ts';
import { ConversationTurnEnding } from '../../events.ts';
import type { UserInputAnswer } from '../../events.ts';
import {
  ComposedMessageDoesNotContainSenderContent,
  MessageText,
  MessageImage,
  MessageFile,
  sender_labeled_composed_message_content,
  message_content_starts_with_command,
} from '../../message_content.ts';
import type { MessageContent } from '../../message_content.ts';
import type { ConversationMessageFiles } from '../../message_files.ts';
import { BackendCliIncompatible } from '../cli_compatibility.ts';
import { claude_sdk_client } from './query.ts';
import type {
  ClaudeSdkClient,
  ClaudeSdkClientFactory,
  ClaudeSdkMessage,
  ClaudeSdkOptions,
} from './query_contracts.ts';
import {
  CLAUDE_CANCEL_SETTLEMENT_TIMEOUT_SECONDS,
  STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS,
} from './configuration.ts';
import { ClaudeEvents, composer_catalog, durable_session_id, validate_message } from './events.ts';
import { ClaudeAsks } from './asks.ts';
import {
  Pending,
  TurnInFlight,
  same_turn,
  settle_parked_asks,
  stop_waiting_for_steers,
} from './turn.ts';
import { error_text, python_repr, result_error_summary } from './failure_summary.ts';

export interface ClaudeAgentSdkChildLaunch {
  claude_executable: string;
  environment_overrides?: readonly (readonly [string, string])[];
}
interface ChildOptions {
  launch: ClaudeAgentSdkChildLaunch;
  resolved_start: ResolvedConversationStart;
  event_sink: B.BackendEventSink;
  message_files: ConversationMessageFiles;
  client_factory?: ClaudeSdkClientFactory;
}
interface PromptOptions {
  sender_label: string;
  sender_content: MessageContent;
  sender_message_count?: number;
  mode: PromptDeliveryMode;
  model_change: string | null;
  reasoning_effort_change: string | null;
  automatic_compaction?: boolean;
  reasoning_effort_is_set?: boolean;
}
type UserContentBlock = Exclude<SDKUserMessage['message']['content'], string>[number];
type Base64ImageSource = Extract<
  Extract<UserContentBlock, { type: 'image' }>['source'],
  { type: 'base64' }
>;
export class ClaudeAgentSdkBackendChild implements B.BackendChild {
  readonly stop_outside_lock = false;
  private readonly launch: ClaudeAgentSdkChildLaunch;
  private resolved_start: ResolvedConversationStart;
  private readonly sink: B.BackendEventSink;
  private readonly message_files: ConversationMessageFiles;
  private readonly client_factory: ClaudeSdkClientFactory;
  private client: ClaudeSdkClient | null = null;
  private session_id: string | null = null;
  private resume_confirmed = false;
  private session_model: string | null = null;
  private session_reasoning_effort: string | null = null;
  private turn: TurnInFlight | null = null;
  private last_ended_turn: TurnInFlight | null = null;
  private reader: Promise<void> | null = null;
  private reader_cancel: AbortController | null = null;
  private wire_broken = false;
  private readonly standard_error: string[] = [];
  private available_command_names = new Set<string>();
  private readonly events: ClaudeEvents;
  private readonly asks: ClaudeAsks;
  constructor(options: ChildOptions) {
    this.launch = options.launch;
    this.resolved_start = options.resolved_start;
    this.sink = options.event_sink;
    this.message_files = options.message_files;
    this.client_factory = options.client_factory ?? claude_sdk_client;
    this.events = new ClaudeEvents(this.sink);
    this.asks = new ClaudeAsks(this.sink, () => this.turn);
  }
  async start(
    resolved_start: ResolvedConversationStart,
    options: { vendor_session_cursor: string | null },
  ): Promise<void> {
    if (this.client !== null || this.reader !== null)
      throw new B.BackendSpawnFailed('the previous Claude child still owns cleanup handles');
    if (resolved_start.workspace_path_policy === WorkspacePathPolicy.executor_default)
      throw new B.BackendSpawnFailed('executor workspace requires native resolution');
    this.resolved_start = resolved_start;
    const cursor = options.vendor_session_cursor;
    const minted = cursor === null ? randomUUID() : null;
    const client = this.client_factory(this.options(resolved_start, cursor, minted));
    this.wire_broken = true;
    this.client = client;
    try {
      await client.connect();
    } catch (error) {
      if (
        error instanceof BackendCliIncompatible ||
        (error instanceof Error && error.name === 'CLINotFoundError')
      )
        throw new B.BackendSpawnFailed(error_text(error));
      const why = this.with_standard_error(error_text(error));
      if (cursor !== null) throw new B.SessionLoadFailed(why);
      throw new B.BackendSpawnFailed(why);
    }
    this.wire_broken = false;
    this.session_id = cursor || minted;
    this.resume_confirmed = cursor === null;
    this.session_model = resolved_start.model;
    this.session_reasoning_effort = resolved_start.reasoning_effort;
    const cancel = new AbortController();
    this.reader_cancel = cancel;
    this.reader = this.read_stream(client, cancel.signal);
    void this.reader.catch(() => {});
    if (minted !== null)
      await this.sink.report(
        null,
        new B.VendorSessionCursorRebound({ vendor_session_cursor: minted }),
      );
    const catalog = composer_catalog(await client.get_server_info());
    if (catalog !== null) {
      this.available_command_names = new Set(catalog.map((entry) => entry.display_text.slice(1)));
      await this.sink.report(null, new B.ComposerCatalogReported({ composer_catalog: catalog }));
    }
  }
  private options(
    start: ResolvedConversationStart,
    resume: string | null,
    sessionId: string | null,
  ): ClaudeSdkOptions {
    const environment = Object.fromEntries([
      ...(this.launch.environment_overrides ?? []),
      ...(start.role_materials?.identity_environment_variables ?? []),
    ]);
    return {
      cwd: start.workspace_folder,
      pathToClaudeCodeExecutable: this.launch.claude_executable,
      systemPrompt: { type: 'preset', preset: 'claude_code' },
      model: start.model,
      ...(start.reasoning_effort === null
        ? {}
        : { effort: start.reasoning_effort as Options['effort'] }),
      permissionMode: start.access === ConversationAccess.full ? 'bypassPermissions' : 'default',
      ...(start.access === ConversationAccess.full
        ? { allowDangerouslySkipPermissions: true }
        : {}),
      ...(resume !== null ? { resume } : {}),
      ...(sessionId !== null ? { sessionId } : {}),
      env: environment,
      includePartialMessages: true,
      canUseTool: this.asks.can_use_tool,
      stderr: (line) => this.note_standard_error(line),
    };
  }
  private connected_client(): ClaudeSdkClient {
    if (!this.client) throw new B.PromptWriteFailed('this child has no bound session');
    return this.client;
  }
  private require_live_wire(): void {
    if (this.wire_broken)
      throw new B.NeedsRebind("this child's wire has already failed", {
        failed_child_recovery: true,
      });
  }
  private require_carried_values(options: PromptOptions): void {
    if (options.model_change !== null && options.model_change !== this.session_model)
      throw new B.NeedsRebind(
        `claude takes its model when the child starts, so ${python_repr(options.model_change)} needs a child started on it`,
      );
    if (
      (options.reasoning_effort_change !== null || options.reasoning_effort_is_set) &&
      options.reasoning_effort_change !== this.session_reasoning_effort
    )
      throw new B.NeedsRebind(
        `claude takes its reasoning effort when the child starts, so ${python_repr(options.reasoning_effort_change)} needs a child started on it`,
      );
  }
  private is_catalog_command(content: MessageContent): boolean {
    return (
      content.length === 1 &&
      content[0] instanceof MessageText &&
      message_content_starts_with_command(content, this.available_command_names)
    );
  }
  private async query_argument(
    content: MessageContent,
    user_message_uuid: NonNullable<SDKUserMessage['uuid']> | null = null,
  ): Promise<string | AsyncIterable<SDKUserMessage>> {
    if (user_message_uuid === null && content.length === 1 && content[0] instanceof MessageText)
      return content[0].text;
    const blocks: UserContentBlock[] = [];
    for (const piece of content) {
      if (piece instanceof MessageText) blocks.push({ type: 'text', text: piece.text });
      else if (piece instanceof MessageImage) {
        let encoded: string;
        try {
          encoded = Buffer.from(
            await this.message_files.read(
              this.resolved_start.conversation_id,
              piece.stored_file_id,
            ),
          ).toString('base64');
        } catch {
          throw new B.PromptWriteFailed(`${piece.stored_file_id} could not be read`);
        }
        blocks.push({
          type: 'image',
          source: {
            type: 'base64',
            // The core validates image media. Python forwards this native value unchanged.
            media_type: piece.media_type as Base64ImageSource['media_type'],
            data: encoded,
          },
        });
      } else if (piece instanceof MessageFile) {
        let path: string;
        try {
          path = this.message_files.path_of(
            this.resolved_start.conversation_id,
            piece.stored_file_id,
          );
        } catch {
          throw new B.PromptWriteFailed(`${piece.stored_file_id} could not be read`);
        }
        blocks.push({
          type: 'text',
          text: `Attached file "${piece.file_name}" (${piece.media_type}, ${piece.byte_count} bytes) is available at ${path}.`,
        });
      }
    }
    async function* one_user_message(): AsyncGenerator<SDKUserMessage> {
      yield {
        type: 'user',
        message: { role: 'user', content: blocks },
        parent_tool_use_id: null,
        ...(user_message_uuid === null ? {} : { uuid: user_message_uuid }),
      };
    }
    return one_user_message();
  }
  async write_prompt(
    turn_token: B.TurnToken,
    content: MessageContent,
    options: PromptOptions,
  ): Promise<B.BackendPromptAccepted> {
    this.require_carried_values(options);
    const client = this.connected_client();
    this.require_live_wire();
    const native_command =
      (options.sender_message_count ?? 1) === 1 && this.is_catalog_command(options.sender_content);
    const automatic = options.automatic_compaction ?? false;
    let delivered: MessageContent;
    try {
      delivered =
        automatic || native_command
          ? options.sender_content
          : sender_labeled_composed_message_content(
              content,
              options.sender_content,
              options.sender_label,
            );
    } catch (error) {
      if (error instanceof ComposedMessageDoesNotContainSenderContent)
        throw new B.PromptWriteFailed(error.message);
      throw error;
    }
    if (automatic || native_command) {
      const asked = await this.query_argument(delivered);
      this.turn = new TurnInFlight(turn_token, automatic);
      try {
        await client.query(asked);
      } catch (error) {
        this.wire_broken = true;
        throw new B.PromptWriteFailed(error_text(error));
      }
      return new B.BackendPromptAccepted({ composed_content_delivered: false });
    }
    const uuid = randomUUID();
    const asked = await this.query_argument(delivered, uuid);
    const turn = new TurnInFlight(turn_token);
    this.turn = turn;
    client.watch_user_message(uuid);
    let failure: unknown = null;
    try {
      await client.query(asked);
    } catch (error) {
      failure = error;
      this.wire_broken = true;
    }
    const admission = await client.wait_for_user_message_admission(uuid);
    if (admission === false) {
      if (this.turn === turn) this.turn = null;
      throw new B.BackendPromptRejected('claude refused the user message');
    }
    if (admission !== true) {
      this.wire_broken = true;
      throw new B.PromptWriteFailed('claude did not acknowledge the user message');
    }
    if (failure !== null) await this.fail_running_turn(error_text(failure));
    return new B.BackendPromptAccepted({ composed_content_delivered: true });
  }
  async steer(
    turn_token: B.TurnToken,
    content: MessageContent,
    options: { sender_content?: MessageContent | null; sender_label: string },
  ): Promise<B.BackendSteerOutcome> {
    const sender_content = options.sender_content ?? content;
    const turn = this.turn;
    const refused = (reason: R) => new B.BackendSteerRefused({ refusal_reason: reason });
    if (!turn || !same_turn(turn.token, turn_token))
      return refused(R.no_running_turn_to_steer_into);
    const uuid = randomUUID();
    const client = this.connected_client();
    const native_command = this.is_catalog_command(sender_content);
    let asked: string | AsyncIterable<SDKUserMessage>;
    try {
      this.require_live_wire();
      asked = await this.query_argument(
        native_command
          ? sender_content
          : sender_labeled_composed_message_content(content, sender_content, options.sender_label),
        uuid,
      );
    } catch (error) {
      if (
        error instanceof ComposedMessageDoesNotContainSenderContent ||
        error instanceof B.NeedsRebind ||
        error instanceof B.PromptWriteFailed
      )
        return refused(R.write_to_backend_failed);
      throw error;
    }
    if (this.turn !== turn || turn.cancel_requested)
      return refused(R.no_running_turn_to_steer_into);
    try {
      this.require_live_wire();
    } catch (error) {
      if (error instanceof B.NeedsRebind) return refused(R.write_to_backend_failed);
      throw error;
    }
    client.watch_user_message(uuid);
    turn.owned_steer_uuids.add(uuid);
    if (this.turn !== turn || turn.cancel_requested || this.client !== client || this.wire_broken) {
      turn.owned_steer_uuids.delete(uuid);
      client.forget_user_message(uuid);
      return refused(
        this.turn !== turn || turn.cancel_requested
          ? R.no_running_turn_to_steer_into
          : R.write_to_backend_failed,
      );
    }
    try {
      await client.query(asked);
    } catch {
      /* A write failure cannot undo native admission. */
    }
    const admission = await client.wait_for_user_message_admission(uuid);
    if (admission === true)
      return new B.BackendSteerAccepted({ composed_content_delivered: !native_command });
    if (admission === false) {
      turn.owned_steer_uuids.delete(uuid);
      return refused(R.write_to_backend_failed);
    }
    return new B.BackendSteerUncertain({ composed_content_delivered: !native_command });
  }
  async cancel_running_turn(): Promise<void> {
    const turn = this.turn;
    if (!turn) return;
    turn.cancel_requested = true;
    const client = this.connected_client();
    let still_queued: ReadonlySet<string>;
    try {
      [, still_queued] = await client.interrupt_and_cancel_queued();
    } catch (error) {
      this.wire_broken = true;
      throw new B.PromptWriteFailed(error_text(error));
    }
    if (still_queued.size) {
      this.wire_broken = true;
      throw new B.PromptWriteFailed('claude did not confirm cancellation of every queued command');
    }
    turn.owned_steer_uuids.clear();
    settle_parked_asks(turn);
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([
        turn.ended.promise,
        new Promise<never>((_, reject) => {
          timer = setTimeout(
            () => reject(new Error('timeout')),
            CLAUDE_CANCEL_SETTLEMENT_TIMEOUT_SECONDS * 1000,
          );
        }),
      ]);
    } catch {
      this.wire_broken = true;
      throw new B.PromptWriteFailed(
        'claude cancelled its queue but did not settle the active command',
      );
    } finally {
      clearTimeout(timer);
    }
  }
  async answer_permission_ask(ask_id: string, option_id: string): Promise<AnswerFate> {
    return this.asks.answer_permission(ask_id, option_id);
  }
  async answer_user_input(
    request_id: string,
    answers: readonly UserInputAnswer[],
  ): Promise<AnswerFate> {
    return this.asks.answer_user_input(request_id, answers);
  }
  async withdraw_delivery(): Promise<boolean> {
    return true;
  }
  async detach(): Promise<void> {
    await this.stop();
  }
  async stop(): Promise<void> {
    this.wire_broken = true;
    this.events.forget_background_tasks();
    const turn = this.turn;
    this.turn = null;
    this.last_ended_turn = null;
    if (turn) {
      stop_waiting_for_steers(turn);
      settle_parked_asks(turn);
    }
    const reader = this.reader;
    if (reader) {
      this.reader_cancel?.abort();
      try {
        await reader;
      } catch (error) {
        console.error(
          `conversation ${this.resolved_start.conversation_id}: Claude message reader failed during cleanup`,
          error,
        );
      } finally {
        this.reader = null;
        this.reader_cancel = null;
      }
    }
    const client = this.client;
    if (client) {
      await client.disconnect();
      this.client = null;
    }
  }
  private async end_turn(
    turn: TurnInFlight,
    ending: ConversationTurnEnding,
    error_summary: string | null,
  ): Promise<void> {
    if (this.turn !== turn) return;
    this.turn = null;
    this.last_ended_turn = turn;
    stop_waiting_for_steers(turn);
    settle_parked_asks(turn);
    try {
      await this.sink.report(
        turn.token,
        new B.TurnEnded({
          ending,
          error_summary,
          standard_error_tail:
            ending === ConversationTurnEnding.failed ? this.standard_error_tail() : null,
        }),
      );
    } finally {
      turn.ended.resolve();
    }
  }
  private async fail_running_turn(why: string): Promise<void> {
    if (this.turn) await this.end_turn(this.turn, ConversationTurnEnding.failed, why);
  }
  private async read_stream(client: ClaudeSdkClient, cancel: AbortSignal): Promise<void> {
    const iterator = client.receive_messages()[Symbol.asyncIterator]();
    try {
      for (;;) {
        const next = await new Promise<IteratorResult<ClaudeSdkMessage> | null>(
          (resolve, reject) => {
            const stop = () => resolve(null);
            if (cancel.aborted) {
              resolve(null);
              return;
            }
            cancel.addEventListener('abort', stop, { once: true });
            void iterator.next().then(
              (value) => {
                cancel.removeEventListener('abort', stop);
                resolve(value);
              },
              (error) => {
                cancel.removeEventListener('abort', stop);
                reject(error);
              },
            );
          },
        );
        if (next === null) return;
        if (next.done) break;
        await this.take_in(next.value);
        if (cancel.aborted) return;
      }
    } catch (error) {
      if (cancel.aborted) return;
      this.wire_broken = true;
      await this.fail_running_turn(this.with_standard_error(error_text(error)));
      this.events.forget_background_tasks();
      await this.sink.report(null, new B.BackendStreamEnded());
      return;
    }
    if (cancel.aborted) return;
    this.wire_broken = true;
    await this.fail_running_turn(this.with_standard_error("the backend's message stream ended"));
    this.events.forget_background_tasks();
    await this.sink.report(null, new B.BackendStreamEnded());
  }
  private async take_in(message: ClaudeSdkMessage): Promise<void> {
    validate_message(message);
    const session_id = durable_session_id(message);
    if (session_id !== null) await this.note_session_id(session_id);
    if (this.wire_broken) return;
    if (await this.events.take_background(message)) return;
    const turn = this.turn;
    if (!turn) {
      if (message.type === 'assistant' && this.last_ended_turn)
        await this.events.assistant_message(this.last_ended_turn, message, true);
      return;
    }
    if (message.type === 'stream_event') await this.events.stream_event(turn, message);
    else if (message.type === 'assistant') await this.events.assistant_message(turn, message);
    else if (message.type === 'user') await this.events.user_message(turn, message);
    else if (message.type === 'result') await this.result_message(turn, message);
    else if (message.type === 'system' && message.subtype === 'compact_boundary')
      await this.sink.report(turn.token, new B.ContextCompacted());
  }
  private async note_session_id(session_id: string): Promise<void> {
    if (session_id === this.session_id) {
      this.resume_confirmed = true;
      return;
    }
    if (!this.resume_confirmed) {
      this.wire_broken = true;
      await this.fail_running_turn(
        `claude answered under session ${python_repr(session_id)} instead of the session ${python_repr(this.session_id)} this conversation asked to resume, so its memory of this conversation is not there`,
      );
      return;
    }
    this.session_id = session_id;
    await this.sink.report(
      null,
      new B.VendorSessionCursorRebound({ vendor_session_cursor: session_id }),
    );
  }
  private forget_settled_steers(turn: TurnInFlight, client: ClaudeSdkClient): void {
    for (const uuid of turn.owned_steer_uuids)
      if (client.user_message_is_settled(uuid)) turn.owned_steer_uuids.delete(uuid);
  }
  private async result_message(turn: TurnInFlight, message: ClaudeSdkMessage): Promise<void> {
    await this.events.usage(turn, message);
    const client = this.connected_client();
    for (const uuid of client.user_message_uuids_for_result(
      typeof message.uuid === 'string' ? message.uuid : null,
    ))
      turn.owned_steer_uuids.delete(uuid);
    this.forget_settled_steers(turn, client);
    if (turn.owned_steer_uuids.size) {
      turn.held_result = message;
      this.wait_out_steers(turn, client);
      return;
    }
    await this.end_turn_from_result(turn, message);
  }
  private wait_out_steers(turn: TurnInFlight, client: ClaudeSdkClient): void {
    const waiting_for = [...turn.owned_steer_uuids];
    turn.steer_settlement_watch?.cancelled.resolve();
    const cancelled = new Pending<void>();
    const task = (async () => {
      try {
        for (const uuid of waiting_for) {
          await Promise.race([client.wait_for_user_message_settlement(uuid), cancelled.promise]);
          if (cancelled.settled) return;
        }
        if (turn.ended.settled || !turn.held_result) return;
        this.forget_settled_steers(turn, client);
        if (!turn.owned_steer_uuids.size) await this.end_turn_from_result(turn, turn.held_result);
      } catch (error) {
        if (!cancelled.settled) await this.fail_running_turn(error_text(error));
      }
    })();
    turn.steer_settlement_watch = { cancelled, task };
    void task.catch(() => {});
  }
  private async end_turn_from_result(turn: TurnInFlight, message: ClaudeSdkMessage): Promise<void> {
    if (
      turn.cancel_requested ||
      message.terminal_reason === 'aborted_streaming' ||
      message.terminal_reason === 'aborted_tools'
    ) {
      await this.end_turn(turn, ConversationTurnEnding.interrupted, null);
    } else if (message.is_error)
      await this.end_turn(turn, ConversationTurnEnding.failed, result_error_summary(message));
    else {
      await this.events.flush_replies(turn);
      await this.end_turn(turn, ConversationTurnEnding.completed, null);
    }
  }
  private note_standard_error(line: string): void {
    this.standard_error.push(line);
    while (
      this.standard_error.reduce((sum, part) => sum + Array.from(part).length, 0) >
        STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS &&
      this.standard_error.length > 1
    )
      this.standard_error.shift();
  }
  private standard_error_tail(): string | null {
    if (!this.standard_error.length) return null;
    return (
      Array.from(this.standard_error.join(''))
        .slice(-STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS)
        .join('')
        .trim() || null
    );
  }
  private with_standard_error(why: string): string {
    const tail = this.standard_error_tail();
    return tail === null ? why : `${why}: ${tail}`;
  }
}
export function create_claude_backend_child_factory(
  launch: ClaudeAgentSdkChildLaunch,
  options: { client_factory?: ClaudeSdkClientFactory } = {},
): B.BackendChildFactory {
  return (values) =>
    new ClaudeAgentSdkBackendChild({ ...values, launch, client_factory: options.client_factory });
}
