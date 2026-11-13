/** Python production_backend_launches: lexical paths and installed agents stay independent. */
import { accessSync, constants, statSync } from 'node:fs';
import { dirname } from 'node:path';
import { pure_posix_path } from '../core/python_path.ts';
import { expand_path } from '../environments/paths.ts';
import { resolve_hermes_python } from '../environments/registry/launch.ts';
import { codex_app_server_child_launch } from '../conversation/backends/codex_app_server/adapter.ts';
import { hermes_acp_child_launch } from '../conversation/backends/hermes_acp/launch.ts';
import { hermes_model_catalog_launch } from '../conversation/backends/hermes_acp/catalog.ts';
import type { ClaudeAgentSdkChildLaunch } from '../conversation/backends/claude_agent_sdk/adapter.ts';
import { read_production_backend_environment } from '../core/config.ts';

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
        return pure_posix_path(candidate);
      } catch (error) {
        if (!(error instanceof Error && 'code' in error)) throw error;
      }
    }
  return name;
}
function hermes_source_root(path: string): string {
  const parents: string[] = [];
  let current = path;
  while (parents.length < 3) {
    const parent = current === '//' ? '//' : dirname(current);
    if (parent === current) break;
    parents.push(parent);
    current = parent;
  }
  return parents[2] ?? parents[0] ?? path;
}
export function production_backend_launches(panels_server_url: string) {
  const environment = read_production_backend_environment();
  const hermes_python = pure_posix_path(resolve_hermes_python(environment.hermes_python, {}));
  const hermes = {
    hermes_python,
    hermes_home: pure_posix_path(expand_path(environment.hermes_home || '~/.hermes')),
    hermes_python_source_root: hermes_source_root(hermes_python),
    panels_server_url,
  };
  let override = environment.claude_executable;
  const app_root = environment.app_root;
  if (override !== undefined && app_root !== undefined) {
    const root = pure_posix_path(app_root);
    const legacy = pure_posix_path(
      root +
        (root.endsWith('/') ? '' : '/') +
        'agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe',
    );
    if (pure_posix_path(expand_path(override)) === legacy) override = undefined;
  }
  const claude: ClaudeAgentSdkChildLaunch = {
    claude_executable:
      override === undefined
        ? on_path('claude', environment.path)
        : pure_posix_path(expand_path(override)),
    environment_overrides: [['PLAN_SERVER_URL', panels_server_url]],
  };
  return {
    claude,
    codex: codex_app_server_child_launch({
      codex_executable: on_path('codex', environment.path),
      panels_server_url,
    }),
    hermes: hermes_acp_child_launch(hermes),
    hermes_catalog: hermes_model_catalog_launch(hermes),
  };
}
export type ProductionBackendLaunches = ReturnType<typeof production_backend_launches>;
