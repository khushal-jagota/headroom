/** Deterministic reference system. Its observations are not durable event records. */
import { isDeepStrictEqual } from 'node:util';
import type { Principal } from '../core/contracts.ts';
import {
  ConversationAlreadyStarted,
  HeldPrompt,
  PromptDeliveryInjected,
  PromptDeliveryQueued,
  PromptDeliveryRefused,
  PromptDeliveryStarted,
  PromptDeliverySuppressed,
  PromptDeliveryUncertain,
  backend_supports_steer,
} from './contracts.ts';
import type {
  AnswerFate,
  ConversationStartRequest,
  ConversationSystem,
  DeploymentRecoveryReference,
  HeldPromptPromotionFate,
  HeldPromptPromotionMode,
  PromptDeliveryFate,
  PromptDeliveryMode,
  PromptDeliveryRefusalReason,
  PromptQueueReason,
  ResolvedConversationStart,
} from './contracts.ts';
import type { UserInputAnswer, UserInputQuestion } from './events.ts';
import {
  MessageText,
  message_content_text,
  prefix_message_content_text,
  require_message_content,
  sender_labeled_composed_message_content,
} from './message_content.ts';
import type { MessageContent } from './message_content.ts';
import { WorkspaceAdmission } from './workspace_admission.ts';
import { resolve_conversation_start_request } from './logic/conversation_start_resolution.ts';
import { leading_run_that_can_share_a_turn, one_prompt_from } from './logic/held_line.ts';

type SendOptions = Parameters<ConversationSystem['send']>[2];
type OwnerMessageOptions = Parameters<ConversationSystem['record_message_to_owner']>[2];

export const InMemoryConversationObservationKind = {
  prompt_delivered: 'prompt_delivered',
  prompt_delivery_refused: 'prompt_delivery_refused',
  prompt_delivery_uncertain: 'prompt_delivery_uncertain',
  prompt_discarded: 'prompt_discarded',
  turn_ended: 'turn_ended',
  permission_asked: 'permission_asked',
  permission_answered: 'permission_answered',
  user_input_requested: 'user_input_requested',
  user_input_answered: 'user_input_answered',
  model_changed: 'model_changed',
  message_to_owner: 'message_to_owner',
} as const;
export type InMemoryConversationObservationKind =
  (typeof InMemoryConversationObservationKind)[keyof typeof InMemoryConversationObservationKind];
export const InMemoryConversationTurnEnding = {
  completed: 'completed',
  failed: 'failed',
  interrupted: 'interrupted',
} as const;
export type InMemoryConversationTurnEnding =
  (typeof InMemoryConversationTurnEnding)[keyof typeof InMemoryConversationTurnEnding];

export class InMemoryConversationObservation {
  readonly kind: InMemoryConversationObservationKind;
  readonly content: MessageContent | null = null;
  readonly sender_label: string | null = null;
  readonly mode: PromptDeliveryMode | null = null;
  readonly turn_ending: InMemoryConversationTurnEnding | null = null;
  readonly refusal_reason: PromptDeliveryRefusalReason | null = null;
  readonly permission_ask_id: string | null = null;
  readonly user_input_request_id: string | null = null;
  readonly user_input_questions: readonly UserInputQuestion[] | null = null;
  readonly user_input_answers: readonly UserInputAnswer[] | null = null;
  readonly model: string | null = null;
  readonly reasoning_effort: string | null = null;
  readonly sender_message_id: string | null = null;
  readonly sent_at_unix_milliseconds: number | null = null;
  readonly sender: Principal | null = null;
  readonly recipient: Principal | null = null;
  constructor(
    options: { kind: InMemoryConversationObservationKind } & Partial<
      Omit<InMemoryConversationObservation, 'text'>
    >,
  ) {
    this.kind = options.kind;
    Object.assign(this, options);
  }
  get text(): string | null {
    return this.content === null ? null : message_content_text(this.content);
  }
}
export class InMemoryBackendPromptWrite {
  readonly content: MessageContent;
  readonly sender_label: string;
  readonly mode: PromptDeliveryMode;
  constructor(options: {
    content: MessageContent;
    sender_label: string;
    mode: PromptDeliveryMode;
  }) {
    this.content = options.content;
    this.sender_label = options.sender_label;
    this.mode = options.mode;
  }
  get text(): string {
    return message_content_text(this.content);
  }
}
export class TurnCannotEndWhilePermissionAskIsPending extends Error {}
export class TurnCannotEndWhileUserInputIsPending extends Error {}

