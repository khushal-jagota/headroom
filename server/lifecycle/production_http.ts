/** The real backend HTTP runtime: the registry's cards, sign-ins and usage, and its dispose. */
import { create_backend_runtime } from '../conversation/snapshot/composition.ts';
import type { BackendHttpRuntime } from '../conversation/snapshot/contracts.ts';
import type { ProductionBindingContext } from './production_bindings.ts';

export function create_production_backend_http(
  context: Pick<
    ProductionBindingContext,
    'database' | 'config' | 'clock' | 'one_shot_children' | 'registry'
  >,
): BackendHttpRuntime {
  return create_backend_runtime({
    database: context.database,
    config: context.config,
    now: () => context.clock.now(),
    one_shot_children: context.one_shot_children,
    registry: context.registry,
  });
}
