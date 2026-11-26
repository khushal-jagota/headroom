import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, vi } from 'vitest';
import { create_commands } from '../../../cli/commands.ts';
import { mac_installation_paths } from '../platform.ts';
import * as host from '../host/entry.ts';
import { run_executor } from '../run.ts';

const account = vi.hoisted(() => ({ home: '' }));
vi.mock('node:os', async (original) => {
  const os = await original<typeof import('node:os')>();
  return { ...os, userInfo: () => ({ ...os.userInfo(), homedir: account.home }) };
});

test('the native requester re-entry runs once and respects an unfinished predecessor swap', async () => {
  account.home = await mkdtemp(join(tmpdir(), 'machine-requester-'));
  const platform = Object.getOwnPropertyDescriptor(process, 'platform')!;
  const service = vi.spyOn(host, 'run_mac_service').mockResolvedValue(0);
  const handoff = new Error('exec replaced this process');
  const exec = vi.spyOn(process, 'execve').mockImplementation(() => {
    throw handoff;
  });
  try {
    Object.defineProperty(process, 'platform', { ...platform, value: 'darwin' });
    const invocation = {
      command: create_commands(),
      values: {
        executor_id: 'machine',
        application_revision: 'a'.repeat(40),
        panels_server_url: 'https://home.example',
      },
      environment: {
        HOME: account.home,
        PLAN_APP_ROOT: account.home,
        PLAN_EXECUTOR_ROOT: mac_installation_paths(account.home).state,
        PLAN_EXECUTOR_RUNTIME_CHILD: '1',
      },
      stdin: async () => Buffer.alloc(0),
    };
    await expect(run_executor(invocation)).rejects.toBe(handoff);
    await run_executor({
      ...invocation,
      environment: {
        ...invocation.environment,
        PANELS_MACHINE_REQUESTER_PID: String(process.ppid),
      },
    });
    // The fixed supervisor forwards its normal exit to launchd. A known unrecoverable
    // journal must stop the machine without starting Workers or restarting it on failure.
    const updates = join(invocation.environment.PLAN_EXECUTOR_ROOT, 'updates');
    await mkdir(updates, { recursive: true });
    await writeFile(
      join(updates, 'launcher-trial.json'),
      JSON.stringify({ error: 'previous signed launcher is damaged' }),
    );
    const blocked = await run_executor(invocation);
    expect(blocked).toMatchObject({
      exit_code: 0,
      stderr: 'previous signed launcher is damaged\n',
    });
    expect(exec).toHaveBeenCalledTimes(1);
    expect(service).toHaveBeenCalledTimes(1);
  } finally {
    Object.defineProperty(process, 'platform', platform);
    vi.restoreAllMocks();
    await rm(account.home, { recursive: true, force: true });
  }
});
