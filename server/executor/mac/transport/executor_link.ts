/** Plain HTTP to the home over the local executor link socket. */
import http from 'node:http';
import { lstatSync } from 'node:fs';
import { connect } from 'node:net';
import { dirname } from 'node:path';
import type { Duplex } from 'node:stream';
import { ExecutorValidationError } from '../../protocol.ts';

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
