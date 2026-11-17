/** Mac Claude launches keep the owner's default Keychain login unless a folder is configured. */
import { expect, test, vi } from 'vitest';
import { join } from 'node:path';
import type { ResolvedConversationStart } from '../../conversation/contracts.ts';
import type { ClaudeAgentSdkChildLaunch } from '../../drivers/claude/runtime/adapter.ts';
import type { OwnedChildProcesses, OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import type { WorkerSupervisionRuntime } from '../../worker_supervision/runtime.ts';
import { BackendSpawnFailed } from '../../conversation/backends/contracts.ts';
import { create_mac_adapter_host, load_executor_drivers } from './adapters.ts';

const { claude_launches } = vi.hoisted(() => ({
  claude_launches: [] as ClaudeAgentSdkChildLaunch[],
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
  expect(host.drivers.map((driver) => driver.driver_kind)).toEqual(['hermes', 'codex', 'claude']);
  const start = { backend_key: 'unknown_backend' } as ResolvedConversationStart;
  expect(() => host.factory(start)).toThrow(BackendSpawnFailed);
  expect(() => host.native_skill_home('unknown_backend')).toThrow(
    'This machine cannot run the unknown_backend backend.',
  );
});
