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
import { cpSync, mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import type { Page } from '@playwright/test';
import { REPO_ROOT, WAIT_MS, api, captureSuccess, cli, expect, proposeAsWorker, stopServer, test, withDatabase } from '../support/fixtures.ts';
import { launchDesktopApp, waitForScreens } from '../support/desktop.ts';
import { createTicketConversation, post } from '../support/conversation.ts';

type NativeRequest = { id: number; url: string; ended: boolean };
type ForwardedRequest = { path: string; method: string; identity: string | null; origin: string | null };

const WORKSPACE = 'section[data-screen="workspace"]';

// The screen snapshot checks real files. Keep builds under TMPDIR, then give each
// server the same private, immutable copy of those files for this test run.
let desktopScreensRoot: string;
test.beforeAll(() => {
  desktopScreensRoot = mkdtempSync(join(tmpdir(), 'desktop-test-screens-'));
  for (const path of ['web/dist', 'assets', 'static'])
    cpSync(realpathSync(join(REPO_ROOT, path)), join(desktopScreensRoot, path), { recursive: true });
});
test.afterAll(() => { rmSync(desktopScreensRoot, { recursive: true, force: true }); });
test.use({
  server: async ({ serverFactory }, use) => use(await serverFactory({ appRoot: desktopScreensRoot })),
  railServer: async ({ serverFactory }, use) => use(await serverFactory({ appRoot: desktopScreensRoot, host: 'test_host', databaseName: 'rail-proof.db' })),
});


/** An address where nothing listens. */
async function closedAddress(): Promise<string> {
  const listener = createServer();
  await new Promise<void>((done) => listener.listen(0, '127.0.0.1', done));
  const { port } = listener.address() as { port: number };
  await new Promise<void>((done) => listener.close(() => done()));
  return `http://127.0.0.1:${port}`;
}

/** First launch always requires the person's explicit choice. */
async function connectLocal(page: Page, address: string): Promise<void> {
  await page.getByRole('radio', { name: 'On this Mac', exact: false }).click();
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    await page.getByRole('radio', { name: 'Yes, connect to it', exact: false }).click();
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await page.fill('#address', address);
  await page.click('#connect');
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

test('the screens reach the server through the app, and links leave it', async ({ serverFactory, newContext }, testInfo) => {
  // Screen verification requires real files, while builds remain under TMPDIR.
  const appRoot = mkdtempSync(join(tmpdir(), 'desktop-forward-screens-'));
  for (const path of ['web/dist', 'assets', 'static'])
    cpSync(realpathSync(join(REPO_ROOT, path)), join(appRoot, path), { recursive: true });
  const server = await serverFactory({ appRoot });
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

  // Even when a local server answers, the app asks the person first.
  const desktop = await launchDesktopApp({ localServerAddress: server.base });
  try {
    const page = desktop.window;
    await connectLocal(page, server.base);
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
      session.defaultSession.webRequest.onBeforeSendHeaders({ urls: [`${base}/*`] }, (details: { url: string; method: string; resourceType: string; requestHeaders: Record<string, string> }, callback: (response: { requestHeaders: Record<string, string> }) => void) => {
        // The foreign iframe below navigates to HTTP directly, without the native forward.
        if (details.resourceType === 'subFrame') { callback({ requestHeaders: details.requestHeaders }); return; }
        const headers = Object.fromEntries(Object.entries(details.requestHeaders).map(([name, value]) => [name.toLowerCase(), String(value)]));
        observed.push({ path: new URL(details.url).pathname, method: details.method, identity: headers['x-panels-client'] ?? null, origin: headers.origin ?? null });
        callback({ requestHeaders: details.requestHeaders });
      });
    }, server.base);


    await desktop.app.evaluate(({ session }, base) => {
      const requests: { id: number; url: string; ended: boolean }[] = [];
      (globalThis as { nativeRequests?: typeof requests }).nativeRequests = requests;
      const filter = { urls: [`${base}/*`] };
      session.defaultSession.webRequest.onSendHeaders(filter, (details: { id: number; url: string }) => {
        requests.push({ id: details.id, url: details.url, ended: false });
      });
      const ended = (details: { id: number }) => {
        for (const request of requests) if (request.id === details.id) request.ended = true;
      };
      session.defaultSession.webRequest.onCompleted(filter, ended);
      session.defaultSession.webRequest.onErrorOccurred(filter, ended);
    }, server.base);
    const nativeRequests = () => desktop.app.evaluate(() =>
      (globalThis as { nativeRequests?: NativeRequest[] }).nativeRequests ?? []);

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
      expect(compatibility).toEqual({ minimum_bridge_version: 2 });
    }, { timeout: WAIT_MS * 3 });

    await test.step('the native forward identifies API reads and files', async () => {
      // The native identity replaces a renderer-supplied value on reads and files too.
      await page.evaluate(async () => {
        await (await fetch('/api/meta', { headers: { 'X-Panels-Client': 'renderer override' } })).text();
        await (await fetch('/files/missing-client-identity-proof')).text();
      });
    }, { timeout: WAIT_MS * 3 });

    await test.step('a foreign frame cannot write through the forward', async () => {
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
    }, { timeout: WAIT_MS * 3 });

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

    // A same-origin artifact reaches the parent's exposed bridge. The IPC sender is
    // the top app frame, so only the separate setup origin prevents a server change.
    const artifactDirectory = join(server.root, 'files', 'tickets', ticketId, 'artifacts');
    mkdirSync(artifactDirectory, { recursive: true });
    writeFileSync(join(artifactDirectory, 'bridge.html'), '<h1>Artifact</h1>');
    await page.evaluate((src) => new Promise<void>((resolve) => {
      const frame = document.createElement('iframe');
      frame.name = 'artifact';
      frame.src = src;
      frame.onload = () => resolve();
      document.body.append(frame);
    }), `/files/tickets/${ticketId}/artifacts/bridge.html`);
    const bridgeAnswer = await page.frame({ name: 'artifact' })!.evaluate((address) =>
      (parent as unknown as { panels: { useServerAddress(address: string): Promise<unknown> } }).panels
        .useServerAddress(address).then(() => 'answered', (error: Error) => error.message), server.base);
    expect(bridgeAnswer).toContain('Only the address page can change the server address.');
    // The explicit authorization error proves refusal before validation, storage and navigation.
    expect(await page.evaluate(() => (window as unknown as { panels: { storedServerAddress(): Promise<string> } }).panels.storedServerAddress())).toBe(server.base);
    expect(await pageUrl(page)).toMatch(/^panels:\/\/app\//);

    await test.step('closed page streams release native upstream requests', async () => {
      const started = performance.now();
      for (let index = 0; index < 8; index++) {
        const marker = `desktop-close-${index}`;
        await page.evaluate(marker => new Promise<void>((resolve, reject) => {
          const source = new EventSource(`/api/changes?proof=${marker}`);
          (window as unknown as { proofStream: EventSource }).proofStream = source;
          source.onopen = () => resolve();
          source.onerror = () => reject(new Error('The forwarded change stream did not open.'));
        }), marker);
        await expect.poll(async () => (await nativeRequests()).some(request => request.url.includes(marker) && !request.ended), { timeout: WAIT_MS }).toBe(true);
        await page.evaluate(() => (window as unknown as { proofStream: EventSource }).proofStream.close());
        await expect.poll(async () => {
          const requests = (await nativeRequests()).filter(request => request.url.includes(marker));
          return requests.length > 0 && requests.every(request => request.ended);
        }, { timeout: WAIT_MS }).toBe(true);
      }
      console.log(`DESKTOP_STREAM_CLOSURE_MS ${Math.round(performance.now() - started)}`);
    });

    await test.step('eight Ticket selections and rapid replacements retain usable streams', async () => {
      const started = performance.now();
      const tickets: { id: string; title: string; brief: string }[] = [];
      for (let index = 1; index <= 8; index++) {
        const ticket = { ...newTicket(`Desktop navigation ${index}`), brief: `Desktop navigation body ${index}.` };
        const created = await api.post(server, '/api/tickets', ticket);
        tickets.push({ id: String(created.id), ...ticket });
      }
      const checkTicket = async (target: Page, ticket: typeof tickets[number]) => {
        await target.evaluate(id => { location.hash = `#/workspace/${id}`; }, ticket.id);
        await expect(target.locator('.ticket-title')).toHaveText(ticket.title, { timeout: WAIT_MS });
        await expect(target.getByText(ticket.brief, { exact: true })).toBeVisible({ timeout: WAIT_MS });
      };
      for (const ticket of tickets) await checkTicket(page, ticket);
      await captureSuccess(page, 'desktop-eight-tickets.png');

      const selectionStream = (request: NativeRequest, id: string) => {
        const url = new URL(request.url);
        return url.pathname === '/api/navigation/view/events' &&
          JSON.parse(url.searchParams.get('view') ?? '{}').selected?.id === id;
      };
      // Replace each scope as soon as its upstream request starts, before its body settles.
      for (const ticket of tickets) {
        await page.evaluate(id => { location.hash = `#/workspace/${id}`; }, ticket.id);
        await expect.poll(async () => (await nativeRequests()).some(request =>
          selectionStream(request, ticket.id) && !request.ended), { timeout: WAIT_MS }).toBe(true);
      }
      const current = tickets.at(-1)!;
      await expect(page.locator('.ticket-title')).toHaveText(current.title, { timeout: WAIT_MS });
      const retired = tickets.slice(0, -1).map(ticket => ticket.id);
      await expect.poll(async () => (await nativeRequests()).filter(request =>
        retired.some(id => selectionStream(request, id)) && !request.ended), { timeout: WAIT_MS }).toEqual([]);
      await expect.poll(async () => (await nativeRequests()).filter(request =>
        new URL(request.url).pathname === '/api/navigation/view/events' && !request.ended)
        .map(request => JSON.parse(new URL(request.url).searchParams.get('view')!).selected?.id), { timeout: WAIT_MS }).toEqual([current.id]);
      const read = await page.evaluate(async () => (await fetch('/api/meta')).status);
      expect(read).toBe(200);
      await api.patch(server, `/api/tickets/${current.id}`, { title: 'Desktop navigation live update' });
      await expect(page.locator('.ticket-title')).toHaveText('Desktop navigation live update', { timeout: WAIT_MS });
      current.title = 'Desktop navigation live update';

      // The diagnostic control shares this exact private server and its eight Tickets.
      const context = await newContext();
      const browserPage = await context.newPage();
      await browserPage.goto(server.base);
      await waitForScreens(browserPage, WORKSPACE);
      for (const ticket of tickets) await checkTicket(browserPage, ticket);
      await captureSuccess(browserPage, 'browser-eight-tickets.png');
      await browserPage.close();
      const trace = JSON.stringify(await nativeRequests(), null, 2);
      await testInfo.attach('native-stream-requests', { body: trace, contentType: 'application/json' });
      if (process.env.PANELS_UI_PROOF_DIR)
        writeFileSync(join(process.env.PANELS_UI_PROOF_DIR, 'native-stream-requests.json'), trace);
      console.log(`DESKTOP_NAVIGATION_AND_CONTROL_MS ${Math.round(performance.now() - started)}`);
    });

    await test.step('compressed responses keep decoded bytes and remove obsolete headers', async () => {
      const path = '/api/tickets?detail=full';
      const compressed = await fetch(`${server.base}${path}`, { headers: { 'accept-encoding': 'gzip' } });
      expect(compressed.headers.get('content-encoding')).toBe('gzip');
      const expected = await compressed.text();
      const decoded = await page.evaluate(async path => {
        const response = await fetch(path, { headers: { 'accept-encoding': 'gzip' } });
        return { text: await response.text(), encoding: response.headers.get('content-encoding'), length: response.headers.get('content-length') };
      }, path);
      expect(decoded).toEqual({ text: expected, encoding: null, length: null });
    });

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
      expect(nativeIdentity).toMatchObject({ kind: 'desktop', version: expect.any(String), bridge_version: 3, device_id: expect.any(String) });
      for (const identity of identities) expect(identity).toEqual(nativeIdentity);
      // The bridge hands the page the same address and identity, for its own WebSocket.
      expect(await page.evaluate(() => (window as unknown as { panels: { liveConnection(): Promise<unknown> } }).panels.liveConnection()))
        .toEqual({ address: server.base, identity: nativeIdentity });
      // The server received and retained these facts, independent of the observation above.
      expect(withDatabase(server, (db) => db.read((sql) => sql.get<{ identity_json: string }>(
        "SELECT identity_json FROM client_versions WHERE kind='desktop' AND device_id=?", [nativeIdentity.device_id],
      )))?.identity_json).toBe(JSON.stringify(nativeIdentity));
    }, { timeout: WAIT_MS * 3 });

    await test.step('a stopped server produces a network failure', async () => {
      // With the server gone, a request fails as a network error, so live streams reconnect.
      await stopServer(server);
      const failure = await page.evaluate(() =>
        fetch('/api/meta').then((response) => `HTTP ${response.status}`, (error: Error) => error.name),
      );
      expect(failure).toBe('TypeError');
    }, { timeout: WAIT_MS * 3 });
  } finally {
    await test.step('close the desktop app', () => desktop.app.close(), { timeout: WAIT_MS * 3 });
    rmSync(appRoot, { recursive: true, force: true });
  }
});

