import { authenticated_local_child } from '../agent_identity/launch.ts';
/** The only adapter-selection point, with explicit environment composition still required. */
import type { ConversationCompositionContext } from '../core/start.ts';
import { dirname, join } from 'node:path';
import { expand_path } from '../environments/paths.ts';
import { resolve_path_sync } from '../core/path_resolution.ts';
import type {
  BackendChildFactory,
  RetainedBackendChildFactory,
} from '../conversation/backends/contracts.ts';
import type { BackendHttpRuntime } from '../conversation/snapshot/contracts.ts';
import type { UsageTranscriptSource } from '../conversation/usage/contracts.ts';
import {
  conversation_execution_refusal,
  local_ticket_execution_refusal,
  canonical_ticket_execution_refusal,
} from '../tickets/execution_admission.ts';
import { LocaleBackendChildFactory } from '../executor/routing.ts';
import { configure_conversation_start } from '../runtime/conversation_start.ts';
import type { StallSource } from '../stalls/contracts.ts';
import type { ProductionServerDependencies } from './production.ts';
import { accessSync, constants, statSync } from 'node:fs';
import { BUILT_IN_DRIVERS } from '../drivers/built_in.ts';
import type { AnyAgentDriver } from '../drivers/core/driver.ts';
import type { DriverHost } from '../drivers/core/host.ts';
import {
  default_instance_configs,
  driver_registry_from_entries,
} from '../drivers/core/registry.ts';
import { KNOWN_BACKEND_INSTANCE_IDS } from '../drivers/built_in_manifests.ts';
import type { DriverRegistry, RegistryEntry } from '../drivers/core/registry.ts';
import { create_production_backend_http } from './production_http.ts';
import { applicationRootFromEnvironment, HOST } from '../core/config.ts';
import { create_production_environment_operations } from '../environments/production.ts';

export type ProductionBackendChildFactory = BackendChildFactory &
  Partial<Pick<RetainedBackendChildFactory, 'recover' | 'retained_runs'>>;
export interface ProductionBindingContext extends ConversationCompositionContext {
  readonly driver_host: DriverHost;
  /** The live instances: the HTTP runtime's cards, sign-ins and usage. */
  readonly registry: DriverRegistry;
}
export interface ProductionBackendBindings {
  readonly drivers: readonly AnyAgentDriver[];
  /** One host per server invocation. */
  readonly driver_host: (context: ConversationCompositionContext) => DriverHost;
  readonly http: (context: ProductionBindingContext) => BackendHttpRuntime;
  /** The endpoint's actual registered sources, alongside checker-owned WAITS/epochs. */
  readonly stall_sources: (context: ProductionBindingContext) => readonly StallSource[];
}
/** The registry built from the drivers, and the host it was built with. */
export interface PreparedProductionBackends {
  readonly registry: DriverRegistry;
  readonly host: DriverHost;
}
export function require_backend_bindings(
  value: Partial<ProductionBackendBindings>,
): ProductionBackendBindings {
  if (!Array.isArray(value.drivers) || value.drivers.length === 0)
    throw new Error('Production backend drivers are required');
  // Stored conversations may name any built-in backend, and keys stay closed until the
  // Database stage, so every built-in backend needs a driver.
  const ids = new Set<string>(default_instance_configs(value.drivers).keys());
  const missing = KNOWN_BACKEND_INSTANCE_IDS.filter((id) => !ids.has(id)).sort();
  if (missing.length)
    throw new Error(`Missing production backend factories: ${missing.join(', ')}`);
  if (typeof value.driver_host !== 'function')
    throw new Error('Production backend driver host binding is required');
  if (typeof value.http !== 'function' || typeof value.stall_sources !== 'function')
    throw new Error('Production backend HTTP and registered stall-source bindings are required');
  return value as ProductionBackendBindings;
}
export function compose_production_backends(
  context: ConversationCompositionContext,
  prepared: PreparedProductionBackends,
  bindings: ProductionBackendBindings,
) {
  // A broken install still fails startup.
  const unavailable = prepared.registry
    .list()
    .flatMap((entry) => (entry.kind === 'live' ? [] : [entry]));
  if (unavailable.length)
    throw new AggregateError(
      unavailable.map((entry) => entry.cause ?? new Error(entry.reason)),
      unavailable.map((entry) => entry.reason).join('\n'),
    );
  // Only an invocation-owned executor supplies remote execution. Persisted presence alone does not.
  const policy =
    context.executor == null ? local_ticket_execution_refusal : canonical_ticket_execution_refusal;
  const execution_refusal = (id: string) =>
    context.database.read((sql) => conversation_execution_refusal(sql, id, policy));
  configure_conversation_start(context.database, {
    db_path: context.config.db_path,
    workspace_root: context.config.workspace_root,
    execution_refusal: policy,
  });
  const require_execution = (id: string) => {
    const refusal = execution_refusal(id);
    if (refusal !== null)
      throw new Error(
        `Execution admission invariant violated: blocked conversation ${id} reached a local backend binding`,
        { cause: refusal },
      );
  };
  const local_only = (factory: ProductionBackendChildFactory): ProductionBackendChildFactory => {
    const guarded: ProductionBackendChildFactory = (values) => {
      require_execution(values.resolved_start.conversation_id);
      return authenticated_local_child(factory(values), context.database);
    };
    const recover = factory.recover,
      runs = factory.retained_runs;
    if (typeof recover === 'function')
      Object.assign(guarded, {
        recover(
          reference: Parameters<RetainedBackendChildFactory['recover']>[0],
          options?: Parameters<RetainedBackendChildFactory['recover']>[1],
        ) {
          require_execution(reference.conversation_id);
          const child = recover.call(factory, reference, options);
          return child === null ? null : authenticated_local_child(child, context.database);
        },
      });
    if (typeof runs === 'function')
      Object.assign(guarded, {
        retained_runs: () => runs.call(factory),
      });
    return guarded;
  };
  const entries = prepared.registry.list().map((entry): RegistryEntry => {
    if (entry.kind !== 'live') return entry;
    const local = entry.instance.child_factory;
    const child_factory =
      context.executor == null
        ? local_only(local)
        : new LocaleBackendChildFactory(
            context.database,
            local,
            context.executor.as_factory(),
          ).as_factory();
    return { ...entry, instance: { ...entry.instance, child_factory } };
  });
  const registry = driver_registry_from_entries([...prepared.registry.drivers.values()], entries);
  const shared: ProductionBindingContext = { ...context, driver_host: prepared.host, registry };
  const supplied_http = bindings.http(shared);
  const http: BackendHttpRuntime =
    context.executor == null
      ? supplied_http
      : {
          database: supplied_http.database,
          backend_snapshots: supplied_http.backend_snapshots,
          backend_state: supplied_http.backend_state,
          backend_usage: supplied_http.backend_usage,
          backend_ids: supplied_http.backend_ids,
          sign_in: (backend_key) => supplied_http.sign_in(backend_key),
          machine_catalog: (machine_id) => context.executor!.catalog_for(machine_id),
          drivers: supplied_http.drivers,
          driver_of: (backend_key) => supplied_http.driver_of(backend_key),
          reports_allowance_usage: (backend_key) =>
            supplied_http.reports_allowance_usage(backend_key),
          shutdown: (options) => supplied_http.shutdown?.(options) ?? Promise.resolve(),
          settled: () => supplied_http.settled(),
        };
  if (http.database !== context.database)
    throw new Error('Production backend HTTP runtime belongs to another database');
  return {
    registry,
    http,
    /** Each live instance's own transcripts, for the recorded usage service. */
    transcript_sources: registry
      .list()
      .flatMap((entry): UsageTranscriptSource[] =>
        entry.kind === 'live' && entry.instance.transcript_usage !== undefined
          ? [{ provider: entry.instance.instance_id, source: entry.instance.transcript_usage }]
          : [],
      ),
    execution_refusal,
    stall_sources: () => [
      ...bindings.stall_sources(shared),
      ...(context.executor?.stall_sources() ?? []),
    ],
  };
}
export function default_production_dependencies(): ProductionServerDependencies {
  return {
    backends: create_production_backend_bindings(),
    environments: {
      factory: create_production_environment_operations,
      dependencies: {
        application_root: applicationRootFromEnvironment(),
        node_executable: process.execPath,
      },
    },
  };
}

