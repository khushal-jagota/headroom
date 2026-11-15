/** One executor owner per destination, shared by startup adoption and new work. */
import type { FastifyInstance } from 'fastify';
import type {
  BackendChildFactory,
  BackendEventSink,
  ChildReference,
  RetainedBackendChildFactory,
  RetainedBackendRun,
} from '../conversation/backends/contracts.ts';
import { BackendSpawnFailed } from '../conversation/backends/contracts.ts';
import type { RemoteCatalogSource } from '../conversation/model_resolution/contracts.ts';
import { retained_open_runs } from '../conversation/runtime/remote.ts';
import { observe_shutdown } from '../conversation/shutdown.ts';
import type { SqliteProcessConversationSystem } from '../conversation/system.ts';
import type { Clock } from '../core/clock.ts';
import type { Database, ReadSql } from '../core/database/contracts.ts';
import type { UpdateBarrier } from '../environments/update_barrier.ts';
import type { MacUpdateServiceContract } from '../environments/updates/mac/contracts.ts';
import { HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';
import { ServerLifecycleError } from '../lifecycle/contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import type { StallSource } from '../stalls/contracts.ts';
import { ticket_id_of } from './routing.ts';
import type { MacRemoteChild } from './vps_remote_child.ts';
import { VpsExecutor } from './vps_transport.ts';
import type { VpsExecutorOptions } from './vps_transport.ts';

export class VpsExecutorRegistry {
  readonly db_path: string;
  private readonly database: Database;
  private readonly clock: Clock;
  private readonly options: VpsExecutorOptions;
  private readonly original_enabled: boolean;
  private readonly owners = new Map<string, VpsExecutor>();
  /** Includes an endpoint whose asynchronous start did not finish. */
  private readonly owned = new Set<VpsExecutor>();
  private readonly pending = new Map<string, Promise<VpsExecutor>>();
  private configuration:
    | {
        core: SqliteProcessConversationSystem;
        app: FastifyInstance;
        barrier: UpdateBarrier | undefined;
      }
    | undefined;
  private updates: MacUpdateServiceContract | undefined;
  private started = false;
  private starting: Promise<void> | undefined;
  private closed = false;
  private actual_stop: Promise<void> | undefined;
  private readonly completion = Promise.withResolvers<void>();

  constructor(
    database: Database,
    clock: Clock,
    options: VpsExecutorOptions & { readonly original_enabled?: boolean },
  ) {
    this.database = database;
    this.clock = clock;
    this.options = options;
    this.db_path = options.db_path;
    this.original_enabled = options.original_enabled ?? false;
    void this.completion.promise.catch(() => {});
  }

  private admit(): void {
    if (this.closed) throw new ServerLifecycleError('Executor registry is closed.');
  }

  private new_owner(machine_id: string): VpsExecutor {
    const owner = new VpsExecutor(this.database, this.clock, { ...this.options, machine_id });
    this.owned.add(owner);
    const configuration = this.configuration;
    if (configuration)
      owner.configure(configuration.core, configuration.app, configuration.barrier);
    if (this.updates) owner.bind_updates(this.updates.for_machine(machine_id));
    return owner;
  }

  /** Run after schema admission and before retained turns enter reconciliation. */
  load(sql: ReadSql): void {
    this.admit();
    for (const { machine_id } of sql.all<{ machine_id: string }>(
      'SELECT machine_id FROM machine_executor_credentials WHERE credential_sha256 IS NOT NULL OR (machine_id=? AND ?)',
      [ORIGINAL_MAC_MACHINE_ID, this.original_enabled ? 1 : 0],
    )) {
      if (machine_id !== HOME_MACHINE_ID && !this.owners.has(machine_id))
        this.owners.set(machine_id, this.new_owner(machine_id));
    }
  }

  get(machine_id: string): VpsExecutor | null {
    return this.owners.get(machine_id) ?? null;
  }

  ensure(machine_id: string): Promise<VpsExecutor> {
    this.admit();
    const pending = this.pending.get(machine_id);
    if (pending) return pending;
    const existing = this.get(machine_id);
    if (existing) return Promise.resolve(existing);
    const creation = Promise.resolve().then(async () => {
      this.admit();
      const registered = this.database.read((sql) =>
        sql.get('SELECT 1 FROM machine_executor_credentials WHERE machine_id=?', [machine_id]),
      );
      if (!registered || machine_id === HOME_MACHINE_ID)
        throw new Error('Unknown executor destination');
      const owner = this.new_owner(machine_id);
      if (this.started) await owner.start();
      this.admit();
      this.owners.set(machine_id, owner);
      return owner;
    });
    this.pending.set(machine_id, creation);
    void creation.then(
      () => this.pending.delete(machine_id),
      () => this.pending.delete(machine_id),
    );
    return creation;
  }

  configure(
    core: SqliteProcessConversationSystem,
    app: FastifyInstance,
    barrier?: UpdateBarrier,
  ): void {
    this.admit();
    this.configuration = { core, app, barrier };
    for (const owner of this.owned) owner.configure(core, app, barrier);
  }

  bind_updates(service: MacUpdateServiceContract): void {
    this.admit();
    this.updates = service;
    for (const owner of this.owned) owner.bind_updates(service.for_machine(owner.machine_id));
  }

  start(): Promise<void> {
    this.admit();
    if (!this.starting) {
      // Later ensures start their endpoint before they publish it in the registry.
      this.started = true;
      this.starting = Promise.all([...this.owners.values()].map((owner) => owner.start())).then(
        () => {},
      );
    }
    return this.starting;
  }

  stop(options: StopOptions): Promise<void> {
    if (!this.actual_stop) {
      this.closed = true;
      this.actual_stop = this.completion.promise;
      const owners = [...this.owned];
      // Each endpoint retains its actual work after its deadline observer expires.
      for (const owner of owners) void owner.stop(options).catch(() => {});
      const pending = [...this.pending.values(), ...(this.starting ? [this.starting] : [])];
      const drain = Promise.allSettled(pending).then(async () => {
        const results = await Promise.allSettled(owners.map((owner) => owner.settled()));
        const failures = results.flatMap((result) =>
          result.status === 'rejected' ? [result.reason] : [],
        );
        if (failures.length)
          throw new AggregateError(failures, 'Executor registry shutdown failed.');
      });
      void drain.then(this.completion.resolve, this.completion.reject);
    }
    return observe_shutdown(
      this.completion.promise,
      options,
      'Executor registry work remains unfinished at the shutdown deadline.',
    );
  }

  settled(): Promise<void> {
    return this.completion.promise;
  }

  configured(): boolean {
    return this.get(ORIGINAL_MAC_MACHINE_ID) !== null;
  }

  health_payload(): Record<string, unknown> | null {
    return this.get(ORIGINAL_MAC_MACHINE_ID)?.health_payload() ?? null;
  }

  is_present(machine_id: string): boolean {
    return this.get(machine_id)?.presence.is_present() ?? false;
  }

  catalog_for(machine_id: string): RemoteCatalogSource | null {
    return this.get(machine_id)?.catalog ?? null;
  }

  stall_sources(): readonly StallSource[] {
    return [...this.owners].flatMap(([machine_id, owner]) =>
      owner.stall_sources().map((source) => ({
        tracked_waits: (sql: ReadSql, now_ms: number) =>
          source.tracked_waits(sql, now_ms).map((wait) => ({ ...wait, machine_id })),
      })),
    );
  }

  retained_runs(): readonly RetainedBackendRun[] {
    return [...this.owners.values()].flatMap((owner) => owner.runs.retained_runs());
  }

  open_runs() {
    const factory = this.as_factory();
    return retained_open_runs({ claude: factory, codex: factory, hermes: factory });
  }

  private owner_of(source: ChildReference): VpsExecutor | null {
    const row = this.database.read((sql) =>
      sql.get<{ machine_id: string }>(
        'SELECT i.machine_id FROM executor_incarnations i JOIN executor_child_assignments a USING(executor_incarnation) WHERE i.executor_incarnation=? AND a.child_id=? AND a.conversation_id=?',
        [source.executor_incarnation, source.child_id, source.conversation_id],
      ),
    );
    return row ? this.get(row.machine_id) : null;
  }

  recover(
    reference: ChildReference,
    options: { event_sink?: BackendEventSink | null } = {},
  ): MacRemoteChild | null {
    return this.owner_of(reference)?.runs.recover(reference, options) ?? null;
  }

  create(options: Parameters<BackendChildFactory>[0]): MacRemoteChild {
    this.admit();
    const ticket_id = ticket_id_of(options.resolved_start);
    const row = this.database.read((sql) =>
      sql.get<{ machine_id: string }>('SELECT machine_id FROM tickets WHERE id=?', [ticket_id]),
    );
    const owner = row && row.machine_id !== HOME_MACHINE_ID ? this.get(row.machine_id) : null;
    if (!owner) throw new BackendSpawnFailed('Ticket executor is unavailable');
    return owner.runs.create(options);
  }

  as_factory(): RetainedBackendChildFactory {
    return Object.assign((options: Parameters<BackendChildFactory>[0]) => this.create(options), {
      recover: this.recover.bind(this),
      retained_runs: this.retained_runs.bind(this),
    });
  }
}
