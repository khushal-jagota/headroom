/** Owned native HTTP/TLS/proxy fixture. No command, address or environment protocol. */
import { readFileSync } from 'node:fs';
import { X509Certificate } from 'node:crypto';
import { rootCertificates } from 'node:tls';
import { createServer as httpServer, request } from 'node:http';
import { createServer as httpsServer } from 'node:https';
import { connect } from 'node:net';
import type { Server, IncomingMessage, ServerResponse } from 'node:http';
import { CLI_TRANSPORT_LIMITS, connection_message } from '../http.ts';

const projects = JSON.stringify({
  projects: [{ id: 'project_transport', name: 'Transport proof' }],
  page: {
    match_count: 1,
    return_count: 1,
    limit: 20,
    offset: 0,
    omitted_before: 0,
    omitted_after: 0,
    complete: true,
    next_offset: null,
  },
});
let proxied = 0;
const answer = (req: IncomingMessage, res: ServerResponse) => {
  if (req.url === '/api/meta') {
    res.end('{"test_mode":true}');
    return;
  }
  if (req.url === '/proxy-count') {
    res.end(JSON.stringify({ proxied }));
    return;
  }
  if (req.url === '/incomplete') {
    req.socket.end('HTTP/1.1 200 OK\r\nContent-Length: 20\r\nConnection: close\r\n\r\nshort');
    return;
  }
  if (req.url === '/headers') {
    // Write actual incomplete header bytes: response is not emitted until400ms.
    const socket = req.socket;
    socket.write('HTTP/1.1 200 OK\r\nContent-Type: application/');
    const first = setTimeout(() => socket.write('json\r\nContent-Length: 14\r\n'), 200);
    const last = setTimeout(() => socket.end('Connection: close\r\n\r\n{"phase":"ok"}'), 400);
    socket.on('close', () => {
      clearTimeout(first);
      clearTimeout(last);
    });
    return;
  }
  res.setHeader('content-type', 'application/json');
  if (req.url === '/slow' || req.url === '/stall') {
    res.write('{"phase":');
    const stalled = req.url === '/stall';
    const first = setTimeout(() => res.write('"ok"'), stalled ? 600 : 200);
    const last = setTimeout(() => res.end('}'), stalled ? 650 : 400);
    res.on('close', () => {
      clearTimeout(first);
      clearTimeout(last);
    });
  } else res.end(projects);
};
const http = httpServer(answer);
const https = httpsServer(
  { cert: readFileSync(process.argv[2]), key: readFileSync(process.argv[3]) },
  answer,
);
async function listen(server: Server): Promise<number> {
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  if (address === null || typeof address === 'string') throw new Error('missing fixture listener');
  return address.port;
}
const httpPort = await listen(http),
  tlsPort = await listen(https);
const proxy = httpServer((req, res) => {
  const target = new URL(req.url!);
  if (target.origin !== `http://127.0.0.1:${httpPort}`) {
    res.writeHead(403).end();
    return;
  }
  proxied++;
  const outgoing = request(target, { method: req.method, headers: req.headers }, (response) => {
    res.writeHead(response.statusCode ?? 500, response.headers);
    response.pipe(res);
  });
  outgoing.on('error', () => res.writeHead(502).end());
  req.pipe(outgoing);
});
proxy.on('connect', (req, client, head) => {
  if (req.url !== `127.0.0.1:${tlsPort}`) {
    client.end('HTTP/1.1 403 Forbidden\r\n\r\n');
    return;
  }
  proxied++;
  const upstream = connect(tlsPort, '127.0.0.1', () => {
    client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
    if (head.length) upstream.write(head);
    upstream.pipe(client);
    client.pipe(upstream);
  });
  upstream.on('error', () => client.destroy());
  client.on('error', () => upstream.destroy());
});
const proxyPort = await listen(proxy);
const messages: Record<string, string> = {};
for (const code of [
  'ECONNREFUSED',
  'ECONNRESET',
  'ENETUNREACH',
  'EHOSTUNREACH',
  'EACCES',
  'EMFILE',
  'ENOENT',
])
  messages[code] = await connection_message(Object.assign(new Error(code), { code }));
process.stdout.write(
  JSON.stringify({
    httpPort,
    tlsPort,
    proxyPort,
    pid: process.pid,
    defaults: CLI_TRANSPORT_LIMITS,
    messages,
    root_fingerprints: rootCertificates
      .map((cert) => new X509Certificate(cert).fingerprint256.replaceAll(':', '').toLowerCase())
      .sort(),
  }) + '\n',
);
