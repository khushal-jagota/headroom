/** One Hermes child/session behind the conversation core's fixed backend contract. */
import { isDeepStrictEqual } from 'node:util';
import { PROTOCOL_VERSION } from '@agentclientprotocol/sdk/unstable';
import type {
  NewSessionResponse,
  LoadSessionResponse,
  PromptResponse,
} from '@agentclientprotocol/sdk/unstable';
import {
  BackendPromptAccepted,
  BackendSpawnFailed,
  BackendSteerAccepted,
  BackendSteerRefused,
  BackendSteerUncertain,
  PermissionAnswerWriteFailed,
  PromptWriteFailed,
  SessionLoadFailed,
  UserInputAnswerWriteFailed,
  VendorSessionCursorRebound,
} from '../contracts.ts';
import type {
  BackendChild,
  BackendChildFactory,
  BackendEventSink,
  BackendSteerOutcome,
  TurnToken,
} from '../contracts.ts';
import { AnswerFate, PromptDeliveryRefusalReason, WorkspacePathPolicy } from '../../contracts.ts';
import type { ResolvedConversationStart } from '../../contracts.ts';
import {
  ComposedMessageDoesNotContainSenderContent,
  MessageText,
  message_content_starts_with_command,
  sender_labeled_composed_message_content,
  text_message_content,
} from '../../message_content.ts';
import type { MessageContent } from '../../message_content.ts';
import type { ConversationMessageFiles } from '../../message_files.ts';
import { within } from '../codex_app_server/process.ts';
import type { AcpChildLaunch } from './launch.ts';
import { HermesAcpLauncher } from './client.ts';
import type { HermesAcpClient } from './client.ts';
import {
  AVAILABLE_COMMANDS_WAIT_TIMEOUT_SECONDS,
  CANCELLED_TURN_ENDING_TIMEOUT_SECONDS,
  ConfigurationNotApplied,
  HermesConfiguration,
  hermes_error_text,
} from './configuration.ts';
import { Deferred, HermesEvents } from './events.ts';
import type { HermesTurn } from './events.ts';
import { HermesMessages } from './messages.ts';

