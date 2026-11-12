import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { afterEach, expect, test, vi } from 'vitest';
import { ConversationAccess } from '../../contracts.ts';
import type { ResolvedConversationStart } from '../../contracts.ts';
import { spawn_codex_process } from '../codex_app_server/process.ts';
import type { OwnedCodexProcess } from '../codex_app_server/process.ts';
import { HermesAcpLauncher } from './client.ts';

vi.mock('../codex_app_server/process.ts', async (original) => ({
  ...(await original<typeof import('../codex_app_server/process.ts')>()),
  spawn_codex_process: vi.fn(),
}));
afterEach(() => vi.restoreAllMocks());
function fake() {
  const child = Object.assign(new EventEmitter(), {
    exitCode: null as number | null,
    signalCode: null,
    stdin: new PassThrough(),
    stdout: new PassThrough(),
    stderr: new PassThrough(),
  });
  let exit!: () => void;
  const exited = new Promise<void>((resolve) => {
    exit = resolve;
  });
  const stop = vi.fn(async () => {
    child.exitCode = 0;
    exit();
    child.stdin.destroy();
    child.stdout.destroy();
    child.stderr.destroy();
  });
  return { child, exited, stop, output_failed: new Promise<Error>(() => {}), output_error: null };
}
const resolved = {
  access: ConversationAccess.full,
  role_materials: null,
  workspace_folder: '/synthetic/workspace',
} as ResolvedConversationStart;
const launch = {
  argv: ['/synthetic/python', '/synthetic/extension.py'],
  environment_overrides: [],
};

test('launch uses the inherited environment without a version probe or parent mutation', async () => {
  const owned = fake();
  vi.mocked(spawn_codex_process).mockResolvedValueOnce(owned as unknown as OwnedCodexProcess);
  const parent = { ...process.env };
  const launcher = new HermesAcpLauncher();
  await launcher.start(launch, resolved);
  try {
    expect(process.env).toEqual(parent);
    expect(vi.mocked(spawn_codex_process).mock.lastCall).toEqual([
      launch.argv,
      {
        environment: expect.objectContaining({ HOME: process.env.HOME, HERMES_YOLO_MODE: '1' }),
        working_directory: '/synthetic/workspace',
      },
    ]);
    const environment = vi.mocked(spawn_codex_process).mock.lastCall![1].environment;
    expect(environment).not.toHaveProperty('BASH_ENV');
    expect(environment).not.toHaveProperty('PLAN_TICKET_ID');
  } finally {
    await launcher.stop();
  }
});

test('shutdown during spawn joins that child and permanently seals later starts', async () => {
  const owned = fake();
  let release!: (value: OwnedCodexProcess) => void;
  vi.mocked(spawn_codex_process).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  );
  const launcher = new HermesAcpLauncher();
  const errors: unknown[] = [];
  owned.stop.mockRejectedValueOnce(new Error('synthetic cleanup failure'));
  const started = launcher.start(launch, resolved, { log: (error) => errors.push(error) });
  const rejected = expect(started).rejects.toThrow('shut down');
  const stopping = launcher.stop();
  release(owned as unknown as OwnedCodexProcess);
  await stopping;
  await rejected;
  expect(errors).toHaveLength(1);
  expect(owned.stop).toHaveBeenCalled();
  const count = vi.mocked(spawn_codex_process).mock.calls.length;
  await expect(launcher.start(launch, resolved)).rejects.toThrow('shut down');
  expect(vi.mocked(spawn_codex_process).mock.calls.length).toBe(count);
});

test('stdin error settles a write whose callback never returns', async () => {
  const owned = fake();
  vi.mocked(spawn_codex_process).mockResolvedValueOnce(owned as unknown as OwnedCodexProcess);
  vi.spyOn(owned.child.stdin, 'write').mockImplementation(() => true);
  const launcher = new HermesAcpLauncher();
  try {
    const client = await launcher.start(launch, resolved);
    const request = client.begin_request('pending-write', {});
    const written = expect(request.written).rejects.toThrow('synthetic stdin error');
    const response = expect(request.response).rejects.toThrow('synthetic stdin error');
    await new Promise<void>((resolve) => setImmediate(resolve));
    owned.child.stdin.emit('error', new Error('synthetic stdin error'));
    await Promise.all([written, response]);
  } finally {
    await launcher.stop();
  }
});