function on_path(name: string, supplied_path: string | undefined): string {
  // glibc sysdeps/unix/confstr.h; Apple gen/FreeBSD/sysctl.c USER_CS_PATH + paths.h.
  const path =
    supplied_path ??
    (process.platform === 'darwin' ? '/usr/bin:/bin:/usr/sbin:/sbin' : '/bin:/usr/bin');
  if (path)
    for (const directory of path.split(':')) {
      const candidate = directory ? directory + (directory.endsWith('/') ? '' : '/') + name : name;
      try {
        if (statSync(candidate).isDirectory()) continue;
        accessSync(candidate, constants.X_OK);
        return candidate;
      } catch (error) {
        if (!(error instanceof Error && 'code' in error)) throw error;
      }
    }
  return name;
}
/** Provider paths stay configured; Worker records use the canonical invocation root. */
function production_driver_host(context: ConversationCompositionContext): DriverHost {
  const environment = { ...process.env };
  return {
    database: context.database,
    environment,
    panels_server_url: `http://${HOST}:${context.config.port}`,
    worker_process_root: resolve_path_sync(
      expand_path(
        join(dirname(resolve_path_sync(expand_path(context.config.db_path))), 'worker-processes'),
      ),
    ),
    child_processes: context.child_processes,
    one_shot_children: context.one_shot_children,
    worker_supervision: context.worker_supervision,
    test_mode: context.config.test_mode,
    now: () => context.clock.now(),
    executable_on_path: (name) => on_path(name, environment.PATH),
  };
}
export function create_production_backend_bindings(): ProductionBackendBindings {
  let owner: ConversationCompositionContext['database'] | undefined;
  let host: DriverHost | undefined;
  let http: BackendHttpRuntime | undefined;
  const current = (context: ConversationCompositionContext) => {
    if (owner !== undefined && owner !== context.database)
      throw new Error('Production backend bindings belong to another server invocation');
    if (host === undefined) {
      owner = context.database;
      host = production_driver_host(context);
    }
    return host;
  };
  return {
    drivers: BUILT_IN_DRIVERS,
    driver_host: current,
    http: (context) => {
      current(context);
      http ??= create_production_backend_http(context);
      return http;
    },
    stall_sources: (context) => {
      current(context);
      return [];
    },
  };
}
