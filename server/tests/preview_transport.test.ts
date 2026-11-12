/**
 * Real Node parsers are required for early upgrade frames and cumulative headers.
 * The existing Linux browser case proves process ownership; these cases replace
 * only that service seam and reuse one small downstream host without a browser.
 */
import http from 'node:http';
import net from 'node:net';
import { once } from 'node:events';
import type { AddressInfo } from 'node:net';
import { expect, test } from 'vitest';
import WebSocket, { WebSocketServer } from 'ws';
import { PreviewTransport } from '../previews/transport.ts';
import type { PreviewService } from '../previews/service.ts';
import type { PreviewRecord } from '../previews/contracts.ts';
import { HEADER_BYTES, REQUEST_BODY_BYTES, WEBSOCKET_MESSAGE_BYTES } from '../previews/config.ts';

async function listen(server: net.Server): Promise<number> {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return (server.address() as AddressInfo).port;
}

function track_sockets(server: net.Server): () => Promise<void> {
  const sockets = new Set<net.Socket>();
  server.on('connection', (socket) => {
    sockets.add(socket);
    socket.once('close', () => sockets.delete(socket));
  });
  return async () => {
    const closed = new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
    for (const socket of sockets) socket.destroy();
    await closed;
  };
}

async function host(upstream_port: number) {
  const record: PreviewRecord = {
    ticket_id: 't_transport',
    generation: 'generation',
    worktree: '/synthetic/worktree',
    public_origin: 'https://panels.example',
    server_epoch: 'synthetic-epoch',
    created_at: 0,
    state: 'active',
    ended_reason: null,
    process: {
      boot_id: 'synthetic-boot',
      pid: 1,
      start_ticks: 1,
      listener_inode: 1,
      port: upstream_port,
      worktree: '/synthetic/worktree',
    },
  };
  const service = {
    check: async () => record,
    before_connection: async () => {},
    after_connection: async () => {},
  } as unknown as PreviewService;
  const transport = new PreviewTransport(service);
  const pending = new Set<Promise<void>>();
  const run = (task: Promise<void>) => {
    pending.add(task);
    void task.finally(() => pending.delete(task));
  };
  const server = http.createServer((request, response) =>
    run(transport.http(request, response, record.ticket_id, record.generation)),
  );
  server.on('upgrade', (request, socket, head) =>
    run(transport.websocket(request, socket, head, record.ticket_id, record.generation)),
  );
  const stop_server = track_sockets(server);
  const port = await listen(server);
  return {
    port,
    path: `/dev/tickets/${record.ticket_id}/${record.generation}/`,
    close: async () => {
      transport.close();
      await stop_server();
      await Promise.all(pending);
    },
  };
}

function read_http(
  port: number,
  path: string,
  headers?: http.OutgoingHttpHeaders,
): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const request = http.get(
      {
        host: '127.0.0.1',
        port,
        path,
        headers,
        method: headers ? 'POST' : 'GET',
        agent: false,
        maxHeaderSize: HEADER_BYTES * 2,
      },
      (response) => {
        const chunks: Buffer[] = [];
        response.on('data', (chunk: Buffer) => chunks.push(chunk));
        response.once('error', reject);
        response.once('end', () =>
          resolve({ status: response.statusCode!, body: Buffer.concat(chunks).toString('utf8') }),
        );
      },
    );
    request.once('error', reject);
  });
}

test('preserves upgrade frames and enforces raw header and transfer limits on one listener pair', async () => {
  const started = performance.now();
  let connections = 0;
  const upstream = http.createServer((request) => {
    const path = request.url!;
    if (path.includes('/aggregate')) {
      const padding = path.endsWith('/aggregate-whitespace')
        ? ' '.repeat(20_000) + 'a'
        : 'a'.repeat(20_000);
      const interim = `HTTP/1.1 103 Early Hints\r\nX-Pad: ${padding}\r\n\r\n`;
      request.socket.end(interim + interim + 'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK');
    } else {
      const prefix = 'HTTP/1.1 200 OK\r\nX-Pad: ';
      const suffix = '\r\nContent-Length: 2\r\n\r\n';
      const size = path.endsWith('/exact')
        ? HEADER_BYTES - Buffer.byteLength(prefix + suffix)
        : path.endsWith('/single')
          ? HEADER_BYTES + 1
          : 20_000;
      request.socket.end(prefix + 'a'.repeat(size) + suffix + 'OK');
    }
  });
  upstream.on('connection', () => connections++);
  const stop_upstream = track_sockets(upstream);
  const sockets = new WebSocketServer({ server: upstream });
  sockets.on('headers', (headers) => headers.push(`X-Pad: ${'a'.repeat(20_000)}`));
  sockets.on('connection', (socket) => {
    socket.send('immediate');
    socket.once('message', () => {
      for (let index = 0; index < 5; index++) socket.send(`burst-${index}`);
    });
  });
  const downstream = await host(await listen(upstream));
  const setup_ms = performance.now() - started;
  const browser = new WebSocket(`ws://127.0.0.1:${downstream.port}${downstream.path}`);
  let deadline: NodeJS.Timeout | undefined;
  try {
    const received = await new Promise<string[]>((resolve, reject) => {
      const messages: string[] = [];
      deadline = setTimeout(() => reject(new Error('preview messages did not arrive')), 2000);
      browser.on('message', (data) => {
        messages.push(data.toString());
        if (messages.length === 1) browser.send('send burst');
        if (messages.length === 6) resolve(messages);
      });
      browser.once('error', reject);
      browser.once('close', () => reject(new Error('preview closed before messages arrived')));
    });
    clearTimeout(deadline);
    expect(received).toEqual(['immediate', 'burst-0', 'burst-1', 'burst-2', 'burst-3', 'burst-4']);
    expect(browser.readyState).toBe(WebSocket.OPEN);
    for (const suffix of ['supported', 'exact'])
      expect(await read_http(downstream.port, downstream.path + suffix)).toEqual({
        status: 200,
        body: 'OK',
      });
    for (const suffix of ['aggregate', 'aggregate-whitespace', 'single'])
      expect(await read_http(downstream.port, downstream.path + suffix)).toEqual({
        status: 502,
        body: 'Preview headers exceed the supported limit.',
      });
    const before = connections;
    expect(
      await read_http(downstream.port, downstream.path + 'oversized-body', {
        'Content-Length': String(REQUEST_BODY_BYTES + 1),
      }),
    ).toEqual({ status: 413, body: 'Preview request body exceeds the supported limit.' });
    expect(connections).toBe(before);
    await new Promise<void>((resolve, reject) => {
      deadline = setTimeout(
        () => reject(new Error('oversized WebSocket message did not close')),
        2000,
      );
      browser.once('close', () => resolve());
      browser.send(Buffer.alloc(WEBSOCKET_MESSAGE_BYTES + 1));
    });
    expect(browser.readyState).toBe(WebSocket.CLOSED);
  } finally {
    clearTimeout(deadline);
    browser.terminate();
    await downstream.close();
    for (const socket of sockets.clients) socket.terminate();
    await new Promise<void>((resolve) => sockets.close(() => resolve()));
    await stop_upstream();
    console.info(
      'PREVIEW_TRANSPORT_BOUNDARY',
      JSON.stringify({
        setup_ms,
        total_ms: performance.now() - started,
        listeners: 2,
        websocket_connections: 1,
        http_requests: 6,
      }),
    );
  }
});
