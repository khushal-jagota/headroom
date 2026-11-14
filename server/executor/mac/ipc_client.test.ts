import { createServer } from 'node:net';
import { mkdtemp, mkdir, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, vi } from 'vitest';
import { request } from './ipc_client.ts';

test('refuses linked or foreign-owner sockets before child capability emission', async () => {
  const root = await mkdtemp(join(tmpdir(), 'mac-ipc-trust-'));
  const actual = join(root, 'actual');
  const linked_run = join(root, 'linked-run');
  const linked_socket = join(root, 'linked-socket');
  await mkdir(join(actual, 'run'), { recursive: true });
  await mkdir(linked_run);
  await symlink(join(actual, 'run'), join(linked_run, 'run'));
  await mkdir(join(linked_socket, 'run'), { recursive: true });
  await symlink(join(actual, 'run', 'executor.sock'), join(linked_socket, 'run', 'executor.sock'));
  let accepted = 0;
  const server = createServer((socket) => {
    accepted++;
    socket.destroy();
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(join(actual, 'run', 'executor.sock'), resolve);
  });
  const environment = {
    ...process.env,
    PLAN_EXECUTOR_CHILD_ID: 'child',
    PLAN_EXECUTOR_CAPABILITY: 'private',
  };
  try {
    await expect(
      request('health', {}, { ...environment, PLAN_EXECUTOR_ROOT: linked_run }),
    ).rejects.toThrow('run directory is a symbolic link');
    await expect(
      request('health', {}, { ...environment, PLAN_EXECUTOR_ROOT: linked_socket }),
    ).rejects.toThrow('must be an owned socket');
    const uid = process.getuid!();
    const owner = vi.spyOn(process, 'getuid').mockReturnValue(uid + 1);
    try {
      await expect(
        request('health', {}, { ...environment, PLAN_EXECUTOR_ROOT: actual }),
      ).rejects.toThrow('must be an owned socket');
    } finally {
      owner.mockRestore();
    }
    expect(accepted).toBe(0);
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
    await rm(root, { recursive: true, force: true });
  }
});
