/**
 * The desktop app against a real server.
 *
 * The app serves the screens from its own scheme and forwards their requests to the one
 * server it holds. Only a real Electron app and a live server together can prove that
 * forward: reads, writes, the live change stream, and a network error, not an HTTP error,
 * when the server is gone. The same run proves the shell's own rules: links that leave the
 * app go to the browser, the bridge refuses the screens, and the address and the window
 * place survive a restart. Only real restarts can prove the last session: the screens'
 * storage on the app's own scheme survives them, and a server change drops it.
 */
import { createServer } from 'node:net';
import type { Page } from '@playwright/test';
import { WAIT_MS, api, captureSuccess, cli, expect, proposeAsWorker, stopServer, test, withDatabase } from '../support/fixtures.ts';
import { launchDesktopApp, waitForScreens } from '../support/desktop.ts';

type ForwardedRequest = { path: string; method: string; identity: string | null; origin: string | null };

const WORKSPACE = 'section[data-screen="workspace"]';

/** An address where nothing listens. */
async function closedAddress(): Promise<string> {
  const listener = createServer();
  await new Promise<void>((done) => listener.listen(0, '127.0.0.1', done));
  const { port } = listener.address() as { port: number };
  await new Promise<void>((done) => listener.close(() => done()));
  return `http://127.0.0.1:${port}`;
}

function newTicket(title: string) {
  return { worker_type: 'coding', title, brief: 'Agreed brief.', manager: { kind: 'chief', id: 'chief' } };
}

async function pageUrl(page: Page): Promise<string> {
  return page.evaluate(() => location.href);
}

/** The session the screens saved for the next launch, as text, or '' before the first save. */
async function savedSession(page: Page): Promise<string> {
  return page.evaluate(
    () =>
      new Promise<string>((resolve, reject) => {
        const open = indexedDB.open('panels-saved-session', 1);
        open.onupgradeneeded = () => open.result.createObjectStore('saved');
        open.onerror = () => reject(open.error);
        open.onsuccess = () => {
          const read = open.result.transaction('saved').objectStore('saved').get('last');
          read.onsuccess = () => {
            open.result.close();
            resolve(read.result ? JSON.stringify(read.result) : '');
          };
          read.onerror = () => reject(read.error);
        };
      }),
  );
}

