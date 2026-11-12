/** Codex behind the fixed backend seam: one child, one thread, one native turn. */
import { isDeepStrictEqual } from 'node:util';
import { JsonNumber } from '../../../core/http/numeric_json.ts';
import * as B from '../contracts.ts';
import type { ResolvedConversationStart, ComposerCatalogEntry, PromptDeliveryMode } from '../../contracts.ts';
import { AnswerFate, ComposerCatalogEntryKind as K, PromptDeliveryRefusalReason as R, WorkspacePathPolicy } from '../../contracts.ts';
import { ConversationTurnEnding as E } from '../../events.ts';
import type { UserInputAnswer } from '../../events.ts';
import { ComposedMessageDoesNotContainSenderContent, MessageText, MessageImage, MessageFile, sender_labeled_composed_message_content, text_message_content } from '../../message_content.ts';
import type { MessageContent } from '../../message_content.ts';
import type { ConversationMessageFiles } from '../../message_files.ts';
import { MessageFileMissing } from '../../record/message_files.ts';
import type { CodexLoginGeneration, CodexLoginRejectionMemory } from '../../snapshot/provider_contracts.ts';
import * as P from './protocol.ts';
import { CodexAppServerClient, CodexAppServerError, CodexChildWouldNotStart, CodexRequestRejected, CodexWireFailed } from './client.ts';
import { child_environment } from './environment.ts';
import { codex_home, read_stored_codex_login, RememberedCodexLoginRejection } from './login.ts';
import { CatalogCoordinator, catalog_snapshot, EMPTY_CATALOG_SNAPSHOT } from './catalog.ts';
import type { CatalogInvocation, CatalogSnapshot } from './catalog.ts';
import { CATALOG_REQUEST_TIMEOUT_SECONDS } from './catalog_config.ts';
import { CodexAuthReading, read_a_failed_turn, error_summary_for } from './auth_failure.ts';
import { CLIENT_NAME, CLIENT_TITLE, CLIENT_VERSION, INITIALIZE_CAPABILITIES, CANCEL_SETTLING_TIMEOUT_SECONDS, ASK_WITHDRAWAL_TIMEOUT_SECONDS } from './adapter_configuration.ts';
import { TurnInFlight, bounded, same_turn } from './turn.ts';
import { plan_entries, tool_call_started, tool_call_finished, error_summary, normalized_failure_summary, goal_inspection_message } from './events.ts';
import { PERMISSION_ASK_OPTIONS, approval_answer, permission_ask, user_input_questions, user_input_answer, repr } from './asks.ts';

export interface CodexChildLaunch {
  argv: readonly string[];
  environment_overrides?: readonly (readonly [string, string])[];
}
export function codex_app_server_child_launch(options: { codex_executable: string; panels_server_url: string }): CodexChildLaunch {
  return { argv: [options.codex_executable, 'app-server'], environment_overrides: [['PLAN_SERVER_URL', options.panels_server_url]] };
}
export type CodexClient = Pick<CodexAppServerClient,
  'start' | 'stop' | 'request' | 'begin_request' | 'finish_request' | 'notify' | 'respond' | 'respond_with_error' |
  'is_alive' | 'end_reason' | 'standard_error_tail' | 'standard_error_bytes_so_far' | 'standard_error_since' | 'settle_standard_error'>;
