/** One real Mac service shares the runtime's store and invocation owner. */
import type { ExecutorUpdateBindings } from '../../production_contracts.ts';
import type { MacUpdateComposition, MacUpdateCompositionContext } from './contracts.ts';
import { MacUpdateService } from './service.ts';
export function create_mac_update_composition(
  context: MacUpdateCompositionContext,
  executor: ExecutorUpdateBindings | null,
): MacUpdateComposition {
  const service = new MacUpdateService(context);
  return {
    service,
    operations: {
      prepare: (release) => service.prepare(release),
      phase: (id) => service.phase(id),
      reported_at: (id) => service.reported_at(id),
      configured: () => (executor === null ? false : executor.configured()),
    },
    reads: {
      health_payload: () => (executor === null ? null : executor.health_payload()),
      status: async () => service.status(),
    },
  };
}
