/**
 * Distinct risk: Chromium reuses a fixed-name file across a native screen swap,
 * or a loaded but unready document prevents recovery. A scripted peer isolates
 * this Electron scheme/renderer boundary; Fastify inject covers the real endpoint.
 * Reuse the existing desktop launcher, with no new host or product timing knobs.
 */
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import type { ScreenPayload } from '../../../server/screens/contracts.ts';
import { captureSuccess, expect, test } from '../support/fixtures.ts';
import { launchDesktopApp } from '../support/desktop.ts';

function snapshot(label: string, ready = true): { digest: string; payload: ScreenPayload } {
  const source: Record<string, string> = {
    'web/dist/index.html': `<!doctype html><html><head><meta charset="utf-8"><title>Screen ${label}</title></head><body style="margin:4rem;background:${label === 'A' ? '#e9f3fa' : '#eaf7ed'};font:24px system-ui"><h1>Screen ${label}</h1><p id="script">Script has not started</p><script src="/static/screen-marker.js"></script></body></html>`,
    'static/screen-marker.js': `document.getElementById('script').textContent='Script ${label}';${ready ? "document.documentElement.dataset.panelsReady='true';" : ''}`,
  };
  const manifest: ScreenPayload['manifest'] = {
    version: 1,
    files: Object.keys(source).sort().map((path) => ({
      path,
      sha256: createHash('sha256').update(source[path]!).digest('hex'),
    })),
  };
  return {
    digest: createHash('sha256').update(JSON.stringify(manifest)).digest('hex'),
    payload: {
      manifest,
      files: Object.fromEntries(Object.entries(source).map(([path, contents]) => [path, Buffer.from(contents).toString('base64')])),
    },
  };
}

test('server screens swap fixed-name assets and recover from an unready copy', async () => {
  const started = Date.now();
  const copies = { A: snapshot('A'), B: snapshot('B'), C: snapshot('C', false), D: snapshot('D') };
  let current: keyof typeof copies = 'A';
  let waiting: keyof typeof copies | null = 'B';
  const downloads: { digest: string; identity: string | undefined; origin: string | undefined }[] = [];
  const peer = createServer((request, response) => {
    response.setHeader('cache-control', 'no-store');
    response.setHeader('content-type', 'application/json');
    if (request.url === '/api/meta') {
      response.end(JSON.stringify({
        app_sha: current.toLowerCase().repeat(40),
        // The newer shell also accepts an older server's bridge floor.
        compatibility: { desktop_shell: { minimum_bridge_version: 1 } },
        screens: { sha256: copies[current].digest },
        waiting_screens: waiting === null ? null : { sha256: copies[waiting].digest },
      }));
      return;
    }
    const digest = request.url?.match(/^\/api\/screens\/([a-f0-9]{64})$/)?.[1];
    const copy = Object.values(copies).find((candidate) => candidate.digest === digest);
    if (copy && digest) {
      const identity = request.headers['x-panels-client'];
      const origin = request.headers.origin;
      downloads.push({ digest, identity: Array.isArray(identity) ? identity[0] : identity, origin: Array.isArray(origin) ? origin[0] : origin });
      response.end(JSON.stringify(copy.payload));
      return;
    }
    // Notifications and browser metadata are irrelevant to the scheme boundary.
    response.writeHead(404);
    response.end('{}');
  });
  await new Promise<void>((resolve) => peer.listen(0, '127.0.0.1', resolve));
  const { port } = peer.address() as { port: number };
  let desktop: Awaited<ReturnType<typeof launchDesktopApp>> | undefined;
  try {
    desktop = await launchDesktopApp({ localServerAddress: `http://127.0.0.1:${port}` });
    const page = desktop.window;
    await page.getByRole('radio', { name: 'On this Mac', exact: false }).click();
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    await page.getByRole('radio', { name: 'Yes, connect to it', exact: false }).click();
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    await page.fill('#address', `http://127.0.0.1:${port}`);
    await page.click('#connect');
    await expect(page.locator('h1')).toHaveText('Screen A', { timeout: 30_000 });
    await expect(page.locator('#script')).toHaveText('Script A');
    await expect.poll(() => downloads.some((entry) => entry.digest === copies.B.digest), { timeout: 30_000 }).toBe(true);
    await expect(page.locator('h1')).toHaveText('Screen A');
    for (const entry of downloads) {
      expect(JSON.parse(entry.identity ?? '{}')).toMatchObject({ kind: 'desktop', bridge_version: 2 });
      expect(entry.origin).toBeUndefined();
    }

    // The real screens route in the hash. A swap must still load a new document there.
    await page.evaluate(() => { location.hash = '#/review'; });
    await expect.poll(() => page.evaluate(() => location.href)).toBe('panels://app/#/review');

    current = 'B';
    waiting = null;
    await expect(page.locator('h1')).toHaveText('Screen B', { timeout: 35_000 });
    await expect(page.locator('#script')).toHaveText('Script B');
    await expect.poll(() => page.evaluate(() => document.documentElement.dataset.panelsReady)).toBe('true');
    await captureSuccess(page, 'screens-B.png');
    const response = await page.evaluate(async () => {
      const reply = await fetch('/static/screen-marker.js');
      return { cache: reply.headers.get('cache-control'), body: await reply.text() };
    });
    expect(response.cache).toBe('no-store');
    expect(response.body).toContain('Script B');

    current = 'C';
    await expect(page.locator('h1')).toHaveText('Screen C', { timeout: 35_000 });
    await expect(page.locator('#script')).toHaveText('Script C');
    await expect(page.locator('h1')).toHaveText('Panels could not open the update', { timeout: 30_000 });
    await expect(page).toHaveURL('panels://recovery/');
    await expect(page.locator('#script')).toHaveCount(0);
    const statePath = join(desktop.userData, 'screens/state.json');
    await expect.poll(async () => JSON.parse(await readFile(statePath, 'utf8')).rejected).toBe(copies.C.digest);
    await captureSuccess(page, 'screens-rollback.png');
    // Reload cannot expose old editable screens while the server still names C.
    await page.reload();
    await expect(page.locator('h1')).toHaveText('Panels could not open the update');
    await expect(page).toHaveURL('panels://recovery/');
    // The real recovery bridge opens a usable, verified replacement.
    current = 'D';
    await page.getByRole('button', { name: 'Try again', exact: true }).click();
    await expect(page.locator('h1')).toHaveText('Screen D', { timeout: 35_000 });
    await expect(page.locator('#script')).toHaveText('Script D');
    await expect.poll(() => page.evaluate(() => document.documentElement.dataset.panelsReady)).toBe('true');
    await captureSuccess(page, 'screens-retry.png');
  } finally {
    await desktop?.app.close();
    peer.closeAllConnections();
    await new Promise<void>((resolve, reject) => peer.close((error) => error ? reject(error) : resolve()));
    console.log(`Screen delivery Electron boundary elapsed: ${Date.now() - started} ms`);
  }
});
