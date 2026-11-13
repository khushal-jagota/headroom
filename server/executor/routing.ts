/** Only Mac Ticket children cross this seam. Recovery never allocates a local child. */
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
    const ticket_id = ticket_id_of(options.resolved_start);
    const remote = this.database.read(
      (sql) =>
        ticket_id !== null &&
        sql.get<{ locale: string }>('SELECT locale FROM tickets WHERE id=?', [ticket_id])
          ?.locale === 'macos',
    );
    return (remote ? this.remote_factory : this.local_factory)(options);
  }
  /** The existing core consumes a callable factory with retained recovery methods. */
  as_factory(): RetainedBackendChildFactory {
    return Object.assign((options: Parameters<BackendChildFactory>[0]) => this.create(options), {
      recover: this.recover.bind(this),
      retained_runs: this.retained_runs.bind(this),
    });
  }
}