type ClientFactory = (options: ConstructorParameters<typeof CodexAppServerClient>[0]) => CodexClient;
interface ChildOptions {
  launch: CodexChildLaunch;
  resolved_start: ResolvedConversationStart;
  event_sink: B.BackendEventSink;
  message_files: ConversationMessageFiles;
  remembered_login_rejection?: CodexLoginRejectionMemory;
  client_factory?: ClientFactory;
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
function same_generation(one: CodexLoginGeneration | null, other: CodexLoginGeneration | null): boolean {
  return one !== null && other !== null && one.account_id === other.account_id && one.last_refresh === other.last_refresh;
}
function text(error: unknown): string { return error instanceof Error ? error.message : String(error); }
function wire(value: object): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined && item !== null));
}
export class CodexAppServerBackendChild implements B.BackendChild {
  readonly stop_outside_lock = false;
  private readonly launch: CodexChildLaunch;
  private resolved_start: ResolvedConversationStart;
  private readonly sink: B.BackendEventSink;
  private readonly message_files: ConversationMessageFiles;
  private readonly rejection: CodexLoginRejectionMemory;
  private readonly home: string;
  private readonly client: CodexClient;
  private thread_id: string | null = null;
  private thread_is_ephemeral = false;
  private model: string | null;
  private reasoning_effort: string | null;
  private turn: TurnInFlight | null = null;
  private stderr_next_turn = 0;
  private asks_raised = 0;
  private catalog: CatalogSnapshot = EMPTY_CATALOG_SNAPSHOT;
  private published_catalog: readonly ComposerCatalogEntry[] | null = null;
  private catalog_publish: Promise<void> = Promise.resolve();
  private readonly coordinator: CatalogCoordinator;
  constructor(options: ChildOptions) {
    if (options.resolved_start.model === 'sol') throw new B.BackendSpawnFailed('The legacy Codex model "sol" cannot run. Choose an explicit Codex model in the model settings before you retry.');
    this.launch = options.launch;
    this.resolved_start = options.resolved_start;
    this.sink = options.event_sink;
    this.message_files = options.message_files;
    this.rejection = options.remembered_login_rejection ?? new RememberedCodexLoginRejection();
    this.home = codex_home({ ...process.env, ...Object.fromEntries(options.launch.environment_overrides ?? []) });
    this.model = options.resolved_start.model;
    this.reasoning_effort = options.resolved_start.reasoning_effort;
    this.client = (options.client_factory ?? ((config) => new CodexAppServerClient(config)))({
      description: options.resolved_start.conversation_id,
      handler: {
        on_notification: (method, notification) => this.on_notification(method, notification as P.ConsumedNotification),
        on_server_request: (method, id, params) => this.on_server_request(method, id, params as P.HandledServerRequest),
        on_child_ended: () => this.on_child_ended(),
      },
    });
    this.coordinator = new CatalogCoordinator({
      read_skills: async () => P.SkillsListResponse.read(await this.catalog_request('skills/list', { cwds: [this.resolved_start.workspace_folder] })),
      read_plugins: async () => P.PluginInstalledResponse.read(await this.catalog_request('plugin/installed', { cwds: [this.resolved_start.workspace_folder] })),
      read_installed: async () => P.AppsInstalledResponse.read(await this.catalog_request('app/installed', { threadId: this.bound_thread(), forceRefresh: true })),
      read_metadata: async (ids: string[]) => P.AppsReadResponse.read(await this.catalog_request('app/read', { appIds: ids, threadId: this.bound_thread(), includeTools: false })),
      publish: () => this.publish_catalog(),
    });
  }
  async start(resolved_start: ResolvedConversationStart, options: { vendor_session_cursor: string | null }): Promise<void> {
    if (resolved_start.workspace_path_policy === WorkspacePathPolicy.executor_default) throw new B.BackendSpawnFailed('executor workspace requires native resolution');
    this.resolved_start = resolved_start;
    this.model = resolved_start.model;
    this.reasoning_effort = resolved_start.reasoning_effort;
    const identity = resolved_start.role_materials?.identity_environment_variables ?? [];
    const environment = child_environment({ overrides: this.launch.environment_overrides, identity });
    try {
      await this.client.start({ argv: this.launch.argv, environment, working_directory: resolved_start.workspace_folder, worker_ticket_id: new Map(identity).get('PLAN_TICKET_ID') });
    } catch (error) { if (error instanceof CodexChildWouldNotStart) throw new B.BackendSpawnFailed(text(error)); throw error; }
    try {
      try {
        await this.client.request('initialize', { clientInfo: { name: CLIENT_NAME, title: CLIENT_TITLE, version: CLIENT_VERSION }, capabilities: INITIALIZE_CAPABILITIES });
        await this.client.notify('initialized');
      } catch (error) { if (error instanceof CodexAppServerError) throw new B.BackendSpawnFailed(text(error)); throw error; }
      const parameters = wire({ cwd: resolved_start.workspace_folder, model: resolved_start.model, approvalPolicy: 'never', approvalsReviewer: 'user', sandbox: 'danger-full-access', developerInstructions: resolved_start.role_materials?.role_text });
      const cursor = options.vendor_session_cursor;
      const method = cursor === null ? 'thread/start' : 'thread/resume';
      let response: P.ThreadStartResponse;
      try {
        const result = await this.client.request(method, cursor === null ? parameters : { threadId: cursor, ...parameters });
        try { response = P.ThreadStartResponse.read(result); }
        catch (error) { throw new B.SessionLoadFailed(`codex answered ${method} with something this cannot read: ${text(error)}`); }
      } catch (error) { if (error instanceof CodexAppServerError) throw new B.SessionLoadFailed(text(error)); throw error; }
      if (cursor !== null && response.thread.id !== cursor) throw new B.SessionLoadFailed(`codex was asked to resume thread ${repr(cursor)} and answered with thread ${repr(response.thread.id)}, so this conversation's memory is not what came back`);
      this.thread_id = response.thread.id;
      this.thread_is_ephemeral = response.thread.ephemeral;
      if (cursor === null) await this.sink.report(null, new B.VendorSessionCursorRebound({ vendor_session_cursor: response.thread.id }));
      await this.publish_catalog();
      this.coordinator.start();
    } catch (error) { await this.coordinator.close(); await this.client.stop(); throw error; }
  }
  async write_prompt(token: B.TurnToken, content: MessageContent, options: PromptOptions): Promise<B.BackendPromptAccepted> {
    const thread_id = this.bound_thread();
    const model = options.model_change ?? this.model;
    const effort = options.reasoning_effort_change === null && !options.reasoning_effort_is_set ? this.reasoning_effort : options.reasoning_effort_change;
    const turn = new TurnInFlight(token, this.stderr_next_turn, this.login_generation());
    this.turn = turn;
    let invocation: CatalogInvocation | null;
    try { invocation = (options.sender_message_count ?? 1) === 1 ? this.catalog_invocation(options.sender_content) : null; }
    catch (error) { this.turn = null; throw error; }
    const native = invocation?.kind === K.command;
    if (native && invocation !== null) {
      const command = invocation.name.split('_')[0]!;
      if (options.sender_content.length !== 1) { this.turn = null; throw new B.PromptWriteFailed(`/${command} cannot carry attachments`); }
      if (options.model_change !== null || options.reasoning_effort_change !== null) { this.turn = null; throw new B.PromptWriteFailed(`/${command} cannot change the model or reasoning effort`); }
      turn.kind = invocation.name === 'compact' ? 'compact' : invocation.name.startsWith('goal_') ? 'goal' : 'review';
    } else {
      try { content = sender_labeled_composed_message_content(content, options.sender_content, options.sender_label); }
      catch (error) { this.turn = null; if (error instanceof ComposedMessageDoesNotContainSenderContent) throw new B.PromptWriteFailed(text(error)); throw error; }
    }
    try {
      turn.turn_id = native ? await this.start_native(thread_id, invocation!, turn) : await this.start_turn(wire({ threadId: thread_id, input: this.turn_input(content, invocation), model, approvalPolicy: 'never', approvalsReviewer: 'user', sandboxPolicy: { type: 'dangerFullAccess' } }), turn, effort);
    } catch (error) {
      if (error instanceof B.PromptWriteFailed || error instanceof B.BackendPromptRejected) { if (this.turn === turn) this.turn = null; }
      throw error;
    }
    if (!native) { this.model = model; this.reasoning_effort = effort; }
    return new B.BackendPromptAccepted({ composed_content_delivered: !native });
  }
  async steer(token: B.TurnToken, content: MessageContent, options: { sender_content?: MessageContent | null; sender_label: string }): Promise<B.BackendSteerOutcome> {
    const turn = this.turn;
    const refused = (refusal_reason: R) => new B.BackendSteerRefused({ refusal_reason });
    if (turn === null) return refused(R.no_running_turn_to_steer_into);
    if (!same_turn(turn.token, token)) return refused(R.running_turn_changed_before_steer);
    if (turn.kind !== 'ordinary') return refused(R.running_turn_cannot_accept_steer);
    if (turn.turn_id === null) return refused(R.no_running_turn_to_steer_into);
    const native = turn.turn_id;
    const sender = options.sender_content ?? content;
    let invocation: CatalogInvocation | null;
    try { invocation = this.catalog_invocation(sender); }
    catch (error) { if (error instanceof B.PromptWriteFailed) return refused(R.message_cannot_be_steered); throw error; }
    if (invocation?.kind === K.command) return refused(R.command_cannot_join_running_turn);
    let thread: string;
    try { thread = this.bound_thread(); } catch (error) { if (error instanceof B.PromptWriteFailed) return refused(R.write_to_backend_failed); throw error; }
    let parameters: Record<string, unknown>;
    try { parameters = { threadId: thread, expectedTurnId: native, input: this.turn_input(sender_labeled_composed_message_content(content, sender, options.sender_label)) }; }
    catch (error) { if (error instanceof B.PromptWriteFailed || error instanceof P.CodexProtocolError || error instanceof ComposedMessageDoesNotContainSenderContent) return refused(R.message_cannot_be_steered); throw error; }
    let result: unknown;
    try { result = await this.client.request('turn/steer', parameters, { poison_wire_on_timeout: false }); }
    catch (error) {
      if (error instanceof CodexRequestRejected) return refused(steer_rejection_reason(error));
      if (error instanceof CodexWireFailed) return error.request_may_have_been_written ? new B.BackendSteerUncertain() : refused(R.write_to_backend_failed);
      throw error;
    }
    try { if (P.TurnSteerResponse.read(result).turnId !== native) return new B.BackendSteerUncertain(); }
    catch (error) { if (error instanceof P.CodexProtocolError) return new B.BackendSteerUncertain(); throw error; }
    return new B.BackendSteerAccepted({ composed_content_delivered: true });
  }
  async cancel_running_turn(): Promise<void> {
    const turn = this.turn;
    if (turn === null) return;
    let id = turn.interrupt_turn_id ?? turn.turn_id;
    if (id === null && turn.awaiting_start_response) {
      const started = await bounded(turn.started.promise, CANCEL_SETTLING_TIMEOUT_SECONDS);
      if (!started.completed) throw new B.PromptWriteFailed('codex start remains unknown during cancellation');
      id = started.value;
    }
    if (id === null) return;
    try { await this.client.request('turn/interrupt', { threadId: this.bound_thread(), turnId: id }); }
    catch (error) { if (error instanceof CodexAppServerError) throw new B.PromptWriteFailed(text(error)); throw error; }
    if (!(await bounded(turn.ended.promise, CANCEL_SETTLING_TIMEOUT_SECONDS)).completed) console.warn(`conversation ${this.resolved_start.conversation_id}: codex took the interrupt but never said the turn ended`);
  }
  async answer_permission_ask(ask_id: string, option_id: string, _options?: { answer_id?: string | null; turn_token?: B.TurnToken | null }): Promise<AnswerFate> {
    const turn = this.turn;
    const parked = turn?.parked_asks.get(ask_id);
    if (turn === null || parked === undefined) throw new B.PermissionAnswerWriteFailed(ask_id);
    turn.parked_asks.delete(ask_id);
    let answer: Record<string, unknown>;
    try { answer = approval_answer(parked.method, option_id); }
    catch (error) { turn.parked_asks.set(ask_id, parked); if (error instanceof P.CodexProtocolError) throw new B.PermissionAnswerWriteFailed(`${repr(option_id)} is not an answer codex offers`); throw error; }
    try { await this.client.respond(parked.request_id, answer); }
    catch (error) { turn.parked_asks.set(ask_id, parked); if (error instanceof CodexAppServerError) throw new B.PermissionAnswerWriteFailed(text(error)); throw error; }
    return AnswerFate.applied;
  }
  async answer_user_input(request_id: string, answers: readonly UserInputAnswer[], _options?: { answer_id?: string | null; turn_token?: B.TurnToken | null }): Promise<AnswerFate> {
    const turn = this.turn;
    const parked = turn?.parked_user_inputs.get(request_id);
    if (turn === null || parked === undefined) throw new B.UserInputAnswerWriteFailed(request_id);
    turn.parked_user_inputs.delete(request_id);
    let answer: Record<string, unknown>;
    try { answer = user_input_answer(answers); }
    catch (error) { turn.parked_user_inputs.set(request_id, parked); if (error instanceof P.CodexProtocolError) throw new B.UserInputAnswerWriteFailed(`${repr(request_id)} has an answer Codex does not accept`); throw error; }
    try { await this.client.respond(parked.request_id, answer); }
    catch (error) { turn.parked_user_inputs.set(request_id, parked); if (error instanceof CodexAppServerError) throw new B.UserInputAnswerWriteFailed(text(error)); throw error; }
    return AnswerFate.applied;
  }
  async withdraw_delivery(): Promise<boolean> { return true; }
  async detach(): Promise<void> { await this.stop(); }
  async stop(): Promise<void> {
    try {
      await this.coordinator.close();
      const turn = this.turn;
      this.turn = null;
      if (turn !== null) await bounded(this.settle_asks(turn), ASK_WITHDRAWAL_TIMEOUT_SECONDS);
    } finally {
      await this.client.stop();
    }
  }
  private async publish_catalog(): Promise<void> {
    const task = this.catalog_publish.then(async () => {
      if (this.coordinator.closed) return;
      const snapshot = catalog_snapshot(this.coordinator.skills, this.coordinator.installed,
        [...this.coordinator.metadata.values()].map(([, app]) => app).filter((app): app is P.ConnectorMetadata => app !== null), this.coordinator.plugins);
      if (isDeepStrictEqual(snapshot.entries, this.published_catalog)) { this.catalog = snapshot; return; }
      await this.sink.report(null, new B.ComposerCatalogReported({ composer_catalog: snapshot.entries }));
      this.catalog = snapshot;
      this.published_catalog = snapshot.entries;
    });
    this.catalog_publish = task.catch(() => {});
    await task;
  }
  private async catalog_request(method: string, parameters: Record<string, unknown>): Promise<unknown> {
    return this.client.request(method, parameters, { poison_wire_on_timeout: false, timeout_seconds: CATALOG_REQUEST_TIMEOUT_SECONDS });
  }
  private catalog_invocation(content: MessageContent): CatalogInvocation | null {
    const first = content[0];
    if (!(first instanceof MessageText)) return null;
    const match = /^([^\s]+)(?:\s+(.*))?$/s.exec(first.text);
    if (match === null) return null;
    const token = match[1]!;
    if (token === '/compact') {
      if (first.text.trimEnd() === '/compact') return { kind: K.command, name: 'compact', path: null };
      throw new B.PromptWriteFailed('/compact does not take arguments');
    }
    if (token === '/review') return { kind: K.command, name: 'review', path: first.text.slice('/review'.length).trim() };
    if (token === '/goal') return goal_invocation(first.text);
    const invocation = this.catalog.resolve(token);
    if (invocation === null && (token.startsWith('$') || token.startsWith('@'))) throw new B.PromptWriteFailed(`${repr(token)} is not in the current Codex catalogue`);
    return invocation;
  }
  private turn_input(content: MessageContent, invocation: CatalogInvocation | null = null): P.UserInput[] {
    const input: P.UserInput[] = [];
    for (const piece of content) {
      if (piece instanceof MessageText) input.push({ type: 'text', text: piece.text });
      else if (piece instanceof MessageImage) input.push({ type: 'localImage', path: this.kept_path(piece.stored_file_id) });
      else if (piece instanceof MessageFile) input.push({ type: 'text', text: `Attached file "${piece.file_name}" (${piece.media_type}, ${piece.byte_count} bytes) is available at ${this.kept_path(piece.stored_file_id)}.` });
    }
    if (invocation?.kind === K.skill) input.push({ type: 'skill', name: invocation.name, path: invocation.path ?? '' });
    else if (invocation?.kind === K.app || invocation?.kind === K.plugin) input.push({ type: 'mention', name: invocation.name, path: invocation.path ?? '' });
    return input;
  }
  private kept_path(id: string): string {
    try { return this.message_files.path_of(this.resolved_start.conversation_id, id); }
    catch (error) { if (error instanceof MessageFileMissing) throw new B.PromptWriteFailed(`${id} is not there`); throw error; }
  }
  private async start_turn(parameters: Record<string, unknown>, turn: TurnInFlight, effort: string | null): Promise<string> {
    turn.awaiting_start_response = true;
    let result: unknown;
    try {
      const answer = await this.client.begin_request('turn/start', { ...parameters, effort });
      result = await this.client.finish_request('turn/start', answer);
    } catch (error) {
      if (error instanceof CodexRequestRejected) {
        if (turn.pending_notifications.length || turn.pending_requests.length) throw new B.PromptWriteFailed('codex rejected start after provisional activity');
        throw new B.BackendPromptRejected(text(error));
      }
      if (error instanceof CodexAppServerError) throw new B.PromptWriteFailed(text(error));
      throw error;
    }
    let id: string;
    try { id = P.TurnStartResponse.read(result).turn.id; }
    catch (error) { if (error instanceof P.CodexProtocolError) throw new B.PromptWriteFailed('codex returned an invalid turn/start response'); throw error; }
    turn.turn_id = id;
    turn.interrupt_turn_id = id;
    turn.awaiting_start_response = false;
    turn.started.resolve(id);
    const notifications = turn.pending_notifications;
    turn.pending_notifications = [];
    for (const [method, value] of notifications) await this.on_notification(method, value);
    const requests = turn.pending_requests;
    turn.pending_requests = [];
    for (const [method, request, value] of requests) await this.on_server_request(method, request, value);
    return id;
  }
  private async start_native(thread_id: string, invocation: CatalogInvocation, turn: TurnInFlight): Promise<string | null> {
    if (invocation.name.startsWith('goal_')) { await this.run_goal(thread_id, invocation, turn); return null; }
    const compact = invocation.name === 'compact';
    const method = compact ? 'thread/compact/start' : 'review/start';
    const parameters = compact ? { threadId: thread_id } : { threadId: thread_id, delivery: 'inline', target: invocation.path ? { type: 'custom', instructions: invocation.path } : { type: 'uncommittedChanges' } };
    let answer: Awaited<ReturnType<CodexClient['begin_request']>>;
    try { answer = await this.client.begin_request(method, parameters); }
    catch (error) { if (error instanceof CodexAppServerError) throw new B.PromptWriteFailed(text(error)); throw error; }
    const accepted = this.client.finish_request(method, answer);
    // Suppress an early rejection until the race selects the operation's acceptance path.
    void accepted.catch(() => {});
    await Promise.race([accepted.then(() => undefined, () => undefined), turn.started.promise]);
    if (turn.started.done) {
      if (compact) void accepted.catch(() => {});
      else void accepted.then((result) => this.bind_review_parent(turn, P.ReviewStartResponse.read(result).turn.id)).catch((error) => console.debug(`conversation ${this.resolved_start.conversation_id}: the review/start response identity call ended with ${text(error)}`));
      return compact ? turn.started.value! : turn.turn_id;
    }
    try {
      const result = await accepted;
      if (compact) { P.ThreadCompactStartResponse.read(result); return turn.turn_id; }
      const id = P.ReviewStartResponse.read(result).turn.id;
      this.bind_review_parent(turn, id);
      return id;
    } catch (error) { if (error instanceof CodexAppServerError || error instanceof P.CodexProtocolError) throw new B.PromptWriteFailed(text(error)); throw error; }
  }
  private bind_review_parent(turn: TurnInFlight, id: string): void {
    if (turn.turn_id !== null && turn.turn_id !== id) throw new CodexAppServerError('review/start returned a turn id that did not match its lifecycle');
    turn.turn_id = id;
    turn.interrupt_turn_id ??= id;
  }
  private async run_goal(thread_id: string, invocation: CatalogInvocation, turn: TurnInFlight): Promise<void> {
    if (this.thread_is_ephemeral) throw new B.PromptWriteFailed('Codex goals are unavailable on an ephemeral thread');
    let message: string;
    try {
      if (invocation.name === 'goal_get') message = goal_inspection_message(P.ThreadGoalGetResponse.read(await this.client.request('thread/goal/get', { threadId: thread_id })).goal);
      else if (invocation.name === 'goal_set') message = `Goal set: ${P.ThreadGoalSetResponse.read(await this.client.request('thread/goal/set', wire({ threadId: thread_id, objective: invocation.path, status: 'active' }))).goal.objective}`;
      else message = P.ThreadGoalClearResponse.read(await this.client.request('thread/goal/clear', { threadId: thread_id })).cleared ? 'Goal cleared.' : 'No goal was set.';
    } catch (error) { if (error instanceof CodexAppServerError || error instanceof P.CodexProtocolError) throw new B.PromptWriteFailed(text(error)); throw error; }
    await this.sink.report(turn.token, new B.CommandReplyReported({ reply_text: message }));
    if (this.turn === turn) this.turn = null;
    await this.sink.report(turn.token, new B.TurnEnded({ ending: E.completed }));
    turn.ended.resolve();
  }
  private bound_thread(): string {
    if (this.thread_id === null || !this.client.is_alive) throw new B.PromptWriteFailed('this child has no live bound thread');
    return this.thread_id;
  }
  private turn_for(id: string): TurnInFlight | null {
    const turn = this.turn;
    if (turn === null) return null;
    if (turn.turn_id === id) return turn;
    if (turn.kind === 'review' && turn.turn_id === null && turn.interrupt_turn_id !== id) {
      turn.turn_id = id;
      turn.interrupt_turn_id ??= id;
      return turn;
    }
    return null;
  }
  private async end_turn(turn: TurnInFlight, ending: E, summary: string | null): Promise<void> {
    if (this.turn !== turn) return;
    if (ending === E.failed) await this.client.settle_standard_error();
    this.turn = null;
    this.stderr_next_turn = this.client.standard_error_bytes_so_far();
    await this.complete_messages(turn);
    await this.settle_asks(turn);
    if (ending === E.failed) summary = this.read_login_failure(turn, summary);
    else if (ending === E.completed && same_generation(turn.login_generation_at_start, this.login_generation())) this.rejection.forget(turn.login_generation_at_start);
    await this.sink.report(turn.token, new B.TurnEnded({ ending, error_summary: summary, standard_error_tail: ending === E.failed ? this.client.standard_error_tail() : null }));
    turn.ended.resolve();
  }
  private login_generation(): CodexLoginGeneration | null { return read_stored_codex_login(this.home).generation; }
  private read_login_failure(turn: TurnInFlight, summary: string | null): string | null {
    const reading = read_a_failed_turn({ error_summary: summary, standard_error_this_turn: this.client.standard_error_since(turn.standard_error_from_byte) });
    if (reading === CodexAuthReading.login_is_rejected && same_generation(turn.login_generation_at_start, this.login_generation())) this.rejection.remember(turn.login_generation_at_start);
    return error_summary_for(reading, summary);
  }
  private async complete_messages(turn: TurnInFlight): Promise<void> {
    for (const texts of turn.agent_message_texts.values()) if (texts.length) await this.sink.report(turn.token, new B.AgentMessageCompleted({ content: text_message_content(texts.join('')) }));
    turn.agent_message_texts.clear();
  }
  private async settle_asks(turn: TurnInFlight): Promise<void> {
    for (const ask of turn.parked_asks.values()) await this.quiet_response(ask.request_id, approval_answer(ask.method, 'decline'));
    turn.parked_asks.clear();
    for (const ask of turn.parked_user_inputs.values()) await this.quiet_response(ask.request_id, user_input_answer([]));
    turn.parked_user_inputs.clear();
  }
  private async quiet_response(id: unknown, response: Record<string, unknown>): Promise<void> {
    try { await this.client.respond(id, response); }
    catch (error) { if (!(error instanceof CodexAppServerError || error instanceof P.CodexProtocolError)) throw error; }
  }
  private async on_notification(method: string, notification: P.ConsumedNotification): Promise<void> {
    const thread = 'threadId' in notification ? notification.threadId : null;
    if (thread !== null && thread !== this.thread_id) return;
    const current = this.turn;
    if (current !== null && current.awaiting_start_response && thread !== null) { current.pending_notifications.push([method, notification]); return; }
    switch (method) {
      case 'turn/started': {
        const value = notification as P.TurnStartedNotification;
        const turn = this.turn;
        if (turn === null || value.threadId !== this.thread_id) return;
        if (turn.kind === 'review') turn.interrupt_turn_id = value.turn.id;
        else if (turn.turn_id === null) { turn.turn_id = value.turn.id; turn.interrupt_turn_id = value.turn.id; }
        else return;
        turn.started.resolve(value.turn.id);
        return;
      }
      case 'turn/completed': {
        const value = notification as P.TurnCompletedNotification;
        const turn = this.turn_for(value.turn.id);
        if (turn === null || value.turn.status === 'inProgress') return;
        const ending = value.turn.status;
        const summary = ending === E.failed ? value.turn.error === null ? normalized_failure_summary(turn.last_error_summary) : error_summary(value.turn.error) : null;
        await this.end_turn(turn, ending, summary);
        return;
      }
      case 'item/agentMessage/delta': {
        const value = notification as P.AgentMessageDeltaNotification;
        const turn = this.turn_for(value.turnId);
        if (turn === null) return;
        const texts = turn.agent_message_texts.get(value.itemId) ?? [];
        texts.push(value.delta);
        turn.agent_message_texts.set(value.itemId, texts);
        await this.sink.report(turn.token, new B.AgentMessageDelta({ text_delta: value.delta }));
        return;
      }
      case 'item/started': {
        const value = notification as P.ItemStartedNotification;
        const turn = this.turn_for(value.turnId);
        if (turn === null) return;
        if (value.item.type === 'reasoning') { await this.sink.report(turn.token, new B.ModelThinkingHappened()); return; }
        const started = tool_call_started(value.item);
        if (started !== null) await this.sink.report(turn.token, new B.ToolCallStarted({ tool_call_id: started.id, title: started.title, tool_kind: started.tool_kind, detail: started.detail }));
        return;
      }
      case 'item/completed': {
        const value = notification as P.ItemCompletedNotification;
        const turn = this.turn_for(value.turnId);
        if (turn === null) return;
        if (value.item.type === 'agentMessage') {
          const item = value.item as P.AgentMessageThreadItem;
          turn.agent_message_texts.delete(item.id);
          await this.sink.report(turn.token, new B.AgentMessageCompleted({ content: text_message_content(item.text) }));
          return;
        }
        if (value.item.type === 'contextCompaction') { await this.sink.report(turn.token, new B.ContextCompacted()); return; }
        const finished = tool_call_finished(value.item);
        if (finished !== null) await this.sink.report(turn.token, new B.ToolCallFinished({ tool_call_id: finished.id, tool_call_status: finished.tool_call_status, detail: finished.detail }));
        return;
      }
      case 'item/commandExecution/outputDelta':
      case 'item/mcpToolCall/progress': {
        const value = notification as P.CommandExecutionOutputDeltaNotification | P.McpToolCallProgressNotification;
        const turn = this.turn_for(value.turnId);
        const detail = 'delta' in value ? value.delta : value.message;
        if (turn !== null && detail) await this.sink.report(turn.token, new B.ToolCallProgress({ tool_call_id: value.itemId, detail }));
        return;
      }
      case 'item/reasoning/textDelta':
      case 'item/reasoning/summaryTextDelta': {
        const value = notification as P.ReasoningTextDeltaNotification;
        const turn = this.turn_for(value.turnId);
        if (turn !== null) await this.sink.report(turn.token, new B.ModelThinkingHappened());
        return;
      }
      case 'turn/plan/updated': {
        const value = notification as P.TurnPlanUpdatedNotification;
        const turn = this.turn_for(value.turnId);
        if (turn !== null) await this.sink.report(turn.token, new B.PlanUpdated({ entries: plan_entries(value.plan) }));
        return;
      }
      case 'thread/tokenUsage/updated': {
        const value = notification as P.ThreadTokenUsageUpdatedNotification;
        const turn = this.turn_for(value.turnId);
        if (turn === null) return;
        const spent = value.tokenUsage.total;
        await this.sink.report(turn.token, new B.TokenUsageReported({ input_tokens: spent.inputTokens, output_tokens: spent.outputTokens, cached_input_tokens: spent.cachedInputTokens, cost_usd: null }));
        return;
      }
      case 'error': {
        const value = notification as P.ErrorNotification;
        const turn = this.turn_for(value.turnId);
        if (turn !== null && !value.willRetry) turn.last_error_summary = error_summary(value.error);
        return;
      }
      case 'skills/changed': this.coordinator.hint_skills(); return;
      case 'app/list/updated': this.coordinator.hint_apps(); return;
    }
  }
  private async on_server_request(method: string, request_id: unknown, params: P.HandledServerRequest): Promise<void> {
    if (params.threadId !== this.thread_id) { await this.client.respond_with_error(request_id, { code: -32600, message: 'request names another thread' }); return; }
    const current = this.turn;
    if (current !== null && current.awaiting_start_response) { current.pending_requests.push([method, request_id, params]); return; }
    if (method === 'item/tool/requestUserInput') { await this.on_user_input(request_id, params as P.ToolRequestUserInputParams); return; }
    const ask = permission_ask(method, params);
    if (ask === null) return;
    const turn = this.turn_for(ask.turn_id);
    if (turn === null) { await this.quiet_response(request_id, approval_answer(method, 'decline')); return; }
    const id = `${ask.turn_id}:${++this.asks_raised}`;
    turn.parked_asks.set(id, { request_id, method });
    await this.sink.report(turn.token, new B.PermissionAskRaised({ ask: new B.BackendPermissionAsk({ ask_id: id, title: ask.title, detail: ask.detail, options: PERMISSION_ASK_OPTIONS }) }));
  }
  private async on_user_input(wire_id: unknown, params: P.ToolRequestUserInputParams): Promise<void> {
    const turn = this.turn_for(params.turnId);
    if (turn === null) { await this.quiet_response(wire_id, user_input_answer([])); return; }
    const id = `${params.turnId}:${params.itemId}`;
    let questions: ReturnType<typeof user_input_questions>;
    try { questions = user_input_questions(params.questions); }
    catch (error) {
      await this.sink.report(turn.token, new B.UserInputFailed({ request_id: id, detail: text(error) }));
      await this.quiet_response(wire_id, user_input_answer([]));
      return;
    }
    turn.parked_user_inputs.set(id, { request_id: wire_id });
    await this.sink.report(turn.token, new B.UserInputRequested({ request: new B.BackendUserInputRequest({ request_id: id, questions }) }));
  }
  private async on_child_ended(): Promise<void> {
    await this.coordinator.close();
    const turn = this.turn;
    if (turn === null) return;
    this.turn = null;
    turn.parked_asks.clear();
    turn.parked_user_inputs.clear();
    await this.complete_messages(turn);
    await this.sink.report(turn.token, new B.TurnEnded({ ending: E.failed, error_summary: this.read_login_failure(turn, this.client.end_reason ?? 'the codex process ended while the turn was running'), standard_error_tail: this.client.standard_error_tail() }));
    turn.ended.resolve();
  }
}
function goal_invocation(text: string): CatalogInvocation {
  const command = text.trimEnd();
  if (command === '/goal' || command === '/goal get') return { kind: K.command, name: 'goal_get', path: null };
  if (command === '/goal clear') return { kind: K.command, name: 'goal_clear', path: null };
  const prefix = '/goal set';
  if (command.startsWith(prefix) && /\s/.test(command[prefix.length] ?? '') && command.slice(prefix.length).trim()) return { kind: K.command, name: 'goal_set', path: command.slice(prefix.length).trim() };
  throw new B.PromptWriteFailed("/goal accepts no argument, 'get', 'set <objective>', or 'clear'");
}
function steer_rejection_reason(rejected: CodexRequestRejected): R {
  if (rejected.code === -32600 && rejected.rejection_message === 'no active turn to steer') return R.no_running_turn_to_steer_into;
  if (rejected.code === -32600 && rejected.rejection_message.startsWith('expected active turn id `')) return R.running_turn_changed_before_steer;
  const data = rejected.data;
  if (data !== null && typeof data === 'object' && !Array.isArray(data) && !(data instanceof JsonNumber) && 'codexErrorInfo' in data) {
    const info = data.codexErrorInfo;
    if (info !== null && typeof info === 'object' && !Array.isArray(info) && !(info instanceof JsonNumber) && 'activeTurnNotSteerable' in info && info.activeTurnNotSteerable !== null && typeof info.activeTurnNotSteerable === 'object' && !Array.isArray(info.activeTurnNotSteerable) && !(info.activeTurnNotSteerable instanceof JsonNumber)) return R.running_turn_cannot_accept_steer;
  }
  return R.backend_rejected_steer;
}
export function create_codex_backend_child_factory(launch: CodexChildLaunch, options: { remembered_login_rejection?: CodexLoginRejectionMemory; client_factory?: ClientFactory } = {}): B.BackendChildFactory {
  const remembered_login_rejection = options.remembered_login_rejection ?? new RememberedCodexLoginRejection();
  return (values) => new CodexAppServerBackendChild({ ...values, launch, remembered_login_rejection, client_factory: options.client_factory });
}