interface BackendSession {
  prompt_writes: InMemoryBackendPromptWrite[];
  pending_permission_answers: Map<string, string | null>;
  pending_user_input_answers: Map<string, readonly UserInputAnswer[] | null>;
  cancellations: number;
  model: string | null;
  reasoning_effort: string | null;
}
interface RunningTurn {
  turn_number: number;
  pending_permission_ask_ids: Set<string>;
  pending_user_input_questions: Map<string, readonly UserInputQuestion[]>;
}
interface HeldMessage {
  held_prompt_id: string;
  content: MessageContent;
  sender_label: string;
  model_change: string | null;
  reasoning_effort_change: string | null;
  sender_message_id: string | null;
  sent_at_unix_milliseconds: number | null;
  sender: Principal | null;
  recipient: Principal | null;
  snapshot_sent_at_unix_milliseconds: number;
  queue_reason: PromptQueueReason;
  recovery_references: readonly DeploymentRecoveryReference[];
  reasoning_effort_is_set: boolean;
}
interface ConversationState {
  resolved_start: ResolvedConversationStart;
  current_model: string | null;
  current_reasoning_effort: string | null;
  backend_session: BackendSession | null;
  running_turn: RunningTurn | null;
  held_prompts: HeldMessage[];
  held_prompts_created: number;
  observations: InMemoryConversationObservation[];
  permission_asks_raised: number;
  user_input_requests_raised: number;
  next_turn_number: number;
  armed_backend_start_failure: boolean;
  armed_session_load_failure: boolean;
  armed_backend_write_failure: boolean;
  retirement_proven: boolean;
  has_delivered_prompt: boolean;
}
interface DeliveryOptions {
  model_change?: string | null;
  reasoning_effort_change?: string | null;
  sender_message_id?: string | null;
  sent_at_unix_milliseconds?: number | null;
  sender?: Principal | null;
  recipient?: Principal | null;
  recovery_references?: readonly DeploymentRecoveryReference[];
  recorded_messages?: readonly HeldMessage[];
  reasoning_effort_is_set?: boolean;
}
function messageIdentity(value: {
  content: MessageContent | null;
  sender_label: string | null;
  sender: Principal | null;
  recipient: Principal | null;
}): unknown[] {
  return [value.content, value.sender_label, value.sender, value.recipient];
}
function changedMessage(): never {
  throw new Error('sender_message_id already names a different message');
}

export class InMemoryConversationSystem implements ConversationSystem {
  private readonly _conversations = new Map<string, ConversationState>();
  readonly workspace_admission = new WorkspaceAdmission();

