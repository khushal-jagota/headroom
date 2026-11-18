/** One Antigravity instance: its ACP child factory, card, allowance usage and transcripts. */
import { homedir } from 'node:os';
import { join } from 'node:path';
import type { DriverCreateInput, DriverInstance } from '../../core/driver.ts';
import type { DriverHost } from '../../core/host.ts';
import { acp_selection_transition } from '../../core/selection.ts';
import { home_path } from '../../core/paths.ts';
import { expand_path } from '../../../environments/paths.ts';
import {
  backend_io_is_test,
  executable_override,
} from '../../../conversation/snapshot/environment.ts';
import { ACP_CHILD_CAPABILITIES } from '../../acp/flavor.ts';
import { create_acp_child_factory } from '../../acp/child.ts';
import { antigravity_driver } from '../driver.ts';
import { antigravity_manifest } from '../manifest.ts';
import { AntigravityUsageAdapter, http_post_usage } from './allowance_usage.ts';
import { AntigravityModelCatalog } from './catalog.ts';
import { APP_DATA_DIRECTORY_NAME } from './configuration.ts';
import { create_antigravity_flavor } from './flavor.ts';
import { create_antigravity_status } from './status.ts';
import { antigravity_transcript_usage } from './transcript_usage.ts';

export async function create_antigravity_instance(
  input: DriverCreateInput<null>,
  host: DriverHost,
): Promise<DriverInstance> {
  const configured_home = home_path(antigravity_manifest.home!, host.environment, homedir(), {
    unset: '||',
    expand_configured: false,
  });
  const override = executable_override(
    antigravity_manifest.executable,
    host.environment,
    expand_path,
  );
  const launch_options = {
    home: expand_path(configured_home),
    worker_process_root: host.worker_process_root,
    panels_server_url: host.panels_server_url,
  };
  const base = {
    instance_id: input.instance_id,
    driver_kind: antigravity_driver.driver_kind,
    capabilities: ACP_CHILD_CAPABILITIES,
    plan_selection_transition: acp_selection_transition,
    supervises_local_launches: true,
    child_factory: create_acp_child_factory(
      create_antigravity_flavor({
        ...launch_options,
        executable: override ?? host.executable_on_path(antigravity_manifest.executable.name),
      }),
      { child_processes: host.child_processes, worker_supervision: host.worker_supervision },
    ),
    transcript_usage: antigravity_transcript_usage(configured_home),
  };
  // A test instance starts no catalog and reads no account. Its card says agy did not answer.
  if (host.test_mode || backend_io_is_test())
    return {
      ...base,
      status: create_antigravity_status({ catalog: null }),
      dispose: async () => {},
    };
  const catalog = new AntigravityModelCatalog(launch_options, {
    child_processes: host.child_processes,
    one_shot_children: host.one_shot_children,
  });
  return {
    ...base,
    status: create_antigravity_status({ catalog: (executable) => catalog.read(executable) }),
    allowance_usage: new AntigravityUsageAdapter({
      credential_path: join(launch_options.home, APP_DATA_DIRECTORY_NAME, 'acp_token.json'),
      http_post: http_post_usage,
      now: () => new Date(),
    }),
    dispose: (options) => catalog.shutdown(options),
  };
}
