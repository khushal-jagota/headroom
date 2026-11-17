/** One Hermes instance: its launch and child factory, and the card its catalog owner answers. */
import type { DriverCreateInput, DriverInstance } from '../../core/driver.ts';
import type { DriverHost } from '../../core/host.ts';
import { configured_file_path } from '../../core/paths.ts';
import { expand_path } from '../../../environments/paths.ts';
import { backend_io_is_test } from '../../../conversation/snapshot/environment.ts';
import { worker_process_root_environment } from '../../../worker_supervision/config.ts';
import { hermes_driver } from '../driver.ts';
import { hermes_manifest } from '../manifest.ts';
import { create_hermes_backend_child_factory } from './adapter.ts';
import { HermesModelCatalogOwner, hermes_model_catalog_launch } from './catalog.ts';
import { hermes_acp_child_launch, hermes_source_root } from './launch.ts';
import type { AcpChildLaunch } from './launch.ts';
import { create_hermes_status } from './status.ts';

/** Shared by the conversation launch and the model catalog launch. */
export function hermes_launch_options(host: DriverHost) {
  const hermes_python = expand_path(
    configured_file_path(hermes_manifest.executable.configured_beside!, host.environment),
  );
  return {
    hermes_python,
    hermes_home: expand_path(configured_file_path(hermes_manifest.home!, host.environment)),
    hermes_python_source_root: hermes_source_root(hermes_python),
    panels_server_url: host.panels_server_url,
  };
}
export function hermes_launch(host: DriverHost): AcpChildLaunch {
  const launch = hermes_acp_child_launch(hermes_launch_options(host));
  return {
    ...launch,
    environment_overrides: [
      ...launch.environment_overrides,
      worker_process_root_environment(host.worker_process_root),
    ],
  };
}
export async function create_hermes_instance(
  input: DriverCreateInput<null>,
  host: DriverHost,
): Promise<DriverInstance> {
  const base = {
    instance_id: input.instance_id,
    driver_kind: hermes_driver.driver_kind,
    capabilities: {
      sessions: { supportsModelSwitchInSession: true },
      turns: { supportsActiveSteering: true },
    },
    child_factory: create_hermes_backend_child_factory(hermes_launch(host), {
      child_processes: host.child_processes,
      worker_supervision: host.worker_supervision,
    }),
  };
  // A test instance starts no catalog owner. Its card says the catalog could not be read.
  if (host.test_mode || backend_io_is_test())
    return { ...base, status: create_hermes_status({ catalog: null }), dispose: async () => {} };
  const owner = new HermesModelCatalogOwner(
    hermes_model_catalog_launch(hermes_launch_options(host)),
    { child_processes: host.child_processes },
  );
  return {
    ...base,
    status: create_hermes_status({
      catalog: async (options) => {
        if (backend_io_is_test())
          throw new Error(
            'Production backend I/O is unavailable in tests. Inject recording adapters.',
          );
        return owner.source(options);
      },
    }),
    dispose: () => owner.shutdown(),
  };
}
