/** Host the same provider factories with this Mac's account and launch settings. */
import { dirname } from 'node:path';
import { pure_posix_path } from '../../core/python_path.ts';
import type { ResolvedConversationStart } from '../../conversation/contracts.ts';
import type { BackendChildFactory } from '../../conversation/backends/contracts.ts';
import { create_claude_backend_child_factory } from '../../conversation/backends/claude_agent_sdk/adapter.ts';
import type { ClaudeAgentSdkChildLaunch } from '../../conversation/backends/claude_agent_sdk/adapter.ts';
import {
  codex_app_server_child_launch,
  create_codex_backend_child_factory,
} from '../../conversation/backends/codex_app_server/adapter.ts';
import type { CodexChildLaunch } from '../../conversation/backends/codex_app_server/adapter.ts';
import {
  codex_home,
  RememberedCodexLoginRejection,
} from '../../conversation/backends/codex_app_server/login.ts';
import { create_hermes_backend_child_factory } from '../../conversation/backends/hermes_acp/adapter.ts';
import { hermes_acp_child_launch } from '../../conversation/backends/hermes_acp/launch.ts';
import type { AcpChildLaunch } from '../../conversation/backends/hermes_acp/launch.ts';
import type { OwnedChildProcesses, OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import { expand_executor_path } from './platform.ts';
export interface MacBackendLaunches {
  readonly claude: ClaudeAgentSdkChildLaunch;
  readonly codex: CodexChildLaunch;
  readonly hermes: AcpChildLaunch;
}
export interface MacAdapterOptions {
  readonly panels_server_url: string;
  readonly executable_path: (name: string) => string | null;
  readonly child_processes: OwnedChildProcesses;
  readonly one_shot_children: OwnedOneShotChildren;
  readonly environment?: Readonly<NodeJS.ProcessEnv>;
}
export interface MacAdapterHost {
  readonly launches: MacBackendLaunches;
  readonly hermes_paths: {
    readonly python: string;
    readonly home: string;
    readonly source_root: string;
  };
  factory(start: ResolvedConversationStart): BackendChildFactory;
  native_skill_home(backend: ResolvedConversationStart['backend_key']): string;
}
function hermes_source_root(python: string): string {
  const first = dirname(python);
  let parent = first;
  for (let depth = 1; depth < 3; depth++) {
    const next = dirname(parent);
    if (next === parent) return first;
    parent = next;
  }
  return parent;
}
export function create_mac_adapter_host(options: MacAdapterOptions): MacAdapterHost {
  const environment = options.environment ?? process.env;
  const expand = (path: string) => pure_posix_path(expand_executor_path(path, environment));
  const on_path = (name: string) => pure_posix_path(options.executable_path(name) ?? name);
  const hermes_python = expand(
    environment.PLAN_HERMES_PYTHON || '~/.hermes/hermes-agent/venv/bin/python',
  );
  const hermes_home = expand(environment.PLAN_HERMES_HOME || '~/.hermes');
  let override = environment.PLAN_CLAUDE_EXECUTABLE;
  const app_root = environment.PLAN_APP_ROOT;
  const legacy =
    app_root === undefined
      ? null
      : pure_posix_path(
          (app_root === '' ? '' : app_root.replace(/\/$/, '') + '/') +
            'agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe',
        );
  if (override !== undefined && legacy !== null && expand(override) === legacy)
    override = undefined;
  const launches: MacBackendLaunches = {
    claude: {
      claude_executable: override === undefined ? on_path('claude') : expand(override),
      environment_overrides: [['PLAN_SERVER_URL', options.panels_server_url]],
    },
    codex: codex_app_server_child_launch({
      codex_executable: on_path('codex'),
      panels_server_url: options.panels_server_url,
    }),
    hermes: hermes_acp_child_launch({
      hermes_python,
      hermes_home,
      hermes_python_source_root: hermes_source_root(hermes_python),
      panels_server_url: options.panels_server_url,
    }),
  };
  return {
    launches,
    hermes_paths: {
      python: hermes_python,
      home: hermes_home,
      source_root: hermes_source_root(hermes_python),
    },
    factory(start) {
      if (start.backend_key === 'claude')
        return create_claude_backend_child_factory(launches.claude, {
          child_processes: options.child_processes,
          one_shot_children: options.one_shot_children,
        });
      if (start.backend_key === 'hermes')
        return create_hermes_backend_child_factory(launches.hermes, {
          child_processes: options.child_processes,
        });
      // Python MacRunner creates a new production factory for each native start.
      // This explicit memory is factory-local, unlike the VPS's durable rejection row.
      return create_codex_backend_child_factory(launches.codex, {
        child_processes: options.child_processes,
        remembered_login_rejection: new RememberedCodexLoginRejection(),
      });
    },
    native_skill_home(backend) {
      if (backend === 'codex') return codex_home(environment);
      if (backend === 'hermes') return hermes_home;
      return environment.CLAUDE_CONFIG_DIR
        ? expand(environment.CLAUDE_CONFIG_DIR)
        : expand('~/.claude');
    },
  };
}
