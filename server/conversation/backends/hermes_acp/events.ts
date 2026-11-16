/** Hermes session news translated to the existing backend event vocabulary. */
import type {
  AvailableCommand,
  ContentBlock,
  JsonRpcId,
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
  ContextCompacted,
  ModelThinkingHappened,
  PermissionAskRaised,
  PlanUpdated,
  TokenUsageReported,
  ToolCallFinished,
  ToolCallProgress,
  ToolCallStarted,
  TurnEnded,
} from '../contracts.ts';
import type { BackendEventSink, TurnToken } from '../contracts.ts';
import { ComposerCatalogEntry, ComposerCatalogEntryKind } from '../../contracts.ts';
import {
  ConversationTurnEnding,
  PermissionAskOption,
  PlanEntry,
  ToolCallStatus,
} from '../../events.ts';
import { joined_runs_of_text, MessageText } from '../../message_content.ts';
import type { MessageContent, MessagePiece } from '../../message_content.ts';
import { normalized_failure_summary } from '../claude_agent_sdk/failure_summary.ts';
import { hermes_error_text } from './configuration.ts';
import type { HermesMessages } from './messages.ts';

export class Deferred<T> {
  readonly promise: Promise<T>;
  settled = false;
  private resolve_value!: (value: T) => void;
  constructor() {
    this.promise = new Promise<T>((resolve) => {
      this.resolve_value = resolve;
    });
  }
  resolve(value: T): void {
    if (!this.settled) {
      this.settled = true;
      this.resolve_value(value);
    }
  }
}
export interface ParkedPermissionAsk {
  readonly answer: Deferred<RequestPermissionResponse>;
  readonly request_id: JsonRpcId;
}
export interface HermesTurn {
  readonly token: TurnToken;
  readonly prompt: Promise<PromptResponse>;
  ending_reporter: Promise<void> | null;
  readonly automatic_compaction: boolean;
  pieces: MessagePiece[];
  message_id: string | null;
  readonly completed_messages: MessageContent[];
  readonly parked_asks: Map<string, ParkedPermissionAsk>;
}
export function tool_call_detail(content?: readonly ToolCallContent[] | null): string | null {
  const texts = (content ?? []).flatMap((item) =>
    item.type === 'content' && item.content.type === 'text' && item.content.text
      ? [item.content.text]
      : [],
  );
  return texts.length ? texts.join('\n') : null;
}
function names_compaction(metadata: Record<string, unknown> | null | undefined): boolean {
  const hermes = metadata?.hermes;
  if (!hermes || typeof hermes !== 'object' || !('sessionProvenance' in hermes)) return false;
  const provenance = hermes.sessionProvenance;
  return (
    !!provenance &&
    typeof provenance === 'object' &&
    'reason' in provenance &&
    provenance.reason === 'compression'
  );
}
const count = '(?:0|[1-9][0-9]{0,2}|[1-9][0-9]{0,2}(?:,[0-9]{3})+)';
const compaction = new RegExp(
  `^Context compressed: (0|[1-9][0-9]*) -> (0|[1-9][0-9]*) messages\\n~${count} -> ~${count} tokens$`,
);
function confirms_compaction(messages: readonly MessageContent[]): boolean {
  if (messages.length !== 1 || messages[0].length !== 1 || !(messages[0][0] instanceof MessageText))
    return false;
  const text = messages[0][0].text;
  const match = compaction.exec(text);
  if (!match || match[0] !== text || match[1].length > 4300 || match[2].length > 4300) return false;
  return BigInt(match[2]) < BigInt(match[1]);
}

export class HermesEvents {
  turn: HermesTurn | null = null;
  available_commands: readonly AvailableCommand[] | null = null;
  readonly first_commands = new Deferred<void>();
  private asks_raised = 0;
  private readonly sink: BackendEventSink;
  private readonly messages: HermesMessages;
  constructor(sink: BackendEventSink, messages: HermesMessages) {
    this.sink = sink;
    this.messages = messages;
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
      case 'tool_call':
        await this.sink.report(
          turn.token,
          new ToolCallStarted({
            tool_call_id: update.toolCallId,
            title: update.title,
            tool_kind: update.kind ?? 'other',
            detail: tool_call_detail(update.content),
          }),
        );
        break;
      case 'tool_call_update': {
        const detail = tool_call_detail(update.content);
        const status = update.status;
        if (status === 'completed' || status === 'failed')
          await this.sink.report(
            turn.token,
            new ToolCallFinished({
              tool_call_id: update.toolCallId,
              tool_call_status: ToolCallStatus[status],
              detail,
            }),
          );
        else if (detail !== null)
          await this.sink.report(
            turn.token,
            new ToolCallProgress({ tool_call_id: update.toolCallId, detail }),
          );
        break;
      }
      case 'usage_update':
        if (update.cost && update.cost.currency.toUpperCase() === 'USD')
          await this.sink.report(
            turn.token,
            new TokenUsageReported({ cost_usd: update.cost.amount }),
          );
        break;
      case 'session_info_update':
        if (!turn.automatic_compaction && names_compaction(update._meta))
          await this.sink.report(turn.token, new ContextCompacted());
        break;
    }
  }
  private async message_chunk(
    turn: HermesTurn,
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
    const tool = params.toolCall;
    const ask_id = `${tool.toolCallId}:${++this.asks_raised}`;
    const answer = new Deferred<RequestPermissionResponse>();
    turn.parked_asks.set(ask_id, { answer, request_id: id });
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
  begin(
    token: TurnToken,
    prompt: Promise<PromptResponse>,
    automatic_compaction: boolean,
  ): HermesTurn {
    const turn: HermesTurn = {
      token,
      prompt,
      ending_reporter: null,
      automatic_compaction,
      pieces: [],
      message_id: null,
      completed_messages: [],
      parked_asks: new Map(),
    };
    this.turn = turn;
    return turn;
  }
  async report_ending(turn: HermesTurn, standard_error_tail: () => string | null): Promise<void> {
    let ending: ConversationTurnEnding = ConversationTurnEnding.completed;
    let error_summary: string | null = null;
    let response: PromptResponse | null = null;
    try {
      response = await turn.prompt;
    } catch (error) {
      ending = ConversationTurnEnding.failed;
      error_summary = normalized_failure_summary(hermes_error_text(error));
    }
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
    }
    if (this.turn !== turn) return;
    this.turn = null;
    await this.flush(turn);
    if (
      ending === ConversationTurnEnding.completed &&
      turn.automatic_compaction &&
      confirms_compaction(turn.completed_messages)
    )
      await this.sink.report(turn.token, new ContextCompacted());
    this.settle_asks(turn);
    await this.sink.report(
      turn.token,
      new TurnEnded({
        ending,
        error_summary,
        standard_error_tail:
          ending === ConversationTurnEnding.failed ? standard_error_tail() : null,
      }),
    );
  }
  private async flush(turn: HermesTurn): Promise<void> {
    if (!turn.pieces.length) return;
    const content = joined_runs_of_text(turn.pieces);
    turn.pieces = [];
    turn.message_id = null;
    if (turn.automatic_compaction) turn.completed_messages.push(content);
    await this.sink.report(turn.token, new AgentMessageCompleted({ content }));
  }
  settle_asks(turn: HermesTurn): void {
    for (const parked of turn.parked_asks.values())
      parked.answer.resolve({ outcome: { outcome: 'cancelled' } });
    turn.parked_asks.clear();
  }
}
