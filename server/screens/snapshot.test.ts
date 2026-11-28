import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import Fastify from 'fastify';
import { expect, test } from 'vitest';
import { loadConfig } from '../core/config.ts';
import { buildClock } from '../core/clock.ts';
import { createDatabaseFromSeed } from '../core/database/index.ts';
import { application_schema_seed } from '../core/database/admission.ts';
import { registerCoreRoutes } from '../core/routes.ts';
import { ScreenSnapshots } from './snapshot.ts';
import type { ScreenMeta, ScreenPayload } from './contracts.ts';

async function build(root: string, marker: string): Promise<void> {
  await mkdir(join(root, 'web/dist'), { recursive: true });
  await mkdir(join(root, 'assets'), { recursive: true });
  await mkdir(join(root, 'static'), { recursive: true });
  await writeFile(join(root, 'web/dist/index.html'), `<main>${marker}</main>`);
  await writeFile(join(root, 'assets/app.css'), `/* ${marker} */`);
  await writeFile(join(root, 'static/icon.svg'), `<svg>${marker}</svg>`);
}

// Distinct risk: meta names one release while download returns different bytes or an
// unadmitted waiting root. Fastify inject proves both HTTP doors with one existing host.
test('metadata and the digest endpoint share only installed and admitted waiting snapshots', async () => {
  const folder = await mkdtemp(join(tmpdir(), 'panels-screen-routes-'));
  const current = join(folder, 'current');
  const waiting = join(folder, 'waiting');
  await build(current, 'current');
  await build(waiting, 'waiting');
  let waiting_root: string | null = waiting;
  const database = createDatabaseFromSeed(
    join(folder, 'test.sqlite'),
    application_schema_seed,
    () => {},
  );
  const config = loadConfig(undefined, {
    PLAN_DB_PATH: join(folder, 'test.sqlite'),
    PLAN_APP_SHA: 'a'.repeat(40),
  });
  const app = Fastify();
  try {
    await registerCoreRoutes(
      app,
      config,
      buildClock(config),
      current,
      database,
      () => waiting_root,
    );
    const response = await app.inject('/api/meta');
    const meta = response.json<ScreenMeta>();
    expect(response.headers['cache-control']).toBe('no-store');
    expect(meta.screens?.sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(meta.waiting_screens?.sha256).not.toBe(meta.screens?.sha256);
    expect(response.json().parts).toBeUndefined();
    const payload_response = await app.inject(`/api/screens/${meta.screens!.sha256}`);
    expect(payload_response.headers['cache-control']).toBe('no-store');
    const payload = payload_response.json<ScreenPayload>();
    expect(createHash('sha256').update(JSON.stringify(payload.manifest)).digest('hex')).toBe(
      meta.screens!.sha256,
    );
    expect(payload.manifest.files.map((file) => file.path)).toEqual([
      'assets/app.css',
      'static/icon.svg',
      'web/dist/index.html',
    ]);
    for (const file of payload.manifest.files)
      expect(
        createHash('sha256').update(Buffer.from(payload.files[file.path]!, 'base64')).digest('hex'),
      ).toBe(file.sha256);
    // Cached bytes remain coherent even if a source tree changes behind the snapshot.
    await writeFile(join(current, 'assets/app.css'), 'changed after admission');
    expect((await app.inject(`/api/screens/${meta.screens!.sha256}`)).json()).toEqual(payload);
    expect((await app.inject(`/api/screens/${meta.waiting_screens!.sha256}`)).statusCode).toBe(200);
    waiting_root = null;
    expect((await app.inject('/api/meta')).json<ScreenMeta>().waiting_screens).toBeNull();
    expect((await app.inject(`/api/screens/${meta.waiting_screens!.sha256}`)).statusCode).toBe(404);
    expect((await app.inject('/api/screens/not-a-digest')).statusCode).toBe(404);
    waiting_root = join(folder, 'retired-waiting');
    const during_swap = (await app.inject('/api/meta')).json<ScreenMeta>();
    expect(during_swap.screens).toEqual(meta.screens);
    expect(during_swap.waiting_screens).toBeNull();
  } finally {
    await app.close();
    database.close();
    await rm(folder, { recursive: true, force: true });
  }
});

// Distinct risk: a link lets the screen payload read files outside its admitted tree.
test('screen snapshots refuse file and ancestor directory links', async () => {
  const root = await mkdtemp(join(tmpdir(), 'panels-screen-links-'));
  try {
    await build(root, 'real');
    await writeFile(join(root, 'outside'), 'not a screen');
    await symlink(join(root, 'outside'), join(root, 'assets/linked'));
    await expect(new ScreenSnapshots(root).meta()).rejects.toThrow('refuses a link');
    await rm(join(root, 'assets/linked'));
    await rm(join(root, 'web'), { recursive: true });
    await mkdir(join(root, 'outside-web/dist'), { recursive: true });
    await writeFile(join(root, 'outside-web/dist/index.html'), 'outside');
    await symlink(join(root, 'outside-web'), join(root, 'web'));
    await expect(new ScreenSnapshots(root).meta()).rejects.toThrow('regular directory');
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
