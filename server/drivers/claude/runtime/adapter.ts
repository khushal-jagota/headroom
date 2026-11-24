import {
  LAUNCH_TOKEN_ENVIRONMENT_NAME,
  UNIDENTIFIED_LAUNCH_TOKEN,
} from '../../../agent_identity/contracts.ts';
/** One selected Claude CLI child, persistent session, and backend event stream. */
import { randomUUID } from 'node:crypto';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import { observe_promise } from '../../../core/promise_observer.ts';
import type { EffortLevel, Options, SDKUserMessage } from '@anthropic-ai/claude-agent-sdk';
import * as B from '../../../conversation/backends/contracts.ts';
import type {
  ResolvedConversationStart,
  PromptDeliveryMode,
  AnswerFate,
} from '../../../conversation/contracts.ts';
import {
  ConversationAccess,
  PromptDeliveryRefusalReason as R,
  WorkspacePathPolicy,
} from '../../../conversation/contracts.ts';
import { ConversationTurnEnding } from '../../../conversation/events.ts';
import type { UserInputAnswer } from '../../../conversation/events.ts';
import { answer_file_references } from '../../../conversation/user_input_attachments.ts';
import {
  ComposedMessageDoesNotContainSenderContent,
  MessageText,
  MessageImage,
  MessageFile,
  sender_labeled_composed_message_content,
  message_content_starts_with_command,
} from '../../../conversation/message_content.ts';
import type { MessageContent } from '../../../conversation/message_content.ts';
import type { ConversationMessageFiles } from '../../../conversation/message_files.ts';
import {
  BackendCliIncompatible,
  ensure_cli_compatible,
} from '../../../conversation/backends/cli_compatibility.ts';
import type {
  OwnedChildProcesses,
  OwnedOneShotChildren,
  StopOptions,
} from '../../../lifecycle/contracts.ts';
import type { WorkerSupervisionRuntime } from '../../../worker_supervision/runtime.ts';
import { worker_ticket_id } from '../../../worker_supervision/logic/worker_launch.ts';
import type { LocalChildIdentity } from '../../../conversation/local_child_contracts.ts';
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
  CLAUDE_CAPABILITIES,
  claude_selection_transition,
  is_live_reasoning_effort,
} from '../models.ts';
import { selection_target } from '../../../conversation/logic/selection_target.ts';
import { Deferred } from '../../../core/deferred.ts';
import { TurnInFlight, same_turn, settle_parked_asks, stop_waiting_for_steers } from './turn.ts';
import {
  display_text,
  error_text,
  normalized_failure_summary,
} from '../../../conversation/backends/failure_summary.ts';

