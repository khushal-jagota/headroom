/** The only adapter-selection point. Real bindings arrive together after Manager admission. */
import type { ConversationCompositionContext } from '../core/start.ts';
import type {
  BackendChildFactory,
  RetainedBackendChildFactory,
} from '../conversation/backends/contracts.ts';
import type { BackendHttpRuntime } from '../conversation/snapshot/contracts.ts';
import { create_durable_codex_login_rejection } from '../conversation/backend_state.ts';
import type { CodexLoginRejectionMemory } from '../conversation/snapshot/provider_contracts.ts';
import {
  conversation_execution_refusal,
  local_ticket_execution_refusal,
  canonical_ticket_execution_refusal,
} from '../tickets/execution_admission.ts';
import { LocaleBackendChildFactory } from '../executor/routing.ts';
import { configure_conversation_start } from '../runtime/conversation_start.ts';
import type { StallSource } from '../stalls/contracts.ts';
import type { ProductionServerDependencies } from './production.ts';

export const BACKEND_KEYS = ['claude', 'codex', 'hermes'] as const;
export type ProductionBackendChildFactory = BackendChildFactory &
  Partial<Pick<RetainedBackendChildFactory, 'recover' | 'retained_runs'>>;
export interface ProductionBindingContext extends ConversationCompositionContext {
  /** Separate provider objects share this database's durable rejection row. */
  readonly codex_login_rejection: CodexLoginRejectionMemory;
}
type ChildBinding = (context: ProductionBindingContext) => BackendChildFactory;
export interface ProductionBackendBindings {
  readonly claude: ChildBinding;
  readonly codex: ChildBinding;
  readonly hermes: ChildBinding;
  readonly http: (context: ProductionBindingContext) => BackendHttpRuntime;
  /** The endpoint's actual registered sources, alongside checker-owned WAITS/epochs. */
  readonly stall_sources: (context: ProductionBindingContext) => readonly StallSource[];
}
export function require_backend_bindings(
  value: Partial<ProductionBackendBindings>,
): ProductionBackendBindings {
  const missing = BACKEND_KEYS.filter((key) => typeof value[key] !== 'function');
  if (missing.length)
    throw new Error(`Missing production backend factories: ${missing.join(', ')}`);
  if (typeof value.http !== 'function' || typeof value.stall_sources !== 'function')
    throw new Error('Production backend HTTP and registered stall-source bindings are required');
  return value as ProductionBackendBindings;
}
export function compose_production_backends(
  context: ConversationCompositionContext,
  bindings: ProductionBackendBindings,
) {
  const shared: ProductionBindingContext = {
    ...context,
    codex_login_rejection: create_durable_codex_login_rejection(context.database, () =>
      context.clock.now(),
    ),
  };
  const local_factories = {
    claude: bindings.claude(shared),
    codex: bindings.codex(shared),
    hermes: bindings.hermes(shared),
  };
  const missing = BACKEND_KEYS.filter((key) => typeof local_factories[key] !== 'function');
  if (missing.length)
    throw new Error(`Missing production backend factories: ${missing.join(', ')}`);
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
      return factory(values);
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
          return recover.call(factory, reference, options);
        },
      });
    if (typeof runs === 'function')
      Object.assign(guarded, {
        retained_runs: () => runs.call(factory),
      });
    return guarded;
  };
  const factories = {
    claude:
      context.executor == null
        ? local_only(local_factories.claude)
        : new LocaleBackendChildFactory(
            context.database,
            local_factories.claude,
            context.executor.runs.as_factory(),
          ).as_factory(),
    codex:
      context.executor == null
        ? local_only(local_factories.codex)
        : new LocaleBackendChildFactory(
            context.database,
            local_factories.codex,
            context.executor.runs.as_factory(),
          ).as_factory(),
    hermes:
      context.executor == null
        ? local_only(local_factories.hermes)
        : new LocaleBackendChildFactory(
            context.database,
            local_factories.hermes,
            context.executor.runs.as_factory(),
          ).as_factory(),
  };
  const supplied_http = bindings.http(shared);
  const http: BackendHttpRuntime =
    context.executor == null
      ? supplied_http
      : {
          database: supplied_http.database,
          backend_snapshots: supplied_http.backend_snapshots,
          backend_state: supplied_http.backend_state,
          backend_usage: supplied_http.backend_usage,
          codex_sign_in: supplied_http.codex_sign_in,
          machine_catalog: () => context.executor!.catalog,
          shutdown: (options) => supplied_http.shutdown?.(options) ?? Promise.resolve(),
          settled: () => supplied_http.settled(),
        };
  if (http.database !== context.database)
    throw new Error('Production backend HTTP runtime belongs to another database');
  return {
    factories,
    http,
    execution_refusal,
    stall_sources: () => [
      ...bindings.stall_sources(shared),
      ...(context.executor?.stall_sources() ?? []),
    ],
  };
}
export function default_production_dependencies(): ProductionServerDependencies {
  // 4a intentionally has no adapter substitutes. 4b replaces this table and binds
  // the required environment/executor dependencies in this one file after approval.
  require_backend_bindings({});
  throw new Error('Production environment and executor bindings are not installed');
}
