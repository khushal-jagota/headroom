/** The only adapter-selection point, with explicit environment composition still required. */
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
} from '../tickets/execution_admission.ts';
import { configure_conversation_start } from '../runtime/conversation_start.ts';
import type { StallSource } from '../stalls/contracts.ts';
import type { ProductionServerDependencies } from './production.ts';
import { create_claude_backend_child_factory } from '../conversation/backends/claude_agent_sdk/adapter.ts';
import { create_codex_backend_child_factory } from '../conversation/backends/codex_app_server/adapter.ts';
import { create_hermes_backend_child_factory } from '../conversation/backends/hermes_acp/adapter.ts';
import { production_backend_launches } from './production_launches.ts';
import { create_production_backend_http } from './production_http.ts';
import { HOST } from '../core/config.ts';

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
  // This composition has no executor router. Unit 22 removes/replaces this one
  // boundary when its real locale routing is composed; presence is not a router.
  const execution_refusal = (id: string) =>
    context.database.read((sql) =>
      conversation_execution_refusal(sql, id, local_ticket_execution_refusal),
    );
  configure_conversation_start(context.database, {
    db_path: context.config.db_path,
    workspace_root: context.config.workspace_root,
    execution_refusal: local_ticket_execution_refusal,
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
    claude: local_only(local_factories.claude),
    codex: local_only(local_factories.codex),
    hermes: local_only(local_factories.hermes),
  };
  const http = bindings.http(shared);
  if (http.database !== context.database)
    throw new Error('Production backend HTTP runtime belongs to another database');
  return {
    factories,
    http,
    execution_refusal,
    stall_sources: () => bindings.stall_sources(shared),
  };
}
export function default_production_dependencies(): ProductionServerDependencies {
  throw new Error('Production environment operations are not installed');
}

export function create_production_backend_bindings(): ProductionBackendBindings {
  let owner: ProductionBindingContext['database'] | undefined;
  let launches: ReturnType<typeof production_backend_launches> | undefined;
  let http: BackendHttpRuntime | undefined;
  const current = (context: ProductionBindingContext) => {
    if (context.config.executor_id !== null)
      throw new Error('Configured production executor router is not installed');
    if (owner !== undefined && owner !== context.database)
      throw new Error('Production backend bindings belong to another server invocation');
    if (launches === undefined) {
      owner = context.database;
      launches = production_backend_launches(`http://${HOST}:${context.config.port}`);
    }
    return launches;
  };
  return {
    claude: (context) =>
      create_claude_backend_child_factory(current(context).claude, {
        child_processes: context.child_processes,
        one_shot_children: context.one_shot_children,
      }),
    codex: (context) =>
      create_codex_backend_child_factory(current(context).codex, {
        child_processes: context.child_processes,
        remembered_login_rejection: context.codex_login_rejection,
      }),
    hermes: (context) =>
      create_hermes_backend_child_factory(current(context).hermes, {
        child_processes: context.child_processes,
      }),
    http: (context) => {
      const selected = current(context);
      http ??= create_production_backend_http(context, selected);
      return http;
    },
    stall_sources: (context) => {
      current(context);
      return [];
    },
  };
}
