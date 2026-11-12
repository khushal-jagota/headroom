/** Hand-written reverse transport. No HTTP or WS handshake precedes socket proof. */
import http, { type IncomingMessage, type ServerResponse } from 'node:http';
import net from 'node:net';
import { once } from 'node:events';
import type { Duplex } from 'node:stream';
import WebSocket, { WebSocketServer, type RawData } from 'ws';
import * as config from './config.ts';
import { PreviewError, base_path, port, type PreviewRecord, type Header } from './contracts.ts';
import { request_headers, response_headers, within_prefix } from './policy.ts';
import { Capacity, TransferBudget, MessageBudget } from './limits.ts';
import { PreviewService } from './service.ts';
const raw_headers = (raw: string[]): Header[] =>
  Array.from({ length: raw.length / 2 }, (_, i) => [raw[i * 2]!, raw[i * 2 + 1]!]);
function headers_object(headers: Header[]): http.OutgoingHttpHeaders {
  const result: http.OutgoingHttpHeaders = {};
  for (const [key, value] of headers) {
    const name = key.toLowerCase();
    const previous = result[name];
    result[name] =
      previous === undefined
        ? value
        : Array.isArray(previous)
          ? [...previous, value]
          : [String(previous), value];
  }
  return result;
}
async function deadline<T>(promise: Promise<T>, ms: number): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error('preview timeout')), Math.max(1, ms));
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}
/** Count raw bytes before Node can normalize whitespace or allocate header fields. */
class HeaderBoundedSocket extends net.Socket {
  private header_pending = true;
  private header_bytes = Buffer.alloc(0);
  private header_admitted = 0;
  private admit(data: Buffer): void {
    let offset = 0;
    while (this.header_pending && offset < data.length) {
      const allowed = config.HEADER_BYTES - this.header_admitted;
      const remaining = allowed - this.header_bytes.length;
      const candidate = Buffer.concat([
        this.header_bytes,
        data.subarray(offset, offset + remaining),
      ]);
      const boundary = candidate.indexOf('\r\n\r\n');
      if (boundary < 0) {
        if (candidate.length >= allowed)
          throw new PreviewError('Preview headers exceed the supported limit.', 502);
        this.header_bytes = candidate;
        return;
      }
      const header_end = boundary + 4;
      const consumed = header_end - this.header_bytes.length;
      const status = candidate.subarray(0, header_end).toString('latin1').split(' ', 2)[1] ?? '';
      this.header_bytes = Buffer.alloc(0);
      this.header_admitted += header_end;
      this.header_pending = status.startsWith('1') && status !== '101';
      offset += consumed;
    }
  }
  override emit(event: string | symbol, ...args: unknown[]): boolean {
    if (event === 'data' && this.header_pending && Buffer.isBuffer(args[0])) {
      try {
        this.admit(args[0]);
      } catch (error) {
        this.destroy(error as Error);
        return false;
      }
    }
    return super.emit(event, ...args);
  }
}
class VerifiedAgent extends http.Agent {
  readonly service: PreviewService;
  readonly record: PreviewRecord;
  constructor(service: PreviewService, record: PreviewRecord) {
    super({ keepAlive: false, maxSockets: 1 });
    this.service = service;
    this.record = record;
  }
  override createConnection(
    _options: net.NetConnectOpts,
    callback?: (error: Error | null, socket: Duplex) => void,
  ): net.Socket {
    // Agent waits for callback. Returning no socket prevents premature request writes.
    void (async () => {
      await this.service.before_connection(this.record);
      const socket = new HeaderBoundedSocket();
      socket.connect({ host: '127.0.0.1', port: port(this.record) });
      socket.pause();
      try {
        await deadline(once(socket, 'connect'), config.CONNECT_SECONDS * 1000);
        await this.service.after_connection(this.record, socket.localPort!);
        callback?.(null, socket);
        setImmediate(() => socket.resume());
      } catch (error) {
        socket.destroy();
        callback?.(error as Error, undefined as unknown as Duplex);
      }
    })().catch((error) => callback?.(error as Error, undefined as unknown as Duplex));
    return undefined as unknown as net.Socket;
  }
}
export class PreviewTransport {
  readonly http_capacity = new Capacity(config.HTTP_GLOBAL, config.HTTP_PER_PREVIEW);
  readonly websocket_capacity = new Capacity(config.WEBSOCKET_GLOBAL, config.WEBSOCKET_PER_PREVIEW);
  readonly budget = new TransferBudget();
  readonly cookie_names = new Map<string, Set<string>>();
  private sessions = new Map<string, Set<() => void>>();
  private websocket_server = new WebSocketServer({
    noServer: true,
    maxPayload: config.WEBSOCKET_MESSAGE_BYTES,
    perMessageDeflate: false,
  });
  readonly service: PreviewService;
  constructor(service: PreviewService) {
    this.service = service;
  }
  private track(generation: string, close: () => void): () => void {
    let sessions = this.sessions.get(generation);
    if (!sessions) {
      sessions = new Set();
      this.sessions.set(generation, sessions);
    }
    sessions.add(close);
    return () => {
      sessions!.delete(close);
      if (!sessions!.size) this.sessions.delete(generation);
    };
  }
  private cookies(generation: string): Set<string> {
    let names = this.cookie_names.get(generation);
    if (!names) {
      names = new Set();
      this.cookie_names.set(generation, names);
    }
    return names;
  }
  revoke(generation: string): void {
    for (const close of this.sessions.get(generation) ?? []) close();
    this.sessions.delete(generation);
    this.cookie_names.delete(generation);
  }
  close(): void {
    for (const generation of [...this.sessions.keys()]) this.revoke(generation);
    this.websocket_server.close();
  }
  async http(
    request: IncomingMessage,
    response: ServerResponse,
    ticket_id: string,
    generation: string,
  ): Promise<void> {
    const started = performance.now();
    let admitted = false;
    let untrack = () => {};
    let upstream: http.ClientRequest | undefined;
    let agent: VerifiedAgent | undefined;
    let timer: NodeJS.Timeout | undefined;
    const closed = () => {
      upstream?.destroy();
      response.destroy();
    };
    try {
      this.http_capacity.acquire(generation);
      admitted = true;
      const record = await this.service.check(ticket_id, generation);
      const path = request.url ?? '/';
      const pathname = path.split('?')[0]!;
      if (!within_prefix(pathname, base_path(record)))
        throw new PreviewError('Preview request must stay under its prepared base path.', 400);
      const headers = request_headers(raw_headers(request.rawHeaders), {
        port: port(record),
        origin: record.public_origin,
        cookie_names: this.cookies(generation),
      });
      const length = request.headers['content-length'];
      if (length && (!/^\d+$/.test(length) || Number(length) > config.REQUEST_BODY_BYTES))
        throw new PreviewError('Preview request body exceeds the supported limit.', 413);
      agent = new VerifiedAgent(this.service, record);
      untrack = this.track(generation, closed);
      timer = setTimeout(closed, config.HTTP_TOTAL_SECONDS * 1000 - (performance.now() - started));
      response.once('close', () => upstream?.destroy());
      upstream = http.request({
        host: '127.0.0.1',
        port: port(record),
        method: request.method,
        path,
        headers: headers_object(headers),
        agent,
        maxHeaderSize: config.HEADER_BYTES,
      });
      upstream.on('error', () => {});
      upstream.setTimeout(config.HTTP_IDLE_SECONDS * 1000, () =>
        upstream?.destroy(new Error('preview timeout')),
      );
      const incoming = new Promise<IncomingMessage>((resolve, reject) => {
        upstream!.once('response', resolve);
        upstream!.once('error', reject);
      });
      let pump_error: unknown;
      const body_pump = (async () => {
        let total = 0;
        for await (const raw of request) {
          const chunk = Buffer.isBuffer(raw) ? raw : Buffer.from(raw);
          total += chunk.length;
          if (total > config.REQUEST_BODY_BYTES)
            throw new PreviewError('Preview request body exceeds the supported limit.', 413);
          for (let offset = 0; offset < chunk.length; offset += config.CHUNK_BYTES) {
            const part = chunk.subarray(offset, offset + config.CHUNK_BYTES);
            await this.budget.consume(part.length);
            if (!upstream!.write(part))
              await deadline(once(upstream!, 'drain'), config.DOWNSTREAM_SEND_SECONDS * 1000);
          }
        }
        upstream!.end();
      })().catch((error) => {
        pump_error = error;
        upstream!.destroy(error as Error);
      });
      const peer = await deadline(
        incoming,
        config.PREHEADER_SECONDS * 1000 - (performance.now() - started),
      );
      const [clean, cookies] = response_headers(raw_headers(peer.rawHeaders), {
        request_url: record.public_origin + pathname,
        origin: record.public_origin,
        prefix: base_path(record),
      });
      await this.service.check(ticket_id, generation);
      const names = this.cookies(generation);
      if (new Set([...names, ...cookies]).size > config.COOKIE_NAMES_PER_GENERATION)
        throw new PreviewError('Preview declares too many cookies.', 502);
      for (const name of cookies) names.add(name);
      response.writeHead(peer.statusCode!, headers_object(clean));
      for await (const raw of peer) {
        const chunk = Buffer.isBuffer(raw) ? raw : Buffer.from(raw);
        for (let offset = 0; offset < chunk.length; offset += config.CHUNK_BYTES) {
          const part = chunk.subarray(offset, offset + config.CHUNK_BYTES);
          await this.budget.consume(part.length);
          if (!response.write(part))
            await deadline(once(response, 'drain'), config.DOWNSTREAM_SEND_SECONDS * 1000);
        }
      }
      await body_pump;
      if (pump_error) throw pump_error;
      response.end();
    } catch (caught) {
      const error =
        (caught as { code?: string }).code === 'HPE_HEADER_OVERFLOW'
          ? new PreviewError('Preview headers exceed the supported limit.', 502)
          : caught;
      if (!response.headersSent && !response.destroyed) {
        const status = error instanceof PreviewError ? error.status_code : 504;
        const message =
          error instanceof PreviewError
            ? error.message
            : 'Preview application timed out or disconnected.';
        response.writeHead(status, {
          'content-type': 'text/plain; charset=utf-8',
          'content-length': Buffer.byteLength(message),
        });
        response.end(message);
      } else response.destroy();
    } finally {
      clearTimeout(timer);
      upstream?.destroy();
      agent?.destroy();
      untrack();
      if (admitted) this.http_capacity.release(generation);
    }
  }
  async websocket(
    request: IncomingMessage,
    socket: Duplex,
    head: Buffer,
    ticket_id: string,
    generation: string,
  ): Promise<void> {
    let admitted = false;
    let peer: WebSocket | undefined;
    let browser: WebSocket | undefined;
    let agent: VerifiedAgent | undefined;
    let untrack = () => {};
    let heartbeat: NodeJS.Timeout | undefined;
    const pong_timers = new Map<WebSocket, NodeJS.Timeout>();
    try {
      this.websocket_capacity.acquire(generation);
      admitted = true;
      const record = await this.service.check(ticket_id, generation);
      const path = request.url ?? '/';
      const pathname = path.split('?')[0]!;
      if (!within_prefix(pathname, base_path(record)))
        throw new PreviewError('Preview WebSocket requires its generation base path.', 400);
      const headers = request_headers(raw_headers(request.rawHeaders), {
        port: port(record),
        origin: record.public_origin,
        cookie_names: this.cookies(generation),
      }).filter(([k]) => !k.toLowerCase().startsWith('sec-websocket-'));
      agent = new VerifiedAgent(this.service, record);
      const protocols = String(request.headers['sec-websocket-protocol'] ?? '')
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
      peer = new WebSocket(`ws://127.0.0.1:${port(record)}${path}`, protocols, {
        headers: headers_object(headers),
        agent,
        maxPayload: config.WEBSOCKET_MESSAGE_BYTES,
        maxHeaderSize: config.HEADER_BYTES,
        perMessageDeflate: false,
        followRedirects: false,
        handshakeTimeout: config.PREHEADER_SECONDS * 1000,
      });
      peer.on('error', () => {});
      const early: [RawData, boolean][] = [];
      const capture = (data: RawData, binary: boolean): void => {
        early.push([data, binary]);
      };
      peer.on('message', capture);
      peer.once('open', () => peer!.pause());
      untrack = this.track(generation, () => {
        peer?.terminate();
        browser?.terminate();
        socket.destroy();
      });
      await deadline(once(peer, 'open'), config.PREHEADER_SECONDS * 1000);
      peer.pause();
      await this.service.check(ticket_id, generation);
      if (peer.readyState !== WebSocket.OPEN)
        throw new PreviewError('Preview WebSocket application disconnected.', 410);
      // Match only the subprotocol that the verified upstream accepted.
      const accepted = peer.protocol;
      if (accepted) request.headers['sec-websocket-protocol'] = accepted;
      else delete request.headers['sec-websocket-protocol'];
      browser = await new Promise<WebSocket>((resolve, reject) => {
        try {
          this.websocket_server.handleUpgrade(request, socket, head, (accepted) => {
            accepted.pause();
            resolve(accepted);
          });
        } catch (error) {
          reject(error);
        }
      });
      browser.on('error', () => {});
      const messages = new MessageBudget();
      const relay = (source: WebSocket, target: WebSocket): void => {
        let queued = 0;
        let chain = Promise.resolve();
        source.on('message', (data: RawData, binary: boolean) => {
          queued++;
          // Socket pause bounds pending data to the current parser chunk.
          // A legal burst in that chunk must drain instead of failing at frame five.
          source.pause();
          chain = chain
            .then(async () => {
              const bytes = Array.isArray(data)
                ? Buffer.concat(data)
                : Buffer.isBuffer(data)
                  ? data
                  : Buffer.from(data);
              if (bytes.length > config.WEBSOCKET_MESSAGE_BYTES)
                throw new PreviewError(
                  'Preview WebSocket message exceeds the supported limit.',
                  413,
                );
              messages.consume();
              await this.budget.consume(bytes.length);
              await deadline(
                new Promise<void>((resolve, reject) =>
                  target.send(bytes, { binary }, (error) => (error ? reject(error) : resolve())),
                ),
                config.DOWNSTREAM_SEND_SECONDS * 1000,
              );
              queued--;
              if (!queued) source.resume();
            })
            .catch(() => {
              source.terminate();
              target.terminate();
            });
        });
      };
      relay(browser, peer);
      peer.off('message', capture);
      relay(peer, browser);
      for (const [data, binary] of early) peer.emit('message', data, binary);
      browser.resume();
      const sides = [peer, browser];
      for (const side of sides)
        side.on('pong', () => {
          clearTimeout(pong_timers.get(side));
          pong_timers.delete(side);
        });
      heartbeat = setInterval(() => {
        for (const side of sides) {
          if (side.readyState !== WebSocket.OPEN) continue;
          if (pong_timers.has(side)) {
            side.terminate();
            continue;
          }
          side.ping();
          pong_timers.set(
            side,
            setTimeout(() => side.terminate(), config.WEBSOCKET_PONG_SECONDS * 1000),
          );
        }
      }, config.WEBSOCKET_PING_SECONDS * 1000);
      peer.resume();
      await Promise.race([once(peer, 'close'), once(browser, 'close')]);
    } catch {
      if (browser?.readyState === WebSocket.OPEN) browser.close(1013);
      else if (!socket.destroyed)
        socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
    } finally {
      clearInterval(heartbeat);
      for (const timer of pong_timers.values()) clearTimeout(timer);
      if (browser?.readyState === WebSocket.OPEN) browser.close();
      if (peer?.readyState === WebSocket.OPEN) peer.close();
      const terminate = setTimeout(() => {
        peer?.terminate();
        browser?.terminate();
        socket.destroy();
      }, config.CLOSE_SECONDS * 1000);
      terminate.unref();
      agent?.destroy();
      untrack();
      if (admitted) this.websocket_capacity.release(generation);
    }
  }
}