test('the screens reach the server through the app, and links leave it', async ({ server }) => {
  const ticketId = (
    await cli(server, ['ticket', 'create', '--input-json', '-'], {
      stdin: JSON.stringify({
        worker_type: 'coding',
        title: 'Desktop ticket before the change',
        brief: 'Agreed brief.',
        manager: { kind: 'chief', id: 'chief' },
      }),
      settleBrief: true,
    })
  ).id as string;

  // A server answers at the local address, so the app opens the screens at once.
  const desktop = await launchDesktopApp({ localServerAddress: server.base });
  try {
    const page = desktop.window;
    await waitForScreens(page, WORKSPACE);
    expect(await pageUrl(page)).toMatch(/^panels:\/\/app\//);
    const preferences = await desktop.app.evaluate(({ BrowserWindow }) => {
      const { sandbox, contextIsolation, nodeIntegration } = BrowserWindow.getAllWindows()[0]!.webContents.getLastWebPreferences()!;
      return { sandbox, contextIsolation, nodeIntegration };
    });
    expect(preferences).toEqual({ sandbox: true, contextIsolation: true, nodeIntegration: false });

    // Observe the native HTTP hop, rather than the renderer's scheme request.
    await desktop.app.evaluate(({ session }, base) => {
      const observed: { path: string; method: string; identity: string | null; origin: string | null }[] = [];
      (globalThis as { forwardedRequests?: typeof observed }).forwardedRequests = observed;
      session.defaultSession.webRequest.onBeforeSendHeaders({ urls: [`${base}/*`] }, (details: { url: string; method: string; requestHeaders: Record<string, string> }, callback: (response: { requestHeaders: Record<string, string> }) => void) => {
        const headers = Object.fromEntries(Object.entries(details.requestHeaders).map(([name, value]) => [name.toLowerCase(), String(value)]));
        observed.push({ path: new URL(details.url).pathname, method: details.method, identity: headers['x-panels-client'] ?? null, origin: headers.origin ?? null });
        callback({ requestHeaders: details.requestHeaders });
      });
    }, server.base);


    // Read: the ticket screen shows the server's ticket.
    await page.evaluate((id) => (location.hash = `#/workspace/${id}`), ticketId);
    const title = page.locator('.ticket-title');
    await expect(title).toHaveText('Desktop ticket before the change', { timeout: WAIT_MS });
    await captureSuccess(page, 'desktop-ticket.png');

    // Live: a change made elsewhere reaches the open screen without a reload.
    await api.patch(server, `/api/tickets/${ticketId}`, { title: 'Desktop ticket after the change' });
    await expect(title).toHaveText('Desktop ticket after the change', { timeout: WAIT_MS });

    // Write: a write from the screens' own relative path passes the server's rules.
    const write = await page.evaluate(async (ticket) => {
      const response = await fetch('/api/tickets', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(ticket),
      });
      return { status: response.status, body: await response.text() };
    }, newTicket('Ticket written by the desktop app'));
    expect(write.status, write.body).toBe(200);
    expect(JSON.stringify(await api.get(server, '/api/tickets?detail=summary'))).toContain('Ticket written by the desktop app');

    await test.step('the native metadata helper reads the bridge floor', async () => {
      const compatibility = await desktop.app.evaluate(async ({ app }, address) => {
        const load = process.getBuiltinModule('node:module').createRequire(`${app.getAppPath()}/build/main.js`);
        const { fetchDesktopCompatibility } = load('./server_meta.js') as {
          fetchDesktopCompatibility(address: string): Promise<{ minimum_bridge_version: number }>;
        };
        return fetchDesktopCompatibility(address);
      }, server.base);
      expect(compatibility).toEqual({ minimum_bridge_version: 1 });
    }, { timeout: WAIT_MS * 3 });

    await test.step('the native forward identifies API reads and files', async () => {
      // The native identity replaces a renderer-supplied value on reads and files too.
      await page.evaluate(async () => {
        await fetch('/api/meta', { headers: { 'X-Panels-Client': 'renderer override' } });
        await fetch('/files/missing-client-identity-proof');
      });
    }, { timeout: WAIT_MS * 3 });

    await test.step('the native forward identifies an open live stream', async () => {
      // EventSource is the production stream API. Closing it does not wait for an
      // endless forwarded response body to settle its cancellation promise.
      await page.evaluate(() => new Promise<void>((resolve, reject) => {
        const source = new EventSource('/api/changes');
        source.onopen = () => { source.close(); resolve(); };
        source.onerror = () => { source.close(); reject(new Error('The forwarded change stream did not open.')); };
      }));
    }, { timeout: WAIT_MS * 3 });

    await test.step('forwarded requests share identity and the server retains it', async () => {
      const forwarded = await desktop.app.evaluate(() => (globalThis as { forwardedRequests?: ForwardedRequest[] }).forwardedRequests ?? []);
      for (const path of ['/api/meta', '/api/tickets', '/files/missing-client-identity-proof', '/api/changes'])
        expect(forwarded.some((request) => request.path === path)).toBe(true);
      const identities = forwarded.map((request) => {
        expect(request.origin).toBeNull();
        expect(request.identity).not.toBeNull();
        return JSON.parse(request.identity!);
      });
      const nativeIdentity = identities[0];
      expect(nativeIdentity).toMatchObject({ kind: 'desktop', version: expect.any(String), bridge_version: 1, device_id: expect.any(String) });
      for (const identity of identities) expect(identity).toEqual(nativeIdentity);
      // The server received and retained these facts, independent of the observation above.
      expect(withDatabase(server, (db) => db.read((sql) => sql.get<{ identity_json: string }>(
        "SELECT identity_json FROM client_versions WHERE kind='desktop' AND device_id=?", [nativeIdentity.device_id],
      )))?.identity_json).toBe(JSON.stringify(nativeIdentity));
    }, { timeout: WAIT_MS * 3 });


    // A page from another site inside the app, such as one an artifact frame opened,
    // cannot write through the forward.
    await page.evaluate(
      (src) =>
        new Promise<void>((done) => {
          const frame = Object.assign(document.createElement('iframe'), { src, name: 'foreign' });
          frame.onload = () => done();
          document.body.append(frame);
        }),
      `${server.base}/api/meta`,
    );
    await page.frame({ name: 'foreign' })!.evaluate(
      (ticket) =>
        fetch('panels://app/api/tickets', {
          method: 'POST',
          mode: 'no-cors',
          body: new Blob([JSON.stringify(ticket)]),
        }).catch(() => undefined),
      newTicket('Ticket written by a foreign page'),
    );
    expect(JSON.stringify(await api.get(server, '/api/tickets?detail=summary'))).not.toContain('Ticket written by a foreign page');

    // Links that leave the app go to the browser, and the app stays on its screens.
    await page.evaluate(() => {
      window.open('https://example.com/new-window', '_blank');
      const link = Object.assign(document.createElement('a'), { href: 'https://example.org/plain-link' });
      document.body.append(link);
      link.click();
    });
    await expect.poll(desktop.openedOutside, { timeout: WAIT_MS }).toEqual([
      'https://example.com/new-window',
      'https://example.org/plain-link',
    ]);
    expect(await pageUrl(page)).toMatch(/^panels:\/\/app\//);

    // The bridge's address calls refuse the screens, and so any artifact inside them.
    const bridgeAnswer = await page.evaluate(() =>
      (window as unknown as { panels: { useServerAddress(address: string): Promise<unknown> } }).panels
        .useServerAddress('http://127.0.0.1:1')
        .then(() => 'answered', () => 'refused'),
    );
    expect(bridgeAnswer).toBe('refused');

    // With the server gone, a request fails as a network error, so live streams reconnect.
    await stopServer(server);
    const failure = await page.evaluate(() =>
      fetch('/api/meta').then((response) => `HTTP ${response.status}`, (error: Error) => error.name),
    );
    expect(failure).toBe('TypeError');
  } finally {
    await desktop.app.close();
  }
});

test('the app takes an address when no local server answers, and keeps it and its window place', async ({
  server,
}) => {
  const nothing = await closedAddress();
  const first = await launchDesktopApp({ localServerAddress: nothing });
  const place = { x: 120, y: 80, width: 1000, height: 700 };
  try {
    const page = first.window;
    await page.waitForSelector('#address-form', { timeout: WAIT_MS * 2 });
    expect(await pageUrl(page)).toBe('panels://setup/');

    await page.fill('#address', nothing);
    await page.click('#connect');
    await expect(page.locator('#problem')).toContainText('No Panels server answered', { timeout: WAIT_MS });
    await captureSuccess(page, 'desktop-address-page.png');

    await page.fill('#address', server.base);
    await page.click('#connect');
    await waitForScreens(page, WORKSPACE);
    await captureSuccess(page, 'desktop-workspace.png');

    await first.app.evaluate(({ BrowserWindow }, bounds) => BrowserWindow.getAllWindows()[0]!.setBounds(bounds), place);
  } finally {
    await first.app.close();
  }

  // The second start uses the stored address, and the window opens where it was left.
  const second = await launchDesktopApp({ localServerAddress: nothing, userData: first.userData });
  try {
    await waitForScreens(second.window, WORKSPACE);
    const bounds = await second.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0]!.getBounds());
    expect(bounds).toEqual(place);
  } finally {
    await second.app.close();
  }
});