export interface ClaudeAgentSdkChildLaunch {
  claude_executable: string;
  environment_overrides?: readonly (readonly [string, string])[];
}
interface ChildOptions {
  child_processes: OwnedChildProcesses;
  one_shot_children: OwnedOneShotChildren;
  worker_supervision: WorkerSupervisionRuntime;
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
  private readonly child_processes: OwnedChildProcesses;
  private readonly one_shot_children: OwnedOneShotChildren;
  private readonly worker_supervision: WorkerSupervisionRuntime;
  private client: ClaudeSdkClient | null = null;
  private session_id: string | null = null;
  private resume_confirmed = false;
  private session_model: string | null = null;
  private session_reasoning_effort: string | null = null;
  private turn: TurnInFlight | null = null;
  private pending_agent_turn: Promise<void> | null = null;
  private last_ended_turn: TurnInFlight | null = null;
  private reader: Promise<void> | null = null;
  private stopping: Promise<void> | null = null;
  private readonly client_settlements = new Set<Promise<void>>();
  private reader_cancel: AbortController | null = null;
  private wire_broken = false;
  private wire_failure: { why: string; cause: unknown } | null = null;
  private readonly standard_error: string[] = [];
  private available_command_names = new Set<string>();
  private readonly events: ClaudeEvents;
  private readonly asks: ClaudeAsks;
  constructor(options: ChildOptions) {
    this.child_processes = options.child_processes;
    this.one_shot_children = options.one_shot_children;
    this.worker_supervision = options.worker_supervision;
    this.launch = options.launch;
    this.resolved_start = options.resolved_start;
    this.sink = options.event_sink;
    this.message_files = options.message_files;
    this.client_factory = options.client_factory ?? claude_sdk_client;
    this.events = new ClaudeEvents(this.sink);
    this.asks = new ClaudeAsks(this.sink, () => this.turn);
  }
  async confirm_cli_compatible(): Promise<void> {
    try {
      await ensure_cli_compatible('claude', this.launch.claude_executable, {
        environment: Object.fromEntries(this.launch.environment_overrides ?? []),
        children: this.one_shot_children,
      });
    } catch (error) {
      if (error instanceof BackendCliIncompatible)
        throw new B.BackendSpawnFailed(error_text(error), { cause: error });
      throw error;
    }
  }
  async start(
    resolved_start: ResolvedConversationStart,
    options: { vendor_session_cursor: string | null },
  ): Promise<void> {
    if (this.client !== null || this.reader !== null)
      throw new B.BackendSpawnFailed('the previous Claude child still owns cleanup handles');
    this.stopping = null;
    this.wire_failure = null;
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
      const why = this.with_standard_error(error_text(error));
      this.break_wire(why, error);
      if (
        error instanceof Error &&
        'errorClass' in error &&
        error.errorClass === 'initialize_timeout'
      )
        throw new B.BackendInitializationTimeout(why, { cause: error });
      if (
        error instanceof BackendCliIncompatible ||
        (error instanceof Error && error.name === 'CLINotFoundError')
      )
        throw new B.BackendSpawnFailed(error_text(error), { cause: error });
      if (cursor !== null) throw new B.SessionLoadFailed(why, { cause: error });
      throw new B.BackendSpawnFailed(why, { cause: error });
    }
    this.wire_broken = false;
    this.wire_failure = null;
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
      [LAUNCH_TOKEN_ENVIRONMENT_NAME, UNIDENTIFIED_LAUNCH_TOKEN],
      ...(start.role_materials?.identity_environment_variables ?? []),
    ]);
    return {
      child_processes: this.child_processes,
      one_shot_children: this.one_shot_children,
      worker_supervision: this.worker_supervision,
      conversation_id: start.conversation_id,
      worker_ticket_id: worker_ticket_id(start),
      custody_sink: this.sink,
      cwd: start.workspace_folder,
      pathToClaudeCodeExecutable: this.launch.claude_executable,
      systemPrompt: { type: 'preset', preset: 'claude_code' },
      model: start.model,
      ...(start.reasoning_effort === null
        ? {}
        : { effort: start.reasoning_effort as Options['effort'] }),
      // Full access runs in auto mode: bypassPermissions cannot switch off Claude Code's
      // dangerous-rm check (2.1.322+), which then asks a person. Auto mode lets the
      // classifier refuse such a command itself, so the agent picks another way.
      permissionMode: start.access === ConversationAccess.full ? 'auto' : 'default',
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
    if (this.wire_broken) {
      const failure = this.wire_failure;
      throw new B.NeedsRebind(
        failure === null
          ? "this child's wire has already failed"
          : `this child's wire has already failed: ${failure.why}`,
        { failed_child_recovery: true, cause: failure?.cause },
      );
    }
  }
  /** Keep the first reason the wire failed, so a later refusal can say why. */
  private break_wire(why: string, cause: unknown = null): void {
    this.wire_broken = true;
    this.wire_failure ??= { why, cause };
  }
  /** The live effort this prompt applies, or null. The core asks `claude_selection_transition`
   * before it writes to this child; a caller that writes directly (the Mac runner, a replayed
   * prompt) meets the same rule here, against the session this child actually runs. */
  private effort_to_apply(options: PromptOptions): EffortLevel | null {
    const current = { model: this.session_model, reasoning_effort: this.session_reasoning_effort };
    const target = selection_target(
      { ...options, reasoning_effort_is_set: options.reasoning_effort_is_set ?? false },
      current,
    );
    const plan = claude_selection_transition({
      current,
      target,
      session_capabilities: CLAUDE_CAPABILITIES,
    });
    if (plan.type !== 'apply_on_next_turn')
      throw new B.NeedsRebind(
        `claude needs a fresh child for model ${JSON.stringify(target.model)} and reasoning effort ${JSON.stringify(target.reasoning_effort)}`,
      );
    const effort = target.reasoning_effort;
    return effort !== current.reasoning_effort &&
      effort !== null &&
      is_live_reasoning_effort(effort)
      ? effort
      : null;
  }
  private async apply_reasoning_effort(
    client: ClaudeSdkClient,
    reasoning_effort: EffortLevel | null,
  ): Promise<void> {
    if (reasoning_effort === null) return;
    try {
      await client.apply_reasoning_effort(reasoning_effort);
    } catch (error) {
      this.break_wire(error_text(error), error);
      throw new B.PromptWriteFailed(error_text(error), { cause: error });
    }
    this.session_reasoning_effort = reasoning_effort;
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
        } catch (error) {
          throw new B.PromptWriteFailed(
            `${piece.stored_file_id} could not be read: ${error_text(error)}`,
            { cause: error },
          );
        }
        blocks.push({
          type: 'image',
          source: {
            type: 'base64',
            // The core validates image media. Forward the native value unchanged.
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
        } catch (error) {
          throw new B.PromptWriteFailed(
            `${piece.stored_file_id} could not be read: ${error_text(error)}`,
            { cause: error },
          );
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
    if (this.pending_agent_turn !== null) await this.pending_agent_turn;
    const reasoning_effort = this.effort_to_apply(options);
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
        throw new B.PromptWriteFailed(error.message, { cause: error });
      throw error;
    }
    if (automatic || native_command) {
      const asked = await this.query_argument(delivered);
      await this.apply_reasoning_effort(client, reasoning_effort);
      this.turn = new TurnInFlight(turn_token, automatic);
      try {
        await client.query(asked);
      } catch (error) {
        this.break_wire(error_text(error), error);
        throw new B.PromptWriteFailed(error_text(error), { cause: error });
      }
      return new B.BackendPromptAccepted({ composed_content_delivered: false });
    }
    const uuid = randomUUID();
    const asked = await this.query_argument(delivered, uuid);
    await this.apply_reasoning_effort(client, reasoning_effort);
    const turn = new TurnInFlight(turn_token);
    turn.prompt_uuid = uuid;
    turn.sent_user_message_uuids.add(uuid);
    this.turn = turn;
    client.watch_user_message(uuid);
    let failure: unknown = null;
    try {
      await client.query(asked);
    } catch (error) {
      failure = error;
      this.break_wire(error_text(error), error);
    }
    const admission = await client.wait_for_user_message_admission(uuid);
    const reason = failure === null ? '' : `: ${error_text(failure)}`;
    const cause = failure === null ? undefined : { cause: failure };
    if (admission === false) {
      if (this.turn === turn) this.turn = null;
      throw new B.BackendPromptRejected(`claude refused the user message${reason}`, cause);
    }
    if (admission !== true) {
      this.break_wire('claude did not acknowledge the user message');
      throw new B.PromptWriteFailed(`claude did not acknowledge the user message${reason}`, cause);
    }
    if (failure !== null) await this.fail_running_turn(error_text(failure));
    return new B.BackendPromptAccepted({ composed_content_delivered: true });
  }
  async steer(
    turn_token: B.TurnToken,
    content: MessageContent,
    options: { sender_content?: MessageContent | null; sender_label: string },
  ): Promise<B.BackendSteerOutcome> {
    if (this.pending_agent_turn !== null) await this.pending_agent_turn;
    const sender_content = options.sender_content ?? content;
    const turn = this.turn;
    const refused = (reason: R, detail: string | null = null) =>
      new B.BackendSteerRefused({ refusal_reason: reason, refusal_detail: detail });
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
        return refused(R.write_to_backend_failed, error.message);
      throw error;
    }
    if (this.turn !== turn || turn.cancel_requested)
      return refused(R.no_running_turn_to_steer_into);
    try {
      this.require_live_wire();
    } catch (error) {
      if (error instanceof B.NeedsRebind) return refused(R.write_to_backend_failed, error.message);
      throw error;
    }
    client.watch_user_message(uuid);
    turn.owned_steer_uuids.add(uuid);
    turn.sent_user_message_uuids.add(uuid);
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
    if (this.pending_agent_turn !== null) await this.pending_agent_turn;
    const turn = this.turn;
    if (!turn) return;
    turn.cancel_requested = true;
    const client = this.connected_client();
    let still_queued: ReadonlySet<string>;
    try {
      [, still_queued] = await client.interrupt_and_cancel_queued();
    } catch (error) {
      this.break_wire(error_text(error), error);
      throw new B.PromptWriteFailed(error_text(error), { cause: error });
    }
    if (still_queued.size) {
      this.break_wire('claude did not confirm cancellation of every queued command');
      throw new B.PromptWriteFailed('claude did not confirm cancellation of every queued command');
    }
    turn.owned_steer_uuids.clear();
    settle_parked_asks(turn);
    try {
      const ended = await observe_promise(
        turn.ended.promise,
        CLAUDE_CANCEL_SETTLEMENT_TIMEOUT_SECONDS * 1000,
      );
      if (!ended.completed) throw new Error('timeout');
    } catch (error) {
      const why = 'claude cancelled its queue but did not settle the active command';
      this.break_wire(why, error);
      throw new B.PromptWriteFailed(why, { cause: error });
    }
  }
  async answer_permission_ask(ask_id: string, option_id: string): Promise<AnswerFate> {
    return this.asks.answer_permission(ask_id, option_id);
  }
  async answer_user_input(
    request_id: string,
    answers: readonly UserInputAnswer[],
  ): Promise<AnswerFate> {
    return this.asks.answer_user_input(
      request_id,
      answer_file_references(answers, this.message_files, this.resolved_start.conversation_id),
    );
  }
  async withdraw_delivery(): Promise<boolean> {
    return true;
  }
  detach(options?: StopOptions): Promise<void> {
    return this.stop(options);
  }
  async local_child_identity(): Promise<LocalChildIdentity | null> {
    return (await this.client?.local_child_identity?.()) ?? null;
  }
  async settled(): Promise<void> {
    await this.stopping?.catch(() => {});
    await this.client?.settled?.();
    while (this.client_settlements.size) await Promise.allSettled([...this.client_settlements]);
  }
  stop(options?: StopOptions): Promise<void> {
    if (this.stopping === null) {
      const task = this.stop_owned(options);
      this.stopping = task;
      void task.catch(() => {
        if (this.stopping === task) this.stopping = null;
      });
    }
    return options === undefined
      ? this.stopping
      : observe_shutdown(this.stopping, options, 'Claude child cleanup remains unconfirmed');
  }
  private async stop_owned(options?: StopOptions): Promise<void> {
    this.break_wire('this child is being shut down');
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
      await client.disconnect(options);
      const settled = client.settled?.() ?? Promise.resolve();
      this.client_settlements.add(settled);
      void settled.finally(() => this.client_settlements.delete(settled)).catch(() => {});
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
          child_disposition: this.wire_broken ? 'broken' : 'reusable',
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
      const why = this.with_standard_error(error_text(error));
      this.break_wire(why, error);
      await this.fail_running_turn(why);
      this.events.forget_background_tasks();
      await this.sink.report(null, new B.BackendStreamEnded());
      return;
    }
    if (cancel.aborted) return;
    const why = this.with_standard_error("the backend's message stream ended");
    this.break_wire(why);
    await this.fail_running_turn(why);
    this.events.forget_background_tasks();
    await this.sink.report(null, new B.BackendStreamEnded());
  }
  private async take_in(message: ClaudeSdkMessage): Promise<void> {
    validate_message(message);
    const session_id = durable_session_id(message);
    if (session_id !== null) await this.note_session_id(session_id);
    if (this.wire_broken) return;
    const wake_reason = this.events.agent_turn_reason(message);
    if (this.turn === null && wake_reason !== null) {
      const pending = (async () => {
        const token = await this.sink.start_agent_turn?.(wake_reason, this.reader_cancel?.signal);
        if (token !== null && token !== undefined && !this.wire_broken && this.turn === null)
          this.turn = new TurnInFlight(token);
      })();
      this.pending_agent_turn = pending;
      try {
        await pending;
      } finally {
        if (this.pending_agent_turn === pending) this.pending_agent_turn = null;
      }
    }
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
      const why = `claude answered under session ${JSON.stringify(session_id)} instead of the session ${JSON.stringify(this.session_id)} this conversation asked to resume, so its memory of this conversation is not there`;
      this.break_wire(why);
      await this.fail_running_turn(why);
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
    const client = this.connected_client();
    const named = client.user_message_uuids_for_result(
      typeof message.uuid === 'string' ? message.uuid : null,
    );
    // Claude runs turns of its own, such as one for a task notification after a resume. Their
    // results name none of this turn's messages, so they neither end nor charge this turn.
    if (
      !turn.cancel_requested &&
      turn.prompt_uuid !== null &&
      ![...named].some((uuid) => turn.sent_user_message_uuids.has(uuid))
    )
      return;
    await this.events.usage(turn, message);
    for (const uuid of named) turn.owned_steer_uuids.delete(uuid);
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
    const cancelled = new Deferred<void>();
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
  options: {
    child_processes: OwnedChildProcesses;
    one_shot_children: OwnedOneShotChildren;
    worker_supervision: WorkerSupervisionRuntime;
    client_factory?: ClaudeSdkClientFactory;
  },
): B.BackendChildFactory {
  return (values) => new ClaudeAgentSdkBackendChild({ ...values, launch, ...options });
}
function result_error_summary(message: Record<string, unknown>): string {
  const status = typeof message.api_error_status === 'number' ? message.api_error_status : null;
  if (status === 401) return normalized_failure_summary(null, status);
  if (Array.isArray(message.errors) && message.errors.length)
    return normalized_failure_summary(message.errors.map(display_text).join('; '), status);
  return normalized_failure_summary(message.result || message.subtype, status);
}
