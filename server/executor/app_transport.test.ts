import { createHash } from 'node:crypto';
import { chmodSync, existsSync, readFileSync, mkdirSync, realpathSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { MachineServerLink } from '../server_link/machine.ts';
import { request } from 'node:http';
import { once } from 'node:events';
import WebSocket from 'ws';
import { ChildProcesses } from '../lifecycle/owned_processes.ts';
import { fileURLToPath } from 'node:url';
import type { AddressInfo } from 'node:net';
import { MachinePreviews } from '../previews/machine.ts';
import { createPreviewRoutes } from '../previews/routes.ts';
import type { MacRunningChild } from './mac/runner/contracts.ts';
import { expect, test, vi } from 'vitest';
import { createApp } from '../app.ts';
import { RealClock } from '../core/clock.ts';
import { loadConfig } from '../core/config.ts';
import { createReadAccess } from '../core/database/index.ts';
import { ChangeSignal } from '../core/changes/index.ts';
import type { MacUpdateServiceContract } from '../environments/updates/mac/contracts.ts';
import { ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';
import { issue_original_credential } from '../machines/executor_credentials.ts';
import { create, fixture, fixture_path } from '../tests/ticket_fixture.ts';
import { PROTOCOL_VERSION } from '../execution_configuration.ts';
import { EXECUTOR_HANDOVER_CLOSE_PATH } from './configuration.ts';
import { VpsExecutorRegistry } from './registry.ts';
import { executor_link_socket_path, open_executor_link } from '../core/http/executor_link.ts';
import { MacHttpClient } from './mac/transport/http.ts';
import { read_bounded } from './mac/transport/sessions.ts';

// A trial's accepted report precedes the supervisor's final keep decision.
// Inject proves the real route/auth/commit boundary without a new network host.
test('only an explicit credentialed close commits the original executor handover', async () => {
  const database = fixture(),
    path = fixture_path(database);
  const grant = issue_original_credential(database, '11111111-1111-4111-8111-111111111111');
  const registry = new VpsExecutorRegistry(database, new RealClock(), {
    db_path: path,
    changes: new ChangeSignal(),
    media_signal: new AbortController().signal,
    original_enabled: true,
  });
  database.read((sql) => registry.load(sql));
  const unexpected = () => {
    throw new Error('No archive or preparation belongs to this proof');
  };
  const service: MacUpdateServiceContract = {
    for_machine: () => service,
    prepare: async () => unexpected(),
    settled: () => Promise.resolve(),
    check: () => ({ schema_version: 1, target: null, prefetch: null }),
    status: () => null,
    phase: () => null,
    reported_at: () => null,
    archive: unexpected,
    prefetch_archive: unexpected,
    report: vi.fn((report: Parameters<MacUpdateServiceContract['report']>[0]) => ({
      request_id: report.request_id,
      target_revision: report.application_revision,
      phase: report.phase,
      detail: null,
    })),
  };
  registry.bind_updates(service);
  const config = loadConfig(undefined, {
    PLAN_DB_PATH: path,
    PLAN_TEST_MODE: '1',
    PLAN_EXECUTOR_ID: 'test-executor',
    PLAN_TRUSTED_INGRESS_PROVIDER: 'tailscale',
    PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN: 'owner@example.com',
    PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN: 'https://localhost',
  });
  const app = await createApp(config, { database, executor: registry });
  const legacy = {
    'Tailscale-User-Login': 'owner@example.com',
    'X-Panels-Executor-ID': 'test-executor',
  };
  const credentialed = { ...legacy, 'X-Panels-Executor-Credential': grant.credential };
  const row = () => {
    const fresh = createReadAccess(path);
    try {
      return fresh.read((sql) =>
        sql.get<Record<string, unknown>>(
          'SELECT * FROM machine_executor_credentials WHERE machine_id=?',
          [ORIGINAL_MAC_MACHINE_ID],
        ),
      )!;
    } finally {
      fresh.close();
    }
  };
  const before = row(),
    ensure = vi.spyOn(registry, 'ensure');
  try {
    expect(before.legacy_closed_at_ms).toBeNull();
    expect(before.pending_credential).toBe(grant.credential);
    const current = await app.inject({
      method: 'POST',
      url: '/api/executor/update',
      remoteAddress: '127.0.0.1',
      headers: credentialed,
      payload: { request_id: 'trial', application_revision: 'a'.repeat(40), phase: 'current' },
    });
    expect(current.statusCode).toBe(200);
    expect(current.json()).toEqual({ confirmed: true });
    expect(service.report).toHaveBeenCalledOnce();
    expect(row()).toEqual(before);
    ensure.mockClear();
    const refused = await app.inject({
      method: 'POST',
      url: EXECUTOR_HANDOVER_CLOSE_PATH,
      remoteAddress: '127.0.0.1',
      headers: legacy,
    });
    expect(refused.statusCode).toBe(403);
    expect(refused.json().error.code).toBe('agent_forbidden');
    expect(row()).toEqual(before);
    const close = () =>
      app.inject({
        method: 'POST',
        url: EXECUTOR_HANDOVER_CLOSE_PATH,
        remoteAddress: '127.0.0.1',
        // The close needs neither work-version admission nor a current incarnation.
        headers: { ...credentialed, 'X-Panels-Executor-Protocol': '0' },
      });
    const closed = await close();
    expect(closed.statusCode).toBe(200);
    expect(closed.json()).toEqual({ closed: true });
    const committed = row();
    expect(committed).toEqual({
      ...before,
      pending_credential: null,
      legacy_closed_at_ms: expect.any(Number),
    });
    const repeated = await close();
    expect(repeated.statusCode).toBe(200);
    expect(repeated.json()).toEqual({ closed: true });
    expect(row()).toEqual(committed);
    expect(ensure).not.toHaveBeenCalled();
    expect(
      (
        await app.inject({
          method: 'GET',
          url: '/api/executor/update',
          remoteAddress: '127.0.0.1',
          headers: legacy,
        })
      ).statusCode,
    ).toBe(403);
  } finally {
    ensure.mockRestore();
    await registry.stop({ deadline: performance.now() / 1000 + 5 });
    await registry.settled();
    await app.close();
  }
});

// The file sync has no child and no conversation. Inject proves its real route, executor
// admission, and Ticket ownership check together with the served folder.
test('a machine saves and removes files for its own Ticket only', async () => {
  const database = fixture(),
    path = fixture_path(database);
  const grant = issue_original_credential(database, '11111111-1111-4111-8111-111111111111');
  const own = create(database),
    foreign = create(database);
  database.write((tx) =>
    tx.run('UPDATE tickets SET machine_id=? WHERE id=?', [ORIGINAL_MAC_MACHINE_ID, own.id]),
  );
  const registry = new VpsExecutorRegistry(database, new RealClock(), {
    db_path: path,
    changes: new ChangeSignal(),
    media_signal: new AbortController().signal,
    original_enabled: true,
  });
  database.read((sql) => registry.load(sql));
  const unexpected = async () => {
    throw new Error('No conversation work belongs to this proof');
  };
  registry.get(ORIGINAL_MAC_MACHINE_ID)!.runs.conversations = {
    emit_answer: unexpected,
    retire_replaced_remote_child: unexpected,
    resume_undelivered_prompt: unexpected,
    await_late_steer: unexpected,
    confirm_remote_child_termination: unexpected,
    confirm_remote_prompt_accepted: unexpected,
    reconcile_remote_command: unexpected,
    stop_interrupted_remote_child: unexpected,
  };
  const config = loadConfig(undefined, {
    PLAN_DB_PATH: path,
    PLAN_TEST_MODE: '1',
    PLAN_EXECUTOR_ID: 'test-executor',
    PLAN_TRUSTED_INGRESS_PROVIDER: 'tailscale',
    PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN: 'owner@example.com',
    PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN: 'https://localhost',
  });
  const app = await createApp(config, { database, executor: registry });
  const identity = {
    protocol_version: PROTOCOL_VERSION,
    application_revision: 'a'.repeat(40),
    spool_format_version: 5,
    executor_incarnation: '11111111-1111-4111-8111-111111111111',
    connection_generation: 0,
  };
  const headers = {
    'Tailscale-User-Login': 'owner@example.com',
    'X-Panels-Executor-ID': 'test-executor',
    'X-Panels-Executor-Credential': grant.credential,
    'X-Panels-Executor-Incarnation': identity.executor_incarnation,
    'X-Panels-Executor-Protocol': String(PROTOCOL_VERSION),
  };
  const bytes = Buffer.from('report bytes');
  const request = (method: 'PUT' | 'DELETE', ticket_id: string, relative_path: string) => {
    const sha256 = createHash('sha256').update(bytes).digest('hex');
    return app.inject({
      method,
      url: `/api/executor/ticket-files/${sha256}`,
      remoteAddress: '127.0.0.1',
      headers: {
        ...headers,
        'Content-Type': 'application/octet-stream',
        'X-Panels-File-Upload': Buffer.from(
          JSON.stringify({
            scope: 'ticket',
            owner_id: ticket_id,
            relative_path,
            sha256,
            byte_count: bytes.byteLength,
          }),
        ).toString('base64url'),
      },
      ...(method === 'PUT' ? { payload: bytes } : {}),
    });
  };
  const served = (ticket_id: string) =>
    join(dirname(path), 'files/tickets', ticket_id, 'artifacts/report.txt');
  const lease = (protocol_version: number, later: object = {}) =>
    app.inject({
      method: 'POST',
      url: '/api/executor/lease',
      remoteAddress: '127.0.0.1',
      headers,
      payload: {
        identity: { ...identity, protocol_version, ...later },
        process_id: 1,
        mac_wall_clock_ms: Date.now(),
        queue_depth: 0,
        oldest_item_age_ms: null,
        waiting_conversations: [],
        waiting_conversations_truncated: false,
        working_turns: [],
        admission_stopped: false,
        cleanup_uncertainties: [],
        ...later,
      },
    });
  try {
    expect((await lease(PROTOCOL_VERSION)).statusCode).toBe(200);
    // The authenticated endpoint owns this transient provider code, not its durable mailbox.
    const controls = registry.get(ORIGINAL_MAC_MACHINE_ID)!.backend_controls;
    let secret_id = '';
    let release_secret!: () => void;
    const secret_gate = new Promise<void>((resolve) => {
      release_secret = resolve;
    });
    const submit = vi.spyOn(controls, 'request').mockImplementation(async (request) => {
      secret_id = request.secret_id!;
      await secret_gate;
      return { sign_in: null };
    });
    const submitted = controls.submit_return_url(
      'antigravity',
      'session',
      'http://localhost/?code=private-provider-code',
    );
    const secret_path = `/api/executor/backend-secrets/${secret_id}`;
    const fetch_secret = (selected_headers: Record<string, string>) =>
      app.inject({
        method: 'GET',
        url: secret_path,
        remoteAddress: '127.0.0.1',
        headers: selected_headers,
      });
    try {
      expect(
        (await fetch_secret({ ...headers, 'X-Panels-Executor-Credential': 'invalid' })).statusCode,
      ).toBe(403);
      expect(
        (
          await fetch_secret({
            ...headers,
            'X-Panels-Executor-Incarnation': '22222222-2222-4222-8222-222222222222',
          })
        ).statusCode,
      ).toBe(409);
      const answer = await fetch_secret(headers);
      expect(answer.statusCode).toBe(200);
      expect(answer.headers['cache-control']).toBe('no-store');
      expect(answer.json()).toEqual({ return_url: 'http://localhost/?code=private-provider-code' });
      expect((await fetch_secret(headers)).statusCode).toBe(410);
    } finally {
      release_secret();
      await submitted;
      submit.mockRestore();
    }
    expect((await request('PUT', own.id, 'artifacts/report.txt')).statusCode).toBe(204);
    expect(readFileSync(served(own.id))).toEqual(bytes);
    const refused = await request('PUT', foreign.id, 'artifacts/report.txt');
    expect(refused.statusCode).toBe(403);
    expect(existsSync(served(foreign.id))).toBe(false);
    expect((await request('PUT', own.id, 'build/app.bin')).statusCode).toBe(400);
    expect((await request('DELETE', foreign.id, 'artifacts/report.txt')).statusCode).toBe(403);
    expect((await request('DELETE', own.id, 'artifacts/report.txt')).statusCode).toBe(204);
    expect(existsSync(served(own.id))).toBe(false);
    // An adjacent release keeps its lease and its requests, with fields this release ignores.
    for (const adjacent of [PROTOCOL_VERSION - 1, PROTOCOL_VERSION + 1]) {
      expect((await lease(adjacent, { later_field: true })).statusCode).toBe(200);
      headers['X-Panels-Executor-Protocol'] = String(adjacent);
      expect((await request('PUT', own.id, 'artifacts/report.txt')).statusCode).toBe(204);
    }
    expect((await lease(PROTOCOL_VERSION + 2)).json().error.code).toBe('executor_version_refused');
    headers['X-Panels-Executor-Protocol'] = String(PROTOCOL_VERSION + 2);
    expect((await request('PUT', own.id, 'artifacts/report.txt')).statusCode).toBe(409);
  } finally {
    await registry.stop({ deadline: performance.now() / 1000 + 5 });
    await registry.settled();
    await app.close();
  }
});

// The local link replaces the Tailscale login with the socket. The real listener, client
// and agent prove the swap end to end; inject has no socket to mark.
test('machine code on the home account reaches executor routes, and only those, over the local link', async () => {
  const database = fixture(),
    path = fixture_path(database);
  const registry = new VpsExecutorRegistry(database, new RealClock(), {
    db_path: path,
    changes: new ChangeSignal(),
    media_signal: new AbortController().signal,
    original_enabled: true,
  });
  database.read((sql) => registry.load(sql));
  const unexpected = async () => {
    throw new Error('No conversation work belongs to this proof');
  };
  registry.get(ORIGINAL_MAC_MACHINE_ID)!.runs.conversations = {
    emit_answer: unexpected,
    retire_replaced_remote_child: unexpected,
    resume_undelivered_prompt: unexpected,
    await_late_steer: unexpected,
    confirm_remote_child_termination: unexpected,
    confirm_remote_prompt_accepted: unexpected,
    reconcile_remote_command: unexpected,
    stop_interrupted_remote_child: unexpected,
  };
  const config = loadConfig(undefined, {
    PLAN_DB_PATH: path,
    PLAN_TEST_MODE: '1',
    PLAN_EXECUTOR_ID: 'test-executor',
    PLAN_TRUSTED_INGRESS_PROVIDER: 'tailscale',
    PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN: 'owner@example.com',
    PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN: 'https://localhost',
  });
  const previews = createPreviewRoutes();
  const app = await createApp(config, {
    database,
    executor: registry,
    previews,
    areas: [{ prefix: '', routes: previews.routes }],
  });
  await app.ready();
  const socket_path = executor_link_socket_path(path);
  const link = await open_executor_link(app.server, socket_path);
  expect(link).not.toBeNull();
  const identity = {
    protocol_version: PROTOCOL_VERSION,
    application_revision: 'a'.repeat(40),
    spool_format_version: 5,
    executor_incarnation: '11111111-1111-4111-8111-111111111111',
    connection_generation: 0,
  };
  // The machine's server memory proves its identity probe also goes over the local link.
  const server_link = new MachineServerLink(join(dirname(path), 'machine'), socket_path);
  const client = new MacHttpClient(socket_path, 'test-executor', identity, { server_link });
  const lease = {
    identity,
    process_id: 1,
    mac_wall_clock_ms: Date.now(),
    queue_depth: 0,
    oldest_item_age_ms: null,
    waiting_conversations: [],
    waiting_conversations_truncated: false,
    working_turns: [],
    admission_stopped: false,
    cleanup_uncertainties: [],
  };
  const machine_previews = new MachinePreviews(client);
  const application_children = new ChildProcesses();
  const ticket_home = join(dirname(path), 'managed-preview-ticket');
  const worktree = join(ticket_home, 'worktree');
  mkdirSync(worktree, { recursive: true });
  const refusal = (url: string, headers: Record<string, string>) =>
    client.sessions.request(
      { loop: 'reads', method: 'POST', path: url, timeout: 10, body: Buffer.from('{}') },
      headers,
      async (response) => {
        const body = JSON.parse(String(await read_bounded(response, 64 * 1024)));
        return `${response.statusCode} ${body.error.message}`;
      },
    );
  try {
    await client.ensure_credential(join(dirname(path), 'link-credential.json'));
    expect(client.headers['X-Panels-Executor-Credential']).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect('error' in (await client.lease(lease))).toBe(false);
    // Reuse this authenticated local-link host to prove native registration and byte relay.
    // Provider setup is irrelevant: the synthetic assignment is the real authority seam.
    const preview_started = performance.now();
    const endpoint = registry.get(ORIGINAL_MAC_MACHINE_ID)!;
    const ticket = create(database, { machine_id: ORIGINAL_MAC_MACHINE_ID });
    const child_reference = {
      executor_incarnation: identity.executor_incarnation,
      child_id: '22222222-2222-4222-8222-222222222222',
      conversation_id: 'preview-conversation',
    };
    database.write((tx) =>
      tx.run('INSERT INTO executor_child_assignments VALUES (?,?,?,?,?,?,NULL)', [
        identity.executor_incarnation,
        child_reference.child_id,
        child_reference.conversation_id,
        'ticket',
        ticket.id,
        Date.now(),
      ]),
    );
    const child = {
      reference: child_reference,
      owner: { kind: 'ticket', id: ticket.id },
      workspace: { machine_id: ORIGINAL_MAC_MACHINE_ID, workspace_id: 'fixture-workspace' },
      localized_start: { workspace_folder: ticket_home },
    } as unknown as MacRunningChild;
    const application = application_children.spawn(
      process.execPath,
      [fileURLToPath(new URL('../previews/testing/application.ts', import.meta.url))],
      { cwd: worktree, stdio: 'pipe' },
    );
    const application_port = (
      JSON.parse(String((await once(application.stdout, 'data'))[0])) as { port: number }
    ).port;
    const prepared = await machine_previews.command(child, { operation: 'prepare' });
    expect(prepared.worktree).toBe(realpathSync(worktree));
    await expect(
      client.preview({
        operation: 'prepare',
        child: { ...child_reference, child_id: '33333333-3333-4333-8333-333333333333' },
      }),
    ).rejects.toThrow('no VPS assignment');
    const registered = await machine_previews.command(child, {
      operation: 'register',
      port: application_port,
    });
    expect(registered.state).toBe('active');
    expect(registered).not.toHaveProperty('process');
    await app.listen({ port: 0, host: '127.0.0.1' });
    const browser_port = (app.server.address() as AddressInfo).port;
    const browser_headers = { Host: 'localhost', 'Tailscale-User-Login': 'owner@example.com' };
    const page = await new Promise<string>((resolve, reject) => {
      const call = request(
        {
          host: '127.0.0.1',
          port: browser_port,
          path: String(registered.base_path),
          headers: browser_headers,
        },
        (response) => {
          const chunks: Buffer[] = [];
          response.on('data', (chunk) => chunks.push(chunk));
          response.once('end', () => resolve(Buffer.concat(chunks).toString()));
          response.once('error', reject);
        },
      );
      call.once('error', reject);
      call.end();
    });
    expect(page).toBe('machine preview');
    const browser = new WebSocket(`ws://127.0.0.1:${browser_port}${registered.base_path}`, {
      headers: browser_headers,
    });
    try {
      await once(browser, 'open');
      const echo = once(browser, 'message');
      browser.send('hot reload');
      expect((await echo)[0].toString()).toBe('hot reload');
    } finally {
      browser.terminate();
    }
    await client.sessions.reset();
    await expect(
      previews.remote.connect({
        ticket_id: ticket.id,
        generation: String(registered.generation),
      } as never),
    ).rejects.toThrow();
    await machine_previews.command(child, { operation: 'stop' });
    await expect(
      previews.remote.connect({
        ticket_id: ticket.id,
        generation: String(registered.generation),
      } as never),
    ).rejects.toThrow();
    console.info(
      'MACHINE_PREVIEW_BOUNDARY',
      JSON.stringify({
        total_ms: performance.now() - preview_started,
        extra_application_listeners: 1,
        browser_sessions: 1,
      }),
    );

    expect(server_link.current()).toMatchObject({ server_url: socket_path });
    expect(server_link.current().server_id).not.toBeNull();
    expect(await refusal('/api/tickets', client.headers)).toBe(
      '403 the local executor link serves executor routes only',
    );
    // A raw path keeps its `..` segments: the router matches the files route, not the executor one.
    const raw_status = await new Promise<number | undefined>((resolve, reject) => {
      const raw = request(
        {
          socketPath: socket_path,
          method: 'PUT',
          path: '/files/managers/x/../../../api/executor/lease',
          headers: client.headers,
        },
        (response) => {
          response.resume();
          resolve(response.statusCode);
        },
      );
      raw.once('error', reject);
      raw.end();
    });
    expect(raw_status).toBe(403);
    expect(
      await refusal('/api/executor/lease', {
        ...client.headers,
        'Tailscale-User-Login': 'owner@example.com',
      }),
    ).toBe('403 the local executor link does not accept a Tailscale login');
    expect(
      await refusal('/api/executor/lease', {
        ...client.headers,
        'X-Panels-Executor-Credential': 'x'.repeat(43),
      }),
    ).toBe('403 Executor credential is required or invalid');
    // The lease loop opens a fresh connection for each request, so each one checks again.
    chmodSync(dirname(socket_path), 0o750);
    await expect(client.lease(lease)).rejects.toThrow('no group or other access');
    chmodSync(dirname(socket_path), 0o700);
    expect('error' in (await client.lease(lease))).toBe(false);
  } finally {
    await machine_previews.stop();
    application_children.force_kill(performance.now() / 1000 + 5);
    await application_children.settled();
    await previews.stop({ deadline: performance.now() / 1000 + 5 }, Promise.resolve());
    await previews.settled();
    link?.close();
    await client.stop({ deadline: performance.now() / 1000 + 5 });
    await registry.stop({ deadline: performance.now() / 1000 + 5 });
    await registry.settled();
    await app.close();
  }
  expect(existsSync(socket_path)).toBe(false);
});
