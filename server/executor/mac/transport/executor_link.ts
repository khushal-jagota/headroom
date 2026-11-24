/** Plain HTTP to the home over the local executor link socket. */
import http from 'node:http';
import { lstatSync } from 'node:fs';
import { connect } from 'node:net';
import { dirname, join } from 'node:path';
import type { Duplex } from 'node:stream';
import { ExecutorValidationError } from '../../protocol.ts';
import { filesRoot } from '../../../files/logic/paths.ts';
import type { ServerFolders } from '../configuration.ts';

/**
 * Machine code on the local link works in the server's own Ticket, Chief and Manager folders.
 * The work folder and the served folder are then one folder, so their files are stored once.
 */
export function executor_link_server_folders(socket_path: string): ServerFolders {
  // The socket's folder sits in the server folder, and filesRoot reads only that parent.
  const tickets = filesRoot(dirname(socket_path), 'ticket');
  return {
    tickets,
    // The Chief's home has no served root. It sits beside the Ticket folder, as the server keeps it.
    chief: join(dirname(tickets), 'chief'),
    managers: filesRoot(dirname(socket_path), 'manager'),
  };
}

/**
 * Only the server's account can make this folder and socket, so a socket that passes is
 * the server's own. Checked before each new connection.
 */
export function require_executor_link_socket(socket_path: string): void {
  const uid = process.getuid!();
  const folder = lstatSync(dirname(socket_path));
  if (!folder.isDirectory() || folder.uid !== uid || (folder.mode & 0o077) !== 0)
    throw new ExecutorValidationError(
      'the executor link folder must be a directory owned by this account with no group or other access',
    );
  const entry = lstatSync(socket_path);
  if (!entry.isSocket() || entry.uid !== uid)
    throw new ExecutorValidationError('the executor link must be a socket owned by this account');
}

export class ExecutorLinkAgent extends http.Agent {
  readonly socket_path: string;
  constructor(socket_path: string, options: http.AgentOptions) {
    super(options);
    this.socket_path = socket_path;
  }
  override createConnection(
    _options: http.ClientRequestArgs,
    callback?: (error: Error | null, stream: Duplex) => void,
  ): Duplex | null | undefined {
    try {
      require_executor_link_socket(this.socket_path);
    } catch (error) {
      callback?.(error as Error, undefined as unknown as Duplex);
      return undefined;
    }
    return connect(this.socket_path);
  }
}
