/** One ACP agent child and session behind the conversation core's fixed backend contract. */
import { isDeepStrictEqual } from 'node:util';
import { PROTOCOL_VERSION } from '@agentclientprotocol/sdk/unstable';
import type {
  LoadSessionResponse,
  NewSessionResponse,
  PromptResponse,
} from '@agentclientprotocol/sdk/unstable';
import {
  BackendPromptAccepted,
  BackendSpawnFailed,
  BackendSteerRefused,
  NeedsRebind,
  PermissionAnswerWriteFailed,
  PromptWriteFailed,
  PromptWriteRefused,
  SessionLoadFailed,
  UserInputAnswerWriteFailed,
  VendorSessionCursorRebound,
} from '../../conversation/backends/contracts.ts';
import type {
  BackendChild,
  BackendChildFactory,
  BackendEventSink,
  BackendSteerOutcome,
  TurnToken,
} from '../../conversation/backends/contracts.ts';
import {
  AnswerFate,
  PromptDeliveryRefusalReason,
  WorkspacePathPolicy,
} from '../../conversation/contracts.ts';
import type { ResolvedConversationStart } from '../../conversation/contracts.ts';
import {
  ComposedMessageDoesNotContainSenderContent,
  MessageText,
  message_content_starts_with_command,
  sender_labeled_composed_message_content,
  text_message_content,
} from '../../conversation/message_content.ts';
import type { MessageContent } from '../../conversation/message_content.ts';
import type { ConversationMessageFiles } from '../../conversation/message_files.ts';
import type { LocalChildIdentity } from '../../conversation/local_child_contracts.ts';
import type { OwnedChildProcesses, StopOptions } from '../../lifecycle/contracts.ts';
import type { WorkerSupervisionRuntime } from '../../worker_supervision/runtime.ts';
import { observe_promise } from '../../core/promise_observer.ts';
import { Deferred } from '../../core/deferred.ts';
import { AcpLauncher } from './client.ts';
import type { AcpClient } from './client.ts';
import {
  AVAILABLE_COMMANDS_WAIT_TIMEOUT_SECONDS,
  CANCELLED_TURN_ENDING_TIMEOUT_SECONDS,
} from './configuration.ts';
import { acp_child_environment } from './environment.ts';
import { AcpEvents } from './events.ts';
import type { AcpTurn } from './events.ts';
import type { AcpFlavor } from './flavor.ts';
import { AcpMessages } from './messages.ts';
import {
  AcpSessionConfiguration,
  ConfigurationNotApplied,
  acp_error_text,
} from './session_configuration.ts';

type PromptOptions = Parameters<BackendChild['write_prompt']>[2];
/** The core's canonical automatic compaction request, sent as the agent's own command. */
const COMPACT_COMMAND = 'compact';

