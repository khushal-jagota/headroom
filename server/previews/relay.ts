/** One machine-initiated channel carries bounded, backpressured application connections. */
import { Duplex } from 'node:stream';
import { once } from 'node:events';
import WebSocket, { type RawData } from 'ws';
import {
  CHUNK_BYTES,
  HEADER_BYTES,
  HTTP_PER_PREVIEW,
  WEBSOCKET_PER_PREVIEW,
  CONNECT_SECONDS,
  WEBSOCKET_PING_SECONDS,
} from './config.ts';
import { PreviewError } from './contracts.ts';

function bytes(data: RawData): Buffer {
  return Array.isArray(data)
    ? Buffer.concat(data)
    : Buffer.isBuffer(data)
      ? data
      : Buffer.from(data);
}
class RelayStream extends Duplex {
  ready = Promise.withResolvers<void>();
  readonly peer: PreviewRelay;
  readonly id: number;
  constructor(peer: PreviewRelay, id: number) {
    super({ highWaterMark: CHUNK_BYTES });
    this.peer = peer;
    this.id = id;
    void this.ready.promise.catch(() => {});
    this.on('error', () => {});
  }
  override _read(): void {
    this.peer.resume();
  }
  override _write(
    chunk: Buffer,
    _encoding: BufferEncoding,
    callback: (error?: Error | null) => void,
  ): void {
    void (async () => {
      for (let offset = 0; offset < chunk.length; offset += CHUNK_BYTES) {
        const body = chunk.subarray(offset, offset + CHUNK_BYTES);
        const frame = Buffer.allocUnsafe(body.length + 4);
        frame.writeUInt32BE(this.id);
        body.copy(frame, 4);
        await this.peer.send(frame);
      }
    })().then(() => callback(), callback);
  }
  override _final(callback: (error?: Error | null) => void): void {
    void this.peer.control('end', this.id).then(() => callback(), callback);
  }
  override _destroy(error: Error | null, callback: (error: Error | null) => void): void {
    this.ready.reject(error ?? new Error('Preview connection closed'));
    if (this.peer.streams.delete(this.id)) void this.peer.control('close', this.id).catch(() => {});
    this.peer.resume();
    callback(error);
  }
}
export class PreviewRelay {
  readonly streams = new Map<number, RelayStream>();
  private next = 0;
  private closed = false;
  private heartbeat: ReturnType<typeof setInterval>;
  private alive = true;
  readonly socket: WebSocket;
  readonly accept?: (stream: Duplex) => Promise<void>;
  constructor(socket: WebSocket, accept?: (stream: Duplex) => Promise<void>) {
    this.socket = socket;
    this.accept = accept;
    socket.on('error', () => this.close());
    socket.once('close', () => this.close());
    socket.on('message', (data, binary) => {
      void this.receive(data, binary).catch(() => this.close());
    });
    socket.on('pong', () => {
      this.alive = true;
    });
    this.heartbeat = setInterval(() => {
      if (!this.alive || socket.readyState !== WebSocket.OPEN) return this.close();
      this.alive = false;
      socket.ping();
    }, WEBSOCKET_PING_SECONDS * 1000);
    this.heartbeat.unref();
  }
  resume(): void {
    if (![...this.streams.values()].some((stream) => stream.readableLength >= CHUNK_BYTES))
      this.socket.resume();
  }
  send(data: Buffer | string): Promise<void> {
    return new Promise((resolve, reject) => {
      if (this.closed || this.socket.readyState !== WebSocket.OPEN)
        return reject(new Error('Preview relay ended'));
      this.socket.send(data, { binary: Buffer.isBuffer(data) }, (error) =>
        error ? reject(error) : resolve(),
      );
    });
  }
  control(type: string, id: number): Promise<void> {
    return this.send(JSON.stringify({ type, id }));
  }
  async connect(): Promise<Duplex> {
    if (this.accept || this.closed || this.streams.size >= HTTP_PER_PREVIEW + WEBSOCKET_PER_PREVIEW)
      throw new PreviewError('Preview connection is unavailable.', 503);
    const stream = new RelayStream(this, ++this.next);
    this.streams.set(stream.id, stream);
    try {
      await this.control('open', stream.id);
      await Promise.race([
        stream.ready.promise,
        once(AbortSignal.timeout(CONNECT_SECONDS * 1000), 'abort').then(() => {
          throw new Error('Preview connection timed out');
        }),
      ]);
      return stream;
    } catch (error) {
      stream.destroy();
      throw error;
    }
  }
  private async receive(raw: RawData, binary: boolean): Promise<void> {
    const data = bytes(raw);
    if (binary) {
      if (data.length < 4 || data.length > CHUNK_BYTES + 4)
        throw new Error('Invalid preview frame');
      const stream = this.streams.get(data.readUInt32BE());
      if (!stream) return;
      if (!stream.push(data.subarray(4))) this.socket.pause();
      return;
    }
    if (data.length > HEADER_BYTES) throw new Error('Invalid preview control');
    const value = JSON.parse(data.toString()) as { type: string; id: number };
    if (!Number.isSafeInteger(value.id) || value.id <= 0 || value.id > 0xffffffff)
      throw new Error('Invalid preview connection');
    if (value.type === 'open') {
      if (
        !this.accept ||
        this.streams.has(value.id) ||
        this.streams.size >= HTTP_PER_PREVIEW + WEBSOCKET_PER_PREVIEW
      )
        throw new Error('Preview connection refused');
      const stream = new RelayStream(this, value.id);
      this.streams.set(value.id, stream);
      try {
        await this.accept(stream);
        await this.control('ready', value.id);
      } catch {
        stream.destroy();
      }
      return;
    }
    const stream = this.streams.get(value.id);
    if (!stream) return;
    if (value.type === 'ready' && !this.accept) stream.ready.resolve();
    else if (value.type === 'end') stream.push(null);
    else if (value.type === 'close') stream.destroy();
    else throw new Error('Invalid preview control');
  }
  close(): void {
    if (this.closed) return;
    this.closed = true;
    clearInterval(this.heartbeat);
    for (const stream of this.streams.values()) stream.destroy();
    this.socket.terminate();
  }
}
