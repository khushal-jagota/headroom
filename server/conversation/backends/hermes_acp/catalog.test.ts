import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { afterEach, expect, test, vi } from 'vitest';
import { spawn_codex_process } from '../codex_app_server/process.ts';
import type { OwnedCodexProcess } from '../codex_app_server/process.ts';
import { HermesModelCatalogOwner } from './catalog.ts';

vi.mock('../codex_app_server/process.ts', async (original) => ({
  ...(await original<typeof import('../codex_app_server/process.ts')>()),
  spawn_codex_process: vi.fn(),
}));
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});
function fake() {
  const child = Object.assign(new EventEmitter(), {
    stdin: new PassThrough(),
    stdout: new PassThrough(),
    stderr: new PassThrough(),
    exitCode: null as number | null,
    signalCode: null,
  });
  let exit!: () => void;
  const exited = new Promise<void>((resolve) => {
    exit = resolve;
  });
  const finish = (stdout = '', stderr = '', code = 0) => {
    child.stdout.end(stdout);
    child.stderr.end(stderr);
    child.exitCode = code;
    exit();
  };
  const stop = vi.fn(async () => {
    finish();
    child.stdin.destroy();
  });
  return {
    child,
    exited,
    stop,
    finish,
    output_failed: new Promise<Error>(() => {}),
    output_error: null,
  };
}
const launch = {
  argv: ['/synthetic/python', '/synthetic/inventory.py'],
  environment_overrides: [['HERMES_HOME', '/synthetic/home']] as const,
};
const owned = (value: ReturnType<typeof fake>) => value as unknown as OwnedCodexProcess;

test('catalog inherits the full environment, appends refresh, and leaves parent values unchanged', async () => {
  vi.stubEnv('UNIT16_CATALOG_INHERITED', 'synthetic inherited value');
  const child = fake();
  vi.mocked(spawn_codex_process).mockImplementationOnce(async () => {
    setImmediate(() => child.finish('{not-json', 'diagnostic', 0));
    return owned(child);
  });
  const before = { ...globalThis.process.env };
  const child_processes = new ChildProcesses();
  const owner = new HermesModelCatalogOwner(launch, { child_processes });
  await expect(owner.source({ refresh: true })).resolves.toEqual({
    exit_code: 0,
    standard_output: '{not-json',
    standard_error: 'diagnostic',
  });
  expect(vi.mocked(spawn_codex_process).mock.lastCall).toEqual([
    [...launch.argv, '--refresh'],
    {
      child_processes,
      environment: { ...before, HERMES_HOME: '/synthetic/home' },
      working_directory: globalThis.process.cwd(),
      close_stdin: true,
    },
  ]);
  expect(globalThis.process.env).toEqual(before);
  await owner.shutdown();
});

test('cancel joins its current child and permits a later fresh catalog read', async () => {
  const first = fake();
  const second = fake();
  vi.mocked(spawn_codex_process)
    .mockResolvedValueOnce(owned(first))
    .mockImplementationOnce(async () => {
      setImmediate(() => second.finish('fresh'));
      return owned(second);
    });
  const owner = new HermesModelCatalogOwner(launch, { child_processes: new ChildProcesses() });
  const pending = owner.source({ refresh: false });
  const rejected = expect(pending).rejects.toThrow('cancelled');
  await owner.cancel();
  await rejected;
  expect(first.stop).toHaveBeenCalledTimes(1);
  await expect(owner.source({ refresh: false })).resolves.toMatchObject({
    standard_output: 'fresh',
  });
  await owner.shutdown();
});

test('shutdown seals a pending spawn and joins it before completion', async () => {
  const child = fake();
  let release!: (value: OwnedCodexProcess) => void;
  vi.mocked(spawn_codex_process).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  );
  const owner = new HermesModelCatalogOwner(launch, { child_processes: new ChildProcesses() });
  const pending = owner.source({ refresh: false });
  const rejected = expect(pending).rejects.toThrow('cancelled');
  const stopping = owner.shutdown();
  const calls = vi.mocked(spawn_codex_process).mock.calls.length;
  await expect(owner.source({ refresh: true })).rejects.toThrow('shutdown');
  expect(vi.mocked(spawn_codex_process).mock.calls.length).toBe(calls);
  release(owned(child));
  await stopping;
  await rejected;
  expect(child.stop).toHaveBeenCalledTimes(1);
});

test('failed cleanup stays retryable and logs once', async () => {
  const child = fake();
  child.stop.mockRejectedValueOnce(new Error('synthetic cleanup failure'));
  vi.mocked(spawn_codex_process).mockResolvedValueOnce(owned(child));
  const errors: unknown[] = [];
  const owner = new HermesModelCatalogOwner(launch, {
    child_processes: new ChildProcesses(),
    log: (error) => errors.push(error),
  });
  const pending = owner.source({ refresh: false });
  const rejected = expect(pending).rejects.toThrow('catalog process did not stop');
  await expect(owner.shutdown()).rejects.toThrow('catalog process did not stop');
  await rejected;
  await owner.shutdown();
  expect(child.stop).toHaveBeenCalledTimes(2);
  expect(errors).toHaveLength(1);
});
