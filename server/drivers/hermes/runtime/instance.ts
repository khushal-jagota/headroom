/** One Hermes instance: the launch and child factory the server composed before drivers. */
import type { DriverCreateInput, DriverInstance } from '../../core/driver.ts';
import type { DriverHost } from '../../core/host.ts';
import { provider_launch_settings } from '../../../core/config.ts';
import { expand_path } from '../../../environments/paths.ts';
import { resolve_hermes_python } from '../../../environments/registry/launch.ts';
import { worker_process_root_environment } from '../../../worker_supervision/config.ts';
import { hermes_driver } from '../driver.ts';
import { create_hermes_backend_child_factory } from './adapter.ts';
import { hermes_acp_child_launch, hermes_source_root } from './launch.ts';
import type { AcpChildLaunch } from './launch.ts';

/** Shared by the conversation launch and the model catalog launch. */
export function hermes_launch_options(host: DriverHost) {
  const settings = provider_launch_settings(host.environment);
  const hermes_python = resolve_hermes_python(settings.hermes_python, {});
  return {
    hermes_python,
    hermes_home: expand_path(settings.hermes_home),
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
  return {
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
    dispose: async () => {},
  };
}
