import net from 'node:net';
import { lstat, mkdir, unlink } from 'node:fs/promises';
import { dirname } from 'node:path';
import { python_os_error_message } from '../core/os_errors.ts';
import {
  CONTROL_EXCHANGE_SECONDS,
  MAX_CONTROL_MESSAGE_BYTES,
  monotonic_seconds,
} from './configuration.ts';
import {
  ServerLifecycleError,
  ServerRestartConnectionError,
  ServerRestartProtocolError,
} from './contracts.ts';
import type { ControlRequest, ControlResponse } from './contracts.ts';
import { decode_request, decode_response, encode_message } from './logic/control.ts';

function read_line(socket: net.Socket, deadline: number): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    let bytes = Buffer.alloc(0),
      settled = false;
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      socket.off('data', data);
      socket.off('end', end);
      socket.off('close', end);
      socket.off('error', failure);
      if (error) reject(error);
      else resolve(bytes);
    };
    const failure = (error: Error) => finish(error);
    const end = () =>
      finish(new ServerRestartProtocolError('control connection closed before a complete message'));
    const data = (chunk: Buffer) => {
      bytes = Buffer.concat([bytes, chunk]);
      if (bytes.length > MAX_CONTROL_MESSAGE_BYTES)
        return finish(new ServerRestartProtocolError('control message exceeds the protocol limit'));
      const newline = bytes.indexOf(10);
      if (newline >= 0) {
        if (newline !== bytes.length - 1)
          return finish(
            new ServerRestartProtocolError('control message must be one bounded JSON line'),
          );
        finish();
      }
    };
    const timer = setTimeout(
      () => finish(new ServerRestartProtocolError('control exchange timed out')),
      Math.max(0, deadline - monotonic_seconds()) * 1000,
    );
    socket.on('data', data);
    socket.once('end', end);
    socket.once('close', end);
    socket.once('error', failure);
  });
}
export async function request_control(
  path: string,
  request: ControlRequest,
  deadline = monotonic_seconds() + CONTROL_EXCHANGE_SECONDS,
): Promise<ControlResponse> {
  const bytes = encode_message(request);
  const socket = net.createConnection(path);
  socket.on('error', () => {});
  const reply = read_line(socket, deadline);
  socket.once('connect', () => socket.write(bytes));
  try {
    return decode_response(await reply, request.version);
  } catch (error) {
    if (error instanceof ServerRestartProtocolError)
      throw new ServerRestartProtocolError(
        `Panels supervisor returned an incompatible response: ${error.message}`,
      );
    throw new ServerRestartConnectionError(
      `Could not connect to the Panels supervisor at ${path}: ${error instanceof Error ? python_os_error_message(error, { include_paths: false }) : 'connection failure'}`,
    );
  } finally {
    socket.end();
    socket.destroy();
  }
}
export async function open_control_server(
  path: string,
  request: (value: ControlRequest) => ControlResponse | null,
  restart: () => void,
): Promise<{ cancel_pending(): void; close(): Promise<void> }> {
  const parent = dirname(path);
  await mkdir(parent, { recursive: true, mode: 0o700 });
  const directory = await lstat(parent);
  if (
    !directory.isDirectory() ||
    directory.isSymbolicLink() ||
    directory.uid !== process.getuid!() ||
    (directory.mode & 0o7777) !== 0o700
  )
    throw new ServerLifecycleError('Control socket parent must be an owned private 0700 directory');
  await unlink(path).catch((error) => {
    if (error.code !== 'ENOENT') throw error;
  });
  const sockets = new Set<net.Socket>(),
    flights = new Set<Promise<void>>();
  const server = net.createServer({ allowHalfOpen: true }, (socket) => {
    sockets.add(socket);
    // Keep an error listener after a frame is consumed, including during half-close.
    socket.on('error', () => {});
    const deadline = monotonic_seconds() + CONTROL_EXCHANGE_SECONDS;
    const expiry = setTimeout(
      () => socket.destroy(new ServerRestartProtocolError('control exchange timed out')),
      CONTROL_EXCHANGE_SECONDS * 1000,
    );
    let timer: ReturnType<typeof setTimeout> | undefined;
    const task = (async () => {
      const value = decode_request(await read_line(socket, deadline));
      const response = request(value);
      if (response === null) return;
      await new Promise<void>((resolve, reject) => {
        const complete = (error?: Error) => {
          socket.off('finish', finished);
          socket.off('close', closed);
          socket.off('error', failed);
          if (error) reject(error);
          else resolve();
        };
        const finished = () => complete();
        const closed = () =>
          complete(new ServerRestartProtocolError('control reply was not delivered'));
        const failed = (error: Error) => complete(error);
        socket.once('finish', finished);
        socket.once('close', closed);
        socket.once('error', failed);
        socket.end(encode_message(response));
      });
      if (value.operation === 'restart') {
        if (!socket.readableEnded && !socket.destroyed)
          await new Promise<void>((resolve, reject) => {
            socket.once('end', resolve);
            socket.once('close', resolve);
            socket.once('error', reject);
            timer = setTimeout(
              () => reject(new ServerRestartProtocolError('control exchange timed out')),
              Math.max(0, deadline - monotonic_seconds()) * 1000,
            );
            socket.resume();
          });
        restart();
      }
    })()
      .catch(() => {})
      .finally(() => {
        clearTimeout(expiry);
        if (timer) clearTimeout(timer);
        sockets.delete(socket);
        socket.destroy();
        flights.delete(task);
      });
    flights.add(task);
  });
  try {
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject);
      const previous_umask = process.umask(0o177);
      try {
        server.listen(path, resolve);
      } finally {
        process.umask(previous_umask);
      }
    });
  } catch (error) {
    server.close();
    await unlink(path).catch(() => {});
    throw error;
  }
  const cancel_pending = () => {
    for (const socket of sockets) socket.destroy();
  };
  return {
    cancel_pending,
    async close() {
      cancel_pending();
      await new Promise<void>((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      );
      await Promise.allSettled([...flights]);
      // libuv unlinks the pathname as it closes this bound server. The supervisor
      // retains its lease through this entire operation, before a successor binds.
    },
  };
}
