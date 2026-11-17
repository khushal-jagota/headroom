/** One independent Mac invocation. It keeps spool custody until every producer actually joins. */
import { randomUUID } from 'node:crypto';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import { create_worker_supervision_runtime } from '../../../worker_supervision/composition.ts';
import { reconcile } from '../../../worker_supervision/inspection.ts';
import { join } from 'node:path';
import { migrate_once } from '../legacy_worker_cleanup.ts';
import { read_cleanup_commands } from '../cleanup_reconciliation.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../../../lifecycle/configuration.ts';
import { ServerLifecycleError, ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import {
  observe_shutdown,
  is_shutdown_business_cancellation,
} from '../../../conversation/shutdown.ts';
import { ConversationMessageFiles } from '../../../conversation/record/message_files.ts';
import { PROTOCOL_VERSION, SPOOL_FORMAT_VERSION } from '../../../execution_configuration.ts';
import { parse_identity } from '../../protocol.ts';
import { credential_path } from '../../credential_file.ts';
import { ExecutorValidationError } from '../../protocol_errors.ts';
import { mac_runtime_settings } from '../../../core/config.ts';
import { handoff_identity } from '../runtime/handoff.ts';
import { read_record } from '../runtime/records.ts';
import type { ExecutorIdentity } from '../../../execution_identity.ts';
import { validate_executor_launch } from '../configuration.ts';
import {
  expand_executor_path,
  legacy_process_inspector,
  mac_agent_process_platform,
} from '../platform.ts';
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
import { failure_detail } from '../failure_detail.ts';
import type {
  MacHostOptions,
  MacHostBindings,
  MacHostContext,
  MacOwnedService,
  MacHostUpdates,
} from './contracts.ts';

/** Startup failure cleanup stays best-effort; the startup error is still rethrown. */
function startup_cleanup_failed(error: unknown): void {
  console.warn('Mac host startup cleanup failed:', failure_detail(error));
}
export class MacService {
  readonly configuration: MacHostOptions;
  readonly bindings: MacHostBindings;
  readonly outbox: OutboxStore;
  readonly journal: AsyncCommandJournal;
  readonly child_processes = new ChildProcesses();
  readonly one_shot_children = new OneShotChildren(this.child_processes);
  readonly worker_supervision = create_worker_supervision_runtime({
    one_shot_children: this.one_shot_children,
  });
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
    this.presence = new MacPresence(identity, Date.now, process.pid, () => ({
      ...outbox.delivery_status(),
    }));
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
      workspace_git: {
        children: this.one_shot_children,
        environment: adapters.environment,
      },
      register_workspace: (child, workspace) => this.client.register_workspace(child, workspace),
      retire_workspace: (state, workspace) => this.client.retire_workspace(state, workspace),
      retirement_is_committed: (state, workspace) =>
        this.client.retire_workspace(state, workspace, { replay: true }),
      cleanup: {
        runtime: this.worker_supervision,
        read_commands: read_cleanup_commands(journal),
        files: this.files,
        inspect_legacy: legacy_process_inspector(this.one_shot_children),
      },
      native: {
        paths: configuration.paths,
        workspace_home: expand_executor_path('~', adapters.environment),
        factory: (start) => adapters.factory(start),
        native_skill_home: (backend) => adapters.native_skill_home(backend),
        files: this.files,
        skills: this.skills,
        message_files: new ConversationMessageFiles(configuration.paths.message_files_anchor),
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
      worker_supervision: this.worker_supervision,
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
      workspace_root: configuration.paths.artifacts,
      presence: this.presence,
      outbox,
      journal,
      runner: this.runner,
      updates: this.updates,
      driver_kinds: adapters.drivers.map((driver) => driver.driver_kind),
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
          now_ms: () => Date.now(),
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
        await service.stop(cleanup_options).catch(startup_cleanup_failed);
        await service.settled().catch(startup_cleanup_failed);
      } else {
        for (const result of await Promise.allSettled([
          files.stop(cleanup_options),
          update_files.stop(cleanup_options),
        ]))
          if (result.status === 'rejected') startup_cleanup_failed(result.reason);
        for (const result of await Promise.allSettled([files.settled(), update_files.settled()]))
          if (result.status === 'rejected') startup_cleanup_failed(result.reason);
        if (outbox) {
          await outbox.stop(cleanup_options).catch(startup_cleanup_failed);
          await outbox.settled().catch(startup_cleanup_failed);
        }
      }
      throw error;
    }
  }
  private failed(error: unknown): void {
    this.failures.add(error);
    this.request_exit(1);
  }
  start(): Promise<void> {
    if (this.options) return Promise.reject(new ServerLifecycleError('Mac host is shutting down.'));
    this.starting ??= (async () => {
      await this.client.ensure_credential(credential_path(this.configuration.paths.root));
      this.controller.signal.throwIfAborted();
      try {
        await migrate_once(
          this.configuration.paths.run,
          {
            runtime: this.worker_supervision,
            files: this.files,
            inspect_legacy: legacy_process_inspector(this.one_shot_children),
            platform: {
              snapshot: (signal) =>
                mac_agent_process_platform(this.one_shot_children).snapshot(signal),
              signal: (target, signum, signal) =>
                mac_agent_process_platform(this.one_shot_children).signal(target, signum, signal),
            },
            warn: console.warn,
          },
          this.controller.signal,
        );
      } catch (error) {
        this.controller.signal.throwIfAborted();
        if (
          !(error instanceof SyntaxError) &&
          !(error instanceof ExecutorValidationError) &&
          !(error instanceof Error && 'code' in error)
        )
          throw error;
        console.warn('Legacy cleanup compatibility pass:', error);
      }
      const reconciliation = await reconcile(
        join(this.configuration.paths.run, 'worker-processes'),
        { runtime: this.worker_supervision },
      );
      for (const owned of reconciliation.records)
        if (!owned.result.complete)
          console.warn(
            `Worker cleanup remains unproven ticket=${owned.ticket_id} conversation=${owned.conversation_id} record=${owned.record_path}: ${owned.result.reason}`,
          );
      await this.runner.refresh_worker_cleanup({ revoke_pending: true });
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
    ).catch((error: unknown) => console.error('Mac host stop failed:', failure_detail(error)));
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
    await this.join([
      (async () => {
        // stop() observes the deadline. File owners wait for actual metadata cleanup.
        void this.worker_supervision.close(this.options!).catch(() => {});
        await this.worker_supervision.shutdown_settled;
      })(),
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
