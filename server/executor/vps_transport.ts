import { report_move_setup } from '../work_moves/operations.ts';
/** Invocation-owned VPS executor. Request disconnects do not cancel admitted saves. */
import { declare_worker_setup } from '../ticket_workspaces/worker_setup.ts';
import type { FastifyInstance } from 'fastify';
import type { UpdateBarrier } from '../environments/update_barrier.ts';
import type { Clock } from '../core/clock.ts';
import type { Database, ReadSql } from '../core/database/contracts.ts';
import type { ChangeSignal } from '../core/changes/index.ts';
import { PlannerError } from '../core/contracts.ts';
import { ORIGINAL_MAC_MACHINE_ID, type ExecutorIdentity } from '../execution_identity.ts';
import { ConversationMessageFiles } from '../conversation/record/message_files.ts';
import type { SqliteProcessConversationSystem } from '../conversation/system.ts';
import type { BackendEventValue } from '../conversation/backends/contracts.ts';
import { retained_open_runs } from '../conversation/runtime/remote.ts';
import { admit_remote_turn } from '../conversation/runtime/remote_turn.ts';
import { observe_shutdown, is_shutdown_business_cancellation } from '../conversation/shutdown.ts';
import { recover_conversations } from '../conversation/deploy_interruptions/coordinator.ts';
import { conversation_machine_id } from '../machines/conversation_machine.ts';
import { ServerLifecycleError, ShutdownDeadlineExceeded } from '../lifecycle/contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import type { StallSource } from '../stalls/contracts.ts';
import type { MacUpdateServiceContract } from '../environments/updates/mac/contracts.ts';
import type { InputFile, UploadSource, WireValue } from './contracts.ts';
import { SqliteCommandMailbox } from './vps_mailbox.ts';
import { MacPresenceService } from './vps_presence.ts';
import { MacRuns } from './vps_runs.ts';
import { MacInbox } from './vps_inbox.ts';
import {
  agent_request_context,
  agent_response_json,
  inject_agent_request,
} from './vps_agent_writes.ts';
import { InboxStore, require_manager_files_machine } from './data/inbox.ts';
import { current_agent_owner } from './data/child_custody.ts';
import { FileStaging, VpsFiles } from './vps_files.ts';
import { VpsCatalogRelay } from './catalog_relay.ts';
import { VpsBackendControlRelay } from './backend_control_relay.ts';
import { observe_promise } from '../core/promise_observer.ts';
import { as_object, decode_event_arguments } from './protocol.ts';
import {
  authorize_retirement,
  has_committed_retirement,
  read_workspace_states_for_tickets,
  register_workspace as register_ticket_workspace,
} from '../ticket_workspaces/data.ts';
import {
  require_current_incarnation_in_transaction,
  require_incarnation_owner,
} from './vps_presence_store.ts';

