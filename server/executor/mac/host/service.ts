/** One independent Mac invocation. It keeps spool custody until every producer actually joins. */
import { randomUUID } from 'node:crypto';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../../../lifecycle/configuration.ts';
import { ServerLifecycleError, ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import {
  observe_shutdown,
  is_shutdown_business_cancellation,
} from '../../../conversation/shutdown.ts';
import { ConversationMessageFiles } from '../../../conversation/record/message_files.ts';
import { PROTOCOL_VERSION, SPOOL_FORMAT_VERSION } from '../../wire.generated.ts';
import { parse_identity } from '../../protocol.ts';
import { mac_runtime_settings } from '../../../core/config.ts';
import { handoff_identity } from '../runtime/handoff.ts';
import { read_record } from '../runtime/records.ts';
import type { ExecutorIdentity } from '../../../execution_identity.ts';
import { validate_executor_launch } from '../configuration.ts';
import { expand_executor_path } from '../platform.ts';
import { FilesService } from '../files.ts';
import { MacSkillsPublisher } from '../skills/index.ts';
import { OutboxStore } from '../storage/outbox.ts';
import { open_command_journal } from '../storage/journal.ts';
import type { AsyncCommandJournal } from '../storage/journal_contracts.ts';
import { MacRunner } from '../runner/index.ts';
import { MacMailbox } from '../mailbox/index.ts';
import { MacHttpClient } from '../transport/http.ts';
import { MacFileTransport } from '../transport/files.ts';
import { MacPresence, MacLeaseLoop } from '../transport/presence.ts';
import { MacLiveSender } from '../transport/live.ts';
import { MacOutboxSender } from '../transport/outbox.ts';
import { MacInputs } from '../inputs.ts';
import { AgentGateway } from '../ipc/gateway.ts';
import { GatewayOutbox } from '../ipc/outbox.ts';
import type { MacCatalog } from '../catalog/index.ts';
import type {
  MacHostOptions,
  MacHostBindings,
  MacHostContext,
  MacOwnedService,
  MacHostUpdates,
  MacAgentProcesses,
} from './contracts.ts';

export class MacService {
  readonly configuration: MacHostOptions;
  readonly bindings: MacHostBindings;
  readonly outbox: OutboxStore;
  readonly journal: AsyncCommandJournal;
  readonly child_processes = new ChildProcesses();
  readonly one_shot_children = new OneShotChildren(this.child_processes);
  readonly files: FilesService;
  readonly update_files: FilesService;
  readonly runtime: ReturnType<typeof mac_runtime_settings>;
  readonly skills: MacSkillsPublisher;
  readonly identity: ExecutorIdentity;
  readonly client: MacHttpClient;
  readonly transfer: MacFileTransport;
  readonly presence: MacPresence;
  readonly runner: MacRunner;
  readonly mailbox: MacMailbox;
  readonly gateway: AgentGateway;
  readonly gateway_outbox: GatewayOutbox;
  readonly catalog: MacCatalog;
  readonly live: MacLiveSender;
  readonly lease: MacLeaseLoop;
  readonly sender: MacOutboxSender;
  readonly updates: MacHostUpdates;
  readonly processes: MacAgentProcesses | null;
  private readonly controller = new AbortController();
  private starting: Promise<void> | undefined;
  private cleanup: Promise<void> | undefined;
  private options: StopOptions | undefined;
  private exit_code = 0;
  private readonly failures = new Set<unknown>();
  private readonly stopping = new Set<MacOwnedService>();
  private resolve_actual!: () => void;
  private reject_actual!: (error: unknown) => void;
  private readonly actual = new Promise<void>((resolve, reject) => {
    this.resolve_actual = resolve;
    this.reject_actual = reject;
  });
  private constructor(
    configuration: MacHostOptions,
    bindings: MacHostBindings,
    outbox: OutboxStore,
    journal: AsyncCommandJournal,
    identity: ExecutorIdentity,
    files: FilesService,
    update_files: FilesService,
    runtime: ReturnType<typeof mac_runtime_settings>,
    replay_pending: boolean,
  ) {
    this.configuration = configuration;
    this.bindings = bindings;
    this.outbox = outbox;
    this.journal = journal;
    this.files = files;
    this.update_files = update_files;
    this.runtime = runtime;
    void this.actual.catch(() => {});
    this.identity = identity;
    const on_failure = (error: unknown) => this.failed(error);
    this.skills = new MacSkillsPublisher(on_failure);
    this.processes = bindings.processes({
      child_processes: this.child_processes,
      one_shot_children: this.one_shot_children,
      files: this.files,
      paths: configuration.paths,
    });
    this.presence = new MacPresence(identity);
    this.client =
      bindings.client?.(
        configuration.panels_server_url,
        configuration.executor_id,
        identity,
        configuration.http ?? {},
      ) ??
      new MacHttpClient(
        configuration.panels_server_url,
        configuration.executor_id,
        identity,
        configuration.http,
      );
    this.transfer = new MacFileTransport(this.client, this.files);
    this.live = new MacLiveSender(this.client);
    const adapters = bindings.adapters(this);
    this.catalog = bindings.catalog(adapters, this, on_failure);
    this.runner = new MacRunner({
      incarnation: identity.executor_incarnation,
      journal,
      outbox,
      live: this.live,
      inputs: new MacInputs(this.transfer, this.files, configuration.paths, on_failure),
      now_ms: Date.now,
      new_update_id: () => randomUUID().replaceAll('-', ''),
      native: {
        paths: configuration.paths,
        workspace_home: expand_executor_path('~', adapters.environment),
        factory: (start) => adapters.factory(start),
        native_skill_home: (backend) => adapters.native_skill_home(backend),
        files: this.files,
        skills: this.skills,
        message_files: new ConversationMessageFiles(configuration.paths.message_files_anchor),
        record_children: () => this.record_children(),
        on_failure,
      },
    });
    if (replay_pending) this.runner.begin_planned_replay();
    this.mailbox = new MacMailbox({
      client: this.client,
      runner: this.runner,
      journal,
      catalog: this.catalog,
      incarnation: identity.executor_incarnation,
      on_failure,
    });
    this.gateway_outbox = new GatewayOutbox(outbox);
    this.gateway = new AgentGateway({
      runner: this.runner,
      outbox: this.gateway_outbox,
      presence: this.presence,
      client: this.client,
      request_exit: () => this.request_exit(0),
      settle_catalogs: () => this.catalog.settle(),
      on_failure,
    });
    const context: MacHostContext = {
      identity,
      runtime,
      child_processes: this.child_processes,
      one_shot_children: this.one_shot_children,
      client: this.client,
      files: this.files,
      update_files: this.update_files,
      transfer: this.transfer,
      outbox,
      gateway_outbox: this.gateway_outbox,
      journal,
      runner: this.runner,
      presence: this.presence,
      paths: configuration.paths,
      request_exit: (code) => this.request_exit(code),
      on_failure,
    };
    this.sender = new MacOutboxSender({
      store: outbox,
      client: this.client,
      files: this.transfer,
      notify_confirmed: (receipt) => this.gateway_outbox.notify_confirmed(receipt),
      warn: console.warn,
      on_failure: (error) => on_failure(error instanceof Error ? error : new Error(String(error))),
    });
    this.updates = bindings.updates(context);
    this.lease = new MacLeaseLoop({
      client: this.client,
      presence: this.presence,
      outbox,
      journal,
      runner: this.runner,
      updates: this.updates,
      record_children: () => this.record_children(),
      replaced: () => this.request_exit(1),
      on_failure: (error) => on_failure(error instanceof Error ? error : new Error(String(error))),
      warn: console.warn,
    });
  }
  static async open(
    configuration: MacHostOptions,
    bindings: MacHostBindings,
    cleanup_options: StopOptions,
  ): Promise<MacService> {
    validate_executor_launch(
      configuration.executor_id,
      configuration.revision,
      configuration.panels_server_url,
    );
    let identity = parse_identity(
      configuration.identity ?? {
        protocol_version: PROTOCOL_VERSION,
        application_revision: configuration.revision,
        spool_format_version: SPOOL_FORMAT_VERSION,
        executor_incarnation: randomUUID(),
        connection_generation: 0,
      },
    );
    let service: MacService | undefined, startup_failure: Error | undefined;
    const on_failure = (error: Error) => {
      startup_failure ??= error;
      service?.failed(error);
    };
    const files = new FilesService(),
      update_files = new FilesService();
    files.on_failure(on_failure);
    update_files.on_failure(on_failure);
    const runtime = configuration.runtime ?? mac_runtime_settings();
    let outbox: OutboxStore | undefined;
    try {
      let replay_pending = false;
      if (runtime.handoff_path !== null) {
        identity = await handoff_identity(identity, update_files, runtime.handoff_path);
        replay_pending =
          (await read_record(update_files, runtime.handoff_path))?.replay_pending === true;
      }
      outbox = await OutboxStore.open(
        configuration.paths.spool,
        {
          monotonic: () => performance.now() / 1000,
          now_ns: () => BigInt(Date.now()) * 1_000_000n,
        },
        cleanup_options,
        on_failure,
      );
      const journal = await open_command_journal(
        outbox,
        identity.executor_incarnation,
        {
          outputs_module: new URL('../runner/logic/outcomes.ts', import.meta.url),
          now_ms: () => BigInt(Date.now()),
          on_failure,
        },
        cleanup_options,
      );
      service = new MacService(
        configuration,
        bindings,
        outbox,
        journal,
        identity,
        files,
        update_files,
        runtime,
        replay_pending,
      );
      if (startup_failure) throw startup_failure;
      return service;
    } catch (error) {
      if (service) {
        await service.stop(cleanup_options).catch(() => {});
        await service.settled().catch(() => {});
      } else {
        await Promise.allSettled([files.stop(cleanup_options), update_files.stop(cleanup_options)]);
        await Promise.allSettled([files.settled(), update_files.settled()]);
        if (outbox) {
          await outbox.stop(cleanup_options).catch(() => {});
          await outbox.settled().catch(() => {});
        }
      }
      throw error;
    }
  }
  private record_children(): Promise<void> {
    return this.processes?.record_children() ?? Promise.resolve();
  }
  private failed(error: unknown): void {
    this.failures.add(error);
    this.request_exit(1);
  }
  start(): Promise<void> {
    if (this.options) return Promise.reject(new ServerLifecycleError('Mac host is shutting down.'));
    this.starting ??= (async () => {
      await this.processes?.end_orphans(this.controller.signal);
      this.controller.signal.throwIfAborted();
      await this.gateway.start(this.configuration.paths.ipc_socket);
      this.controller.signal.throwIfAborted();
      await this.mailbox.start(() => {
        this.controller.signal.throwIfAborted();
        this.updates.start();
        this.sender.start();
        this.lease.start();
      });
    })();
    void this.starting.catch((error) => {
      if (!this.controller.signal.aborted)
        this.failed(error instanceof Error ? error : new Error(String(error)));
    });
    return this.starting;
  }
  request_exit(code: number): void {
    if (code !== 0 && this.exit_code === 0) this.exit_code = code;
    void this.stop(
      this.options ?? { deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS },
    ).catch(() => {});
  }
  async run(): Promise<number> {
    try {
      await this.start();
    } catch (error) {
      if (!(this.controller.signal.aborted && is_shutdown_business_cancellation(error)))
        throw error;
    }
    await this.actual;
    return this.exit_code;
  }
  private begin_stop(owner: MacOwnedService): void {
    this.stopping.add(owner);
    try {
      void owner.stop(this.options!).catch((error) => {
        if (
          !(error instanceof ShutdownDeadlineExceeded) &&
          !(
            error instanceof ServerLifecycleError && error.cause instanceof ShutdownDeadlineExceeded
          )
        )
          this.failures.add(error);
      });
    } catch (error) {
      this.failures.add(error);
    }
  }
  stop(options: StopOptions): Promise<void> {
    this.options ??= options;
    if (!this.cleanup) {
      this.cleanup = Promise.resolve().then(() => this.drain());
      void this.cleanup.then(this.resolve_actual, this.reject_actual);
      this.runner.seal();
      this.child_processes.seal();
      this.one_shot_children.seal();
      this.controller.abort();
      for (const owner of [
        this.gateway,
        this.mailbox,
        this.lease,
        this.updates,
        this.sender,
        this.live,
        this.catalog,
        this.runner,
        this.client,
        ...(this.processes === null ? [] : [this.processes]),
      ])
        this.begin_stop(owner);
    } else for (const owner of this.stopping) this.begin_stop(owner);
    return observe_shutdown(
      this.actual,
      options,
      'Mac host ownership remains unfinished at the shutdown deadline.',
    );
  }
  private async join(operations: readonly Promise<unknown>[]): Promise<void> {
    for (const result of await Promise.allSettled(operations))
      if (
        result.status === 'rejected' &&
        !(this.controller.signal.aborted && is_shutdown_business_cancellation(result.reason))
      )
        this.failures.add(result.reason);
  }
  private async drain(): Promise<void> {
    await this.join([
      this.starting ?? Promise.resolve(),
      ...[...this.stopping].map((owner) => owner.settled()),
      this.child_processes.settled(),
      this.one_shot_children.settled(),
    ]);
    for (const owner of [this.update_files, this.files, this.journal, this.outbox]) {
      this.begin_stop(owner);
      await this.join([owner.settled()]);
    }
    if (this.failures.size)
      throw new AggregateError(
        [...this.failures],
        'Mac host cleanup failed after actual settlement.',
      );
  }
  settled(): Promise<void> {
    return this.actual;
  }
}