test('a link in a message opens in the browser, and a page of the connected server opens in the app', async ({ railServer }) => {
  const server = railServer;
  const conversationId = 'desktop-link-conversation';
  const ticketId = await createTicketConversation(server, { conversationId, title: 'Ticket with links' });
  const otherId = String((await api.post(server, '/api/tickets', newTicket('Ticket the link opens'))).id);
  await post(server, `/api/test/signals/${conversationId}/message`, {
    kind: 'ticket',
    id: ticketId,
    label: 'Proof Worker',
    text: `A test copy runs at https://panels.example.org:8473, and [the ticket](${server.base}/#/workspace/${otherId}) is here.`,
  });
  const desktop = await launchDesktopApp({ localServerAddress: server.base });
  try {
    const page = desktop.window;
    await connectLocal(page, server.base);
    await waitForScreens(page, WORKSPACE);
    await page.evaluate((id) => (location.hash = `#/workspace/${id}`), ticketId);
    await page.locator('[data-conversation-expand]').dispatchEvent('click');
    const links = page.locator('.markdown-block a');
    await expect(links).toHaveCount(2, { timeout: WAIT_MS });
    await desktop.app.evaluate(({ dialog }) => {
      const shown: string[] = [];
      (globalThis as { shownDialogs?: string[] }).shownDialogs = shown;
      dialog.showMessageBox = (async (_window: unknown, options: { detail?: string }) => {
        shown.push(options.detail ?? '');
        return { response: 0, checkboxChecked: false };
      }) as unknown as typeof dialog.showMessageBox;
    });
    // A press as the person makes it: the mouse at the link, not a call on the element.
    // Playwright's own click waits for a navigation that the shell cancels, so it is not used.
    const press = async (index: number) => {
      const place = () => page.evaluate((i) => {
        const link = document.querySelectorAll('.markdown-block a')[i]!;
        link.scrollIntoView({ block: 'center' });
        const { x, y, height } = link.getBoundingClientRect();
        return { x, y, height, hit: document.elementFromPoint(x + 3, y + height / 2) === link };
      }, index);
      // The conversation is still moving into place when its first link appears.
      let box = await place();
      for (let settled = 0; settled < 3; ) {
        await page.waitForTimeout(100);
        const next = await place();
        settled = next.x === box.x && next.y === box.y && next.hit ? settled + 1 : 0;
        box = next;
      }
      await page.mouse.click(box.x + 3, box.y + box.height / 2);
    };

    await press(0);
    await expect.poll(desktop.openedOutside, { timeout: WAIT_MS }).toEqual(['https://panels.example.org:8473/']);

    // The connected server's own page opens in the app, not in the browser.
    await press(1);
    await expect.poll(() => pageUrl(page), { timeout: WAIT_MS }).toBe(`panels://app/#/workspace/${otherId}`);
    await expect(page.locator('.ticket-title')).toHaveText('Ticket the link opens', { timeout: WAIT_MS });
    expect(await desktop.openedOutside()).toEqual(['https://panels.example.org:8473/']);

    // A link the app cannot open says so.
    await page.evaluate(() => window.open('ftp://files.example.org/report', '_blank'));
    await expect
      .poll(() => desktop.app.evaluate(() => (globalThis as { shownDialogs?: string[] }).shownDialogs ?? []), { timeout: WAIT_MS })
      .toEqual([expect.stringContaining('ftp://files.example.org/report')]);
    expect(await desktop.openedOutside()).toEqual(['https://panels.example.org:8473/']);
  } finally {
    await desktop.app.close();
  }
});

