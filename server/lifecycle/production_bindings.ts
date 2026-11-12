/** The only adapter-selection point. Real bindings arrive together after Manager admission. */
import type { ConversationCompositionContext } from '../core/start.ts';
import type { BackendChildFactory } from '../conversation/backends/contracts.ts';
import type { BackendHttpRuntime } from '../conversation/snapshot/contracts.ts';
import { create_durable_codex_login_rejection } from '../conversation/backend_state.ts';
import type { CodexLoginRejectionMemory } from '../conversation/snapshot/provider_contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { read_ticket_by_conversation_id } from '../tickets/data/index.ts';
import {
  CURRENT_EXECUTION_LOCALE,
  execution_unavailable_reason,
} from '../tickets/locale_configuration.ts';
import type { StallSource } from '../stalls/contracts.ts';
import type { ProductionServerDependencies } from './production.ts';

export const BACKEND_KEYS = ['claude', 'codex', 'hermes'] as const;
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
  const local_only =
    (factory: BackendChildFactory): BackendChildFactory =>
    (values) => {
      context.database.read((sql) => {
        let ticket;
        try {
          ticket = read_ticket_by_conversation_id(sql, values.resolved_start.conversation_id);
        } catch (error) {
          if (error instanceof PlannerError && error.code === 'not_found') return;
          throw error;
        }
        if (ticket.locale === 'macos')
          throw new PlannerError('validation', execution_unavailable_reason(sql, ticket.locale), {
            ticket_id: ticket.id,
            locale: ticket.locale,
            machine_locale: CURRENT_EXECUTION_LOCALE,
          });
      });
      return factory(values);
    };
  const factories = {
    claude: local_only(local_factories.claude),
    codex: local_only(local_factories.codex),
    hermes: local_only(local_factories.hermes),
  };
  const http = bindings.http(shared);
  if (http.database !== context.database)
    throw new Error('Production backend HTTP runtime belongs to another database');
  return { factories, http, stall_sources: () => bindings.stall_sources(shared) };
}
export function default_production_dependencies(): ProductionServerDependencies {
  // 4a intentionally has no adapter substitutes. 4b replaces this table and binds
  // the required environment/executor dependencies in this one file after approval.
  require_backend_bindings({});
  throw new Error('Production environment and executor bindings are not installed');
}