type PromptOptions = Parameters<BackendChild['write_prompt']>[2];
type SteerOptions = Parameters<BackendChild['steer']>[2];
function wire_token(token: TurnToken): { conversationId: string; turnNumber: number } {
  return { conversationId: token.conversation_id, turnNumber: token.turn_number };
}
export class HermesAcpBackendChild implements BackendChild {
  readonly stop_outside_lock = false;
  private readonly launch: AcpChildLaunch;
  private resolved_start: ResolvedConversationStart;
  private readonly sink: BackendEventSink;
  private readonly launcher = new HermesAcpLauncher();
  private readonly messages: HermesMessages;
  private readonly events: HermesEvents;
  private client: HermesAcpClient | null = null;
  private configuration: HermesConfiguration | null = null;
  private stopped = false;
  // Publish the turn only after its actual write. Early notifications wait for that
  // milestone, while session-load replay still sees no turn and is discarded.
  private writing_turn: Deferred<void> | null = null;
  constructor(options: {
    launch: AcpChildLaunch;
    resolved_start: ResolvedConversationStart;
    event_sink: BackendEventSink;
    message_files: ConversationMessageFiles;
  }) {
    this.launch = options.launch;
    this.resolved_start = options.resolved_start;
    this.sink = options.event_sink;
    this.messages = new HermesMessages(
      options.message_files,
      options.resolved_start.conversation_id,
    );
    this.events = new HermesEvents(this.sink, this.messages);
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
      const client = await this.launcher.start(this.launch, resolved_start, {
        session_update: async (params) => {
          await this.writing_turn?.promise;
          await this.events.session_update(params);
        },
        request_permission: async (params, id) => {
          await this.writing_turn?.promise;
          return this.events.request_permission(params, id);
        },
        log: (error) => this.log(error),
      });
      this.client = client;
      const config = new HermesConfiguration(client);
      this.configuration = config;
      try {
        await client.request('initialize', {
          protocolVersion: PROTOCOL_VERSION,
          clientInfo: { name: 'panels', title: 'Panels', version: '2.0.0' },
        });
      } catch (error) {
        throw new BackendSpawnFailed(hermes_error_text(error));
      }
      this.require_running();
      const binding = new Deferred<void>();
      try {
        if (options.vendor_session_cursor === null) {
          let response: NewSessionResponse;
          try {
            response = await client.request(
              'session/new',
              {
                cwd: resolved_start.workspace_folder,
                mcpServers: [],
              },
              { after_response: () => binding.promise },
            );
          } catch (error) {
            throw new SessionLoadFailed(hermes_error_text(error));
          }
          config.bind(response.sessionId, response);
          await this.sink.report(
            null,
            new VendorSessionCursorRebound({ vendor_session_cursor: response.sessionId }),
          );
        } else {
          let response: LoadSessionResponse;
          try {
            response = await client.request(
              'session/load',
              {
                cwd: resolved_start.workspace_folder,
                sessionId: options.vendor_session_cursor,
                mcpServers: [],
              },
              { after_response: () => binding.promise },
            );
          } catch (error) {
            throw new SessionLoadFailed(hermes_error_text(error));
          }
          config.bind(options.vendor_session_cursor, response);
        }
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
  private require_running(): void {
    if (this.stopped) throw new PromptWriteFailed('this child is being shut down');
  }
  private bound(): { client: HermesAcpClient; config: HermesConfiguration; sessionId: string } {
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
    if (!isDeepStrictEqual(content, text_message_content('/compact')))
      throw new PromptWriteFailed(
        'an automatic compaction write did not carry the canonical /compact request',
      );
    if (
      !(await within(this.events.first_commands.promise, AVAILABLE_COMMANDS_WAIT_TIMEOUT_SECONDS))
    )
      throw new PromptWriteFailed('this Hermes session did not advertise its commands');
    if (!this.events.available_commands?.some((command) => command.name === 'compress'))
      throw new PromptWriteFailed('this Hermes session did not advertise the compress command');
    return text_message_content('/compress');
  }
  async write_prompt(
    token: TurnToken,
    content: MessageContent,
    options: PromptOptions,
  ): Promise<BackendPromptAccepted> {
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
        throw new PromptWriteFailed(error.message);
      }
    }
    const { config } = this.bound();
    const previous = [config.model, config.reasoning_effort] as const;
    try {
      await config.apply_values(options.model_change, options.reasoning_effort_change);
    } catch (error) {
      if (!(error instanceof ConfigurationNotApplied)) throw error;
      throw new PromptWriteFailed(error.message);
    }
    try {
      await this.write_prompt_to_wire(
        token,
        content,
        options,
        options.automatic_compaction ?? false,
      );
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
  private async write_prompt_to_wire(
    token: TurnToken,
    content: MessageContent,
    options: PromptOptions,
    automatic_compaction: boolean,
  ): Promise<void> {
    const { client, config, sessionId } = this.bound();
    config.require_wire();
    const prompt = await this.messages.prompt_blocks(content);
    this.require_running();
    config.require_wire();
    const writing = new Deferred<void>();
    this.writing_turn = writing;
    let turn: HermesTurn | null = null;
    const request = client.begin_request<PromptResponse>(
      'session/prompt',
      {
        sessionId,
        prompt,
        _meta: {
          sender_label: options.sender_label,
          delivery_mode: options.mode,
          panelsTurnToken: wire_token(token),
        },
      },
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
      this.require_running();
      turn = this.events.begin(token, request.response, automatic_compaction);
      turn.ending_reporter = this.events.report_ending(turn, () =>
        this.launcher.standard_error_tail(),
      );
      void turn.ending_reporter.catch((error) => this.log(error));
    } catch (error) {
      config.wire_broken = true;
      void request.response.catch((failure) => {
        if (!this.stopped) this.log(failure);
      });
      throw new PromptWriteFailed(`the prompt did not reach the wire: ${hermes_error_text(error)}`);
    } finally {
      if (this.writing_turn === writing) this.writing_turn = null;
      writing.resolve();
    }
  }
  async steer(
    token: TurnToken,
    content: MessageContent,
    options: SteerOptions,
  ): Promise<BackendSteerOutcome> {
    const sender_content = options.sender_content ?? content;
    if (!this.events.turn || !isDeepStrictEqual(this.events.turn.token, token))
      return new BackendSteerRefused({
        refusal_reason: PromptDeliveryRefusalReason.running_turn_changed_before_steer,
      });
    if (!content.length || !content.every((piece) => piece instanceof MessageText))
      return new BackendSteerRefused({
        refusal_reason: PromptDeliveryRefusalReason.message_cannot_be_steered,
      });
    const native_command = this.is_catalog_command(sender_content);
    let bound: ReturnType<HermesAcpBackendChild['bound']>;
    try {
      bound = this.bound();
      bound.config.require_wire();
    } catch (error) {
      if (!(error instanceof PromptWriteFailed)) throw error;
      return new BackendSteerRefused({
        refusal_reason: PromptDeliveryRefusalReason.write_to_backend_failed,
      });
    }
    let wire_content: MessageContent;
    try {
      wire_content = native_command
        ? sender_content
        : sender_labeled_composed_message_content(content, sender_content, options.sender_label);
    } catch (error) {
      if (!(error instanceof ComposedMessageDoesNotContainSenderContent)) throw error;
      return new BackendSteerRefused({
        refusal_reason: PromptDeliveryRefusalReason.message_cannot_be_steered,
      });
    }
    const turnToken = wire_token(token);
    let response: unknown;
    try {
      response = await bound.config.guarded(() =>
        bound.client.request('_panels/steer', {
          sessionId: bound.sessionId,
          turnToken,
          text: wire_content.map((piece) => (piece as MessageText).text).join('\n\n'),
          senderLabel: options.sender_label,
        }),
      );
    } catch (error) {
      if (!(error instanceof PromptWriteFailed)) throw error;
      return new BackendSteerUncertain({ composed_content_delivered: !native_command });
    }
    if (
      !response ||
      typeof response !== 'object' ||
      !('turnToken' in response) ||
      !isDeepStrictEqual(response.turnToken, turnToken)
    )
      return new BackendSteerUncertain({ composed_content_delivered: !native_command });
    if ('accepted' in response && response.accepted === true)
      return new BackendSteerAccepted({ composed_content_delivered: !native_command });
    if ('accepted' in response && response.accepted === false)
      return new BackendSteerRefused({
        refusal_reason: PromptDeliveryRefusalReason.backend_rejected_steer,
      });
    return new BackendSteerUncertain({ composed_content_delivered: !native_command });
  }
  async cancel_running_turn(): Promise<void> {
    const { client, config, sessionId } = this.bound();
    const turn = this.events.turn;
    config.require_wire();
    await config.guarded(() => client.notify('session/cancel', { sessionId }));
    if (turn) await this.wait_for_ending(turn);
  }
  private async wait_for_ending(turn: HermesTurn): Promise<void> {
    const tasks = [turn.prompt, turn.ending_reporter].filter(
      (task): task is Promise<PromptResponse> | Promise<void> => task !== null,
    );
    if (!(await within(Promise.allSettled(tasks), CANCELLED_TURN_ENDING_TIMEOUT_SECONDS)))
      console.warn(
        `conversation ${turn.token.conversation_id} did not hear its backend finish the turn it cancelled within ${CANCELLED_TURN_ENDING_TIMEOUT_SECONDS} seconds; carrying on`,
      );
  }
  async answer_permission_ask(ask_id: string, option_id: string): Promise<AnswerFate> {
    const turn = this.events.turn;
    const parked = turn?.parked_asks.get(ask_id);
    if (!turn || !parked || parked.answer.settled || parked.request_id === null)
      throw new PermissionAnswerWriteFailed(ask_id);
    let client: HermesAcpClient;
    try {
      const bound = this.bound();
      bound.config.require_wire();
      client = bound.client;
    } catch (error) {
      if (!(error instanceof PromptWriteFailed)) throw error;
      throw new PermissionAnswerWriteFailed(ask_id);
    }
    const reached_wire = client.response_written(parked.request_id);
    parked.answer.resolve({ outcome: { outcome: 'selected', optionId: option_id } });
    try {
      await reached_wire;
    } catch {
      turn.parked_asks.delete(ask_id);
      throw new PermissionAnswerWriteFailed(ask_id);
    }
    turn.parked_asks.delete(ask_id);
    return AnswerFate.applied;
  }
  async answer_user_input(request_id: string): Promise<AnswerFate> {
    throw new UserInputAnswerWriteFailed(request_id);
  }
  async withdraw_delivery(): Promise<boolean> {
    return true;
  }
  async detach(): Promise<void> {
    await this.stop();
  }
  async stop(): Promise<void> {
    this.stopped = true;
    this.writing_turn?.resolve();
    const turn = this.events.turn;
    this.events.turn = null;
    if (turn) {
      this.events.settle_asks(turn);
      void turn.prompt.catch(() => {});
    }
    this.client?.close();
    await this.launcher.stop();
  }
  private log(error: unknown): void {
    console.warn(
      `conversation ${this.resolved_start.conversation_id}: ${hermes_error_text(error)}`,
    );
  }
}
export function create_hermes_backend_child_factory(launch: AcpChildLaunch): BackendChildFactory {
  return (options) => new HermesAcpBackendChild({ launch, ...options });
}
