/** One strict shared-codec frame per private Unix connection, with actual socket joins. */
import { createServer, type Server, type Socket } from 'node:net';
import { mkdir, unlink } from 'node:fs/promises';
import { dirname } from 'node:path';
import { encode_frame, read_typed_frame } from '../../protocol.ts';
import type { IpcRequest, IpcResponse } from '../../wire.generated.ts';
import { CLI_RESULT_TIMEOUT_SECONDS, CLI_TRANSPORT_RESERVE_SECONDS } from '../configuration.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';

function read_exact(socket: Socket, length: number): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error?: Error, bytes?: Buffer) => {
      if (settled) return;
      settled = true;
      socket.off('readable', available);
      socket.off('end', ended);
      socket.off('close', ended);
      socket.off('error', failed);
      if (error) reject(error);
      else resolve(bytes!);
    };
    const ended = () => finish(new Error('executor IPC closed without a request'));
    const failed = (error: Error) => finish(error);
    const available = () => {
      const bytes = socket.read(length) as Buffer | null;
      if (bytes !== null) finish(undefined, bytes);
      else if (socket.readableEnded || socket.destroyed) ended();
    };
    socket.on('readable', available);
    socket.once('end', ended);
    socket.once('close', ended);
    socket.once('error', failed);
    available();
  });
}
export class GatewayListener {
  private readonly handle: (request: IpcRequest) => Promise<IpcResponse>;
  private readonly on_failure: (error: Error) => void;
  private server: Server | undefined;
  private opening: Promise<void> | undefined;
  private closing: Promise<void> | undefined;
  private closed = false;
  private readonly sockets = new Set<Socket>();
  private readonly handlers = new Set<Promise<void>>();
  constructor(
    handle: (request: IpcRequest) => Promise<IpcResponse>,
    on_failure: (error: Error) => void,
  ) {
    this.handle = handle;
    this.on_failure = on_failure;
  }
  start(path: string): Promise<void> {
    if (this.closed || this.opening)
      return Promise.reject(new ServerLifecycleError('Mac IPC listener cannot start.'));
    this.opening = (async () => {
      await mkdir(dirname(path), { recursive: true, mode: 0o700 });
      await unlink(path).catch((error) => {
        if (error.code !== 'ENOENT') throw error;
      });
      if (this.closed) throw new ServerLifecycleError('Mac IPC listener is shutting down.');
      const server = (this.server = createServer({ allowHalfOpen: true }, (socket) =>
        this.accept(socket),
      ));
      await new Promise<void>((resolve, reject) => {
        const failed = (error: Error) => reject(error);
        server.once('error', failed);
        const old_umask = process.umask(0o177);
        try {
          server.listen(path, () => {
            server.off('error', failed);
            server.on('error', this.on_failure);
            resolve();
          });
        } finally {
          process.umask(old_umask);
        }
      });
    })();
    return this.opening;
  }
  private accept(socket: Socket): void {
    if (this.closed) {
      socket.destroy();
      return;
    }
    this.sockets.add(socket);
    const closed = new Promise<void>((resolve) => socket.once('close', resolve));
    socket.on('error', () => {});
    const expiry = setTimeout(
      () => socket.destroy(),
      (CLI_RESULT_TIMEOUT_SECONDS + CLI_TRANSPORT_RESERVE_SECONDS) * 1000,
    );
    const task = (async () => {
      let request: IpcRequest;
      try {
        request = await read_typed_frame(
          { readexactly: (length) => read_exact(socket, length) },
          'IpcRequest',
        );
      } catch {
        return;
      } // Framing and connection failures never admit a publication.
      let response: IpcResponse;
      try {
        response = await this.handle(request);
      } catch (error) {
        if (!this.closed)
          this.on_failure(error instanceof Error ? error : new Error(String(error)));
        return;
      }
      if (socket.destroyed) return;
      await new Promise<void>((resolve) => {
        socket.once('close', resolve);
        socket.end(encode_frame(response, 'IpcResponse'), () => {
          socket.off('close', resolve);
          resolve();
        });
      });
    })().finally(async () => {
      clearTimeout(expiry);
      socket.destroy();
      await closed;
      this.sockets.delete(socket);
    });
    this.handlers.add(task);
    void task.then(
      () => this.handlers.delete(task),
      (error) => {
        this.handlers.delete(task);
        this.on_failure(error instanceof Error ? error : new Error(String(error)));
      },
    );
  }
  close(): Promise<void> {
    this.closed = true;
    for (const socket of this.sockets) socket.destroy();
    if (!this.closing)
      this.closing = (async () => {
        await this.opening?.catch(() => {});
        if (this.server?.listening)
          await new Promise<void>((resolve, reject) =>
            this.server!.close((error) => (error ? reject(error) : resolve())),
          );
        while (this.handlers.size) await Promise.allSettled([...this.handlers]);
      })();
    return this.closing;
  }
}
