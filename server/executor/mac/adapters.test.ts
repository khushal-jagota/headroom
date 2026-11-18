/** Mac Claude launches keep the owner's default Keychain login unless a folder is configured. */
import { expect, test, vi } from 'vitest';
import { join } from 'node:path';
import {
  ConversationAccess,
  ConversationRoleMaterials,
  ResolvedConversationStart,
  WorkspacePathPolicy,
} from '../../conversation/contracts.ts';
import type { AcpFlavor } from '../../drivers/acp/flavor.ts';
import { acp_child_environment } from '../../drivers/acp/environment.ts';
import type { ExecutorCatalogCustody } from '../../drivers/core/executor.ts';
import { parse_backend_instance_id } from '../../drivers/core/identity.ts';
import type { ClaudeAgentSdkChildLaunch } from '../../drivers/claude/runtime/adapter.ts';
import type { AntigravityLaunchOptions } from '../../drivers/antigravity/runtime/flavor.ts';
import { AntigravityModelCatalog } from '../../drivers/antigravity/runtime/catalog.ts';
import type { OwnedChildProcesses, OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import type { WorkerSupervisionRuntime } from '../../worker_supervision/runtime.ts';
import { BackendSpawnFailed } from '../../conversation/backends/contracts.ts';
import { create_mac_adapter_host, load_executor_drivers } from './adapters.ts';
import { BUILT_IN_DRIVERS } from '../../drivers/built_in.ts';

const { claude_launches, antigravity_flavors } = vi.hoisted(() => ({
  claude_launches: [] as ClaudeAgentSdkChildLaunch[],
  antigravity_flavors: [] as { options: AntigravityLaunchOptions; flavor: AcpFlavor }[],
}));
vi.mock('../../drivers/claude/runtime/adapter.ts', async (original) => ({
  ...(await original<typeof import('../../drivers/claude/runtime/adapter.ts')>()),
  create_claude_backend_child_factory: (launch: ClaudeAgentSdkChildLaunch) => {
    claude_launches.push(launch);
    return () => {
      throw new Error('This proof must not start Claude.');
    };
  },
}));
vi.mock('../../drivers/antigravity/runtime/flavor.ts', async (original) => {
  const actual = await original<typeof import('../../drivers/antigravity/runtime/flavor.ts')>();
  return {
    ...actual,
    create_antigravity_flavor: (
      options: AntigravityLaunchOptions,
      one_shot_children: OwnedOneShotChildren,
    ) => {
      const flavor = actual.create_antigravity_flavor(options, one_shot_children);
      antigravity_flavors.push({ options, flavor });
      return flavor;
    },
  };
});
vi.mock('../../conversation/backends/cli_compatibility.ts', async (original) => ({
  ...(await original<typeof import('../../conversation/backends/cli_compatibility.ts')>()),
  ensure_cli_compatible: vi.fn(),
}));

const home = '/synthetic/owner';
const drivers = await load_executor_drivers();
function mac_host(environment: NodeJS.ProcessEnv, platform: NodeJS.Platform = 'darwin') {
  return create_mac_adapter_host({
    drivers,
    panels_server_url: 'http://127.0.0.1:9',
    child_processes: {} as OwnedChildProcesses,
    one_shot_children: {} as OwnedOneShotChildren,
    worker_supervision: {} as WorkerSupervisionRuntime,
    environment: { HOME: home, ...environment },
    executable_path: () => null,
    platform,
  });
}
function claude_launch(environment: NodeJS.ProcessEnv, platform: NodeJS.Platform = 'darwin') {
  claude_launches.length = 0;
  const host = mac_host(environment, platform);
  host.factory({ backend_key: 'claude' } as ResolvedConversationStart);
  expect(claude_launches).toHaveLength(1);
  return {
    host,
    environment: new Map(claude_launches[0]!.environment_overrides),
  };
}

test('an unconfigured Mac Claude launch omits CLAUDE_CONFIG_DIR but publishes to ~/.claude', () => {
  for (const environment of [{}, { CLAUDE_CONFIG_DIR: '' }]) {
    const launch = claude_launch(environment);
    expect(launch.environment.has('CLAUDE_CONFIG_DIR')).toBe(false);
    expect(launch.environment.get('PLAN_SERVER_URL')).toBe('http://127.0.0.1:9');
    expect('CLAUDE_CONFIG_DIR' in launch.host.environment).toBe(false);
    expect(launch.host.native_skill_home('claude')).toEqual({
      path: join(home, '.claude'),
      skills_directory: 'skills',
    });
  }
});

test('an explicit Mac CLAUDE_CONFIG_DIR is passed through unchanged', () => {
  const configured = '/synthetic/configured-claude';
  const launch = claude_launch({ CLAUDE_CONFIG_DIR: configured });
  expect(launch.environment.get('CLAUDE_CONFIG_DIR')).toBe(configured);
  expect(launch.host.environment.CLAUDE_CONFIG_DIR).toBe(configured);
  expect(launch.host.native_skill_home('claude').path).toBe(configured);
});

test('the shared Linux executor host still names the resolved Claude folder', () => {
  const launch = claude_launch({}, 'linux');
  expect(launch.environment.get('CLAUDE_CONFIG_DIR')).toBe(join(home, '.claude'));
  expect(launch.host.native_skill_home('claude').path).toBe(join(home, '.claude'));
});

test('a backend this build has no driver for is refused, not launched as another', () => {
  const host = mac_host({});
  const kinds = host.drivers.map((driver) => driver.driver_kind);
  expect(kinds).toEqual(BUILT_IN_DRIVERS.map((driver) => driver.driver_kind));
  expect(kinds).not.toContain('hermes');
  const start = { backend_key: 'hermes' } as ResolvedConversationStart;
  expect(() => host.factory(start)).toThrow(BackendSpawnFailed);
  expect(() => host.native_skill_home('hermes')).toThrow(
    'This machine cannot run the hermes backend.',
  );
});

// Risk: Antigravity on an executor omits GEMINI_HOME or leaks host Google credentials into agy acp,
// or fails to wire catalog probes and shutdown through ExecutorCatalogCustody.
test('Antigravity runs on Mac and Linux executor hosts without leaking host Google credentials', async () => {
  antigravity_flavors.length = 0;
  const host = create_mac_adapter_host({
    drivers,
    panels_server_url: 'http://127.0.0.1:9',
    child_processes: {} as OwnedChildProcesses,
    one_shot_children: {} as OwnedOneShotChildren,
    worker_supervision: {} as WorkerSupervisionRuntime,
    environment: {
      HOME: home,
      PATH: '/synthetic/bin',
      GEMINI_API_KEY: 'secret-gemini-key',
      GOOGLE_APPLICATION_CREDENTIALS: '/synthetic/adc.json',
    },
    executable_path: (name) => (name === 'agy' ? '/synthetic/bin/agy' : null),
  });
  expect(host.native_skill_home('antigravity')).toEqual({
    path: join(home, '.gemini'),
    skills_directory: 'config/skills',
  });
  const start = new ResolvedConversationStart({
    conversation_id: 'conv-1',
    backend_key: parse_backend_instance_id('antigravity'),
    model: 'gemini-4-hydrogen',
    reasoning_effort: 'high',
    role_materials: new ConversationRoleMaterials({
      role_text: '',
      identity_environment_variables: [['PLAN_WORKER_PROCESS_ROOT', '/synthetic/workers']],
    }),
    workspace_folder: '/synthetic/work',
    workspace_path_policy: WorkspacePathPolicy.absolute,
    access: ConversationAccess.full,
  });
  host.factory(start);
  expect(antigravity_flavors).toHaveLength(1);
  const { options, flavor } = antigravity_flavors[0]!;
  expect(options.executable).toBe('/synthetic/bin/agy');
  expect(options.home).toBe(join(home, '.gemini'));
  expect(options.worker_process_root).toBeUndefined();
  const launch = await flavor.launch(start);
  const child_env = acp_child_environment(
    launch.environment_overrides,
    start,
    launch.inherited_environment,
  );
  expect(child_env).toMatchObject({
    HOME: home,
    PATH: '/synthetic/bin',
    GEMINI_HOME: join(home, '.gemini'),
    BROWSER: 'false',
    PLAN_SERVER_URL: 'http://127.0.0.1:9',
    PLAN_WORKER_PROCESS_ROOT: '/synthetic/workers',
  });
  expect(child_env).not.toHaveProperty('GEMINI_API_KEY');
  expect(child_env).not.toHaveProperty('GOOGLE_APPLICATION_CREDENTIALS');

  const antigravity = host.drivers.find((driver) => driver.driver_kind === 'antigravity')!;
  const read = vi
    .spyOn(AntigravityModelCatalog.prototype, 'read')
    .mockResolvedValue({ signed_in: false });
  const shutdown = vi.spyOn(AntigravityModelCatalog.prototype, 'shutdown').mockResolvedValue();
  try {
    const signal = new AbortController().signal;
    const stop_options = { deadline: 123 };
    const custody: ExecutorCatalogCustody = {
      probe: (fn) => fn(signal),
      retain: (error) => error,
      log: () => {},
      stop_options: () => stop_options,
    };
    const source = antigravity.executor.catalog(antigravity.host, custody);
    const card = await source.status.probe({
      environment: {
        executable_path: () => '/synthetic/bin/agy',
        real_path: (p) => p,
        user_local_npm_prefix: () => '/synthetic/npm',
        prefix_is_owned_and_writable: () => false,
        latest_released_version: async () => null,
        run: async () => ({ exit_code: 0, standard_output: '1.5.10', standard_error: '' }),
      },
      model_version: () => [],
      discover_update_advisory: false,
      refresh: true,
      now: new Date(),
    });
    expect(read).toHaveBeenCalledWith('/synthetic/bin/agy', signal);
    expect(card.identity?.status).toBe('unauthenticated');
    await source.shutdown?.();
    expect(shutdown).toHaveBeenCalledWith(stop_options);
  } finally {
    read.mockRestore();
    shutdown.mockRestore();
  }
});
