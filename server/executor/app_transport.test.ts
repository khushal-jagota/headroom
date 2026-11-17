import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
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
  try {
    const lease = await app.inject({
      method: 'POST',
      url: '/api/executor/lease',
      remoteAddress: '127.0.0.1',
      headers,
      payload: {
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
      },
    });
    expect(lease.statusCode).toBe(200);
    expect((await request('PUT', own.id, 'artifacts/report.txt')).statusCode).toBe(204);
    expect(readFileSync(served(own.id))).toEqual(bytes);
    const refused = await request('PUT', foreign.id, 'artifacts/report.txt');
    expect(refused.statusCode).toBe(403);
    expect(existsSync(served(foreign.id))).toBe(false);
    expect((await request('PUT', own.id, 'build/app.bin')).statusCode).toBe(400);
    expect((await request('DELETE', foreign.id, 'artifacts/report.txt')).statusCode).toBe(403);
    expect((await request('DELETE', own.id, 'artifacts/report.txt')).statusCode).toBe(204);
    expect(existsSync(served(own.id))).toBe(false);
  } finally {
    await registry.stop({ deadline: performance.now() / 1000 + 5 });
    await registry.settled();
    await app.close();
  }
});
