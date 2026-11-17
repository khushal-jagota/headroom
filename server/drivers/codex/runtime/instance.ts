/** One Codex instance: the launch and child factory the server composed before drivers. */
import type { DriverCreateInput, DriverInstance } from '../../core/driver.ts';
import type { DriverHost } from '../../core/host.ts';
import { create_durable_codex_login_rejection } from '../../../conversation/backend_state.ts';
import { worker_process_root_environment } from '../../../worker_supervision/config.ts';
import { codex_driver } from '../driver.ts';
import { codex_manifest } from '../manifest.ts';
import { codex_app_server_child_launch, create_codex_backend_child_factory } from './adapter.ts';
import type { CodexChildLaunch } from './adapter.ts';

export function codex_launch(host: DriverHost): CodexChildLaunch {
  const launch = codex_app_server_child_launch({
    codex_executable: host.executable_on_path(codex_manifest.executable.name),
    panels_server_url: host.panels_server_url,
  });
  return {
    ...launch,
    environment_overrides: [
      ...(launch.environment_overrides ?? []),
      worker_process_root_environment(host.worker_process_root),
    ],
  };
}
export async function create_codex_instance(
  input: DriverCreateInput<null>,
  host: DriverHost,
): Promise<DriverInstance> {
  return {
    instance_id: input.instance_id,
    driver_kind: codex_driver.driver_kind,
    capabilities: {
      sessions: { supportsModelSwitchInSession: true },
      turns: { supportsActiveSteering: true },
    },
    child_factory: create_codex_backend_child_factory(codex_launch(host), {
      child_processes: host.child_processes,
      worker_supervision: host.worker_supervision,
      // Separate instances share this database's durable rejection row.
      remembered_login_rejection: create_durable_codex_login_rejection(host.database, host.now),
    }),
    dispose: async () => {},
  };
}
