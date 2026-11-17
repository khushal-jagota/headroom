/** Non-Home Ticket children cross this seam. Recovery never allocates a local child. */
import { BackendSpawnFailed } from '../conversation/backends/contracts.ts';
import { PYTHON_MAC_BACKEND_KEYS } from './machine_drivers.ts';
import type {
  BackendChild,
  BackendChildFactory,
  BackendEventSink,
  ChildReference,
  RetainedBackendChildFactory,
  RetainedBackendRun,
} from '../conversation/backends/contracts.ts';
import type { ResolvedConversationStart } from '../conversation/contracts.ts';
import type { Database } from '../core/database/contracts.ts';
import { canonical_principal } from '../agent_identity/data.ts';
import { authenticated_local_child } from '../agent_identity/launch.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';

export function ticket_id_of(start: ResolvedConversationStart): string | null {
  const environment = Object.fromEntries(
    start.role_materials?.identity_environment_variables ?? [],
  );
  return environment.PLAN_ACTOR === 'worker' ? (environment.PLAN_TICKET_ID ?? null) : null;
}
export class LocaleBackendChildFactory {
  private readonly database: Database;
  private readonly local_factory: BackendChildFactory;
  private readonly remote_factory: RetainedBackendChildFactory;
  constructor(
    database: Database,
    local_factory: BackendChildFactory,
    remote_factory: RetainedBackendChildFactory,
  ) {
    this.database = database;
    this.local_factory = local_factory;
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
    const remote = this.database.read((sql) => {
      const principal = canonical_principal(sql, options.resolved_start.conversation_id);
      const row =
        principal?.kind === 'ticket'
          ? sql.get<{ machine_id: string }>('SELECT machine_id FROM tickets WHERE id=?', [
              principal.id,
            ])
          : undefined;
      return row !== undefined && row.machine_id !== HOME_MACHINE_ID;
    });
    if (remote && !PYTHON_MAC_BACKEND_KEYS.has(options.resolved_start.backend_key))
      throw new BackendSpawnFailed(
        `The Ticket's machine cannot run the ${options.resolved_start.backend_key} backend`,
      );
    const child = (remote ? this.remote_factory : this.local_factory)(options);
    return remote ? child : authenticated_local_child(child, this.database);
  }
  /** The existing core consumes a callable factory with retained recovery methods. */
  as_factory(): RetainedBackendChildFactory {
    return Object.assign((options: Parameters<BackendChildFactory>[0]) => this.create(options), {
      recover: this.recover.bind(this),
      retained_runs: this.retained_runs.bind(this),
    });
  }
}
