/** Real TLS proves saved-link reuse and authority refusal before effects or work consumption. */
import { test, expect } from 'vitest';
import { execFileSync } from 'node:child_process';
import { createServer } from 'node:https';
import type { Server } from 'node:https';
import { readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { MachineServerLink } from './machine.ts';
import type { ServerLocation } from './contracts.ts';
import {
  SERVER_LOCATION_HEADER,
  SERVER_LOCATION_PATH,
  SERVER_ID_HEADER,
  MOVE_NUMBER_HEADER,
} from './contracts.ts';
import { HttpSessions, read_bounded } from '../executor/mac/transport/sessions.ts';
import { CONNECTORS } from '../executor/mac/transport/http.ts';

// One fixture supplies both endpoints. No application/backend host is needed.
test('TLS link follows a moved pointer, fences uploads, and adopts lease address replies without deadlock', async () => {
  const root = mkdtempSync(join(tmpdir(), 'server-link-tls-'));
  const key = join(root, 'key.pem'),
    certificate = join(root, 'cert.pem');
  execFileSync(
    'openssl',
    [
      'req',
      '-x509',
      '-newkey',
      'rsa:2048',
      '-nodes',
      '-keyout',
      key,
      '-out',
      certificate,
      '-days',
      '1',
      '-subj',
      '/CN=localhost',
      '-addext',
      'subjectAltName=DNS:localhost,IP:127.0.0.1',
    ],
    { stdio: 'ignore' },
  );
  const tls = { key: readFileSync(key), cert: readFileSync(certificate) };
  const servers: Server[] = [],
    owners: HttpSessions[] = [];
  let old_origin = '',
    new_origin = '',
    mode:
      | 'announce'
      | 'moved'
      | 'foreign'
      | 'stale'
      | 'unavailable'
      | 'probe_unavailable'
      | 'proxy_unavailable' = 'announce';
  let release_late: (() => void) | undefined;
  let late_entered: (() => void) | undefined;
  let old_work = 0,
    new_work = 0,
    probe_credentials = 0,
    consumed = 0,
    probes = 0,
    refused = 0;
  const active = (origin: string, move_number = 1): ServerLocation => ({
    server_id: 'server',
    move_number,
    server_url: origin,
    state: 'active',
  });
  async function listen(old: boolean): Promise<string> {
    const server = createServer(tls, (request, response) => {
      const origin = old ? old_origin : new_origin;
      if (mode === 'proxy_unavailable') {
        request.resume();
        response.writeHead(503).end('<html>Server restarting</html>');
        return;
      }
      let location = active(origin);
      if (old && mode === 'moved') location = { ...active(new_origin), state: 'moved' };
      if (!old && mode === 'foreign') location = { ...location, server_id: 'foreign' };
      if (!old && mode === 'stale') location = active(origin, 0);
      if (request.url === SERVER_LOCATION_PATH) {
        probes++;
        if (mode === 'probe_unavailable') {
          response.writeHead(503).end('<html>Restart in progress</html>');
          return;
        }
        if (request.headers.authorization || request.headers['x-panels-executor-credential'])
          probe_credentials++;
        response.writeHead(location.state === 'moved' ? 503 : 200, {
          'content-type': 'application/json',
        });
        response.end(JSON.stringify(location));
        return;
      }
      response.setHeader(
        SERVER_LOCATION_HEADER,
        Buffer.from(JSON.stringify(location)).toString('base64url'),
      );
      if (
        location.state === 'moved' ||
        request.headers[SERVER_ID_HEADER] !== location.server_id ||
        request.headers[MOVE_NUMBER_HEADER] !== String(location.move_number)
      ) {
        refused++;
        request.resume();
        response.writeHead(location.state === 'moved' ? 503 : 409).end('refused');
        return;
      }
      if (!old && mode === 'unavailable') {
        request.resume();
        response.writeHead(503).end('unavailable');
        return;
      }
      if (old) old_work++;
      else new_work++;
      if (old && mode === 'announce') location = active(new_origin);
      if (request.url === '/delayed') {
        const captured = location;
        release_late = () => {
          response.setHeader(
            SERVER_LOCATION_HEADER,
            Buffer.from(JSON.stringify(captured)).toString('base64url'),
          );
          response.end('late work');
        };
        late_entered?.();
        request.resume();
        return;
      }
      response.setHeader(
        SERVER_LOCATION_HEADER,
        Buffer.from(JSON.stringify(location)).toString('base64url'),
      );
      request.resume();
      response.end('work');
    });
    servers.push(server);
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    const address = server.address();
    if (!address || typeof address === 'string') throw new Error('missing address');
    return `https://127.0.0.1:${address.port}`;
  }
  try {
    old_origin = await listen(true);
    new_origin = await listen(false);
    const link = new MachineServerLink(join(root, 'machine'), old_origin);
    const make_owner = (saved = link) => {
      const owner = new HttpSessions(old_origin, CONNECTORS, {
        tls: { ca: tls.cert },
        server_link: saved,
      });
      owners.push(owner);
      return owner;
    };
    const request = (owner: HttpSessions) =>
      owner.request(
        {
          loop: 'lease',
          method: 'POST',
          path: '/api/executor/lease',
          body: Buffer.from('private work'),
          timeout: 2,
        },
        { 'X-Panels-Executor-Credential': 'private' },
        async (response) => {
          if (response.statusCode === 200) consumed++;
          return (await read_bounded(response, 1024)).toString();
        },
      );
    expect(await request(make_owner())).toBe('work');
    expect(link.current_url).toBe(new_origin);
    const bootstrap_probes = probes;
    expect(await request(make_owner())).toBe('work');
    expect(probes).toBe(bootstrap_probes);
    const restarted_link = new MachineServerLink(join(root, 'machine'), old_origin);
    expect(await request(make_owner(restarted_link))).toBe('work');
    expect(probes).toBe(bootstrap_probes);
    expect(new_work).toBe(2);
    mode = 'foreign';
    await expect(request(make_owner())).rejects.toThrow('another server');
    expect(new_work).toBe(2);
    expect(probes).toBe(bootstrap_probes);
    mode = 'stale';
    await expect(request(make_owner())).rejects.toThrow('older move number');
    expect(new_work).toBe(2);
    // The failed known-link request causes one anonymous recheck, with no replay.
    expect(probes).toBe(bootstrap_probes + 1);
    mode = 'moved';
    expect(await request(make_owner())).toBe('work');
    expect(probes).toBe(bootstrap_probes + 2);
    expect(new_work).toBe(3);
    const before_steady = probes;
    expect(await request(make_owner())).toBe('work');
    expect(probes).toBe(before_steady);

    const fresh = new MachineServerLink(join(root, 'fresh-machine'), old_origin);
    expect(await request(make_owner(fresh))).toBe('work');
    expect(fresh.current_url).toBe(new_origin);
    expect(old_work).toBe(1);
    expect(new_work).toBe(5);
    expect(consumed).toBe(6);
    expect(refused).toBe(1);
    expect(probe_credentials).toBe(0);
    mode = 'unavailable';
    const before_failure = probes;
    expect(await request(make_owner())).toBe('unavailable');
    expect(probes).toBe(before_failure);
    mode = 'moved';
    expect(await request(make_owner())).toBe('work');
    expect(probes).toBe(before_failure + 1);
    expect(consumed).toBe(7);
    const entered = new Promise<void>((resolve) => {
      late_entered = resolve;
    });
    const late = make_owner().request(
      { loop: 'reads', method: 'GET', path: '/delayed', timeout: 2 },
      {},
      async (response) => {
        consumed++;
        return read_bounded(response, 1024);
      },
    );
    await entered;
    await link.prepare(async () => active(new_origin, 2), new_origin);
    release_late!();
    await expect(late).rejects.toThrow('older move number');
    expect(consumed).toBe(7);
    const restart = new MachineServerLink(join(root, 'fresh-machine'), old_origin);
    expect(restart.current_url).toBe(new_origin);
    // A proxy's temporary 503 page is availability failure, not a corrupt server identity.
    mode = 'probe_unavailable';
    const waiting_link = new MachineServerLink(join(root, 'restart-machine'), old_origin);
    const waiting_owner = make_owner(waiting_link);
    await expect(request(waiting_owner)).rejects.toMatchObject({ status: 503 });
    mode = 'moved';
    expect(await request(waiting_owner)).toBe('work');
    mode = 'proxy_unavailable';
    await expect(request(waiting_owner)).rejects.toMatchObject({ status: 503 });
    mode = 'moved';
    expect(await request(waiting_owner)).toBe('work');
  } finally {
    await Promise.all(owners.map((owner) => owner.close()));
    await Promise.all(
      servers.map(
        (server) =>
          new Promise<void>((resolve, reject) => {
            server.close((error) => (error ? reject(error) : resolve()));
            server.closeAllConnections();
          }),
      ),
    );
    rmSync(root, { recursive: true, force: true });
  }
}, 15000);
