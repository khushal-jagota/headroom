/** One real Mac service shares the runtime's store and invocation owner. */
import type { MacUpdateComposition, MacUpdateCompositionContext } from './contracts.ts';
import { MacUpdateService } from './service.ts';
export function create_mac_update_composition(
  context: MacUpdateCompositionContext,
): MacUpdateComposition {
  const service = new MacUpdateService(context);
  return {
    service,
    operations: {
      machine_states: (release) => service.machine_states(release),
      retry_machine: (machine_id, attempt_id) => service.retry_machine(machine_id, attempt_id),
      prepare: (release) => service.prepare(release),
      prepare_home: (release) => service.prepare_home(release),
    },
  };
}
