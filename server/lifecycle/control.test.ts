/** Gap: the hosted supervisor uses a private parent and does not prove refusal
 * before unlink/bind when the configured parent grants wider access.
 */
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { open_control_server } from './control.ts';

test('control refuses a wider socket parent before replacing its pathname', async () => {
  const root = await mkdtemp(join(tmpdir(), 'panels-control-'));
  let server: Awaited<ReturnType<typeof open_control_server>> | undefined;
  try {
    const parent = join(root, 'wide');
    await mkdir(parent);
    await chmod(parent, 0o755);
    const socket = join(parent, 'control.sock');
    await writeFile(socket, 'retained pathname');
    const opening = open_control_server(
      socket,
      () => null,
      () => {},
    ).then((opened) => {
      server = opened;
      return opened;
    });
    await expect(opening).rejects.toThrow('owned private 0700 directory');
    expect(await readFile(socket, 'utf8')).toBe('retained pathname');
  } finally {
    try {
      await server?.close();
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  }
});
