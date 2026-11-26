/** The only adapter-selection point, with explicit environment composition still required. */
import type { ConversationCompositionContext } from '../core/start.ts';
import type {
  BackendChildFactory,
  RetainedBackendChildFactory,
} from '../conversation/backends/contracts.ts';
import type { BackendHttpRuntime } from '../conversation/snapshot/contracts.ts';
import type { UsageTranscriptSource } from '../conversation/usage/contracts.ts';
import { canonical_ticket_execution_refusal } from '../tickets/execution_admission.ts';
import { LocaleBackendChildFactory } from '../executor/routing.ts';
import {
  configure_conversation_start,
  configured_conversation_execution_refusal,
} from '../runtime/conversation_start.ts';
import { conversation_machine_refusal } from '../machines/conversation_machine.ts';
import type { StallSource } from '../stalls/contracts.ts';
import type { ProductionServerDependencies } from './production.ts';
import { BUILT_IN_DRIVERS } from '../drivers/built_in.ts';
import type { AnyAgentDriver } from '../drivers/core/driver.ts';
import {
  default_instance_configs,
  driver_registry_from_entries,
} from '../drivers/core/registry.ts';
import { KNOWN_BACKEND_INSTANCE_IDS } from '../drivers/built_in_manifests.ts';
import type { DriverRegistry, RegistryEntry } from '../drivers/core/registry.ts';
import { create_production_backend_http } from './production_http.ts';
import { applicationRootFromEnvironment } from '../core/config.ts';
import { create_production_environment_operations } from '../environments/production.ts';

export type ProductionBackendChildFactory = BackendChildFactory &
  Partial<Pick<RetainedBackendChildFactory, 'recover' | 'retained_runs'>>;
export interface ProductionBindingContext extends ConversationCompositionContext {
  /** The live instances: the HTTP runtime's cards, sign-ins and usage. */
  readonly registry: DriverRegistry;
}
export interface ProductionBackendBindings {
  readonly drivers: readonly AnyAgentDriver[];
  readonly http: (context: ProductionBindingContext) => BackendHttpRuntime;
  /** The endpoint's actual registered sources, alongside checker-owned WAITS/epochs. */
  readonly stall_sources: (context: ProductionBindingContext) => readonly StallSource[];
}
/** The registry built from the drivers, and the host it was built with. */
export interface PreparedProductionBackends {
  readonly registry: DriverRegistry;
}
export function require_backend_bindings(
  value: Partial<ProductionBackendBindings>,
): ProductionBackendBindings {
  if (!Array.isArray(value.drivers) || value.drivers.length === 0)
    throw new Error('Production backend drivers are required');
  // Launches are admitted against KNOWN_BACKEND_INSTANCE_IDS (require_conversation_backend_key),
  // so every built-in backend needs a driver here, or an admitted launch has nothing to run on.
  // A stored key outside that list is not this check's concern: it reads as unavailable.
  const ids = new Set<string>(default_instance_configs(value.drivers).keys());
  const missing = KNOWN_BACKEND_INSTANCE_IDS.filter((id) => !ids.has(id)).sort();
  if (missing.length)
    throw new Error(`Missing production backend factories: ${missing.join(', ')}`);
  if (typeof value.http !== 'function' || typeof value.stall_sources !== 'function')
    throw new Error('Production backend HTTP and registered stall-source bindings are required');
  return value as ProductionBackendBindings;
}
export function compose_production_backends(
  context: ConversationCompositionContext,
  prepared: PreparedProductionBackends,
  bindings: ProductionBackendBindings,
) {
  const policy = canonical_ticket_execution_refusal;
  configure_conversation_start(context.database, {
    db_path: context.config.db_path,
    workspace_root: context.config.workspace_root,
    execution_refusal: policy,
    conversation_refusal: conversation_machine_refusal,
  });
  const execution_refusal = (id: string) =>
    context.database.read((sql) =>
      configured_conversation_execution_refusal(context.database, sql, id),
    );
  const entries = prepared.registry.list().map((entry): RegistryEntry => {
    if (entry.kind !== 'live') return entry;
    const executor = context.executor;
    const child_factory =
      executor == null
        ? () => {
            throw new Error('Machine execution is unavailable');
          }
        : new LocaleBackendChildFactory(context.database, executor.as_factory()).as_factory();
    return {
      ...entry,
      instance: { ...entry.instance, child_factory, supervises_local_launches: false },
    };
  });
  const registry = driver_registry_from_entries([...prepared.registry.drivers.values()], entries);
  const shared: ProductionBindingContext = { ...context, registry };
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
          machine_backends: (machine_id) => context.executor!.backend_controls_for(machine_id),
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
    /**
     * Each driver's transcripts on this server, for the recorded usage service. They come from the
     * drivers, as T3 Code's built-in usage drivers do: the server's instances run nothing.
     */
    transcript_sources: [...default_instance_configs(bindings.drivers)].flatMap(
      ([instance_id, config]): UsageTranscriptSource[] => {
        const driver = bindings.drivers.find(
          (candidate) => candidate.driver_kind === config.driver,
        );
        return driver?.transcript_usage === undefined
          ? []
          : [{ provider: instance_id, source: driver.transcript_usage(process.env) }];
      },
    ),
    execution_refusal,
    stall_sources: () => [
      ...bindings.stall_sources(shared),
      ...(context.executor?.stall_sources() ?? []),
    ],
  };
}
/** Server instances contain pure driver rules and a machine factory, never a native runtime. */
export function prepare_machine_backends(
  drivers: readonly AnyAgentDriver[],
): PreparedProductionBackends {
  const entries: RegistryEntry[] = [...default_instance_configs(drivers)].map(
    ([instance_id, config]) => {
      const driver = drivers.find((candidate) => candidate.driver_kind === config.driver)!;
      return {
        kind: 'live',
        driver,
        instance: {
          instance_id,
          driver_kind: driver.driver_kind,
          capabilities: driver.capabilities,
          plan_selection_transition: driver.plan_selection_transition,
          child_factory: () => {
            throw new Error('The machine factory is not composed');
          },
          status: {
            version_detects_upgrade: false,
            probe: async () => {
              throw new Error('Backend status belongs to the execution machine');
            },
          },
          dispose: async () => {},
        },
      };
    },
  );
  return { registry: driver_registry_from_entries(drivers, entries) };
}
export function default_production_dependencies(): ProductionServerDependencies {
  return {
    backends: create_production_backend_bindings(),
    environments: {
      factory: create_production_environment_operations,
      dependencies: {
        application_root: applicationRootFromEnvironment(),
      },
    },
  };
}

export function create_production_backend_bindings(): ProductionBackendBindings {
  return {
    drivers: BUILT_IN_DRIVERS,
    http: create_production_backend_http,
    stall_sources: () => [],
  };
}
