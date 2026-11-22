import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import Fastify from 'fastify';
import { expect, test } from 'vitest';
import { createDatabaseFromSeed, createReadAccess } from '../core/database/index.ts';
import { application_schema_seed } from '../core/database/admission.ts';
import { loadConfig } from '../core/config.ts';
import { buildClock } from '../core/clock.ts';
import { installMiddleware } from '../core/http/middleware.ts';
import { registerCoreRoutes } from '../core/routes.ts';
import { CLIENT_COMPATIBILITY, supported_machine_versions } from './policy.ts';
import { parse_client_identity } from './identity.ts';
import { client_version_meta } from './meta.ts';

// Distinct risk: a native forward without Origin bypasses compatibility, or a rejected
// declaration disappears from diagnostics. Reuse Fastify inject and the schema seed.
test('declared versions gate writes and accepted device versions survive a database reopen', async () => {
  const root = mkdtempSync(join(tmpdir(), 'panels-client-versions-'));
  const path = join(root, 'test.sqlite');
  const database = createDatabaseFromSeed(path, application_schema_seed, () => {});
  const app = Fastify();
  const config = loadConfig(undefined, {
    PLAN_DB_PATH: path,
    PLAN_TEST_MODE: '1',
    PLAN_APP_SHA: 'a'.repeat(40),
  });
  const clock = buildClock(config);
  installMiddleware(app, config, undefined, undefined, undefined, { database, clock });
  await registerCoreRoutes(
    app,
    config,
    clock,
    { subscribe: () => () => {}, emit() {} },
    root,
    database,
  );
  app.post('/api/write', async () => ({ ok: true }));
  const identity = {
    kind: 'desktop',
    device_id: 'desktop-one',
    version: '0.0.1',
    bridge_version: 1,
  };
  const headers = (value: unknown) => ({ 'x-panels-client': JSON.stringify(value) });
  const saved = () =>
    database.read((sql) =>
      sql.all<{ identity_json: string }>(
        "SELECT identity_json FROM client_versions WHERE kind='desktop'",
      ),
    );
  try {
    expect((await app.inject({ method: 'POST', url: '/api/write' })).statusCode).toBe(200);
    expect(
      (
        await app.inject({
          method: 'POST',
          url: '/api/write',
          headers: headers({ ...identity, bridge_version: undefined }),
        })
      ).statusCode,
    ).toBe(426);
    expect(
      (
        await app.inject({
          method: 'POST',
          url: '/api/write',
          headers: headers({ ...identity, bridge_version: 0 }),
        })
      ).statusCode,
    ).toBe(426);
    expect(saved().map((row) => JSON.parse(row.identity_json))).toEqual([
      { ...identity, bridge_version: 0 },
    ]);
    const meta = await app.inject({
      url: '/api/meta',
      headers: headers({ ...identity, bridge_version: 0 }),
    });
    expect(meta.statusCode).toBe(200);
    expect(meta.headers['cache-control']).toBe('no-store');
    expect(meta.json()).toEqual({
      test_mode: true,
      app_sha: 'a'.repeat(40),
      compatibility: CLIENT_COMPATIBILITY,
    });
    expect(
      (await app.inject({ method: 'POST', url: '/api/write', headers: headers(identity) }))
        .statusCode,
    ).toBe(200);
    expect(saved().map((row) => JSON.parse(row.identity_json))).toEqual([identity]);
    const newer = { ...identity, version: '0.0.2', bridge_version: 2 };
    expect(
      (await app.inject({ method: 'POST', url: '/api/write', headers: headers(newer) })).statusCode,
    ).toBe(200);
    // The record describes the last version sent, even when the endpoint does not exist.
    const latest = { ...newer, version: '0.0.3' };
    expect((await app.inject({ url: '/api/missing', headers: headers(latest) })).statusCode).toBe(
      404,
    );
    expect(saved().map((row) => JSON.parse(row.identity_json))).toEqual([latest]);
    config.trusted_ingress_provider = 'tailscale';
    config.trusted_ingress_allowed_login = 'owner@example.test';
    config.trusted_ingress_canonical_origin = 'https://panels.example';
    expect(
      (
        await app.inject({
          method: 'POST',
          url: '/api/write',
          headers: { ...headers(identity), origin: 'https://other.example' },
        })
      ).statusCode,
    ).toBe(403);
    expect(saved().map((row) => JSON.parse(row.identity_json))).toEqual([latest]);
    const duplicate = await app.inject({
      method: 'POST',
      url: '/api/write',
      headers: { 'x-panels-client': [JSON.stringify(identity), JSON.stringify(identity)] },
    });
    expect(duplicate.statusCode).toBe(400);
    expect(
      (await app.inject({ url: '/api/meta', headers: { 'x-panels-client': '{bad' } })).statusCode,
    ).toBe(400);
    const legacy_ios = await app.inject({
      url: '/api/meta',
      headers: { 'x-panels-ios-build': '0' },
    });
    expect(legacy_ios.statusCode).toBe(426);
    expect(legacy_ios.json().error.code).toBe('ios_update_required');
    const ios = await app.inject({
      url: '/api/meta',
      headers: headers({ kind: 'ios', device_id: 'ios-one', version: '1', build: 0 }),
    });
    expect(ios.statusCode).toBe(426);
    const old_page = await app.inject({
      method: 'POST',
      url: '/api/write',
      headers: { 'x-panels-app-sha': 'b'.repeat(40) },
    });
    expect(old_page.statusCode).toBe(409);
  } finally {
    await app.close();
    database.close();
  }
  const reopened = createReadAccess(path);
  try {
    expect(
      reopened
        .read((sql) =>
          sql.all<{ identity_json: string }>(
            "SELECT identity_json FROM client_versions WHERE kind='desktop'",
          ),
        )
        .map((row) => JSON.parse(row.identity_json)),
    ).toEqual([{ ...identity, version: '0.0.3', bridge_version: 2 }]);
  } finally {
    reopened.close();
    rmSync(root, { recursive: true, force: true });
  }
});