/** What the test reads of an Electron notification the app tried to show. */
type RecordedNotification = { title: string; body: string; emit(event: 'click'): boolean };

// Only the built app against a live server can prove the native path: Electron's network
// stack holds the server's notification stream, the native side shows what the policy
// allowed, and a click opens the right screen. macOS itself is replaced by a recorder.
test('the native side shows a notification from the server, and a click opens its place', async ({ railServer }) => {
  const server = railServer;
  const ticketId = String((await api.post(server, '/api/tickets', newTicket('Ticket that waits on Khushal'))).id);
  await api.post(server, `/api/tickets/${ticketId}/accept/brief`, {
    next_ceiling: 'needs_what_changes',
    next_holder: { kind: 'owner', id: 'owner' },
  });
  const desktop = await launchDesktopApp({ localServerAddress: server.base });
  try {
    const page = desktop.window;
    await desktop.app.evaluate(({ Notification }) => {
      const shown: RecordedNotification[] = [];
      (globalThis as { shownNotifications?: RecordedNotification[] }).shownNotifications = shown;
      Notification.prototype.show = function (this: RecordedNotification) {
        shown.push(this);
      };
    });
    await waitForScreens(page, WORKSPACE);

    // In the app, Settings says that the Mac gets the notifications, not that Web Push is missing.
    // The native side opened its stream at start, long before this screen is up.
    await page.evaluate(() => (location.hash = '#/settings/notifications'));
    const device = page.locator('[data-settings-row="notifications:device"]');
    await expect(device).toContainText('This Mac gets the notifications', { timeout: WAIT_MS });
    await captureSuccess(page, 'desktop-notification-settings.png');

    await proposeAsWorker(server, ticketId, 'A proposal for Khushal.');
    await api.post(server, '/api/test/poll/notifications', {});
    const shown = () =>
      desktop.app.evaluate(() =>
        ((globalThis as { shownNotifications?: RecordedNotification[] }).shownNotifications ?? []).map(
          ({ title, body }) => ({ title, body }),
        ),
      );
    await expect.poll(shown, { timeout: WAIT_MS }).toEqual([
      { title: 'Ticket that waits on Khushal', body: expect.stringContaining('waits for your approval') },
    ]);

    await desktop.app.evaluate(() =>
      (globalThis as { shownNotifications?: RecordedNotification[] }).shownNotifications![0]!.emit('click'),
    );
    await expect.poll(() => pageUrl(page), { timeout: WAIT_MS }).toBe(`panels://app/#/workspace/${ticketId}`);
    await expect(page.locator('.ticket-title')).toHaveText('Ticket that waits on Khushal', { timeout: WAIT_MS });
  } finally {
    await desktop.app.close();
  }
});

