/** An ACP session's news, translated to the backend event vocabulary. */
import type {
  AvailableCommand,
  ContentBlock,
  JsonRpcId,
  PermissionOption,
  PromptResponse,
  RequestPermissionRequest,
  RequestPermissionResponse,
  SessionNotification,
  ToolCallContent,
} from '@agentclientprotocol/sdk/unstable';
import {
  AgentMessageCompleted,
  AgentMessageDelta,
  BackendPermissionAsk,
  ComposerCatalogReported,
  ModelThinkingHappened,
  PermissionAskRaised,
  PlanUpdated,
  TokenUsageReported,
  ToolCallFinished,
  ToolCallProgress,
  ToolCallStarted,
  TurnEnded,
} from '../../conversation/backends/contracts.ts';
import type { BackendEventSink, TurnToken } from '../../conversation/backends/contracts.ts';
import { ComposerCatalogEntry, ComposerCatalogEntryKind } from '../../conversation/contracts.ts';
import {
  ConversationTurnEnding,
  PermissionAskOption,
  PlanEntry,
  ToolCallStatus,
} from '../../conversation/events.ts';
import { joined_runs_of_text, MessageText } from '../../conversation/message_content.ts';
import type { MessagePiece } from '../../conversation/message_content.ts';
import { normalized_failure_summary } from '../../conversation/backends/failure_summary.ts';
import { Deferred } from '../../core/deferred.ts';
import type { AcpRequest } from './client.ts';
import { OPEN_TOOL_CALL_SILENCE_LIMITS } from './configuration.ts';
import type { AcpFlavor, AcpPromptOutcomeWatch } from './flavor.ts';
import type { AcpMessages } from './messages.ts';
import { acp_error_text } from './session_configuration.ts';

export interface ParkedPermissionAsk {
  readonly answer: Deferred<RequestPermissionResponse>;
  readonly request_id: JsonRpcId;
  readonly tool_call_id: string;
  readonly options: readonly PermissionOption[];
}
export interface AcpTurn {
  readonly token: TurnToken;
  readonly request: AcpRequest<PromptResponse>;
  readonly prompt: Promise<PromptResponse>;
  ending_reporter: Promise<void> | null;
  pieces: MessagePiece[];
  message_id: string | null;
  readonly parked_asks: Map<string, ParkedPermissionAsk>;
  /** Tool calls started in this turn and not yet finished. A tool call cannot outlive its turn. */
  readonly open_tool_calls: Set<string>;
  /** Resolves with the failure text once the agent has been silent too long. */
  readonly silence: Deferred<string>;
  silence_timer: ReturnType<typeof setTimeout> | null;
  /** Silence limits passed, since the agent was last heard, while only an open tool call held the clock. */
  limits_held_by_tool_calls: number;
  readonly outcome_watch: AcpPromptOutcomeWatch | null;
}
/** How the ending reporter reads and changes the child's state. */
export interface AcpEndingFacts {
  standard_error_tail(): string | null;
  wire_ended(): boolean;
  break_wire(cause: unknown): void;
}
export function tool_call_detail(content?: readonly ToolCallContent[] | null): string | null {
  const texts = (content ?? []).flatMap((item) =>
    item.type === 'content' && item.content.type === 'text' && item.content.text
      ? [item.content.text]
      : [],
  );
  return texts.length ? texts.join('\n') : null;
}

