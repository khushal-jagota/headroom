/** Host the same provider factories with this Mac's account and launch settings. */
import type { ConversationBackendKey } from '../wire.generated.ts';
import { copy_environment } from '../../core/environment.ts';
import { provider_launch_settings } from '../../core/config.ts';
import { claude_executable_override } from '../../conversation/snapshot/environment.ts';
import type { ResolvedConversationStart } from '../../conversation/contracts.ts';
import type { BackendChildFactory } from '../../conversation/backends/contracts.ts';
import { create_claude_backend_child_factory } from '../../drivers/claude/runtime/adapter.ts';
import type { ClaudeAgentSdkChildLaunch } from '../../drivers/claude/runtime/adapter.ts';
import {
  codex_app_server_child_launch,
  create_codex_backend_child_factory,
} from '../../drivers/codex/runtime/adapter.ts';
import type { CodexChildLaunch } from '../../drivers/codex/runtime/adapter.ts';
import { codex_home, RememberedCodexLoginRejection } from '../../drivers/codex/runtime/login.ts';
import { create_hermes_backend_child_factory } from '../../drivers/hermes/runtime/adapter.ts';
import {
  hermes_acp_child_launch,
  hermes_source_root,
} from '../../drivers/hermes/runtime/launch.ts';
import type { AcpChildLaunch } from '../../drivers/hermes/runtime/launch.ts';
import type { OwnedChildProcesses, OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import { expand_executor_path } from './platform.ts';
import type { WorkerSupervisionRuntime } from '../../worker_supervision/runtime.ts';
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
  readonly worker_supervision: WorkerSupervisionRuntime;
  readonly environment?: Readonly<NodeJS.ProcessEnv>;
  /** Defaults to this process's platform; the Linux executor shares this host. */
  readonly platform?: NodeJS.Platform;
}
export interface MacAdapterHost {
  readonly environment: Readonly<NodeJS.ProcessEnv>;
  executable_path(name: string): string | null;
  readonly launches: MacBackendLaunches;
  readonly hermes_paths: {
    readonly python: string;
    readonly home: string;
    readonly source_root: string;
  };
  factory(start: ResolvedConversationStart): BackendChildFactory;
  native_skill_home(backend: ConversationBackendKey): string;
}
export function create_mac_adapter_host(options: MacAdapterOptions): MacAdapterHost {
  const settings = provider_launch_settings(options.environment);
  const environment = settings.environment;
  const expand = (path: string) => expand_executor_path(path, environment);
  const executables = new Map(
    ['claude', 'codex'].map((name) => [name, options.executable_path(name)]),
  );
  const on_path = (name: string) => executables.get(name) ?? name;
  const hermes_python = expand(settings.hermes_python);
  const hermes_home = expand(settings.hermes_home);
  const override = claude_executable_override(environment, expand);
  environment.PLAN_HERMES_PYTHON = hermes_python;
  environment.PLAN_HERMES_HOME = hermes_home;
  if (override !== undefined) environment.PLAN_CLAUDE_EXECUTABLE = override;
  environment.CODEX_HOME = expand(settings.codex_home);
  // Panels needs the resolved folder for skills, but a set CLAUDE_CONFIG_DIR, even the
  // default, makes macOS Claude read a suffixed Keychain item instead of the owner's login.
  const claude_home = expand(settings.claude_home);
  if ((options.platform ?? process.platform) !== 'darwin' || environment.CLAUDE_CONFIG_DIR)
    environment.CLAUDE_CONFIG_DIR = claude_home;
  else delete environment.CLAUDE_CONFIG_DIR;
  const launch_environment = Object.entries(copy_environment(environment));
  const native_launch = <
    T extends { environment_overrides?: readonly (readonly [string, string])[] },
  >(
    launch: T,
  ): T => ({
    ...launch,
    environment_overrides: [...launch_environment, ...(launch.environment_overrides ?? [])],
  });
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
    environment,
    executable_path: (name) => executables.get(name) ?? null,
    launches,
    hermes_paths: {
      python: hermes_python,
      home: hermes_home,
      source_root: hermes_source_root(hermes_python),
    },
    factory(start) {
      if (start.backend_key === 'claude')
        return create_claude_backend_child_factory(native_launch(launches.claude), {
          child_processes: options.child_processes,
          one_shot_children: options.one_shot_children,
          worker_supervision: options.worker_supervision,
        });
      if (start.backend_key === 'hermes')
        return create_hermes_backend_child_factory(native_launch(launches.hermes), {
          child_processes: options.child_processes,
          worker_supervision: options.worker_supervision,
        });
      // This explicit memory is factory-local, unlike the VPS's durable rejection row.
      return create_codex_backend_child_factory(native_launch(launches.codex), {
        child_processes: options.child_processes,
        worker_supervision: options.worker_supervision,
        remembered_login_rejection: new RememberedCodexLoginRejection(),
      });
    },
    native_skill_home(backend) {
      if (backend === 'codex') return codex_home(environment);
      if (backend === 'hermes') return hermes_home;
      return claude_home;
    },
  };
}