  workspace_activity(): 'idle' | 'busy' | 'unknown' {
    if (this.workspace_admission.closed) return 'unknown';
    if (
      this.workspace_admission.active ||
      [...this._conversations.values()].some(
        (state) => state.running_turn !== null || state.held_prompts.length > 0,
      )
    )
      return 'busy';
    return 'idle';
  }
  async run_with_workspace_admission(
    operation: () => void,
    options: { require_idle: boolean },
  ): Promise<boolean> {
    return this.workspace_admission.claim(operation, {
      idle: () => this.workspace_activity() === 'idle',
      require_idle: options.require_idle,
    });
  }
  async start_conversation(request: ConversationStartRequest): Promise<void> {
    if (this.workspace_admission.closed)
      throw new Error('Workspace admission is closed for an owner update.');
    const resolved = resolve_conversation_start_request(request);
    if (this._conversations.has(resolved.conversation_id))
      throw new ConversationAlreadyStarted(resolved.conversation_id);
    this._conversations.set(resolved.conversation_id, {
      resolved_start: resolved,
      current_model: resolved.model,
      current_reasoning_effort: resolved.reasoning_effort,
      backend_session: null,
      running_turn: null,
      held_prompts: [],
      held_prompts_created: 0,
      observations: [],
      permission_asks_raised: 0,
      user_input_requests_raised: 0,
      next_turn_number: 1,
      armed_backend_start_failure: false,
      armed_session_load_failure: false,
      armed_backend_write_failure: false,
      retirement_proven: false,
      has_delivered_prompt: false,
    });
  }
  async send(
    conversation_id: string,
    content: MessageContent,
    options: SendOptions,
  ): Promise<PromptDeliveryFate> {
    if (this.workspace_admission.closed)
      throw new Error('Workspace admission is closed for an owner update.');
    require_message_content(content);
    const {
      sender_label,
      mode = 'queue',
      sender_message_id = null,
      sender = null,
      recipient = null,
      model_change = null,
      reasoning_effort_change = null,
      reasoning_effort_is_set = false,
    } = options;
    if (options.record_refusal && sender_message_id === null)
      throw new Error('record_refusal requires a sender_message_id');
    const state = this._conversations.get(conversation_id);
    if (!state) return new PromptDeliveryRefused({ refusal_reason: 'no_such_conversation' });
    if (sender_message_id !== null) {
      for (const [position, held] of state.held_prompts.entries()) {
        if (held.sender_message_id !== sender_message_id) continue;
        if (
          !isDeepStrictEqual(
            [
              ...messageIdentity(held),
              held.model_change,
              held.reasoning_effort_change,
              held.reasoning_effort_is_set,
            ],
            [
              ...messageIdentity({ content, sender_label, sender, recipient }),
              model_change,
              reasoning_effort_change,
              reasoning_effort_is_set,
            ],
          )
        )
          changedMessage();
        return new PromptDeliveryQueued({ queue_position: position + 1 });
      }
      for (const observed of [...state.observations].reverse()) {
        if (observed.sender_message_id !== sender_message_id) continue;
        if (
          !isDeepStrictEqual(
            messageIdentity(observed),
            messageIdentity({ content, sender_label, sender, recipient }),
          )
        )
          changedMessage();
        if (observed.kind === 'prompt_delivered')
          return observed.mode === 'steer'
            ? new PromptDeliveryInjected()
            : new PromptDeliveryStarted();
        if (observed.kind === 'prompt_delivery_refused')
          return new PromptDeliveryRefused({ refusal_reason: observed.refusal_reason! });
        if (observed.kind === 'prompt_delivery_uncertain') return new PromptDeliveryUncertain();
        if (observed.kind === 'prompt_discarded')
          return new PromptDeliverySuppressed({ reason: 'discarded' });
        changedMessage();
      }
    }
    if (state.running_turn === null)
      return this._start_turn(
        state,
        content,
        sender_label,
        mode === 'steer' ? 'queue' : mode,
        options,
      );
    if (mode === 'queue')
      return this._hold_prompt(state, content, sender_label, 'requested', options);
    if (mode === 'steer') {
      let queue_reason: PromptQueueReason;
      if (content.some((piece) => !(piece instanceof MessageText))) queue_reason = 'attachment';
      else if (model_change !== null || reasoning_effort_change !== null || reasoning_effort_is_set)
        queue_reason = 'run_change';
      else {
        const fate = this._steer(state, content, sender_label, options);
        if (fate instanceof PromptDeliveryInjected) return fate;
        queue_reason = 'steer_refused';
      }
      return this._hold_prompt(state, content, sender_label, queue_reason, options);
    }
    this._end_running_turn(state, 'interrupted');
    const fate = this._start_turn(state, content, sender_label, mode, options);
    if (fate instanceof PromptDeliveryRefused) this._drain(state);
    return fate;
  }
  private _hold_prompt(
    state: ConversationState,
    content: MessageContent,
    sender_label: string,
    queue_reason: PromptQueueReason,
    options: DeliveryOptions,
  ): PromptDeliveryQueued {
    state.held_prompts_created += 1;
    state.held_prompts.push({
      held_prompt_id: `held-${state.held_prompts_created}`,
      content,
      sender_label,
      model_change: options.model_change ?? null,
      reasoning_effort_change: options.reasoning_effort_change ?? null,
      sender_message_id: options.sender_message_id ?? null,
      sent_at_unix_milliseconds: options.sent_at_unix_milliseconds ?? null,
      sender: options.sender ?? null,
      recipient: options.recipient ?? null,
      snapshot_sent_at_unix_milliseconds:
        options.sent_at_unix_milliseconds ?? state.held_prompts_created,
      queue_reason,
      recovery_references: options.recovery_references ?? [],
      reasoning_effort_is_set: options.reasoning_effort_is_set ?? false,
    });
    return new PromptDeliveryQueued({ queue_position: state.held_prompts.length });
  }
  async record_message_to_owner(
    conversation_id: string,
    content: MessageContent,
    options: OwnerMessageOptions,
  ): Promise<void> {
    require_message_content(content);
    const state = this._conversations.get(conversation_id);
    if (!state) throw new Error('no such conversation');
    const sender_message_id = options.sender_message_id ?? null;
    if (sender_message_id !== null) {
      for (const observed of state.observations) {
        if (observed.sender_message_id !== sender_message_id) continue;
        if (
          observed.kind !== 'message_to_owner' ||
          !isDeepStrictEqual(
            [observed.content, observed.sender, observed.recipient],
            [content, options.sender, options.recipient],
          )
        )
          changedMessage();
        return;
      }
    }
    state.observations.push(
      new InMemoryConversationObservation({
        kind: 'message_to_owner',
        content,
        sender_label: options.sender_label,
        sender_message_id,
        sent_at_unix_milliseconds: options.sent_at_unix_milliseconds ?? null,
        sender: options.sender,
        recipient: options.recipient,
      }),
    );
  }
  async interrupt(conversation_id: string): Promise<void> {
    const state = this._conversations.get(conversation_id);
    if (!state || state.running_turn === null) return;
    this._end_running_turn(state, 'interrupted');
    this._drain(state);
  }
  async held_prompts(conversation_id: string): Promise<readonly HeldPrompt[]> {
    const state = this._conversations.get(conversation_id);
    if (!state) return [];
    return state.held_prompts.map(
      (held) =>
        new HeldPrompt({
          held_prompt_id: held.held_prompt_id,
          content: held.content,
          sender_label: held.sender_label,
          sender_message_id: held.sender_message_id,
          sent_at_unix_milliseconds: held.snapshot_sent_at_unix_milliseconds,
          sender: held.sender,
          recipient: held.recipient,
          queue_reason: held.queue_reason,
          delivery_uncertain: false,
        }),
    );
  }
  async promote_held_prompt(
    conversation_id: string,
    held_prompt_id: string,
    mode: HeldPromptPromotionMode,
  ): Promise<HeldPromptPromotionFate | null> {
    const state = this._conversations.get(conversation_id);
    if (!state) return null;
    const position = state.held_prompts.findIndex((held) => held.held_prompt_id === held_prompt_id);
    if (position === -1) return null;
    const held = state.held_prompts[position];
    if (
      mode === 'steer' &&
      (held.model_change !== null ||
        held.reasoning_effort_change !== null ||
        held.reasoning_effort_is_set)
    )
      return new PromptDeliveryQueued({ queue_position: position + 1 });
    state.held_prompts.splice(position, 1);
    let fate: HeldPromptPromotionFate;
    if (mode === 'send_now') {
      if (state.running_turn !== null) this._end_running_turn(state, 'interrupted');
      // Python forwards the held record, not reasoning_effort_is_set directly.
      fate = this._start_turn(state, held.content, held.sender_label, 'send_now', {
        ...held,
        reasoning_effort_is_set: false,
        recorded_messages: [held],
        recovery_references: [],
      });
    } else
      fate = this._steer(state, held.content, held.sender_label, {
        sender_message_id: held.sender_message_id,
        sent_at_unix_milliseconds: held.sent_at_unix_milliseconds,
        sender: held.sender,
        recipient: held.recipient,
      });
    if (fate instanceof PromptDeliveryRefused) {
      state.observations.push(
        new InMemoryConversationObservation({
          kind: 'prompt_delivery_refused',
          content: held.content,
          sender_label: held.sender_label,
          mode: mode === 'send_now' ? 'send_now' : 'steer',
          refusal_reason: fate.refusal_reason,
          sender_message_id: held.sender_message_id,
          sent_at_unix_milliseconds: held.sent_at_unix_milliseconds,
          sender: held.sender,
          recipient: held.recipient,
        }),
      );
      this._drain(state);
    }
    return fate;
  }
  async discard_held_prompt(conversation_id: string, held_prompt_id: string): Promise<boolean> {
    const state = this._conversations.get(conversation_id);
    if (!state) return false;
    const position = state.held_prompts.findIndex((held) => held.held_prompt_id === held_prompt_id);
    if (position === -1) return false;
    const [held] = state.held_prompts.splice(position, 1);
    this._observe_message(state, 'prompt_discarded', held);
    return true;
  }
  async retirement_is_confirmed(conversation_id: string): Promise<boolean> {
    const state = this._conversations.get(conversation_id);
    return state !== undefined && (state.retirement_proven || state.backend_session === null);
  }
  async kill(conversation_id: string): Promise<void> {
    const state = this._conversations.get(conversation_id);
    if (!state) return;
    for (const held of state.held_prompts.splice(0))
      this._observe_message(state, 'prompt_discarded', held);
    if (state.running_turn !== null) this._end_running_turn(state, 'interrupted');
    state.retirement_proven = true;
  }
  async is_running(conversation_id: string): Promise<boolean> {
    return this._conversations.get(conversation_id)?.running_turn != null;
  }
  async has_pending_permission_ask(conversation_id: string): Promise<boolean> {
    return (
      (this._conversations.get(conversation_id)?.running_turn?.pending_permission_ask_ids.size ??
        0) > 0
    );
  }
  async has_pending_user_input(conversation_id: string): Promise<boolean> {
    return (
      (this._conversations.get(conversation_id)?.running_turn?.pending_user_input_questions.size ??
        0) > 0
    );
  }

