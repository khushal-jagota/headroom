/** The old server's machine code keeps its work folders after the move and starts to send files. */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test } from 'vitest';
import { HOME_MACHINE_ID } from '../../execution_identity.ts';
import { launch_server_folders, work_folders_are_served } from './server_folders.ts';

const roots: string[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

test('the saved server folders survive an HTTPS address, which no longer serves them', () => {
  const root = mkdtempSync(join(tmpdir(), 'server-folders-'));
  roots.push(root);
  const socket = join(root, 'server/data/executor-link/server.sock');
  const folders = launch_server_folders(root, HOME_MACHINE_ID, socket);
  expect(folders).toEqual({
    tickets: join(root, 'server/data/files/tickets'),
    chief: join(root, 'server/data/files/chief'),
    managers: join(root, 'server/data/files/sprint-items'),
  });
  expect(work_folders_are_served(folders, socket)).toBe(true);
  // After the move the saved address is the new server's HTTPS origin.
  expect(launch_server_folders(root, HOME_MACHINE_ID, 'https://new.example')).toEqual(folders);
  expect(work_folders_are_served(folders, 'https://new.example')).toBe(false);
  expect(work_folders_are_served(folders, join(root, 'other/data/executor-link/server.sock'))).toBe(
    false,
  );
  // Another identity in the same root keeps its own folders.
  expect(launch_server_folders(root, 'machine-b', socket)).toBeNull();
});
