/** Inspect the real pinned SDK's catalog launch without starting any process. */
import child_process from 'node:child_process';
import type { SpawnOptions } from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import { expect, it, vi } from 'vitest';
import { ensure_cli_compatible } from './cli_compatibility.ts';

vi.mock('./cli_compatibility.ts', () => ({
  ensure_cli_compatible: vi.fn().mockResolvedValue(undefined),
}));

it('pins actual SDK catalog metadata despite inherited Python SDK markers', async () => {
  const methods = [
    'spawn',
    'spawnSync',
    'exec',
    'execSync',
    'execFile',
    'execFileSync',
    'fork',
  ] as const;
  const originals = new Map(methods.map((name) => [name, child_process[name]]));
  const unexpected: string[] = [];
  const sentinel = new Error('SDK metadata probe stops before a process starts');
  let captured: { command: string; args: readonly string[]; options: SpawnOptions } | undefined;
  for (const name of methods) {
    Object.defineProperty(child_process, name, {
      configurable: true,
      writable: true,
      value: (...args: unknown[]) => {
        if (name === 'spawn' && args[0] === '/synthetic/claude-metadata-probe') {
          captured = {
            command: args[0],
            args: args[1] as readonly string[],
            options: args[2] as SpawnOptions,
          };
          throw sentinel;
        }
        unexpected.push(name);
        throw new Error(`Process API ${name} is forbidden in the SDK metadata probe`);
      },
    });
  }
  syncBuiltinESMExports();
  vi.stubEnv('CLAUDE_CODE_ENTRYPOINT', 'sdk-py');
  vi.stubEnv('CLAUDE_AGENT_SDK_VERSION', '0.2.152');
  vi.stubEnv('SDK_INHERITED_TEST_VALUE', 'inherited');
  vi.stubEnv('CLAUDECODE', '1');
  vi.stubEnv('NODE_OPTIONS', '--sdk-environment-probe');
  vi.stubEnv('DEBUG', 'sdk-environment-probe');
  try {
    // Builtin process entry points are fail-closed before the real SDK loads.
    const { probe_claude_model_catalog } = await import('./claude_model_catalog.ts');
    const expected_environment: NodeJS.ProcessEnv = {
      ...process.env,
      CLAUDE_CODE_ENTRYPOINT: 'sdk-ts',
      CLAUDE_AGENT_SDK_VERSION: '0.3.316',
    };
    delete expected_environment.CLAUDECODE;
    await expect(probe_claude_model_catalog('/synthetic/claude-metadata-probe')).rejects.toThrow(
      sentinel.message,
    );
    expect(ensure_cli_compatible).toHaveBeenCalledExactlyOnceWith(
      'claude',
      '/synthetic/claude-metadata-probe',
    );
    expect(captured).toBeDefined();
    expect(captured!.command).toBe('/synthetic/claude-metadata-probe');
    expect(captured!.options.env).toEqual(expected_environment);
    expect(captured!.options.env!.NODE_OPTIONS).toBe('--sdk-environment-probe');
    expect(captured!.options.env!.DEBUG).toBe('sdk-environment-probe');
    expect(captured!.options.env).not.toHaveProperty('CLAUDECODE');
    expect(captured!.options.env!.CLAUDE_CODE_ENTRYPOINT).toBe('sdk-ts');
    expect(captured!.options.env!.CLAUDE_AGENT_SDK_VERSION).toBe('0.3.316');
    expect(captured!.options.env!.SDK_INHERITED_TEST_VALUE).toBe('inherited');
    expect(captured!.options.env!.HOME).toBe(process.env.HOME);
    expect(unexpected).toEqual([]);
  } finally {
    for (const name of methods) {
      Object.defineProperty(child_process, name, {
        configurable: true,
        writable: true,
        value: originals.get(name),
      });
    }
    syncBuiltinESMExports();
    vi.unstubAllEnvs();
  }
});