import {
  COMMAND_POLL_HOLD_SECONDS,
  LEASE_SERVER_LIMIT_SECONDS,
  COMMAND_OUTCOME_SERVER_LIMIT_SECONDS,
  MAX_COMMAND_OUTCOMES_PER_REPORT,
  LIVE_READ_SERVER_LIMIT_SECONDS,
  LIVE_EVENT_SERVER_LIMIT_SECONDS,
  MAX_LIVE_EVENTS_PER_BATCH,
} from './configuration.ts';

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
  readonly machine_id?: string;
}
/** The supplied database and every child-process owner belong to the server invocation. */
export class VpsExecutor {
  readonly machine_id: string;
  private readonly database: Database;
  private readonly clock: Clock;
  readonly db_path: string;
  readonly application_revision: string;
  readonly mailbox: SqliteCommandMailbox;
  readonly presence: MacPresenceService;
  readonly runs: MacRuns;
  readonly inbox: MacInbox;
  readonly catalog: VpsCatalogRelay;
  readonly backend_controls: VpsBackendControlRelay;
  readonly message_files: ConversationMessageFiles;
  readonly files: VpsFiles;
  app!: FastifyInstance;
  updates: MacUpdateServiceContract | null = null;
  update_barrier: UpdateBarrier | undefined;
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
    this.database = database;
    this.clock = clock;
    this.machine_id = options.machine_id ?? ORIGINAL_MAC_MACHINE_ID;
    this.db_path = options.db_path;
    this.application_revision = options.application_revision ?? 'unknown';
    this.mailbox = new SqliteCommandMailbox(database, { machine_id: this.machine_id });
    this.presence = new MacPresenceService(database, this.mailbox, {
      changes: options.changes,
      machine_id: this.machine_id,
      application_revision: this.application_revision,
    });
    this.runs = new MacRuns(database, this.presence, this.mailbox);
    const staging = new FileStaging(options.db_path, this.machine_id);
    this.inbox = new MacInbox(new InboxStore(database, staging), clock, this.runs);
    this.catalog = new VpsCatalogRelay(database, this.runs, this.presence, this.mailbox);
    this.backend_controls = new VpsBackendControlRelay(this.mailbox, this.presence);
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
  configure(
    conversations: SqliteProcessConversationSystem,
    app: FastifyInstance,
    update_barrier?: UpdateBarrier,
  ): void {
    this.bind_conversations(conversations);
    this.app = app;
    this.inbox.agent_writes.app = app;
    this.update_barrier = update_barrier;
    this.mailbox.start_admission = conversations.start_admission;
    this.presence.returned = async () => {
      this.catalog.connected();
      // A server start skips the recovery of a Ticket whose machine is away. When the machine
      // connects, for example the server's own machine code after the switch, it runs now.
      void this.recover_waiting_turns(conversations).catch((error: unknown) => {
        if (!is_shutdown_business_cancellation(error))
          console.error(`Recovery for machine ${this.machine_id} failed`, error);
      });
    };
  }
  private async recover_waiting_turns(conversations: SqliteProcessConversationSystem) {
    // A Ticket's turn follows its Ticket's machine; the Chief's, a Manager's or any other
    // conversation's turn follows the machine its conversation records.
    const conversation_ids = this.database.read((sql) =>
      sql
        .all<{ conversation_id: string }>(
          "SELECT DISTINCT d.source_conversation_id AS conversation_id FROM deploy_interruptions d WHERE d.recovery_state='awaiting_action'",
        )
        .map((row) => row.conversation_id)
        .filter((id) => conversation_machine_id(sql, id) === this.machine_id),
    );
    if (conversation_ids.length === 0) return;
    await recover_conversations(conversations, this.database, {
      conversation_ids,
      signal: conversations.shutdown_signal,
    });
  }
  bind_updates(service: MacUpdateServiceContract): void {
    this.updates = service;
    this.presence.update_check = () => service.check();
  }
  open_runs() {
    const remote = this.runs.as_factory();
    return retained_open_runs([remote]);
  }
  async start(): Promise<void> {
    this.admit();
    this.runs.require_conversations();
    this.presence.record();
    this.mailbox.high_sequence();
    await this.runs.resume_replacement_drains();
  }
  private async owned_work<T>(
    work: () => Promise<T>,
    answer_within?: number,
    protect_update = false,
  ): Promise<T> {
    this.admit();
    const { promise: task, resolve, reject } = Promise.withResolvers<T>();
    this.owned.add(task);
    void task.then(
      () => this.owned.delete(task),
      () => this.owned.delete(task),
    );
    try {
      resolve(
        !protect_update || !this.update_barrier
          ? work()
          : this.update_barrier.operation((admitted) => {
              if (!admitted) throw new PlannerError('retry_later', 'Home update in progress.');
              return work();
            }),
      );
    } catch (error) {
      reject(error);
    }
    if (answer_within === undefined) return task;
    const answer = await observe_promise(task, answer_within * 1000);
    if (!answer.completed) throw new PlannerError('retry_later', 'the VPS is still saving this');
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
  workspace_states(
    identity: ExecutorIdentity,
    request: WireValue<'WorkspaceStatesRequest'>,
  ): Promise<WireValue<'WorkspaceStatesReply'>> {
    return this.owned_work(
      async () =>
        this.database.read((sql) => {
          require_current_incarnation_in_transaction(
            sql,
            identity.executor_incarnation,
            this.machine_id,
          );
          for (const ticket_id of request.ticket_ids)
            if (sql.get('SELECT 1 FROM ticket_workspace_lifecycle WHERE ticket_id=?', [ticket_id]))
              this.require_ticket_owner(sql, ticket_id);
          return {
            states: read_workspace_states_for_tickets(sql, request.ticket_ids),
            at_vps_ms: this.clock.nowUnix() * 1000,
          };
        }),
      LIVE_READ_SERVER_LIMIT_SECONDS,
    );
  }
  register_workspace(
    identity: ExecutorIdentity,
    registration: WireValue<'WorkspaceRegistrationRequest'>,
  ): Promise<void> {
    return this.owned_work(
      async () => {
        this.database.write((tx) => {
          require_current_incarnation_in_transaction(
            tx,
            identity.executor_incarnation,
            this.machine_id,
          );
          if (registration.child.executor_incarnation !== identity.executor_incarnation)
            throw new PlannerError(
              'agent_forbidden',
              'workspace child belongs to another executor',
            );
          const assignment = this.runs.store.open_child_assignment(tx, registration.child);
          if (assignment === null || assignment[0] !== 'ticket')
            throw new PlannerError('agent_forbidden', 'workspace child has no Ticket assignment');
          register_ticket_workspace(
            tx,
            assignment[1],
            registration.child.conversation_id,
            registration.workspace,
          );
        });
      },
      LIVE_READ_SERVER_LIMIT_SECONDS,
      true,
    );
  }
  /** The machine declares its Worker setup step while this child's start command is open. */
  report_worker_setup(
    identity: ExecutorIdentity,
    report: WireValue<'WorkerSetupReport'>,
  ): Promise<void> {
    return this.owned_work(
      async () => {
        this.database.write((tx) => {
          require_current_incarnation_in_transaction(
            tx,
            identity.executor_incarnation,
            this.machine_id,
          );
          if (report.child.executor_incarnation !== identity.executor_incarnation)
            throw new PlannerError('agent_forbidden', 'setup child belongs to another executor');
          const assignment = this.runs.store.open_child_assignment(tx, report.child);
          if (assignment === null)
            throw new PlannerError('agent_forbidden', 'setup child has no open assignment');
          // A report that arrives after its start settled, or from a start the Ticket has
          // since replaced, declares nothing.
          if (
            !(assignment[0] === 'ticket'
              ? tx.get('SELECT 1 FROM tickets WHERE id=? AND conversation_id=?', [
                  assignment[1],
                  report.child.conversation_id,
                ])
              : tx.get('SELECT 1 FROM agents WHERE agent_key=? AND conversation_id=?', [
                  assignment[0] === 'chief' ? 'chief_of_staff' : `manager_${assignment[1]}`,
                  report.child.conversation_id,
                ])) ||
            !tx.get(
              "SELECT 1 FROM executor_commands WHERE machine_id=? AND executor_incarnation=? AND child_id=? AND conversation_id=? AND operation='start' AND state IN ('waiting','delivered')",
              [
                this.machine_id,
                report.child.executor_incarnation,
                report.child.child_id,
                report.child.conversation_id,
              ],
            )
          )
            return;
          report_move_setup(
            tx,
            report.child.conversation_id,
            report.step,
            this.clock.now().getTime(),
          );
          if (assignment[0] === 'ticket')
            declare_worker_setup(tx, {
              ticket_id: assignment[1],
              conversation_id: report.child.conversation_id,
              machine_id: this.machine_id,
              step: report.step,
              now: this.clock.nowUnix(),
            });
        });
      },
      LIVE_READ_SERVER_LIMIT_SECONDS,
      true,
    );
  }
  retire_workspace(
    identity: ExecutorIdentity,
    state: WireValue<'WorkspaceState'>,
    workspace: WireValue<'WorkspaceIdentity'>,
    options: { replay?: boolean } = {},
  ): Promise<WireValue<'WorkspaceRetirementReply'>> {
    return this.owned_work(
      async () =>
        this.database.write((tx) => {
          require_current_incarnation_in_transaction(
            tx,
            identity.executor_incarnation,
            this.machine_id,
          );
          this.require_ticket_owner(tx, state.ticket_id);
          const proof = tx.get(
            'SELECT 1 FROM ticket_workspace_conversations w ' +
              'JOIN executor_child_assignments a ON a.conversation_id=w.conversation_id ' +
              'JOIN executor_incarnations i ON i.executor_incarnation=a.executor_incarnation ' +
              'WHERE w.ticket_id=? AND w.machine_id=? AND w.workspace_id=? AND i.machine_id=? LIMIT 1',
            [state.ticket_id, workspace.machine_id, workspace.workspace_id, this.machine_id],
          );
          // The Ticket is this machine's. A workspace that the server registered before
          // the switch to machine code stays with the server's own cleanup.
          if (proof === undefined) return { authorized: false };
          // An adopted workspace can still hold a process that the server started before the
          // switch. Its record must be retired before the folder goes.
          if (
            !options.replay &&
            tx.get(
              "SELECT 1 FROM ticket_workspace_conversations w JOIN conversation_child_custody c USING(conversation_id) WHERE w.machine_id=? AND w.workspace_id=? AND c.state!='retired' LIMIT 1",
              [workspace.machine_id, workspace.workspace_id],
            ) !== undefined
          )
            return { authorized: false };
          return {
            authorized: options.replay
              ? has_committed_retirement(tx, state, workspace)
              : authorize_retirement(tx, state, this.clock.nowUnix(), workspace),
          };
        }),
      LIVE_READ_SERVER_LIMIT_SECONDS,
      true,
    );
  }
  private require_ticket_owner(sql: ReadSql, ticket_id: string): void {
    const owned = sql.get(
      'SELECT 1 FROM tickets WHERE id=? AND machine_id=? UNION ALL ' +
        'SELECT 1 FROM executor_child_assignments a JOIN executor_incarnations i ' +
        "USING(executor_incarnation) WHERE a.principal_kind='ticket' AND a.principal_id=? " +
        'AND i.machine_id=? LIMIT 1',
      [ticket_id, this.machine_id, ticket_id, this.machine_id],
    );
    if (owned === undefined)
      throw new PlannerError('agent_forbidden', 'Ticket belongs to another machine');
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
    if (after < 0 || !(wait_seconds >= 0 && wait_seconds <= COMMAND_POLL_HOLD_SECONDS))
      throw new PlannerError('validation', 'invalid poll cursor or wait');
    if (!this.update_barrier) return this.mailbox.poll(identity, after, wait_seconds, options);
    return this.update_barrier.operation((admitted) => {
      if (!admitted) throw new PlannerError('retry_later', 'Home update in progress.');
      return this.mailbox.poll(identity, after, wait_seconds, options);
    });
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
      true,
    );
  }
  async accept_items(
    identity: ExecutorIdentity,
    conversation_id: string,
    batch: WireValue<'ItemBatch'>,
  ): Promise<WireValue<'ItemBatchReply'>> {
    const reply = await this.owned_work(
      () => this.inbox.accept_batch(conversation_id, batch, identity),
      undefined,
      true,
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
    return this.owned_work(
      () => this.files.upload(identity, sha256, upload, content_length, chunks),
      undefined,
      true,
    );
  }
  private ticket_file_authority(
    identity: ExecutorIdentity,
    manifest: WireValue<'FileManifest'>,
  ): () => void {
    return () =>
      this.database.read((sql) => {
        require_current_incarnation_in_transaction(
          sql,
          identity.executor_incarnation,
          this.machine_id,
        );
        if (manifest.scope === 'manager')
          require_manager_files_machine(sql, manifest.owner_id, this.machine_id);
        else this.require_ticket_owner(sql, manifest.owner_id);
      });
  }
  save_ticket_file(
    identity: ExecutorIdentity,
    sha256: string,
    manifest: WireValue<'FileManifest'>,
    content_length: WireValue<'FileManifest'>['byte_count'] | null,
    chunks: UploadSource,
  ): Promise<void> {
    return this.owned_work(
      () =>
        this.files.save_ticket_file(
          sha256,
          manifest,
          content_length,
          chunks,
          this.ticket_file_authority(identity, manifest),
        ),
      undefined,
      true,
    );
  }
  remove_ticket_file(
    identity: ExecutorIdentity,
    manifest: WireValue<'FileManifest'>,
  ): Promise<void> {
    return this.owned_work(
      () => this.files.remove_ticket_file(manifest, this.ticket_file_authority(identity, manifest)),
      undefined,
      true,
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
  check_preview_current(identity: ExecutorIdentity): void {
    this.database.read((sql) =>
      require_current_incarnation_in_transaction(
        sql,
        identity.executor_incarnation,
        this.machine_id,
      ),
    );
  }
  preview_ticket(identity: ExecutorIdentity, child: WireValue<'ChildReference'>): string {
    this.admit();
    this.database.read((sql) => {
      require_current_incarnation_in_transaction(
        sql,
        identity.executor_incarnation,
        this.machine_id,
      );
      require_incarnation_owner(sql, child.executor_incarnation, this.machine_id);
    });
    const principal = this.runs.authority(child);
    if (principal.kind !== 'ticket')
      throw new PlannerError('agent_forbidden', 'Previews require a Ticket');
    return principal.id;
  }
  async read(
    identity: ExecutorIdentity,
    read: WireValue<'LiveReadRequest'>,
  ): Promise<WireValue<'JsonObject'>> {
    this.admit();
    this.database.read((sql) => {
      require_current_incarnation_in_transaction(
        sql,
        identity.executor_incarnation,
        this.machine_id,
      );
      require_incarnation_owner(sql, read.child.executor_incarnation, this.machine_id);
    });
    if (read.path === '/api/executor/capabilities') {
      this.runs.authority(read.child);
      return { ticket_send_back: true, cli_request: true };
    }
    if (!read.path.startsWith('/') || read.path.startsWith('//'))
      throw new PlannerError('validation', 'an executor read path must be a server path');
    // Ordinary route authority judges the read, as for a local agent.
    this.runs.authority(read.child);
    const principal = this.database.read((sql) => current_agent_owner(sql, read.child));
    const target = new URL(read.path, 'http://executor');
    // The path can carry its own query, as `panels api` sends it.
    const params = new URLSearchParams(target.search);
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
                : typeof element === 'number'
                  ? JSON.stringify(element)
                  : JSON.stringify(element),
        );
    }
    target.search = params.toString();
    const controller = new AbortController();
    const answer = await observe_promise(
      inject_agent_request(
        this.app,
        agent_request_context(principal, read.child.conversation_id),
        { method: 'GET', url: target.pathname + target.search, body: null },
        controller.signal,
      ),
      LIVE_READ_SERVER_LIMIT_SECONDS * 1000,
    );
    if (!answer.completed) {
      controller.abort();
      throw new PlannerError('retry_later', 'the read took too long');
    }
    return agent_response_json(answer.value.statusCode, answer.value.body, answer.value.headers);
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
    const task = this.owned_work(report);
    const answer = await observe_promise(task, LIVE_EVENT_SERVER_LIMIT_SECONDS * 1000);
    if (!answer.completed) {
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
      const drain = (async () => {
        // Admitted saves can still enqueue run bookkeeping from their committed results.
        const retained = await Promise.allSettled(pending);
        const services = [
          this.runs,
          this.catalog,
          this.backend_controls,
          this.inbox,
          this.files,
          this.presence,
        ];
        for (const service of services) void service.stop(options).catch(() => {});
        // Catalog and backend cancellation own their final mailbox withdrawals.
        const mailbox_drain = Promise.allSettled([
          this.catalog.settled(),
          this.backend_controls.settled(),
        ]).then(async () => {
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
        const results = [
          ...retained,
          ...(await Promise.allSettled([
            ...services.map((service) => service.settled()),
            mailbox_drain,
          ])),
        ];
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
      })();
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