  complete_running_turn(conversation_id: string): void {
    const state = this._state(conversation_id);
    if (state.running_turn === null) return;
    if (state.running_turn.pending_permission_ask_ids.size)
      throw new TurnCannotEndWhilePermissionAskIsPending(conversation_id);
    if (state.running_turn.pending_user_input_questions.size)
      throw new TurnCannotEndWhileUserInputIsPending(conversation_id);
    this._end_running_turn(state, 'completed');
    this._drain(state);
  }
  fail_running_turn(conversation_id: string): void {
    const state = this._state(conversation_id);
    if (state.running_turn === null) return;
    this._end_running_turn(state, 'failed');
    this._drain(state);
  }
  raise_permission_ask(conversation_id: string): string {
    const state = this._state(conversation_id);
    if (state.running_turn === null || state.backend_session === null)
      throw new Error(`conversation ${conversation_id} has no running turn`);
    state.permission_asks_raised += 1;
    const ask_id = `ask-${state.permission_asks_raised}`;
    state.running_turn.pending_permission_ask_ids.add(ask_id);
    state.backend_session.pending_permission_answers.set(ask_id, null);
    state.observations.push(
      new InMemoryConversationObservation({ kind: 'permission_asked', permission_ask_id: ask_id }),
    );
    return ask_id;
  }
  answer_permission_ask(conversation_id: string, ask_id: string, option_id: string): AnswerFate {
    const state = this._conversations.get(conversation_id);
    if (
      !state?.running_turn ||
      !state.backend_session ||
      !state.running_turn.pending_permission_ask_ids.has(ask_id)
    )
      return 'refused';
    state.running_turn.pending_permission_ask_ids.delete(ask_id);
    state.backend_session.pending_permission_answers.set(ask_id, option_id);
    state.observations.push(
      new InMemoryConversationObservation({
        kind: 'permission_answered',
        permission_ask_id: ask_id,
      }),
    );
    return 'applied';
  }
  raise_user_input(conversation_id: string, questions: readonly UserInputQuestion[]): string {
    const state = this._state(conversation_id);
    if (!state.running_turn || !state.backend_session)
      throw new Error(`conversation ${conversation_id} has no running turn`);
    state.user_input_requests_raised += 1;
    const request_id = `user-input-${state.user_input_requests_raised}`;
    state.running_turn.pending_user_input_questions.set(request_id, questions);
    state.backend_session.pending_user_input_answers.set(request_id, null);
    state.observations.push(
      new InMemoryConversationObservation({
        kind: 'user_input_requested',
        user_input_request_id: request_id,
        user_input_questions: questions,
      }),
    );
    return request_id;
  }
  answer_user_input(
    conversation_id: string,
    request_id: string,
    answers: readonly UserInputAnswer[],
  ): AnswerFate {
    const state = this._conversations.get(conversation_id);
    if (!state?.running_turn || !state.backend_session) return 'refused';
    const questions = state.running_turn.pending_user_input_questions.get(request_id);
    const by_id = new Map(answers.map((answer) => [answer.question_id, answer]));
    if (
      !questions ||
      by_id.size !== answers.length ||
      !isDeepStrictEqual(
        new Set(by_id.keys()),
        new Set(questions.map((question) => question.question_id)),
      )
    )
      return 'refused';
    const ordered = questions.map((question) => by_id.get(question.question_id)!);
    if (
      questions.some((question, index) => {
        const answer = ordered[index];
        return (
          answer.answers.length === 0 ||
          answer.answers.some((value) => !value) ||
          (!question.multi_select && answer.answers.length !== 1) ||
          (!question.allow_other &&
            answer.answers.some(
              (value) => !question.options.some((option) => option.label === value),
            ))
        );
      })
    )
      return 'refused';
    state.running_turn.pending_user_input_questions.delete(request_id);
    state.backend_session.pending_user_input_answers.set(request_id, ordered);
    state.observations.push(
      new InMemoryConversationObservation({
        kind: 'user_input_answered',
        user_input_request_id: request_id,
        user_input_answers: ordered,
      }),
    );
    return 'applied';
  }
  arm_backend_start_failure(conversation_id: string): void {
    this._state(conversation_id).armed_backend_start_failure = true;
  }
  arm_session_load_failure(conversation_id: string): void {
    this._state(conversation_id).armed_session_load_failure = true;
  }
  arm_backend_write_failure(conversation_id: string): void {
    this._state(conversation_id).armed_backend_write_failure = true;
  }
  backend_prompt_writes(conversation_id: string): readonly InMemoryBackendPromptWrite[] {
    return [...(this._conversations.get(conversation_id)?.backend_session?.prompt_writes ?? [])];
  }
  backend_permission_answer(conversation_id: string, ask_id: string): string | null {
    return (
      this._conversations
        .get(conversation_id)
        ?.backend_session?.pending_permission_answers.get(ask_id) ?? null
    );
  }
  backend_user_input_answers(
    conversation_id: string,
    request_id: string,
  ): readonly UserInputAnswer[] | null {
    return (
      this._conversations
        .get(conversation_id)
        ?.backend_session?.pending_user_input_answers.get(request_id) ?? null
    );
  }
  backend_cancellations(conversation_id: string): number {
    return this._conversations.get(conversation_id)?.backend_session?.cancellations ?? 0;
  }
  backend_model(conversation_id: string): string | null {
    return this._conversations.get(conversation_id)?.backend_session?.model ?? null;
  }
  backend_reasoning_effort(conversation_id: string): string | null {
    return this._conversations.get(conversation_id)?.backend_session?.reasoning_effort ?? null;
  }
  observations(conversation_id: string): readonly InMemoryConversationObservation[] {
    return [...(this._conversations.get(conversation_id)?.observations ?? [])];
  }

