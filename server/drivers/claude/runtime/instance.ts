/** One Claude instance: the launch and child factory the server composed before drivers. */
import type { DriverCreateInput, DriverInstance } from '../../core/driver.ts';
import type { DriverHost } from '../../core/host.ts';
import { expand_path } from '../../../environments/paths.ts';
import { claude_executable_override } from '../../../conversation/snapshot/environment.ts';
import { worker_process_root_environment } from '../../../worker_supervision/config.ts';
import { claude_driver } from '../driver.ts';
import { claude_manifest } from '../manifest.ts';
import { create_claude_backend_child_factory } from './adapter.ts';
import type { ClaudeAgentSdkChildLaunch } from './adapter.ts';

export function claude_launch(host: DriverHost): ClaudeAgentSdkChildLaunch {
  const override = claude_executable_override(host.environment, expand_path);
  return {
    claude_executable:
      override === undefined
        ? host.executable_on_path(claude_manifest.executable.name)
        : expand_path(override),
    environment_overrides: [
      ['PLAN_SERVER_URL', host.panels_server_url],
      worker_process_root_environment(host.worker_process_root),
    ],
  };
}
export async function create_claude_instance(
  input: DriverCreateInput<null>,
  host: DriverHost,
): Promise<DriverInstance> {
  return {
    instance_id: input.instance_id,
    driver_kind: claude_driver.driver_kind,
    capabilities: {
      sessions: { supportsModelSwitchInSession: false },
      turns: { supportsActiveSteering: true },
    },
    child_factory: create_claude_backend_child_factory(claude_launch(host), {
      child_processes: host.child_processes,
      one_shot_children: host.one_shot_children,
      worker_supervision: host.worker_supervision,
    }),
    dispose: async () => {},
  };
}
