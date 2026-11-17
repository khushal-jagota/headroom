/** One Codex instance: its launch and child factory, card, sign-in, allowance usage and transcripts. */
import { homedir } from 'node:os';
import type { DriverCreateInput, DriverInstance } from '../../core/driver.ts';
import type { DriverHost } from '../../core/host.ts';
import { turn_scoped_selection_transition } from '../../core/selection.ts';
import { home_path } from '../../core/paths.ts';
import { backend_io_is_test } from '../../../conversation/snapshot/environment.ts';
import { create_durable_codex_login_rejection } from './login_rejection.ts';
import { create_codex_providers } from './providers.ts';
import { create_codex_status } from './status.ts';
import { codex_transcript_usage } from './transcript_usage.ts';
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
  const base = {
    instance_id: input.instance_id,
    driver_kind: codex_driver.driver_kind,
    capabilities: {
      sessions: { supportsModelSwitchInSession: true },
      turns: { supportsActiveSteering: true },
    },
    plan_selection_transition: turn_scoped_selection_transition,
    child_factory: create_codex_backend_child_factory(codex_launch(host), {
      child_processes: host.child_processes,
      worker_supervision: host.worker_supervision,
      // Separate instances share this database's durable rejection row.
      remembered_login_rejection: create_durable_codex_login_rejection(host.database, host.now),
    }),
    transcript_usage: codex_transcript_usage(
      home_path(codex_manifest.home!, host.environment, homedir(), {
        unset: '||',
        expand_configured: false,
      }),
    ),
  };
  // A test instance reads no catalog, login or account, and cannot sign in.
  if (host.test_mode || backend_io_is_test())
    return {
      ...base,
      status: create_codex_status({ catalog: null, read_login_standing: null }),
      dispose: async () => {},
    };
  const providers = create_codex_providers({
    database: host.database,
    now: host.now,
    one_shot_children: host.one_shot_children,
    child_processes: host.child_processes,
  });
  return {
    ...base,
    status: create_codex_status({
      catalog: providers.catalog,
      read_login_standing: providers.read_login_standing,
    }),
    sign_in: providers.sign_in,
    allowance_usage: providers.allowance_usage,
    dispose: () => providers.dispose(),
  };
}
