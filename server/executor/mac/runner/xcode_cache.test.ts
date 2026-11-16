/** Cache delivery is new: use the native start fixture through the backend factory.
 * No provider, Git process, server, or Xcode runs in this test.
 */
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { platform, tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, vi } from 'vitest';
import { ExecutorPaths } from '../configuration.ts';
import { native_fixture_options } from '../testing/runner_fixture.ts';
import { MacNativeChildren } from './start_stop.ts';
import type { StartRun } from '../../wire.generated.ts';
import { prepare_xcode_compilation_cache } from './xcode_cache.ts';

vi.mock('node:os', async (original) => ({
  ...(await original<typeof import('node:os')>()),
  platform: vi.fn(() => 'darwin'),
}));
vi.mock('../../../ticket_workspaces/filesystem.ts', async (original) => ({
  ...(await original<typeof import('../../../ticket_workspaces/filesystem.ts')>()),
  async prepare_workspace(directory: string) {
    const workspace = join(directory, 'worktree');
    await mkdir(workspace, { recursive: true });
    return workspace;
  },
}));

test('managed Mac starts deliver a shared cache through the backend without shared build products', async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'mac-xcode-cache-')));
  const paths = new ExecutorPaths(root);
  const inherited = join(root, 'native configuration.xcconfig');
  await writeFile(inherited, 'PRODUCT_NAME = NativeName\n');
  vi.stubEnv('XCODE_XCCONFIG_FILE', inherited);
  const configurations: string[] = [];
  let starts = 0;
  const unexpected = async (): Promise<never> => {
    throw new Error('No prompt belongs to this launch proof');
  };
  try {
    for (const mode of ['mac-one', 'mac-two', 'linux', 'unmanaged'] as const) {
      vi.mocked(platform).mockReturnValue(mode === 'linux' ? 'linux' : 'darwin');
      const managed = mode !== 'unmanaged';
      const reference = {
        executor_incarnation: 'synthetic',
        child_id: mode,
        conversation_id: mode,
      };
      const input: StartRun = {
        canonical_skill_snapshot: { required_skill_names: [], sources: [] },
        resolved_start: {
          conversation_id: mode,
          backend_key: 'claude',
          model: 'synthetic',
          reasoning_effort: null,
          access: 'full',
          workspace_folder: root,
          workspace_path_policy: 'absolute',
          role_materials: {
            role_text: '',
            identity_environment_variables: [
              ['PLAN_TICKET_ID', mode],
              ['PLAN_TICKET_WORKSPACE_MANAGED', managed ? '1' : '0'],
              ['XCODE_XCCONFIG_FILE', '/remote/must-not-reach-native.xcconfig'],
            ],
          },
        },
      };
      const owner = new MacNativeChildren({
        ...native_fixture_options({
          paths,
          workspace_home: root,
          native_skill_home: () => root,
          snapshot_directory: root,
          on_failure: (error) => {
            throw error;
          },
          factory: (start) => {
            const environment = new Map(start.role_materials!.identity_environment_variables);
            const configuration = environment.get('XCODE_XCCONFIG_FILE');
            if (mode.startsWith('mac-')) {
              expect(configuration).toBe(
                join(paths.artifacts, mode, 'xcode-compilation-cache.xcconfig'),
              );
              configurations.push(configuration!);
            } else expect(configuration).toBeUndefined();
            return () => ({
              stop_outside_lock: false,
              withdraw_delivery: unexpected,
              detach: unexpected,
              write_prompt: unexpected,
              steer: unexpected,
              cancel_running_turn: unexpected,
              answer_permission_ask: unexpected,
              answer_user_input: unexpected,
              async start(resolved) {
                expect(resolved).toBe(start);
                starts++;
              },
              async stop() {},
            });
          },
        }),
        async register_workspace() {},
        event_sink: () => ({ async report() {} }),
      });
      try {
        await owner.start(reference, input, () => {});
      } finally {
        await owner.stop({ deadline: performance.now() / 1000 + 2 });
        await owner.settled();
      }
    }
    expect(starts).toBe(4);
    const texts = await Promise.all(configurations.map((path) => readFile(path, 'utf8')));
    expect(texts[0]).toBe(texts[1]);
    expect(texts[0]).toContain(`#include "${inherited}"`);
    expect(texts[0]).toContain(`COMPILATION_CACHE_CAS_PATH = ${join(root, 'cache', 'xcode')}`);
    expect(texts[1]).toContain(`COMPILATION_CACHE_CAS_PATH = ${join(root, 'cache', 'xcode')}`);
    expect(texts[0]).not.toMatch(/DERIVED_DATA|SYMROOT|OBJROOT|MODULE_CACHE/);
    await rm(join(paths.artifacts, 'mac-one'), { recursive: true });
    expect(await readFile(configurations[1]!, 'utf8')).toBe(texts[1]);
  } finally {
    vi.unstubAllEnvs();
    vi.mocked(platform).mockReturnValue('darwin');
    await rm(root, { recursive: true, force: true });
  }
});

test('native configuration paths cannot inject xcconfig lines', async () => {
  await expect(
    prepare_xcode_compilation_cache('/synthetic', '/synthetic/ticket', 'bad"\nSETTING=YES'),
  ).rejects.toThrow('unsupported characters');
});