// Distinct risk: metadata advertises a downloaded release or disagrees with the machine
// admission boundaries. File fixtures and the shared policy prove this without a server.
test('metadata describes optional installed release facts and the actual compatibility boundaries', () => {
  const root = mkdtempSync(join(tmpdir(), 'panels-version-meta-'));
  try {
    expect(client_version_meta(root)).toEqual({ compatibility: CLIENT_COMPATIBILITY });
    const parts = {
      server: { app_sha: 'a', artifact_digest: 'b'.repeat(64) },
      screens: { sha256: 'c'.repeat(64) },
      machines: {
        'linux-x86_64': { archive_sha256: 'd'.repeat(64), build_id: null },
        'darwin-arm64': { archive_sha256: 'e'.repeat(64), build_id: 'machine-build' },
      },
    };
    writeFileSync(
      join(root, 'manifest.json'),
      JSON.stringify({ app_sha: 'a', release_number: 123, parts }),
    );
    expect(client_version_meta(root, 'a')).toEqual({
      compatibility: CLIENT_COMPATIBILITY,
      release_number: 123,
      parts,
    });
    expect(client_version_meta(root, 'b')).toEqual({ compatibility: CLIENT_COMPATIBILITY });
    writeFileSync(join(root, 'manifest.json'), JSON.stringify({ app_sha: 'a' }));
    expect(client_version_meta(root, 'a')).toEqual({ compatibility: CLIENT_COMPATIBILITY });
    const { minimum_protocol_version: min, maximum_protocol_version: max } =
      CLIENT_COMPATIBILITY.machine;
    expect(supported_machine_versions(min, CLIENT_COMPATIBILITY.outbox_format)).toBe(true);
    expect(supported_machine_versions(max, CLIENT_COMPATIBILITY.outbox_format)).toBe(true);
    expect(supported_machine_versions(min - 1, CLIENT_COMPATIBILITY.outbox_format)).toBe(false);
    expect(supported_machine_versions(max + 1, CLIENT_COMPATIBILITY.outbox_format)).toBe(false);
    expect(supported_machine_versions(min, CLIENT_COMPATIBILITY.outbox_format + 1)).toBe(false);
    expect(() =>
      parse_client_identity([
        JSON.stringify({ kind: 'desktop', device_id: 'one', version: '1', bridge_version: true }),
      ]),
    ).toThrow('nonnegative integer');
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