export interface AcpChildDependencies {
  readonly child_processes: OwnedChildProcesses;
  readonly worker_supervision: WorkerSupervisionRuntime;
}
export class AcpBackendChild implements BackendChild {
  readonly stop_outside_lock = false;
  private readonly flavor: AcpFlavor;
  private resolved_start: ResolvedConversationStart;
  private readonly sink: BackendEventSink;
  private readonly launcher: AcpLauncher;
  private readonly messages: AcpMessages;
  private readonly events: AcpEvents;
  private client: AcpClient | null = null;
  private configuration: AcpSessionConfiguration | null = null;
  private stopped = false;
  constructor(
    flavor: AcpFlavor,
    dependencies: AcpChildDependencies,
    options: {
      resolved_start: ResolvedConversationStart;
      event_sink: BackendEventSink;
      message_files: ConversationMessageFiles;
    },
  ) {
    this.flavor = flavor;
    this.launcher = new AcpLauncher(dependencies.child_processes, dependencies.worker_supervision, {
      label: flavor.label,
      custody_sink: options.event_sink,
    });
    this.resolved_start = options.resolved_start;
    this.sink = options.event_sink;
    this.messages = new AcpMessages(options.message_files, options.resolved_start.conversation_id);
    this.events = new AcpEvents(this.sink, this.messages, flavor, (error) => this.log(error));
  }
  async start(
    resolved_start: ResolvedConversationStart,
    options: { vendor_session_cursor: string | null },
  ): Promise<void> {
    if (resolved_start.workspace_path_policy === WorkspacePathPolicy.executor_default)
      throw new BackendSpawnFailed('executor workspace requires native resolution');
    if (this.stopped) throw new BackendSpawnFailed('this child is being shut down');
    this.resolved_start = resolved_start;
    try {
      const launch = this.flavor.launch(resolved_start);
      const client = await this.launcher.start(
        {
          argv: launch.argv,
          environment: acp_child_environment(launch.environment_overrides, resolved_start),
        },
        resolved_start,
        {
          session_update: async (params) => {
            await this.events.session_update(params);
          },
          request_permission: async (params, id) => {
            return this.events.request_permission(params, id);
          },
          log: (error) => this.log(error),
        },
      );
      this.client = client;
      const config = new AcpSessionConfiguration(client, this.flavor.label);
      this.configuration = config;
      try {
        await client.request('initialize', {
          protocolVersion: PROTOCOL_VERSION,
          clientInfo: { name: 'panels', title: 'Panels', version: '2.0.0' },
        });
      } catch (error) {
        throw new BackendSpawnFailed(this.start_failure_text(error), { cause: error });
      }
      this.require_running();
      const binding = new Deferred<void>();
      try {
        let response: NewSessionResponse | LoadSessionResponse;
        const cursor = options.vendor_session_cursor;
        try {
          response = await client.request(
            cursor === null ? 'session/new' : 'session/load',
            cursor === null
              ? { cwd: resolved_start.workspace_folder, mcpServers: [] }
              : { cwd: resolved_start.workspace_folder, sessionId: cursor, mcpServers: [] },
            { after_response: () => binding.promise },
          );
        } catch (error) {
          // A signed-out agent did not start. Any other refusal lost this session.
          if (this.flavor.requires_sign_in?.(error))
            throw new BackendSpawnFailed(this.start_failure_text(error), { cause: error });
          throw new SessionLoadFailed(this.start_failure_text(error), { cause: error });
        }
        if (cursor === null) {
          const session_id = (response as NewSessionResponse).sessionId;
          config.bind(session_id, response);
          await this.sink.report(
            null,
            new VendorSessionCursorRebound({ vendor_session_cursor: session_id }),
          );
        } else config.bind(cursor, response);
      } finally {
        binding.resolve();
      }
      this.require_running();
      await config.start_values(resolved_start);
    } catch (error) {
      try {
        await this.stop();
      } catch {
        // The launcher reports its cleanup failure once and preserves startup's error.
      }
      throw error;
    }
  }
  /** The signed-out text, or the agent's own with its standard error tail. */
  private start_failure_text(error: unknown): string {
    const signed_out = this.flavor.requires_sign_in?.(error);
    if (signed_out) return signed_out;
    const why = acp_error_text(error);
    const tail = this.launcher.standard_error_tail()?.trim();
    return tail ? `${why}: ${tail}` : why;
  }
  private require_running(): void {
    if (this.stopped) throw new PromptWriteFailed('this child is being shut down');
  }
  private bound(): { client: AcpClient; config: AcpSessionConfiguration; sessionId: string } {
    this.require_running();
    if (!this.client || !this.configuration)
      throw new PromptWriteFailed('this child has no bound session');
    const sessionId = this.configuration.bound_session();
    return { client: this.client, config: this.configuration, sessionId };
  }
  private is_catalog_command(content: MessageContent): boolean {
    return (
      content.length === 1 &&
      content[0] instanceof MessageText &&
      this.events.available_commands !== null &&
      message_content_starts_with_command(
        content,
        new Set(this.events.available_commands.map((command) => command.name)),
      )
    );
  }
  private async automatic_compaction_content(content: MessageContent): Promise<MessageContent> {
    if (!isDeepStrictEqual(content, text_message_content(`/${COMPACT_COMMAND}`)))
      throw new PromptWriteRefused(
        'an automatic compaction write did not carry the canonical /compact request',
      );
    if (
      !(
        await observe_promise(
          this.events.first_commands.promise,
          AVAILABLE_COMMANDS_WAIT_TIMEOUT_SECONDS * 1000,
        )
      ).completed
    )
      throw new PromptWriteRefused(
        `this ${this.flavor.label} session did not advertise its commands`,
      );
    if (!this.events.available_commands?.some((command) => command.name === COMPACT_COMMAND))
      throw new PromptWriteRefused(
        `this ${this.flavor.label} session did not advertise the ${COMPACT_COMMAND} command`,
      );
    return content;
  }
  /** A child whose process or wire ended while it was idle cannot take a prompt: rebind now. */
  private require_live_child(): void {
    const config = this.configuration;
    if (this.stopped || config === null || !config.wire_ended || this.events.turn !== null) return;
    const failure = config.wire_failure_text();
    throw new NeedsRebind(
      failure.why === null
        ? "this child's wire has already failed"
        : `this child's wire has already failed: ${failure.why}`,
      { failed_child_recovery: true, cause: failure.cause },
    );
  }
  async write_prompt(
    token: TurnToken,
    content: MessageContent,
    options: PromptOptions,
  ): Promise<BackendPromptAccepted> {
    this.require_live_child();
    const native_command =
      (options.sender_message_count ?? 1) === 1 && this.is_catalog_command(options.sender_content);
    if (options.automatic_compaction)
      content = await this.automatic_compaction_content(options.sender_content);
    else if (native_command) content = options.sender_content;
    else {
      try {
        content = sender_labeled_composed_message_content(
          content,
          options.sender_content,
          options.sender_label,
        );
      } catch (error) {
        if (!(error instanceof ComposedMessageDoesNotContainSenderContent)) throw error;
        throw new PromptWriteFailed(error.message, { cause: error });
      }
    }
    const { config } = this.bound();
    const previous = [config.model, config.reasoning_effort] as const;
    try {
      await config.apply_values(options.model_change, options.reasoning_effort_change);
    } catch (error) {
      // The prompt was not sent: the agent refused the selection first.
      if (!(error instanceof ConfigurationNotApplied)) throw error;
      throw new PromptWriteRefused(error.message, { cause: error });
    }
    try {
      await this.write_prompt_to_wire(token, content);
    } catch (error) {
      if (
        error instanceof PromptWriteFailed &&
        (options.model_change !== null || options.reasoning_effort_change !== null)
      )
        await config.restore(previous);
      throw error;
    }
    return new BackendPromptAccepted({
      composed_content_delivered: !(options.automatic_compaction || native_command),
    });
  }
  private async write_prompt_to_wire(token: TurnToken, content: MessageContent): Promise<void> {
    const { client, config, sessionId } = this.bound();
    config.require_wire();
    const prompt = await this.messages.prompt_blocks(content);
    this.require_running();
    config.require_wire();
    const writing = new Deferred<void>();
    let turn: AcpTurn | null = null;
    const request = client.begin_request<PromptResponse>(
      'session/prompt',
      { sessionId, prompt },
      {
        after_response: async () => {
          await writing.promise;
          // The reporter owns diagnostics. Its failure must not deadlock the wire or
          // produce the same diagnostic again through the response barrier.
          await turn?.ending_reporter?.catch(() => {});
        },
      },
    );
    try {
      await request.written;
      turn = this.events.begin(token, request, sessionId);
      turn.ending_reporter = this.events.report_ending(turn, {
        standard_error_tail: () => this.launcher.standard_error_tail(),
        wire_ended: () => config.wire_ended,
        break_wire: (cause) => config.break_wire(cause),
      });
      void turn.ending_reporter.catch((error) => this.log(error));
    } catch (error) {
      config.break_wire(error);
      void request.response.catch((failure) => {
        if (!this.stopped) this.log(failure);
      });
      throw new PromptWriteFailed(`the prompt did not reach the wire: ${acp_error_text(error)}`, {
        cause: error,
      });
    } finally {
      writing.resolve();
    }
  }
  /** ACP has no steer. The message waits and becomes the next prompt. */
  async steer(): Promise<BackendSteerOutcome> {
    return new BackendSteerRefused({
      refusal_reason: PromptDeliveryRefusalReason.backend_cannot_steer,
    });
  }
  /** `session/cancel`, then the prompt's own reply ends the turn as interrupted. */
  async cancel_running_turn(): Promise<void> {
    const { client, config, sessionId } = this.bound();
    const turn = this.events.turn;
    config.require_wire();
    await config.guarded(() => client.notify('session/cancel', { sessionId }));
    if (turn) await this.wait_for_ending(turn);
  }
  private async wait_for_ending(turn: AcpTurn): Promise<void> {
    const tasks = [turn.prompt, turn.ending_reporter].filter(
      (task): task is Promise<PromptResponse> | Promise<void> => task !== null,
    );
    if (
      !(
        await observe_promise(
          Promise.allSettled(tasks),
          CANCELLED_TURN_ENDING_TIMEOUT_SECONDS * 1000,
        )
      ).completed
    )
      console.warn(
        `conversation ${turn.token.conversation_id} did not hear its backend finish the turn it cancelled within ${CANCELLED_TURN_ENDING_TIMEOUT_SECONDS} seconds; carrying on`,
      );
  }
  async answer_permission_ask(ask_id: string, option_id: string): Promise<AnswerFate> {
    const turn = this.events.turn;
    const parked = turn?.parked_asks.get(ask_id);
    if (!turn || !parked || parked.answer.settled || parked.request_id === null)
      throw new PermissionAnswerWriteFailed(ask_id);
    let client: AcpClient;
    try {
      const bound = this.bound();
      bound.config.require_wire();
      client = bound.client;
    } catch (error) {
      if (!(error instanceof PromptWriteFailed)) throw error;
      throw new PermissionAnswerWriteFailed(`${ask_id}: ${error.message}`, { cause: error });
    }
    const reached_wire = client.response_written(parked.request_id);
    parked.answer.resolve({ outcome: { outcome: 'selected', optionId: option_id } });
    try {
      await reached_wire;
    } catch (error) {
      turn.parked_asks.delete(ask_id);
      throw new PermissionAnswerWriteFailed(`${ask_id}: ${acp_error_text(error)}`, {
        cause: error,
      });
    }
    turn.parked_asks.delete(ask_id);
    return AnswerFate.applied;
  }
  /** No ACP agent of this base raises a structured question, so there is none to answer. */
  async answer_user_input(request_id: string): Promise<AnswerFate> {
    throw new UserInputAnswerWriteFailed(request_id);
  }
  async withdraw_delivery(): Promise<boolean> {
    return true;
  }
  detach(options?: StopOptions): Promise<void> {
    return this.stop(options);
  }
  local_child_identity(): Promise<LocalChildIdentity | null> {
    return this.launcher.local_child_identity();
  }
  settled(): Promise<void> {
    return this.launcher.settled();
  }
  async stop(options?: StopOptions): Promise<void> {
    this.stopped = true;
    const turn = this.events.turn;
    this.events.turn = null;
    if (turn) {
      this.events.abandon(turn);
      void turn.prompt.catch(() => {});
    }
    this.client?.close();
    await this.launcher.stop(options);
  }
  private log(error: unknown): void {
    console.warn(`conversation ${this.resolved_start.conversation_id}: ${acp_error_text(error)}`);
  }
}
export function create_acp_child_factory(
  flavor: AcpFlavor,
  dependencies: AcpChildDependencies,
): BackendChildFactory {
  return (options) => new AcpBackendChild(flavor, dependencies, options);
}
