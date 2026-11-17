/** One Claude instance: its launch and child factory, card, allowance usage and transcripts. */
import { homedir } from 'node:os';
import { join } from 'node:path';
import type { DriverCreateInput, DriverInstance } from '../../core/driver.ts';
import type { DriverHost } from '../../core/host.ts';
import { claude_selection_transition } from './selection.ts';
import { home_path } from '../../core/paths.ts';
import { expand_path } from '../../../environments/paths.ts';
import {
  backend_io_is_test,
  executable_override,
} from '../../../conversation/snapshot/environment.ts';
import { http_get_usage } from '../../../conversation/backend_usage/http.ts';
import { worker_process_root_environment } from '../../../worker_supervision/config.ts';
import { claude_driver } from '../driver.ts';
import { claude_manifest } from '../manifest.ts';
import { create_claude_backend_child_factory } from './adapter.ts';
import type { ClaudeAgentSdkChildLaunch } from './adapter.ts';
import { ClaudeUsageAdapter } from './allowance_usage.ts';
import { create_claude_catalog_source } from './catalog_source.ts';
import { create_claude_status } from './status.ts';
import { claude_transcript_usage } from './transcript_usage.ts';

export function claude_launch(host: DriverHost): ClaudeAgentSdkChildLaunch {
  const override = executable_override(claude_manifest.executable, host.environment, expand_path);
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
  const base = {
    instance_id: input.instance_id,
    driver_kind: claude_driver.driver_kind,
    capabilities: {
      sessions: { supportsModelSwitchInSession: false },
      turns: { supportsActiveSteering: true },
    },
    plan_selection_transition: claude_selection_transition,
    child_factory: create_claude_backend_child_factory(claude_launch(host), {
      child_processes: host.child_processes,
      one_shot_children: host.one_shot_children,
      worker_supervision: host.worker_supervision,
    }),
    transcript_usage: claude_transcript_usage(
      home_path(claude_manifest.home!, host.environment, homedir(), {
        unset: '||',
        expand_configured: false,
      }),
    ),
  };
  // A test instance reads no catalog or account. Its card says Claude did not answer.
  if (host.test_mode || backend_io_is_test())
    return { ...base, status: create_claude_status({ catalog: null }), dispose: async () => {} };
  const catalog = create_claude_catalog_source(host.one_shot_children);
  const home = home_path(claude_manifest.home!, host.environment, homedir(), {
    unset: '??',
    expand_configured: false,
  });
  return {
    ...base,
    status: create_claude_status({ catalog: catalog.source }),
    allowance_usage: new ClaudeUsageAdapter({
      credential_path: join(home, '.credentials.json'),
      http_get: http_get_usage,
      now: () => new Date(),
    }),
    dispose: (options) => catalog.dispose(options),
  };
}
