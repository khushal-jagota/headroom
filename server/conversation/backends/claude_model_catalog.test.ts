/** Startup catalog probes never submit a user prompt or select another executable. */
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { afterEach, expect, it, vi } from 'vitest';
import { spawn } from 'node:child_process';
import { load_claude_sdk } from './claude_sdk.ts';
import { ensure_cli_compatible } from './cli_compatibility.ts';
import { probe_claude_model_catalog } from './claude_model_catalog.ts';
import { OneShotChildren } from '../../lifecycle/one_shot_children.ts';
import { Deferred } from '../runtime/state.ts';

const { query } = vi.hoisted(() => ({
  query: vi.fn<(typeof import('@anthropic-ai/claude-agent-sdk'))['query']>(),
}));

vi.mock('node:child_process', () => ({ spawn: vi.fn(), spawnSync: vi.fn() }));
vi.mock('@anthropic-ai/claude-agent-sdk', () => ({ query }));
vi.mock('./cli_compatibility.ts', () => ({ ensure_cli_compatible: vi.fn() }));
await load_claude_sdk();
afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  vi.useRealTimers();
});
const models = [
  { value: 'haiku', resolvedModel: 'claude-haiku-4-5', supportedEffortLevels: ['low'] },
];

it('checks before the query, uses the same explicit path, and closes an input with no prompts', async () => {
  const calls: string[] = [];
  vi.mocked(ensure_cli_compatible).mockImplementation(async () => {
    calls.push('version');
  });
  const close = vi.fn();
  vi.mocked(query).mockImplementation((options) => {
    calls.push('query');
    expect(options.options?.pathToClaudeCodeExecutable).toBe('/synthetic/selected-cli');
    expect(options.options?.maxTurns).toBe(1);
    return { initializationResult: async () => ({ models }), close } as never;
  });
  const result = await probe_claude_model_catalog('/synthetic/selected-cli');
  expect(calls).toEqual(['version', 'query']);
  expect(result.models[0]?.model_id).toBe('claude-haiku-4-5');
  expect(close).toHaveBeenCalled();
  const options = vi.mocked(query).mock.calls[0]![0];
  expect(await (options.prompt as AsyncIterable<unknown>)[Symbol.asyncIterator]().next()).toEqual({
    value: undefined,
    done: true,
  });
  expect(spawn).not.toHaveBeenCalled();
});

it('does not construct an SDK query after a refused version check', async () => {
  vi.mocked(ensure_cli_compatible).mockRejectedValueOnce(new Error('selected CLI too old'));
  await expect(probe_claude_model_catalog('/synthetic/old-cli')).rejects.toThrow(
    'selected CLI too old',
  );
  expect(query).not.toHaveBeenCalled();
  expect(spawn).not.toHaveBeenCalled();
});

it('closes the exact probe on timeout and reaps only its recorded child', async () => {
  vi.useFakeTimers();
  const children = new OneShotChildren();
  const initialization = new Deferred<unknown>();
  const signal_group = vi.spyOn(process, 'kill').mockReturnValue(true);
  vi.mocked(ensure_cli_compatible).mockResolvedValue(undefined);
  const child = Object.assign(new EventEmitter(), {
    pid: 424242,
    exitCode: null as number | null,
    signalCode: null as string | null,
    kill: vi.fn(() => true),
    stdin: new PassThrough(),
    stdout: new PassThrough(),
    stderr: new PassThrough(),
  });
  vi.mocked(spawn).mockReturnValue(child as never);
  const close = vi.fn();
  vi.mocked(query).mockImplementation((options) => {
    options.options!.spawnClaudeCodeProcess!({
      command: '/synthetic/selected-cli',
      args: [],
      cwd: '/synthetic/workspace',
      env: {},
    } as never);
    return { initializationResult: () => initialization.promise, close } as never;
  });
  const probing = probe_claude_model_catalog('/synthetic/selected-cli', children);
  let settled = false;
  void probing
    .finally(() => {
      settled = true;
    })
    .catch(() => {});
  const result = expect(probing).rejects.toThrow(
    'claude did not finish its startup handshake in time',
  );
  await vi.advanceTimersByTimeAsync(30_000);
  expect(close).toHaveBeenCalled();
  expect(signal_group).toHaveBeenCalledExactlyOnceWith(-child.pid, 'SIGKILL');
  expect(settled).toBe(false);
  expect(ensure_cli_compatible).toHaveBeenCalledWith('claude', '/synthetic/selected-cli', {
    children,
  });
  child.signalCode = 'SIGKILL';
  child.emit('exit', null, 'SIGKILL');
  child.emit('close', null);
  await vi.advanceTimersByTimeAsync(0);
  expect(settled).toBe(false);
  initialization.resolve({ models });
  await result;
  children.seal();
  await children.settled();
  expect(spawn).toHaveBeenCalledExactlyOnceWith('/synthetic/selected-cli', [], {
    cwd: '/synthetic/workspace',
    env: expect.objectContaining({
      CLAUDE_CODE_ENTRYPOINT: 'sdk-ts',
      CLAUDE_AGENT_SDK_VERSION: '0.3.316',
    }),
    stdio: ['pipe', 'pipe', 'pipe'],
    detached: process.platform !== 'win32',
  });
});

it('leaves malformed catalog transformation errors outside the startup error wrapper', async () => {
  vi.mocked(ensure_cli_compatible).mockResolvedValue(undefined);
  vi.mocked(query).mockReturnValue({
    initializationResult: async () => ({
      models: [{ value: 'haiku', resolvedModel: 'claude-haiku', supportedEffortLevels: 7 }],
    }),
    close: vi.fn(),
  } as never);
  await expect(probe_claude_model_catalog('/synthetic/selected-cli')).rejects.toBeInstanceOf(
    TypeError,
  );
});

it('retains the startup failure and reaps its child if SDK close also fails', async () => {
  vi.mocked(ensure_cli_compatible).mockResolvedValue(undefined);
  const child = Object.assign(new EventEmitter(), {
    pid: 424242,
    exitCode: null as number | null,
    signalCode: null as string | null,
    kill: vi.fn(),
    stdin: new PassThrough(),
    stdout: new PassThrough(),
    stderr: new PassThrough(),
  });
  const signal_group = vi.spyOn(process, 'kill').mockImplementation(() => {
    child.signalCode = 'SIGKILL';
    queueMicrotask(() => {
      child.emit('exit', null, 'SIGKILL');
      child.emit('close', null);
    });
    return true;
  });
  vi.mocked(spawn).mockReturnValue(child as never);
  vi.mocked(query).mockImplementation((options) => {
    options.options!.spawnClaudeCodeProcess!({
      command: '/synthetic/selected-cli',
      args: [],
      cwd: '/synthetic/workspace',
      env: {},
    } as never);
    return {
      initializationResult: async () => {
        throw new Error('primary startup failure');
      },
      close: () => {
        throw new Error('secondary close failure');
      },
    } as never;
  });
  await expect(probe_claude_model_catalog('/synthetic/selected-cli')).rejects.toThrow(
    'primary startup failure',
  );
  expect(signal_group).toHaveBeenCalledExactlyOnceWith(-child.pid, 'SIGKILL');
});
