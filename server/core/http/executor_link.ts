/** The local executor link: machine code on this machine and account reaches the server through one Unix socket. */
import { chmodSync, lstatSync, mkdirSync, rmSync } from 'node:fs';
import type { Server as HttpServer } from 'node:http';
import { createServer, type Socket } from 'node:net';
import { dirname, join, resolve } from 'node:path';

/** Machine code setup and the docs name the socket through this function. */
export function executor_link_socket_path(db_path: string): string {
  return join(dirname(resolve(db_path)), 'executor-link', 'server.sock');
}

const executor_link_connections = new WeakSet<Socket>();

/** A request on one of these connections is a local executor link request. */
export function is_executor_link_connection(socket: unknown): boolean {
  return executor_link_connections.has(socket as Socket);
}

function missing(error: unknown): boolean {
  return (error as NodeJS.ErrnoException).code === 'ENOENT';
}

export interface ExecutorLinkListener {
  /** Stop accepting and remove the socket. Accepted connections stay with the HTTP server. */
  close(): void;
}

/**
 * The caller holds the runtime ownership lock, so no other server uses this data folder.
 * Each accepted connection goes to the HTTP server, so its routes, hooks and request
 * lifetime apply unchanged.
 */
export async function open_executor_link(
  http_server: HttpServer,
  socket_path: string,
): Promise<ExecutorLinkListener> {
  const folder = dirname(socket_path);
  try {
    mkdirSync(folder, { mode: 0o700 }); // Only the last part: a missing data folder stays an error.
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
  }
  const entry = lstatSync(folder);
  if (!entry.isDirectory() || entry.uid !== process.getuid!())
    throw new Error(`The executor link folder is not a directory owned by this account: ${folder}`);
  chmodSync(folder, 0o700);
  let stale;
  try {
    stale = lstatSync(socket_path);
  } catch (error) {
    if (!missing(error)) throw error;
  }
  if (stale !== undefined) {
    if (!stale.isSocket())
      throw new Error(`Something other than a socket is at the executor link: ${socket_path}`);
    rmSync(socket_path);
  }
  const listener = createServer((socket) => {
    executor_link_connections.add(socket);
    http_server.emit('connection', socket);
  });
  await new Promise<void>((resolve_listen, reject) => {
    listener.once('error', reject);
    listener.listen(socket_path, () => {
      listener.off('error', reject);
      resolve_listen();
    });
  });
  listener.on('error', (error) => console.error('Executor link listener failed', error));
  try {
    chmodSync(socket_path, 0o600);
  } catch (error) {
    listener.close();
    throw error;
  }
  let closed = false;
  return {
    close() {
      if (closed) return;
      closed = true;
      listener.close();
      rmSync(socket_path, { force: true });
    },
  };
}