  private _state(conversation_id: string): ConversationState {
    const state = this._conversations.get(conversation_id);
    if (!state) throw new Error(conversation_id);
    return state;
  }
  private _establish_backend_session(
    state: ConversationState,
  ): BackendSession | PromptDeliveryRefusalReason {
    state.retirement_proven = false;
    if (state.backend_session !== null) return state.backend_session;
    if (state.armed_backend_start_failure) return 'backend_did_not_start';
    if (state.armed_session_load_failure) return 'session_did_not_load';
    state.backend_session = {
      prompt_writes: [],
      pending_permission_answers: new Map(),
      pending_user_input_answers: new Map(),
      cancellations: 0,
      model: state.current_model,
      reasoning_effort: state.current_reasoning_effort,
    };
    return state.backend_session;
  }
  private _write_to_backend(
    state: ConversationState,
    content: MessageContent,
    sender_label: string,
    mode: PromptDeliveryMode,
    options: DeliveryOptions = {},
  ): BackendSession | PromptDeliveryRefused {
    const established = this._establish_backend_session(state);
    if (typeof established === 'string')
      return new PromptDeliveryRefused({ refusal_reason: established });
    if (state.armed_backend_write_failure)
      return new PromptDeliveryRefused({ refusal_reason: 'write_to_backend_failed' });
    const model_change = options.model_change ?? null;
    const reasoning_effort_change = options.reasoning_effort_change ?? null;
    const recorded = options.recorded_messages ?? [];
    const explicit_effort =
      recorded.length > 0
        ? recorded[0].reasoning_effort_is_set
        : (options.reasoning_effort_is_set ?? false);
    if (model_change !== null || reasoning_effort_change !== null || explicit_effort) {
      if (model_change !== null) {
        state.current_model = model_change;
        established.model = model_change;
      }
      if (reasoning_effort_change !== null || explicit_effort) {
        state.current_reasoning_effort = reasoning_effort_change;
        established.reasoning_effort = reasoning_effort_change;
      }
      state.observations.push(
        new InMemoryConversationObservation({
          kind: 'model_changed',
          model: state.current_model,
          reasoning_effort: state.current_reasoning_effort,
        }),
      );
    }
    const role = state.resolved_start.role_materials;
    const composed =
      role !== null && !state.has_delivered_prompt
        ? prefix_message_content_text(content, role.role_text, '\n\n')
        : content;
    state.has_delivered_prompt = true;
    established.prompt_writes.push(
      new InMemoryBackendPromptWrite({
        content: sender_labeled_composed_message_content(composed, content, sender_label),
        sender_label,
        mode,
      }),
    );
    if (recorded.length > 0) {
      for (const message of recorded)
        this._observe_message(state, 'prompt_delivered', message, { mode });
    } else
      state.observations.push(
        new InMemoryConversationObservation({
          kind: 'prompt_delivered',
          content,
          sender_label,
          mode,
          sender_message_id: options.sender_message_id ?? null,
          sent_at_unix_milliseconds: options.sent_at_unix_milliseconds ?? null,
          sender: options.sender ?? null,
          recipient: options.recipient ?? null,
        }),
      );
    return established;
  }
  private _start_turn(
    state: ConversationState,
    content: MessageContent,
    sender_label: string,
    mode: PromptDeliveryMode,
    options: DeliveryOptions = {},
  ): PromptDeliveryStarted | PromptDeliveryRefused {
    const written = this._write_to_backend(state, content, sender_label, mode, options);
    if (written instanceof PromptDeliveryRefused) return written;
    state.running_turn = {
      turn_number: state.next_turn_number,
      pending_permission_ask_ids: new Set(),
      pending_user_input_questions: new Map(),
    };
    state.next_turn_number += 1;
    return new PromptDeliveryStarted();
  }
  private _steer(
    state: ConversationState,
    content: MessageContent,
    sender_label: string,
    options: DeliveryOptions = {},
  ): PromptDeliveryInjected | PromptDeliveryRefused {
    if (!backend_supports_steer(state.resolved_start.backend_key))
      return new PromptDeliveryRefused({ refusal_reason: 'backend_cannot_steer' });
    if (state.running_turn === null)
      return new PromptDeliveryRefused({ refusal_reason: 'no_running_turn_to_steer_into' });
    // Steering never applies model changes. The send path queues those requests.
    const written = this._write_to_backend(state, content, sender_label, 'steer', {
      sender_message_id: options.sender_message_id,
      sent_at_unix_milliseconds: options.sent_at_unix_milliseconds,
      sender: options.sender,
      recipient: options.recipient,
      recovery_references: options.recovery_references,
      reasoning_effort_is_set: options.reasoning_effort_is_set,
    });
    return written instanceof PromptDeliveryRefused ? written : new PromptDeliveryInjected();
  }
  private _end_running_turn(
    state: ConversationState,
    ending: InMemoryConversationTurnEnding,
  ): void {
    state.running_turn = null;
    if (ending === 'interrupted' && state.backend_session !== null)
      state.backend_session.cancellations += 1;
    state.observations.push(
      new InMemoryConversationObservation({ kind: 'turn_ended', turn_ending: ending }),
    );
  }
  private _observe_message(
    state: ConversationState,
    kind: InMemoryConversationObservationKind,
    message: HeldMessage,
    extra: Partial<InMemoryConversationObservation> = {},
  ): void {
    state.observations.push(
      new InMemoryConversationObservation({
        kind,
        content: message.content,
        sender_label: message.sender_label,
        sender_message_id: message.sender_message_id,
        sent_at_unix_milliseconds: message.sent_at_unix_milliseconds,
        sender: message.sender,
        recipient: message.recipient,
        ...extra,
      }),
    );
  }
  private _drain(state: ConversationState): void {
    while (state.running_turn === null && state.held_prompts.length > 0) {
      const run = leading_run_that_can_share_a_turn(state.held_prompts);
      state.held_prompts.splice(0, run.length);
      const held = run[0];
      const fate = this._start_turn(state, one_prompt_from(run), held.sender_label, 'queue', {
        ...held,
        recorded_messages: run,
        reasoning_effort_is_set: false,
        recovery_references: [],
      });
      if (fate instanceof PromptDeliveryRefused)
        for (const message of run)
          this._observe_message(state, 'prompt_delivery_refused', message, {
            mode: 'queue',
            refusal_reason: fate.refusal_reason,
          });
    }
  }
}
