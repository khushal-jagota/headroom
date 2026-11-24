/**
 * Two separate facts about the server's own folders. Machine code with the home identity that
 * launched on the local link works in them, and keeps working in them after the server moves
 * away, so its sessions resume in the same place. They are also the served folders only while
 * the current address is the local link of that same server folder.
 */
import { readFileSync } from 'node:fs';
import { isAbsolute, join } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import { atomic_write_json } from '../../environments/updates/attempts.ts';
import { HOME_MACHINE_ID } from '../../execution_identity.ts';
import { local_server_address } from '../../server_link/logic.ts';
import { ExecutorValidationError } from '../protocol_errors.ts';
import type { ServerFolders } from './configuration.ts';
import { executor_link_server_folders } from './transport/executor_link.ts';

export function server_folders_path(root: string): string {
  return join(root, 'server-folders.json');
}
function read_server_folders(root: string): ServerFolders | null {
  let value: unknown;
  try {
    value = JSON.parse(readFileSync(server_folders_path(root), 'utf8'));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw error;
  }
  const folders = value as Partial<Record<keyof ServerFolders, unknown>> | null;
  const paths = [folders?.tickets, folders?.chief, folders?.managers];
  if (!paths.every((path) => typeof path === 'string' && isAbsolute(path)))
    throw new ExecutorValidationError('The saved server folders are invalid.');
  return {
    tickets: folders!.tickets,
    chief: folders!.chief,
    managers: folders!.managers,
  } as ServerFolders;
}
/**
 * The work folders for this launch. Only the home identity on the local link works in the
 * server's folders, and that choice is saved, so a later launch on another address keeps them.
 */
export function launch_server_folders(
  root: string,
  executor_id: string,
  server_url: string,
): ServerFolders | null {
  if (executor_id !== HOME_MACHINE_ID) return null;
  const saved = read_server_folders(root);
  const link = local_server_address(server_url);
  if (link === null) return saved;
  const folders = executor_link_server_folders(link);
  if (!isDeepStrictEqual(saved, folders))
    atomic_write_json(server_folders_path(root), { ...folders });
  return folders;
}
/** True while the current address is the local link whose server folder holds these folders. */
export function work_folders_are_served(folders: ServerFolders | null, address: string): boolean {
  if (folders === null) return false;
  const link = local_server_address(address);
  return link !== null && isDeepStrictEqual(executor_link_server_folders(link), folders);
}
