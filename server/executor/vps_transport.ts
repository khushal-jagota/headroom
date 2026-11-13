/** Invocation-owned VPS executor. Request disconnects do not cancel admitted saves. */
import type { FastifyInstance } from 'fastify';
import type { Clock } from '../core/clock.ts';
import type { Database } from '../core/database/contracts.ts';
import type { ChangeSignal } from '../core/changes/index.ts';
import { legacyActor, PlannerError } from '../core/contracts.ts';
import type { ExecutorIdentity } from '../execution_identity.ts';
import { ConversationMessageFiles } from '../conversation/record/message_files.ts';
import type { SqliteProcessConversationSystem } from '../conversation/system.ts';
import type { BackendEventValue } from '../conversation/backends/contracts.ts';
import { retained_open_runs } from '../conversation/runtime/remote.ts';
import { admit_remote_turn } from '../conversation/runtime/remote_turn.ts';
import { observe_shutdown, is_shutdown_business_cancellation } from '../conversation/shutdown.ts';
import { ServerLifecycleError, ShutdownDeadlineExceeded } from '../lifecycle/contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import type { StallSource } from '../stalls/contracts.ts';
import type { MacUpdateServiceContract } from '../environments/updates/mac/contracts.ts';
import type { InputFile, UploadSource, WireValue } from './contracts.ts';
import { SqliteCommandMailbox } from './vps_mailbox.ts';
import { MacPresenceService } from './vps_presence.ts';
import { MacRuns } from './vps_runs.ts';
import { MacInbox } from './vps_inbox.ts';
import { InboxStore } from './data/inbox.ts';
import { FileStaging, VpsFiles } from './vps_files.ts';
import { VpsCatalogRelay } from './catalog_relay.ts';
import { answer_by } from './item_budget.ts';
import { as_object, decode_event_arguments, integer_value } from './protocol.ts';
import { parse_executor_json } from './protocol_json.ts';
import { JsonNumber, stringify_python_json } from '../core/http/numeric_json.ts';
import { python_repr } from './protocol_errors.ts';
import {
  COMMAND_POLL_HOLD_SECONDS,
  LEASE_SERVER_LIMIT_SECONDS,
  COMMAND_OUTCOME_SERVER_LIMIT_SECONDS,
  MAX_COMMAND_OUTCOMES_PER_REPORT,
  LIVE_READ_SERVER_LIMIT_SECONDS,
  LIVE_EVENT_SERVER_LIMIT_SECONDS,
  MAX_LIVE_EVENTS_PER_BATCH,
} from './configuration.ts';

