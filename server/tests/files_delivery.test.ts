import { mkdirSync, writeFileSync, symlinkSync, utimesSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { expect, test } from 'vitest';
import { fixture, fixture_path, create } from './ticket_fixture.ts';
import { createApp } from '../app.ts';
import { loadConfig } from '../core/config.ts';
import { routes } from '../files/routes.ts';
// Direct agent filesystem writes bypass database revisions. HTTP injection proves
// validators describe delivered bytes and HTML reads respect real symlink targets.
// The existing video browser test separately proves the browser's media stack.
test('agent file rewrites invalidate cached bytes and confine HTML navigation', async () => {
  const database = fixture(),
    ticket = create(database),
    db = fixture_path(database);
  const root = join(dirname(db), 'files', 'tickets', ticket.id),
    site = join(root, 'site');
  mkdirSync(site, { recursive: true });
  const document = join(site, 'index.html');
  writeFileSync(document, '<h1>One</h1>');
  writeFileSync(join(root, 'outside.html'), '<h1>Outside</h1>');
  symlinkSync(join(root, 'outside.html'), join(site, 'escape.html'));
  const config = loadConfig(undefined, { PLAN_DB_PATH: db, PLAN_TEST_MODE: '1' });
  const app = await createApp(config, { database, areas: [{ prefix: '', routes }] });
  try {
    const url = `/files/tickets/${ticket.id}/site/index.html`;
    const first = await app.inject({ url });
    expect(first.statusCode).toBe(200);
    expect(first.headers['content-disposition']).toBe('attachment; filename="index.html"');
    expect(first.headers['cache-control']).toBe('private, no-cache');
    const cached = await app.inject({
      url,
      headers: { 'if-none-match': String(first.headers.etag) },
    });
    expect(cached.statusCode).toBe(304);
    expect(cached.headers['content-disposition']).toBeUndefined();
    expect(cached.headers['x-content-type-options']).toBeUndefined();
    utimesSync(document, 1000, 1000);
    writeFileSync(document, '<h1>Two</h1>');
    utimesSync(document, 1000, 1000);
    const changed = await app.inject({
      url,
      headers: { 'if-none-match': String(first.headers.etag) },
    });
    expect(changed.statusCode).toBe(200);
    expect(changed.body).toBe('<h1>Two</h1>');
    expect(changed.headers.etag).not.toBe(first.headers.etag);
    const escaped = await app.inject({
      url: `/files/tickets/${ticket.id}/site/escape.html`,
      headers: { 'x-panels-artifact-root': url },
    });
    expect(escaped.statusCode).toBe(404);
    const normal = await app.inject({ url: `/files/tickets/${ticket.id}/site/escape.html` });
    expect(normal.statusCode).toBe(200);
    const unsafe = await app.inject({ url: `/files/tickets/${ticket.id}/site%252findex.html` });
    expect(unsafe.statusCode).toBe(404);
  } finally {
    await app.close();
  }
});