export class AcpEvents {
  turn: AcpTurn | null = null;
  available_commands: readonly AvailableCommand[] | null = null;
  readonly first_commands = new Deferred<void>();
  private asks_raised = 0;
  private readonly sink: BackendEventSink;
  private readonly messages: AcpMessages;
  private readonly flavor: AcpFlavor;
  private readonly log: (error: unknown) => void;
  constructor(
    sink: BackendEventSink,
    messages: AcpMessages,
    flavor: AcpFlavor,
    log: (error: unknown) => void,
  ) {
    this.sink = sink;
    this.messages = messages;
    this.flavor = flavor;
    this.log = log;
  }
  async session_update(params: SessionNotification): Promise<void> {
    const update = params.update;
    if (update.sessionUpdate === 'available_commands_update') {
      this.available_commands = update.availableCommands;
      this.first_commands.resolve();
      await this.sink.report(
        null,
        new ComposerCatalogReported({
          composer_catalog: update.availableCommands.map(
            (command) =>
              new ComposerCatalogEntry({
                kind: ComposerCatalogEntryKind.command,
                display_text: `/${command.name}`,
                insertion_text: `/${command.name} `,
                description: command.description,
                argument_hint: command.input?.hint ?? null,
              }),
          ),
        }),
      );
      return;
    }
    const turn = this.turn;
    if (!turn) return;
    this.heard_from_agent(turn);
    switch (update.sessionUpdate) {
      case 'agent_thought_chunk':
        await this.sink.report(turn.token, new ModelThinkingHappened());
        break;
      case 'plan':
        await this.sink.report(
          turn.token,
          new PlanUpdated({
            entries: update.entries.map(
              (entry) => new PlanEntry({ text: entry.content, status: entry.status }),
            ),
          }),
        );
        break;
      case 'agent_message_chunk':
        await this.message_chunk(turn, update.content, update.messageId ?? null);
        break;
      case 'tool_call': {
        const status = update.status;
        // An ask already allowed may have opened it.
        if (status === 'completed' || status === 'failed')
          turn.open_tool_calls.delete(update.toolCallId);
        else turn.open_tool_calls.add(update.toolCallId);
        await this.sink.report(
          turn.token,
          new ToolCallStarted({
            tool_call_id: update.toolCallId,
            title: update.title,
            tool_kind: update.kind ?? 'other',
            detail: tool_call_detail(update.content),
          }),
        );
        // A tool call can arrive already finished, and no update will finish it later.
        if (status === 'completed' || status === 'failed')
          await this.sink.report(
            turn.token,
            new ToolCallFinished({
              tool_call_id: update.toolCallId,
              tool_call_status: ToolCallStatus[status],
            }),
          );
        break;
      }
      case 'tool_call_update': {
        const detail = tool_call_detail(update.content);
        const status = update.status;
        if (status === 'completed' || status === 'failed') {
          turn.open_tool_calls.delete(update.toolCallId);
          await this.sink.report(
            turn.token,
            new ToolCallFinished({
              tool_call_id: update.toolCallId,
              tool_call_status: ToolCallStatus[status],
              detail,
            }),
          );
        } else if (detail !== null)
          await this.sink.report(
            turn.token,
            new ToolCallProgress({ tool_call_id: update.toolCallId, detail }),
          );
        break;
      }
      case 'usage_update':
        await this.sink.report(
          turn.token,
          new TokenUsageReported({
            context_input_tokens: update.used,
            cost_usd:
              update.cost && update.cost.currency.toUpperCase() === 'USD'
                ? update.cost.amount
                : null,
          }),
        );
        break;
    }
  }
  private async message_chunk(
    turn: AcpTurn,
    content: ContentBlock,
    message_id: string | null,
  ): Promise<void> {
    const piece = await this.messages.piece_of(content);
    if (!piece) return;
    if (message_id !== null && turn.message_id !== null && message_id !== turn.message_id)
      await this.flush(turn);
    if (message_id !== null) turn.message_id = message_id;
    turn.pieces.push(piece);
    if (piece instanceof MessageText)
      await this.sink.report(turn.token, new AgentMessageDelta({ text_delta: piece.text }));
  }
  async request_permission(
    params: RequestPermissionRequest,
    id: JsonRpcId,
  ): Promise<{ answer: Promise<RequestPermissionResponse> }> {
    const turn = this.turn;
    if (!turn) return { answer: Promise.resolve({ outcome: { outcome: 'cancelled' } }) };
    this.heard_from_agent(turn);
    const tool = params.toolCall;
    const ask_id = `${tool.toolCallId}:${++this.asks_raised}`;
    const answer = new Deferred<RequestPermissionResponse>();
    turn.parked_asks.set(ask_id, {
      answer,
      request_id: id,
      tool_call_id: tool.toolCallId,
      options: params.options,
    });
    await this.sink.report(
      turn.token,
      new PermissionAskRaised({
        ask: new BackendPermissionAsk({
          ask_id,
          title: tool.title || tool.toolCallId,
          detail: tool_call_detail(tool.content),
          options: params.options.map(
            (option) =>
              new PermissionAskOption({
                option_id: option.optionId,
                label: option.name,
                option_kind: option.kind,
              }),
          ),
        }),
      }),
    );
    return { answer: answer.promise };
  }
  /** The flavor's outcome watch, taken before the prompt is written. */
  watch_outcome(session_id: string): AcpPromptOutcomeWatch | null {
    try {
      return this.flavor.watch_prompt_outcome?.(session_id) ?? null;
    } catch (error) {
      this.log(error);
      return null;
    }
  }
  /**
   * Called before the next prompt is written. A turn still current then was ended by the core
   * without its reply, as after an unanswered cancel. It is abandoned, so its clock cannot fail
   * the next turn, and its open tool calls are finished as failed, since none will finish later.
   */
  async abandon_current(): Promise<void> {
    const turn = this.turn;
    if (!turn) return;
    this.turn = null;
    this.abandon(turn);
    try {
      await this.finish_open_tool_calls(turn, ToolCallStatus.failed);
    } catch (error) {
      this.log(error);
    }
  }
  /** The written prompt's turn. */
  begin(
    token: TurnToken,
    request: AcpRequest<PromptResponse>,
    outcome_watch: AcpPromptOutcomeWatch | null,
  ): AcpTurn {
    const turn: AcpTurn = {
      token,
      request,
      prompt: request.response,
      ending_reporter: null,
      pieces: [],
      message_id: null,
      parked_asks: new Map(),
      open_tool_calls: new Set(),
      silence: new Deferred<string>(),
      silence_timer: null,
      limits_held_by_tool_calls: 0,
      outcome_watch,
    };
    this.turn = turn;
    this.heard_from_agent(turn);
    return turn;
  }
  /**
   * A permission answer reached the agent: it is working again. An allowed tool runs until the
   * agent reports it finished; a refused one never starts.
   */
  answered(turn: AcpTurn, ask_id: string, option_id: string): void {
    const parked = turn.parked_asks.get(ask_id);
    turn.parked_asks.delete(ask_id);
    // The turn may have ended while the answer was on its way.
    if (this.turn !== turn) return;
    const kind = parked?.options.find((option) => option.optionId === option_id)?.kind;
    if (parked && (kind === 'allow_once' || kind === 'allow_always'))
      turn.open_tool_calls.add(parked.tool_call_id);
    this.heard_from_agent(turn);
  }
  /**
   * Restart the silence clock. A turn waiting on a person is not silent. A turn waiting on a tool
   * the agent still runs is not silent for OPEN_TOOL_CALL_SILENCE_LIMITS limits, because an agent
   * can leave a tool call open for good and then hang.
   */
  private heard_from_agent(turn: AcpTurn): void {
    turn.limits_held_by_tool_calls = 0;
    this.arm_silence(turn);
  }
  private arm_silence(turn: AcpTurn): void {
    const limit = this.flavor.prompt_inactivity_timeout_ms;
    if (limit === undefined) return;
    if (turn.silence_timer !== null) clearTimeout(turn.silence_timer);
    turn.silence_timer = setTimeout(() => {
      turn.silence_timer = null;
      if (turn.parked_asks.size > 0) return this.heard_from_agent(turn);
      if (
        turn.open_tool_calls.size > 0 &&
        turn.limits_held_by_tool_calls < OPEN_TOOL_CALL_SILENCE_LIMITS
      ) {
        turn.limits_held_by_tool_calls += 1;
        return this.arm_silence(turn);
      }
      const silent_for = duration_text(limit * (turn.limits_held_by_tool_calls + 1));
      turn.silence.resolve(
        `${this.flavor.label} sent nothing for ${silent_for}, so Panels ended this turn. The next message starts a fresh ${this.flavor.label}.`,
      );
    }, limit);
  }
  /** The failure text a prompt error carries: the flavor's signed-out text, or the agent's. */
  private failure_text(error: unknown): string {
    return this.flavor.requires_sign_in?.(error) ?? acp_error_text(error);
  }
  async report_ending(turn: AcpTurn, facts: AcpEndingFacts): Promise<void> {
    let ending: ConversationTurnEnding = ConversationTurnEnding.completed;
    let error_summary: string | null = null;
    let response: PromptResponse | null = null;
    let silent = false;
    const heard = await Promise.race([
      turn.prompt.then(
        (value) => ({ response: value }),
        (error: unknown) => ({ error }),
      ),
      turn.silence.promise.then((text) => ({ silence: text })),
    ]);
    if (turn.silence_timer !== null) clearTimeout(turn.silence_timer);
    turn.silence_timer = null;
    if ('silence' in heard) {
      // The agent may still be working. Its state is unknown, so this child takes no more prompts.
      silent = true;
      ending = ConversationTurnEnding.failed;
      error_summary = normalized_failure_summary(heard.silence);
      // Only the current turn speaks for the child. An abandoned turn's clock is already stopped.
      if (this.turn === turn) facts.break_wire(new Error(heard.silence));
      turn.request.cancel(new Error(heard.silence));
      void turn.prompt.catch(() => {});
    } else if ('error' in heard) {
      ending = ConversationTurnEnding.failed;
      error_summary = normalized_failure_summary(this.failure_text(heard.error));
    } else response = heard.response;
    // An abandoned turn's ending is no longer the core's: it reports nothing more.
    if (this.turn !== turn) return;
    if (response) {
      if (response.stopReason === 'cancelled') ending = ConversationTurnEnding.interrupted;
      const usage = response.usage;
      if (usage)
        await this.sink.report(
          turn.token,
          new TokenUsageReported({
            input_tokens: usage.inputTokens,
            output_tokens: usage.outputTokens,
            cached_input_tokens: usage.cachedReadTokens ?? null,
            cost_usd: null,
          }),
        );
      if (response.stopReason === 'end_turn' && turn.outcome_watch) {
        let failure: string | null = null;
        try {
          failure = await turn.outcome_watch.failure();
        } catch (error) {
          this.log(error);
        }
        if (failure !== null) {
          ending = ConversationTurnEnding.failed;
          error_summary = normalized_failure_summary(failure);
        }
      }
    }
    if (this.turn !== turn) return;
    this.turn = null;
    await this.flush(turn);
    await this.finish_open_tool_calls(
      turn,
      ending === ConversationTurnEnding.completed
        ? ToolCallStatus.completed
        : ToolCallStatus.failed,
    );
    this.settle_asks(turn);
    await this.sink.report(
      turn.token,
      new TurnEnded({
        ending,
        error_summary,
        standard_error_tail:
          ending === ConversationTurnEnding.failed && !silent ? facts.standard_error_tail() : null,
        child_disposition: silent || facts.wire_ended() ? 'broken' : 'reusable',
      }),
    );
  }
  /** Panels never ends a tool call by itself, so one still open when its turn ends is ended here. */
  private async finish_open_tool_calls(turn: AcpTurn, status: ToolCallStatus): Promise<void> {
    const open = [...turn.open_tool_calls];
    turn.open_tool_calls.clear();
    for (const tool_call_id of open)
      await this.sink.report(
        turn.token,
        new ToolCallFinished({ tool_call_id, tool_call_status: status }),
      );
  }
  private async flush(turn: AcpTurn): Promise<void> {
    if (!turn.pieces.length) return;
    const content = joined_runs_of_text(turn.pieces);
    turn.pieces = [];
    turn.message_id = null;
    await this.sink.report(turn.token, new AgentMessageCompleted({ content }));
  }
  settle_asks(turn: AcpTurn): void {
    for (const parked of turn.parked_asks.values())
      parked.answer.resolve({ outcome: { outcome: 'cancelled' } });
    turn.parked_asks.clear();
  }
  /** Stop the silence clock of a turn that ends without its reporter, as at shutdown. */
  abandon(turn: AcpTurn): void {
    if (turn.silence_timer !== null) clearTimeout(turn.silence_timer);
    turn.silence_timer = null;
    this.settle_asks(turn);
  }
}
/** "90 seconds", "1 minute", "15 minutes": a limit as a person reads it. */
function duration_text(ms: number): string {
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds} ${seconds === 1 ? 'second' : 'seconds'}`;
  const minutes = Math.round(seconds / 60);
  return `${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`;
}
