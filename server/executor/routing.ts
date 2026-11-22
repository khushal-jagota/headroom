/** Every conversation's child runs through its machine's code. Recovery never allocates a local child. */
import { BackendSpawnFailed } from '../conversation/backends/contracts.ts';
import { machine_can_run, machine_driver_kinds } from './machine_drivers.ts';
import type {
  BackendChild,
  BackendChildFactory,
  BackendEventSink,
  ChildReference,
  RetainedBackendChildFactory,
  RetainedBackendRun,
} from '../conversation/backends/contracts.ts';
import type { Database } from '../core/database/contracts.ts';
import { conversation_machine_id } from '../machines/conversation_machine.ts';

export class LocaleBackendChildFactory {
  private readonly database: Database;
  private readonly remote_factory: RetainedBackendChildFactory;
  constructor(database: Database, remote_factory: RetainedBackendChildFactory) {
    this.database = database;
    this.remote_factory = remote_factory;
  }
  retained_runs(): readonly RetainedBackendRun[] {
    return this.remote_factory.retained_runs();
  }
  recover(
    reference: ChildReference,
    options: { event_sink?: BackendEventSink | null } = {},
  ): BackendChild | null {
    return this.remote_factory.recover(reference, options);
  }
  create(options: Parameters<BackendChildFactory>[0]): BackendChild {
    const driver_kinds = this.database.read((sql) => {
      const machine_id = conversation_machine_id(sql, options.resolved_start.conversation_id);
      return machine_id === null ? null : machine_driver_kinds(sql, machine_id);
    });
    if (driver_kinds === null)
      throw new BackendSpawnFailed('The conversation has no execution machine');
    if (!machine_can_run(driver_kinds, options.resolved_start.backend_key))
      throw new BackendSpawnFailed(
        `The conversation's machine cannot run the ${options.resolved_start.backend_key} backend`,
      );
    return this.remote_factory(options);
  }
  /** The existing core consumes a callable factory with retained recovery methods. */
  as_factory(): RetainedBackendChildFactory {
    return Object.assign((options: Parameters<BackendChildFactory>[0]) => this.create(options), {
      recover: this.recover.bind(this),
      retained_runs: this.retained_runs.bind(this),
    });
  }
}
