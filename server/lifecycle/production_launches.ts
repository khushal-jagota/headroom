/** Installed agents use their configured paths without filesystem canonicalization. */
import { accessSync, constants, statSync } from 'node:fs';
import { expand_path } from '../environments/paths.ts';
import { resolve_hermes_python } from '../environments/registry/launch.ts';
import { codex_app_server_child_launch } from '../conversation/backends/codex_app_server/adapter.ts';
import {
  hermes_acp_child_launch,
  hermes_source_root,
} from '../conversation/backends/hermes_acp/launch.ts';
import { hermes_model_catalog_launch } from '../conversation/backends/hermes_acp/catalog.ts';
import type { ClaudeAgentSdkChildLaunch } from '../conversation/backends/claude_agent_sdk/adapter.ts';
import { provider_launch_settings } from '../core/config.ts';
import { claude_executable_override } from '../conversation/snapshot/environment.ts';

function on_path(name: string, supplied_path: string | undefined): string {
  // glibc sysdeps/unix/confstr.h; Apple gen/FreeBSD/sysctl.c USER_CS_PATH + paths.h.
  const path =
    supplied_path ??
    (process.platform === 'darwin' ? '/usr/bin:/bin:/usr/sbin:/sbin' : '/bin:/usr/bin');
  if (path)
    for (const directory of path.split(':')) {
      const candidate = directory ? directory + (directory.endsWith('/') ? '' : '/') + name : name;
      try {
        if (statSync(candidate).isDirectory()) continue;
        accessSync(candidate, constants.X_OK);
        return candidate;
      } catch (error) {
        if (!(error instanceof Error && 'code' in error)) throw error;
      }
    }
  return name;
}
export function production_backend_launches(panels_server_url: string) {
  const settings = provider_launch_settings();
  const environment = settings.environment;
  const hermes_python = resolve_hermes_python(settings.hermes_python, {});
  const hermes = {
    hermes_python,
    hermes_home: expand_path(settings.hermes_home),
    hermes_python_source_root: hermes_source_root(hermes_python),
    panels_server_url,
  };
  const override = claude_executable_override(environment, expand_path);
  const claude: ClaudeAgentSdkChildLaunch = {
    claude_executable:
      override === undefined ? on_path('claude', environment.PATH) : expand_path(override),
    environment_overrides: [['PLAN_SERVER_URL', panels_server_url]],
  };
  return {
    claude,
    codex: codex_app_server_child_launch({
      codex_executable: on_path('codex', environment.PATH),
      panels_server_url,
    }),
    hermes: hermes_acp_child_launch(hermes),
    hermes_catalog: hermes_model_catalog_launch(hermes),
  };
}
export type ProductionBackendLaunches = ReturnType<typeof production_backend_launches>;