test('the app reopens with the last session, marks it until the server replaces it, and drops it for another server', async ({
  server,
  serverFactory,
}) => {
  const ticketId = (
    await cli(server, ['ticket', 'create', '--input-json', '-'], {
      stdin: JSON.stringify(newTicket('Saved ticket before the change')),
      settleBrief: true,
    })
  ).id as string;
  const nothing = await closedAddress();
  const title = (page: Page) => page.locator('.ticket-title');
  const railRow = (page: Page) => page.locator(`[data-ticket-id="${ticketId}"]`).first();
  const savedMark = (page: Page) => page.locator('[data-navigation-health]:visible');

  const first = await launchDesktopApp({ localServerAddress: server.base });
  try {
    await waitForScreens(first.window, WORKSPACE);
    await first.window.evaluate((id) => (location.hash = `#/workspace/${id}`), ticketId);
    await expect(title(first.window)).toHaveText('Saved ticket before the change', { timeout: WAIT_MS });
    await expect.poll(() => savedSession(first.window), { timeout: WAIT_MS }).toContain('Saved ticket before the change');
  } finally {
    await first.app.close();
  }

  // With the server up, the app reopens on the same Ticket, the server's newer title
  // replaces the saved one, and the mark for saved data clears.
  await api.patch(server, `/api/tickets/${ticketId}`, { title: 'Saved ticket after the change' });
  const second = await launchDesktopApp({ localServerAddress: nothing, userData: first.userData });
  try {
    const page = second.window;
    await expect(title(page)).toHaveText('Saved ticket after the change', { timeout: WAIT_MS });
    expect(await pageUrl(page)).toBe(`panels://app/#/workspace/${ticketId}`);
    await expect(savedMark(page)).toHaveCount(0, { timeout: WAIT_MS });
    await expect.poll(() => savedSession(page), { timeout: WAIT_MS }).toContain('Saved ticket after the change');
  } finally {
    await second.app.close();
  }

  // With the server gone, the last session still shows at once, with its mark.
  const other = await serverFactory();
  await stopServer(server);
  const third = await launchDesktopApp({ localServerAddress: nothing, userData: first.userData });
  try {
    const page = third.window;
    await expect(title(page)).toHaveText('Saved ticket after the change', { timeout: WAIT_MS });
    await expect(railRow(page)).toBeVisible();
    await expect(savedMark(page)).toBeVisible({ timeout: WAIT_MS });
    // Once navigation gives up on the server, the line says so, and the status settles.
    await expect(page.locator('[data-navigation-health="reconnecting"]:visible')).toBeVisible({ timeout: WAIT_MS });
    await expect(page.locator('[data-shell-status]:visible [data-out]')).toHaveCount(0);
    await captureSuccess(page, 'desktop-last-session-offline.png');

    // Another server has other Tickets, so nothing of the last session shows under it.
    await third.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0]!.loadURL('panels://setup/'));
    await page.waitForSelector('#address-form', { timeout: WAIT_MS });
    await page.fill('#address', other.base);
    await page.click('#connect');
    await waitForScreens(page, WORKSPACE);
    expect(await pageUrl(page)).not.toContain(ticketId);
    await expect(railRow(page)).toHaveCount(0);
    expect(await savedSession(page)).not.toContain('Saved ticket');
  } finally {
    await third.app.close();
  }
});