test('setup works offline, and the app keeps an address and window place', async ({
  server,
}) => {
  const nothing = await closedAddress();
  const first = await launchDesktopApp({ localServerAddress: nothing });
  const place = { x: 120, y: 80, width: 1000, height: 700 };
  try {
    const page = first.window;
    await expect(page.getByRole('heading', { name: 'Where does Panels run?' })).toBeVisible({ timeout: WAIT_MS });
    await page.getByRole('radio', { name: 'On this Mac', exact: false }).click();
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    await page.getByRole('radio', { name: 'Yes, connect to it', exact: false }).click();
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
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
    await connectLocal(page, server.base);
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
    await connectLocal(first.window, server.base);
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
  const other = await serverFactory({ appRoot: desktopScreensRoot });
  await stopServer(server);
  const third = await launchDesktopApp({ localServerAddress: nothing, userData: first.userData });
  try {
    const page = third.window;
    await expect(title(page)).toHaveText('Saved ticket after the change', { timeout: WAIT_MS });
    await expect(railRow(page)).toBeVisible();
    await expect(page.locator('[data-shell-status]:visible')).toContainText("Can't reach Panels", { timeout: WAIT_MS });
    // The desktop warning replaces the navigation detail in this state.
    await expect(page.locator('[data-shell-status]:visible [data-out]')).toHaveCount(0);
    await captureSuccess(page, 'desktop-last-session-offline.png');

    // Another server has other Tickets, so nothing of the last session shows under it.
    await third.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0]!.loadURL('panels://setup/'));
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(title(page)).toHaveText('Saved ticket after the change', { timeout: WAIT_MS });
    await page.getByRole('button', { name: 'Server connection details' }).click();
    await expect(page.getByRole('dialog')).toContainText(server.base);
    await captureSuccess(page, 'desktop-cannot-reach-server.png');
    await page.getByRole('link', { name: 'Change server…', exact: true }).click();
    await expect.poll(() => pageUrl(page)).toBe('panels://setup/');
    await page.getByRole('radio', { name: 'On this Mac', exact: false }).click();
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    await page.getByRole('radio', { name: 'Yes, connect to it', exact: false }).click();
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
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