const READABLE_PATHS = [
  '/api/tickets',
  '/api/managers',
  '/api/sprint',
  '/api/projects',
  '/api/worker-types',
  '/api/skills',
  '/files/sprint-items/',
  '/files/managers/',
];
const LIVE_METHODS = new Set([
  'agent_message_delta',
  'model_thinking_happened',
  'tool_call_progress',
]);
export interface VpsExecutorOptions {
  readonly db_path: string;
  readonly changes: ChangeSignal;
  readonly media_signal: AbortSignal;
  readonly application_revision?: string;
}
/** The supplied database and every child-process owner belong to the server invocation. */
export class VpsExecutor {
  readonly db_path: string;
  readonly application_revision: string;
  readonly mailbox: SqliteCommandMailbox;
  readonly presence: MacPresenceService;
  readonly runs: MacRuns;
  readonly inbox: MacInbox;
  readonly catalog: VpsCatalogRelay;
  readonly message_files: ConversationMessageFiles;
  readonly files: VpsFiles;
  app!: FastifyInstance;
  updates: MacUpdateServiceContract | null = null;
  private readonly owned = new Set<Promise<unknown>>();
  private closed = false;
  private actual_stop: Promise<void> | null = null;
  private resolve_settlement!: () => void;
  private reject_settlement!: (error: unknown) => void;
  private readonly actual_settlement = new Promise<void>((resolve, reject) => {
    this.resolve_settlement = resolve;
    this.reject_settlement = reject;
  });
  constructor(database: Database, clock: Clock, options: VpsExecutorOptions) {
    this.db_path = options.db_path;
    this.application_revision = options.application_revision ?? 'unknown';
    this.mailbox = new SqliteCommandMailbox(database);
    this.presence = new MacPresenceService(database, this.mailbox, {
      changes: options.changes,
      application_revision: this.application_revision,
    });
    this.runs = new MacRuns(database, this.presence, this.mailbox);
    const staging = new FileStaging(options.db_path);
    this.inbox = new MacInbox(new InboxStore(database, staging), clock, this.runs);
    this.catalog = new VpsCatalogRelay(database, this.runs, this.presence, this.mailbox);
    this.message_files = new ConversationMessageFiles(options.db_path);
    this.files = new VpsFiles(options.db_path, this.inbox, this.mailbox, this.message_files, {
      signal: options.media_signal,
    });
    this.presence.incarnation_replaced = (replaced, current) =>
      this.runs.incarnation_replaced(replaced, current);
    this.presence.returned = async () => this.catalog.connected();
    void this.actual_settlement.catch(() => {});
  }
  private admit(): void {
    if (this.closed) throw new ServerLifecycleError('Executor endpoint is closed.');
  }
  bind_conversations(conversations: SqliteProcessConversationSystem): void {
    this.inbox.conversations = {
      take_backend_event: (turn, event, options) =>
        conversations.take_backend_event(turn, event as unknown as BackendEventValue, options),
      take_answer_application: (...args) => conversations.take_answer_application(...args),
      fence_replaced_remote_child: (reference, options) =>
        conversations.fence_replaced_remote_child(reference, options),
    };
    this.inbox.agent_writes.conversations = conversations;
    this.runs.conversations = conversations;
  }
  configure(conversations: SqliteProcessConversationSystem, app: FastifyInstance): void {
    this.bind_conversations(conversations);
    this.app = app;
  }
  bind_updates(service: MacUpdateServiceContract): void {
    this.updates = service;
    this.presence.update_check = () => service.check();
  }
  configured(): boolean {
    return true;
  }
  open_runs() {
    const remote = this.runs.as_factory();
    return retained_open_runs({ claude: remote, codex: remote, hermes: remote });
  }
  async start(): Promise<void> {
    this.admit();
    this.runs.require_conversations();
    this.presence.record();
    this.mailbox.high_sequence();
    await this.runs.resume_replacement_drains();
  }
  private async owned_work<T>(work: () => Promise<T>, answer_within?: number): Promise<T> {
    this.admit();
    const task = Promise.resolve().then(() => {
      this.admit();
      return work();
    });
    this.owned.add(task);
    void task.then(
      () => this.owned.delete(task),
      () => this.owned.delete(task),
    );
    if (answer_within === undefined) return task;
    const answer = await answer_by(task, performance.now() / 1000 + answer_within);
    if (!answer.done) throw new PlannerError('retry_later', 'the VPS is still saving this');
    return answer.value;
  }
  health() {
    return this.presence.health();
  }
  health_payload(): WireValue<'JsonObject'> {
    return as_object(this.health(), 'ExecutorHealthRead');
  }
  stall_sources(): readonly StallSource[] {
    return [this.mailbox.stall_source(), this.runs.stall_source(), this.presence.stall_source()];
  }
  record_contact(): void {
    this.presence.record_contact();
  }
  require_current(options: {
    incarnation_header: string | null;
    protocol_header: string | null;
  }): ExecutorIdentity {
    return this.presence.require_current(options);
  }
  lease(
    request: WireValue<'LeaseRequest'>,
  ): Promise<WireValue<'LeaseReply'> | WireValue<'LeaseRefused'>> {
    return this.owned_work(() => {
      this.runs.require_conversations();
      return this.presence.accept_lease(request);
    }, LEASE_SERVER_LIMIT_SECONDS);
  }
  refuse_old_websocket(): Promise<void> {
    return this.owned_work(() => this.presence.refuse_old_websocket());
  }
  async poll(
    identity: ExecutorIdentity,
    after: WireValue<'WaitingConversation'>['oldest_sequence'],
    wait_seconds: number,
    options: { signal?: AbortSignal } = {},
  ): Promise<WireValue<'MailboxPollReply'>> {
    this.admit();
    if (
      integer_value(after) < 0n ||
      !(wait_seconds >= 0 && wait_seconds <= COMMAND_POLL_HOLD_SECONDS)
    )
      throw new PlannerError('validation', 'invalid poll cursor or wait');
    return this.mailbox.poll(identity, after, wait_seconds, options);
  }
  record_outcomes(
    identity: ExecutorIdentity,
    report: WireValue<'CommandOutcomeReport'>,
  ): Promise<WireValue<'CommandOutcomeReply'>> {
    if (report.outcomes.length > MAX_COMMAND_OUTCOMES_PER_REPORT)
      throw new PlannerError('validation', 'too many results in one report');
    return this.owned_work(
      () => this.mailbox.record_results(identity, report),
      COMMAND_OUTCOME_SERVER_LIMIT_SECONDS,
    );
  }
  async accept_items(
    identity: ExecutorIdentity,
    conversation_id: string,
    batch: WireValue<'ItemBatch'>,
  ): Promise<WireValue<'ItemBatchReply'>> {
    const reply = await this.owned_work(() =>
      this.inbox.accept_batch(conversation_id, batch, identity),
    );
    if (reply.receipts.length) this.presence.note_conversation_activity(conversation_id);
    return reply;
  }
  upload(
    identity: ExecutorIdentity,
    sha256: string,
    upload: WireValue<'FileUploadRequest'>,
    content_length: WireValue<'FileManifest'>['byte_count'] | null,
    chunks: UploadSource,
  ): Promise<void> {
    return this.owned_work(() =>
      this.files.upload(identity, sha256, upload, content_length, chunks),
    );
  }
  input_file(
    identity: ExecutorIdentity,
    command_id: string,
    stored_file_id: string,
  ): Promise<InputFile> {
    this.admit();
    return this.files.input_file(identity, command_id, stored_file_id);
  }
  async read(
    _identity: ExecutorIdentity,
    read: WireValue<'LiveReadRequest'>,
  ): Promise<WireValue<'JsonObject'>> {
    this.admit();
    if (!READABLE_PATHS.some((prefix) => read.path.startsWith(prefix)))
      throw new PlannerError('validation', 'unsupported executor read');
    const principal = this.runs.authority(read.child);
    const headers: Record<string, string> = { 'x-plan-actor': legacyActor(principal) };
    if (principal.kind === 'ticket') headers['x-plan-ticket-id'] = principal.id;
    const params = new URLSearchParams();
    for (const [name, value] of Object.entries(read.params)) {
      const values = Array.isArray(value) ? value : [value];
      for (const element of values)
        params.append(
          name,
          element === null
            ? ''
            : typeof element === 'string'
              ? element
              : typeof element === 'boolean'
                ? String(element).toLowerCase()
                : typeof element === 'number' || element instanceof JsonNumber
                  ? stringify_python_json(element)
                  : python_repr(element),
        );
    }
    const target = new URL(read.path, 'http://executor');
    target.search = params.toString();
    const controller = new AbortController();
    const answer = await answer_by(
      this.app.inject({
        method: 'GET',
        url: target.pathname + target.search,
        headers,
        signal: controller.signal,
      }),
      performance.now() / 1000 + LIVE_READ_SERVER_LIMIT_SECONDS,
    );
    if (!answer.done) {
      controller.abort();
      throw new PlannerError('retry_later', 'the read took too long');
    }
    const response = answer.value;
    try {
      const value = parse_executor_json(response.rawPayload);
      return value !== null &&
        typeof value === 'object' &&
        !Array.isArray(value) &&
        !(value instanceof JsonNumber)
        ? (value as WireValue<'JsonObject'>)
        : { data: value };
    } catch {
      return { text: response.body };
    }
  }
  async live(
    identity: ExecutorIdentity,
    conversation_id: string,
    batch: WireValue<'LiveEventBatch'>,
  ): Promise<void> {
    this.admit();
    if (
      batch.events.length > MAX_LIVE_EVENTS_PER_BATCH ||
      batch.events.some((event) => !LIVE_METHODS.has(event.method))
    )
      throw new PlannerError('validation', 'a live batch carries only live events');
    let expired = false;
    const report = async () => {
      for (const event of batch.events) {
        if (expired || this.closed) return;
        if (
          event.child.executor_incarnation !== identity.executor_incarnation ||
          event.child.conversation_id !== conversation_id
        )
          continue;
        const child = this.runs.children.get(event.child.child_id);
        if (
          !child ||
          child.reference.executor_incarnation !== event.child.executor_incarnation ||
          child.reference.conversation_id !== event.child.conversation_id ||
          child.event_sink === null
        )
          continue;
        const turn = event.turn === null ? null : admit_remote_turn(event.turn);
        await child.event_sink.report(turn, decode_event_arguments(event) as never);
      }
    };
    const task = report();
    this.owned.add(task);
    void task.then(
      () => this.owned.delete(task),
      () => this.owned.delete(task),
    );
    const answer = await answer_by(
      task,
      performance.now() / 1000 + LIVE_EVENT_SERVER_LIMIT_SECONDS,
    );
    if (!answer.done) {
      expired = true;
      return;
    }
    if (batch.events.length) this.presence.note_conversation_activity(conversation_id);
  }
  stop(options: StopOptions): Promise<void> {
    if (this.actual_stop === null) {
      this.closed = true;
      this.actual_stop = this.actual_settlement;
      const pending = [...this.owned];
      const services = [this.runs, this.catalog, this.inbox, this.files, this.presence];
      for (const service of services) void service.stop(options).catch(() => {});
      // Catalog cancellation owns a final waiting-row withdrawal through the mailbox.
      const mailbox_drain = this.catalog
        .settled()
        .catch(() => {})
        .then(async () => {
          const results = await Promise.allSettled([
            Promise.resolve().then(() => this.mailbox.stop(options)),
            this.mailbox.settled(),
          ]);
          const errors = results.flatMap((result) =>
            result.status === 'rejected' && !(result.reason instanceof ShutdownDeadlineExceeded)
              ? [result.reason]
              : [],
          );
          if (errors.length) throw new AggregateError(errors, 'Executor mailbox shutdown failed.');
        });
      const drain = Promise.allSettled([
        ...pending,
        ...services.map((service) => service.settled()),
        mailbox_drain,
      ]).then((results) => {
        const errors = [
          ...new Set(
            results.flatMap((result) =>
              result.status === 'rejected' &&
              !(result.reason instanceof PlannerError) &&
              !is_shutdown_business_cancellation(result.reason) &&
              !(result.reason instanceof ShutdownDeadlineExceeded)
                ? [result.reason]
                : [],
            ),
          ),
        ];
        if (errors.length) throw new AggregateError(errors, 'Executor shutdown failed.');
      });
      void drain.then(this.resolve_settlement, this.reject_settlement);
    }
    return observe_shutdown(
      this.actual_settlement,
      options,
      'Executor work remains unfinished at the shutdown deadline.',
    );
  }
  settled(): Promise<void> {
    return this.actual_settlement;
  }
}
