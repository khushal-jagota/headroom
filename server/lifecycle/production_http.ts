import { decode_machine_update } from '../conversation/snapshot/machine.ts';
/** The server reads catalogs and provider controls from machine code only. */
import { BackendStateStore } from '../conversation/backend_state.ts';
import type { BackendHttpRuntime } from '../conversation/snapshot/contracts.ts';
import type { ProductionBindingContext } from './production_bindings.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';
import { HttpError } from '../core/http/errors.ts';

export function create_production_backend_http(
  context: ProductionBindingContext,
): BackendHttpRuntime {
  const catalog = () => {
    const source = context.executor?.catalog_for(HOME_MACHINE_ID);
    if (!source) throw new HttpError(503, 'The selected machine is unavailable.');
    return source;
  };
  const drivers = [...context.registry.drivers.values()];
  return {
    database: context.database,
    backend_state: new BackendStateStore(context.database),
    backend_snapshots: {
      retained: (key) => context.executor?.catalog_for(HOME_MACHINE_ID)?.retained(key) ?? null,
      snapshot: (key, options) => catalog().snapshot(key, options),
      snapshots: async (options) =>
        Promise.all(
          context.registry
            .list()
            .map((entry) =>
              catalog().snapshot(
                entry.kind === 'live' ? entry.instance.instance_id : entry.instance_id,
                options,
              ),
            ),
        ),
      update_backend: async (key, options) => {
        const controls = context.executor?.backend_controls_for(HOME_MACHINE_ID);
        if (!controls) throw new HttpError(503, 'The selected machine is unavailable.');
        return decode_machine_update(
          await controls.request({ backend_key: key, operation: 'update_backend' }, options),
        );
      },
    },
    backend_usage: {
      refresh: async () => {
        throw new HttpError(503, 'Allowance reads require machine controls.');
      },
    },
    backend_ids: context.registry
      .list()
      .map((entry) => (entry.kind === 'live' ? entry.instance.instance_id : entry.instance_id)),
    sign_in: () => null,
    machine_catalog: (id) => context.executor?.catalog_for(id) ?? null,
    machine_backends: (id) => context.executor?.backend_controls_for(id) ?? null,
    drivers,
    driver_of: (id) => context.registry.get(id)?.driver ?? null,
    reports_allowance_usage: () => false,
    shutdown: async () => {},
    settled: async () => {},
  };
}
