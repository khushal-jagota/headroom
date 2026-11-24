/** A real server restart crosses both authenticated transports while one machine process keeps its exact turn. */
import { test, expect, vi } from 'vitest';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createServer } from 'node:https';
import { request as http_request } from 'node:http';
import { once } from 'node:events';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline';
import { setTimeout as pause } from 'node:timers/promises';
import { ChildProcesses } from '../lifecycle/owned_processes.ts';
import { start_native_server } from '../executor/mac/testing/native_server.ts';
import { executor_link_socket_path } from '../core/http/executor_link.ts';
import { credential_path, write_credential } from '../executor/credential_file.ts';
import type { JsonObject, ItemBatch, ItemBatchReply } from '../executor/wire.generated.ts';

async function until<T>(read: () => Promise<T>, accepts: (value: T) => boolean): Promise<T> {
  const signal = AbortSignal.timeout(40_000);
  for (;;) {
    signal.throwIfAborted();
    const value = await read();
    if (accepts(value)) return value;
    await pause(50, undefined, { signal });
  }
}

// The two transports prove distinct ingress/connection-recreation boundaries. Both reuse
// the production server composition and the existing real machine-code fixture.
test.each(['socket', 'https'] as const)(
  'machine turn and commands survive a 35-second %s server restart',
  async (transport) => {
    const started = performance.now();
    const root = await mkdtemp(join(tmpdir(), 'restart-'));
    const owner = new ChildProcesses();
    const database = join(root, 'home.db');
    let target = '';
    let drop_next_write_reply = false;
    let lost_replies = 0;
    const tls_key = join(root, 'key.pem'),
      tls_cert = join(root, 'cert.pem');
    execFileSync(
      'openssl',
      [
        'req',
        '-x509',
        '-newkey',
        'rsa:2048',
        '-nodes',
        '-keyout',
        tls_key,
        '-out',
        tls_cert,
        '-days',
        '1',
        '-subj',
        '/CN=localhost',
        '-addext',
        'subjectAltName=DNS:localhost,IP:127.0.0.1',
      ],
      { stdio: 'ignore' },
    );
    const proxy = createServer(
      { key: await readFile(tls_key), cert: await readFile(tls_cert) },
      (incoming, outgoing) => {
        if (!target) {
          incoming.resume();
          outgoing.writeHead(503).end();
          return;
        }
        const chunks: Buffer[] = [];
        incoming.on('data', (chunk: Buffer) => chunks.push(chunk));
        incoming.on('error', (error) => outgoing.destroy(error));
        incoming.on('end', () => {
          const body = Buffer.concat(chunks);
          const drop =
            drop_next_write_reply &&
            incoming.url?.includes('/items') &&
            body.includes('Filed despite lost reply');
          if (drop) drop_next_write_reply = false;
          const request = http_request(
            new URL(incoming.url!, target),
            {
              method: incoming.method,
              headers: { ...incoming.headers, 'tailscale-user-login': 'restart@example.invalid' },
            },
            (response) => {
              if (drop) {
                expect(response.statusCode).toBe(200);
                const chunks: Buffer[] = [];
                response.on('data', (chunk: Buffer) => chunks.push(chunk));
                response.once('end', () => {
                  const request_body = JSON.parse(body.toString()) as ItemBatch;
                  const submission = request_body.items.find((item) =>
                    JSON.stringify(item).includes('Filed despite lost reply'),
                  )!.envelope.submission_id;
                  const receipt_body = JSON.parse(
                    Buffer.concat(chunks).toString(),
                  ) as ItemBatchReply;
                  expect(
                    receipt_body.receipts.find((receipt) => receipt.submission_id === submission),
                  ).toMatchObject({ disposition: 'accepted' });
                  lost_replies++;
                  outgoing.destroy();
                });
              } else {
                outgoing.writeHead(response.statusCode!, response.headers);
                response.pipe(outgoing);
              }
            },
          );
          request.on('error', (error) => outgoing.destroy(error));
          outgoing.on('close', () => request.destroy());
          request.end(body);
        });
      },
    );
    proxy.listen(0, '127.0.0.1');
    await once(proxy, 'listening');
    const address = proxy.address();
    assert.ok(address && typeof address !== 'string');
    const origin = `https://127.0.0.1:${address.port}`;
    for (const [key, value] of Object.entries({
      PLAN_DB_PATH: database,
      PLAN_PORT: '0',
      PLAN_TEST_MODE: '1',
      PLAN_EXECUTOR_ID: 'test-executor',
      PLAN_DISPATCH_ENABLED: 'false',
      PLAN_LOGS_DIR: join(root, 'logs'),
      PLAN_BACKUP_DIR: join(root, 'backups'),
      PLAN_DISPATCHER_LOCK_PATH: join(root, 'dispatcher.lock'),
      PLAN_SERVER_CONTROL_SOCKET: join(root, 'control.sock'),
      PLAN_TRUSTED_INGRESS_PROVIDER: 'tailscale',
      PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN: 'restart@example.invalid',
      PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN: origin,
    }))
      vi.stubEnv(key, value);
    let server: Awaited<ReturnType<typeof start_native_server>> | undefined;
    let peer: ReturnType<ChildProcesses['spawn']> | undefined;
    let peer_error = '';
    let serial = 0;
    const pending = new Map<
      number,
      { resolve(value: JsonObject): void; reject(error: Error): void }
    >();
    let ready!: () => void;
    const readiness = new Promise<void>((resolve) => {
      ready = resolve;
    });
    const command = (operation: string, values: JsonObject = {}): Promise<JsonObject> => {
      const id = ++serial;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        peer!.stdin!.write(JSON.stringify({ id, operation, ...values }) + '\n');
      });
    };
    try {
      server = await start_native_server();
      target = server.address;
      const credential = server.credential;
      const state_root = join(root, 'machine');
      await write_credential(credential_path(state_root), credential);
      const home = join(root, 'provider-home');
      await mkdir(join(home, 'skills'), { recursive: true });
      const environment = { ...process.env, HOME: home, CODEX_HOME: home, CLAUDE_CONFIG_DIR: home };
      for (const name of Object.keys(environment))
        if (name.startsWith('PLAN_')) delete environment[name as keyof typeof environment];
      const input = join(root, 'machine.json');
      await writeFile(
        input,
        JSON.stringify({
          origin: transport === 'socket' ? executor_link_socket_path(database) : origin,
          fixture_origin: origin,
          allocated_port: address.port,
          ca_path: tls_cert,
          fixture_root: root,
          fixture_database_path: database,
          root: state_root,
          revision: 'restart-proof',
          environment,
        }),
      );
      peer = owner.spawn(
        process.execPath,
        [fileURLToPath(new URL('../executor/mac/testing/end_to_end.ts', import.meta.url)), input],
        { env: environment, stdio: ['pipe', 'pipe', 'pipe'] },
      );
      peer.stderr!.on('data', (chunk) => {
        peer_error += String(chunk);
      });
      peer.once('exit', (code) => {
        for (const waiter of pending.values())
          waiter.reject(new Error(`Machine exited ${code}: ${peer_error}`));
        pending.clear();
      });
      const lines = createInterface({ input: peer.stdout! });
      lines.on('line', (line) => {
        const response = JSON.parse(line) as {
          ready?: boolean;
          id: number;
          result: JsonObject;
          error?: string;
        };
        if (response.ready) ready();
        else {
          const waiter = pending.get(response.id);
          pending.delete(response.id);
          if (response.error) waiter?.reject(new Error(response.error));
          else waiter?.resolve(response.result);
        }
      });
      await Promise.race([
        readiness,
        pause(35_000).then(() => {
          throw new Error(`Machine startup timeout: ${peer_error}`);
        }),
      ]);
      await server.wait_for_presence();
      const turn = await server.create_turn('codex');
      const before = await until(
        () => command('state'),
        (value) => (value.calls as JsonObject).prompts === 1,
      );
      const original_child = (before.children as JsonObject[])[0]!.reference;
      const original_turn = before.turn;
      await server.stop();
      server = undefined;
      target = '';
      const stopped_at = performance.now();
      let read_finished = false,
        write_finished = false;
      const read = command('ipc', {
        kind: 'read',
        payload: { path: `/api/tickets/${turn.ticket_id}/worker-self`, params: {} },
      }).then((value) => {
        read_finished = true;
        return value;
      });
      const write = command('ipc', {
        kind: 'write',
        payload: {
          operation: 'cli.request',
          arguments: {
            method: 'POST',
            path: `/api/tickets/${turn.ticket_id}/propose`,
            body: { body: 'Filed during restart' },
          },
        },
      }).then((value) => {
        write_finished = true;
        return value;
      });
      void read.catch(() => {});
      void write.catch(() => {});
      await command('emit', { kind: 'tool', native_id: 'offline-tool' });
      await command('emit', {
        kind: 'progress',
        native_id: 'offline-tool',
        detail: 'offline progress',
      });
      await command('emit', { kind: 'completed', text: 'plain answer' });
      await pause(35_000);
      const offline_seconds = (performance.now() - stopped_at) / 1000;
      expect(read_finished).toBe(false);
      expect(write_finished).toBe(false);
      const offline = await command('state');
      expect((offline.children as JsonObject[])[0]!.reference).toEqual(original_child);
      expect(offline.turn).toEqual(original_turn);
      expect((offline.calls as JsonObject).stops).toBe(0);
      const offline_events = (offline.pending as JsonObject[]).filter(
        (item) => item.kind === 'backend_event',
      );
      expect(offline_events.map((item) => (item.payload as JsonObject).method)).toEqual([
        'tool_call_started',
        'tool_call_progress',
        'agent_message_completed',
      ]);

      server = await start_native_server(credential);
      target = server.address;
      const [read_result, write_result] = await Promise.all([read, write]);
      expect(read_result.error).toBeNull();
      expect((read_result.result as JsonObject).id).toBe(turn.ticket_id);
      expect(write_result.error).toBeNull();
      expect(write_result.unconfirmed ?? null).toBeNull();
      const revision = () =>
        server!.context.database.read(
          (sql) =>
            sql.get<{ revision: number }>(
              'SELECT pending_proposal_revision AS revision FROM tickets WHERE id=?',
              [turn.ticket_id],
            )!.revision,
        );
      expect(revision()).toBe(1);
      if (transport === 'https') {
        drop_next_write_reply = true;
        const lost = await command('ipc', {
          kind: 'write',
          payload: {
            operation: 'cli.request',
            arguments: {
              method: 'POST',
              path: `/api/tickets/${turn.ticket_id}/propose`,
              body: { body: 'Filed despite lost reply' },
            },
          },
        });
        expect(lost.error).toBeNull();
        expect(lost.unconfirmed ?? null).toBeNull();
        expect(lost_replies).toBe(1);
      }
      expect(revision()).toBe(transport === 'https' ? 2 : 1);
      await command('emit', { kind: 'tool_finished', native_id: 'offline-tool' });
      await command('emit', { kind: 'end' });
      const events = await server.turn_result(turn.conversation_id);
      const kinds = events.map((event) => event.payload.kind);
      expect(
        kinds.filter((kind) => ['tool_call_started', 'agent_message', 'turn_ended'].includes(kind)),
      ).toEqual(['tool_call_started', 'agent_message', 'turn_ended']);
      const after = await until(
        () => command('state'),
        (state) =>
          offline_events.every((item) =>
            (state.receipts as JsonObject[]).some(
              (receipt) =>
                receipt.submission_id === item.submission_id && receipt.disposition === 'accepted',
            ),
          ),
      );
      expect((after.children as JsonObject[])[0]!.reference).toEqual(original_child);
      expect((after.calls as JsonObject).starts).toBe(1);
      expect((after.calls as JsonObject).prompts).toBe(1);
      expect((after.calls as JsonObject).stops).toBe(0);
      console.log(
        JSON.stringify({
          transport,
          offline_seconds,
          elapsed_seconds: (performance.now() - started) / 1000,
          original_child,
          original_turn,
          lost_replies,
        }),
      );
      await command('stop');
      lines.close();
    } finally {
      await server?.stop();
      owner.force_kill(performance.now() / 1000 + 20);
      await owner.settled();
      proxy.closeAllConnections();
      await new Promise<void>((resolve) => proxy.close(() => resolve()));
      await rm(root, { recursive: true, force: true });
      vi.unstubAllEnvs();
    }
  },
  180_000,
);
