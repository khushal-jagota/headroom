/** Startup catalog probes never submit a user prompt or select another executable. */
import { EventEmitter } from 'node:events';
import { afterEach, expect, it, vi } from 'vitest';
import { spawn } from 'node:child_process';
import { query } from '@anthropic-ai/claude-agent-sdk';
import { ensure_cli_compatible } from './cli_compatibility.ts';
import { probe_claude_model_catalog } from './claude_model_catalog.ts';

vi.mock('node:child_process', () => ({ spawn: vi.fn() }));
vi.mock('@anthropic-ai/claude-agent-sdk', () => ({ query: vi.fn() }));
vi.mock('./cli_compatibility.ts', () => ({ ensure_cli_compatible: vi.fn() }));
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
  vi.mocked(ensure_cli_compatible).mockResolvedValue(undefined);
  const child = Object.assign(new EventEmitter(), {
    exitCode: null as number | null,
    signalCode: null as string | null,
    kill: vi.fn(() => {
      child.signalCode = 'SIGKILL';
      queueMicrotask(() => child.emit('close', null));
      return true;
    }),
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
    return { initializationResult: () => new Promise(() => {}), close } as never;
  });
  const result = expect(probe_claude_model_catalog('/synthetic/selected-cli')).rejects.toThrow(
    'claude did not finish its startup handshake in time',
  );
  await vi.advanceTimersByTimeAsync(30_000);
  await result;
  expect(close).toHaveBeenCalled();
  expect(child.kill).toHaveBeenCalledExactlyOnceWith('SIGKILL');
  expect(spawn).toHaveBeenCalledExactlyOnceWith('/synthetic/selected-cli', [], {
    cwd: '/synthetic/workspace',
    env: {},
    stdio: ['pipe', 'pipe', 'pipe'],
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
    exitCode: null as number | null,
    signalCode: null as string | null,
    kill: vi.fn(() => {
      child.signalCode = 'SIGKILL';
      queueMicrotask(() => child.emit('close', null));
      return true;
    }),
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
  expect(child.kill).toHaveBeenCalledExactlyOnceWith('SIGKILL');
});
