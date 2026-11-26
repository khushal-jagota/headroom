/**
 * Every explicit conversation open lands at the bottom on its first visible frame and stays.
 *
 * The risk is an open that shows the thread before it is placed: a frame above the newest
 * line, a slide down to it, a body that grows after the reveal, a scope fetch that changes
 * the thread under the reader, or "Jump to latest" flashing on. Only real layout in a browser,
 * fed by a live server's tail and pages over a slow network, shows it. Each case reads the
 * thread on every animation frame from the first frame the thread is visible, for 1.5 s, with
 * 80 ms added to every conversation request, and holds five rules:
 *
 * 1. The thread is at its bottom (within 2 px) on the first frame and every frame after.
 * 2. The bottom visible item on the first frame is the bottom visible item on the last.
 * 3. The thread does not grow by more than 2 px and never moves down by more than 2 px.
 * 4. An idle open asks for no history page after it is revealed.
 * 5. "Jump to latest" never shows.
 *
 * One scripted host per test. The two lens tests share their cases: a Ticket opened from the
 * dock, by direct link and as a user-owned Ticket that opens itself, the Chief, a Manager, a
 * 390 px phone, the desktop window, and Tickets whose newest reply is a chart, an image with no
 * dimensions, or 70 KB of Markdown, and in Full a running turn with live output. The third
 * test covers the ways back: a tab return at the bottom and scrolled up, a dock reopen, a lens
 * switch, and a return whose saved line cannot be restored. Each case attaches its frames.
 */
import { writeFileSync } from 'node:fs';
import { deflateSync } from 'node:zlib';
import type { BrowserContext, BrowserContextOptions, Page, TestInfo } from '@playwright/test';
import { api, bindConversation, cli, expect, test, WAIT_MS, type ServerHandle } from './support/fixtures.ts';
import { post } from './support/conversation.ts';

type Lens = 'full' | 'focus';

type ProbeOptions = {
  /** How long to read after the first visible frame. */
  windowMs: number;
  /** How long to wait for the first visible frame. */
  waitMs: number;
  /** An item whose distance from the thread's top each frame records. */
  trackKey: string | null;
  /** A selector that must match before a visible frame counts as the first. */
  readyWhen: string | null;
};

type Frame = {
  t: number;
  visible: boolean;
  scrollTop: number | null;
  scrollHeight: number | null;
  clientHeight: number | null;
  distance: number | null;
  bottomKey: string | null;
  jump: boolean;
  entry: string | null;
  tracked: number | null;
};

type ProbeState = {
  frames: Frame[];
  done: boolean;
  failure: string | null;
  /** Page fetches after the probe started, and after the first visible frame. */
  pageFetchesAfterStart: string[];
  pageFetchesAfterReveal: string[];
};

type PageSetup = { autostart: ProbeOptions | null; visibilityControl: boolean };

declare global {
  interface Window {
    __startOpenProbe(options: ProbeOptions): void;
    __openProbe?: ProbeState;
    __pageFetches?: { at: number; url: string }[];
    __setDocumentHidden?: (hidden: boolean) => void;
  }
}

const PROBE: ProbeOptions = { windowMs: 1500, waitMs: 15_000, trackKey: null, readyWhen: null };
const LATENCY_MS = 80;
const LENS_STORAGE_KEY = 'panels.conversation.lens';
/** No image extension, so the Markdown image stays unclaimed: a bare `<img>` with no size. */
const IMAGE_URL = 'http://images.example.test/render?frame=1';

/**
 * The page's own instruments, installed before the app's scripts. It records every history
 * page fetch on the page's clock and defines the frame probe. A test can also take over
 * `document.visibilityState`, which is how it hides and shows the tab.
 */
function installInstruments(setup: PageSetup): void {
  const fetches: { at: number; url: string }[] = [];
  window.__pageFetches = fetches;
  const original = window.fetch;
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    if (url.includes('/view/pages')) fetches.push({ at: performance.now(), url });
    return original.call(window, input, init);
  };
  if (setup.visibilityControl) {
    let hidden = false;
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => (hidden ? 'hidden' : 'visible') });
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
    window.__setDocumentHidden = (value: boolean) => {
      hidden = value;
      document.dispatchEvent(new Event('visibilitychange'));
    };
  }
  window.__startOpenProbe = (options: ProbeOptions) => {
    const state: ProbeState = { frames: [], done: false, failure: null, pageFetchesAfterStart: [], pageFetchesAfterReveal: [] };
    window.__openProbe = state;
    const started = performance.now();
    let first: number | null = null;
    const visibleThread = (): HTMLElement | null => {
      for (const thread of Array.from(document.querySelectorAll<HTMLElement>('[data-conversation-thread]'))) {
        if (thread.clientHeight > 0 && getComputedStyle(thread).visibility === 'visible' && thread.querySelector('[data-conversation-view-key]'))
          return thread;
      }
      return null;
    };
    const finish = () => {
      state.pageFetchesAfterStart = fetches.filter((entry) => entry.at >= started).map((entry) => entry.url);
      state.pageFetchesAfterReveal = first === null ? [] : fetches.filter((entry) => entry.at >= first!).map((entry) => entry.url);
      state.done = true;
    };
    const read = () => {
      const now = performance.now();
      const thread = visibleThread();
      if (first === null) {
        if (thread === null || (options.readyWhen !== null && document.querySelector(options.readyWhen) === null)) {
          if (now - started > options.waitMs) {
            state.failure = 'The thread never became visible';
            finish();
          } else requestAnimationFrame(read);
          return;
        }
        first = now;
      }
      const jump = document.querySelector('[data-conversation-jump]') !== null;
      const entry = document.querySelector('[data-conversation-entry]')?.getAttribute('data-conversation-entry') ?? null;
      if (thread === null) {
        state.frames.push({ t: now - first, visible: false, scrollTop: null, scrollHeight: null, clientHeight: null, distance: null, bottomKey: null, jump, entry, tracked: null });
      } else {
        const box = thread.getBoundingClientRect();
        let bottomKey: string | null = null;
        for (const item of Array.from(thread.querySelectorAll<HTMLElement>('[data-conversation-view-key]'))) {
          const rect = item.getBoundingClientRect();
          if (rect.height > 0 && rect.top < box.bottom - 1 && rect.bottom > box.top + 1) bottomKey = item.dataset.conversationViewKey ?? null;
        }
        const tracked = options.trackKey === null
          ? null
          : thread.querySelector(`[data-conversation-view-key="${CSS.escape(options.trackKey)}"]`)?.getBoundingClientRect().top ?? null;
        state.frames.push({
          t: now - first,
          visible: true,
          scrollTop: thread.scrollTop,
          scrollHeight: thread.scrollHeight,
          clientHeight: thread.clientHeight,
          distance: thread.scrollHeight - thread.clientHeight - thread.scrollTop,
          bottomKey,
          jump,
          entry,
          tracked: tracked === null ? null : tracked - box.top,
        });
      }
      if (now - first >= options.windowMs) finish();
      else requestAnimationFrame(read);
    };
    requestAnimationFrame(read);
  };
  if (setup.autostart) window.__startOpenProbe(setup.autostart);
}

/** A context with the lens chosen, conversation requests slowed, and the test image served. */
async function measuredContext(
  newContext: (options?: BrowserContextOptions) => Promise<BrowserContext>,
  lens: Lens,
  options: BrowserContextOptions & { desktopWindow?: boolean } = {},
): Promise<BrowserContext> {
  const { desktopWindow = false, ...contextOptions } = options;
  const context = await newContext({ viewport: { width: 1280, height: 800 }, ...contextOptions });
  await context.addInitScript(([key, value]) => window.localStorage.setItem(key, value), [LENS_STORAGE_KEY, lens] as const);
  if (desktopWindow) {
    // What the desktop app's preload does on macOS (desktop/src/preload.ts).
    await context.addInitScript(() => {
      const mark = () => document.documentElement.toggleAttribute('data-desktop-window', true);
      if (document.documentElement) mark();
      else document.addEventListener('readystatechange', mark, { once: true });
    });
  }
  await context.route('**/api/conversation/**', async (route) => {
    await new Promise((done) => setTimeout(done, LATENCY_MS));
    await route.continue();
  });
  await context.route('http://images.example.test/**', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: TEST_IMAGE }));
  return context;
}

/** Open a screen with the instruments installed and wait for its first reconciliation. */
async function openScreen(context: BrowserContext, server: ServerHandle, route: string, readySelector: string, setup: Partial<PageSetup> = {}): Promise<Page> {
  const page = await context.newPage();
  await page.addInitScript(installInstruments, { autostart: setup.autostart ?? null, visibilityControl: setup.visibilityControl ?? false });
  await page.goto(`${server.base}/${route}`);
  await page.waitForSelector(readySelector, { timeout: WAIT_MS });
  await page.waitForFunction(
    () => ((window as unknown as { __plannerDebug?: { sseReconciliations: number } }).__plannerDebug?.sseReconciliations ?? 0) >= 1,
    undefined,
    { timeout: WAIT_MS },
  );
  return page;
}

/** Wait until the dock has drawn and settled, so the open starts from rest. */
async function dockReady(page: Page): Promise<void> {
  await page.locator('[data-conversation-rest-bar]').waitFor({ timeout: WAIT_MS });
  await page.waitForTimeout(400);
}

/** Start the probe, do the action, and read the probe when its window ends. */
async function measure(page: Page, action: (() => Promise<void>) | null, options: Partial<ProbeOptions> = {}): Promise<ProbeState> {
  if (action !== null) {
    await page.evaluate((probe) => window.__startOpenProbe(probe), { ...PROBE, ...options });
    await action();
  }
  await page.waitForFunction(() => window.__openProbe?.done === true, undefined, { timeout: PROBE.waitMs + 5_000 });
  return await page.evaluate(() => window.__openProbe!);
}

/** Start the probe and show the tab in one task, so the first frame follows the return. */
async function returnToTab(page: Page, options: Partial<ProbeOptions> = {}): Promise<ProbeState> {
  await page.evaluate((probe) => {
    window.__startOpenProbe(probe);
    window.__setDocumentHidden!(false);
  }, { ...PROBE, ...options });
  return await measure(page, null);
}

/** Frames are written into the test's output folder, which the browser job keeps. */
async function attachFrames(testInfo: TestInfo, name: string, state: ProbeState): Promise<void> {
  const path = testInfo.outputPath(`${name}.frames.json`);
  writeFileSync(path, JSON.stringify(state, null, 1));
  await testInfo.attach(`${name}.frames.json`, { path, contentType: 'application/json' });
}

/** A screenshot only for a case that has just failed, before its page closes. */
async function captureIfFailed(page: Page, testInfo: TestInfo, errorsBefore: number, name: string): Promise<void> {
  if (testInfo.errors.length === errorsBefore) return;
  await testInfo.attach(`${name}.png`, { body: await page.screenshot({ timeout: 5_000 }).catch(() => Buffer.alloc(0)), contentType: 'image/png' });
}

/** The five rules, as soft checks so every case of a test reports. */
async function expectOpenAtBottom(testInfo: TestInfo, name: string, state: ProbeState, options: { idle?: boolean } = {}): Promise<void> {
  await attachFrames(testInfo, name, state);
  expect.soft(state.failure, `${name}: ${state.failure}`).toBeNull();
  const frames = state.frames;
  expect.soft(frames.length, `${name}: frames read`).toBeGreaterThan(10);
  if (frames.length === 0) return;
  const first = frames[0];
  const last = frames.at(-1)!;
  const shown = frames.filter((frame) => frame.visible);
  // A thread shorter than its view is at its bottom by definition, which would prove nothing.
  expect.soft((last.scrollHeight ?? 0) - (last.clientHeight ?? 0), `${name}: the thread is long enough to scroll`).toBeGreaterThan(300);
  const offBottom = frames.filter((frame) => !frame.visible || frame.distance! > 2).map((frame) => `${frame.t.toFixed(0)} ms: ${frame.visible ? `${frame.distance} px above the bottom` : 'not visible'}`);
  expect.soft(offBottom, `${name}: rule 1, every frame at the bottom`).toEqual([]);
  expect.soft(first.bottomKey, `${name}: rule 2, the first frame's bottom item is the last frame's`).toBe(last.bottomKey);
  const growth = Math.max(...shown.map((frame) => frame.scrollHeight!)) - (first.scrollHeight ?? 0);
  expect.soft(growth, `${name}: rule 3, the thread grew after the reveal`).toBeLessThanOrEqual(2);
  const downward = shown.slice(1).flatMap((frame, index) => {
    const moved = frame.scrollTop! - shown[index].scrollTop!;
    return moved > 2 ? [`${frame.t.toFixed(0)} ms: down ${moved} px`] : [];
  });
  expect.soft(downward, `${name}: rule 3, no downward jump`).toEqual([]);
  if (options.idle !== false) expect.soft(state.pageFetchesAfterReveal, `${name}: rule 4, no history page after the reveal`).toEqual([]);
  expect.soft(frames.filter((frame) => frame.jump).map((frame) => `${frame.t.toFixed(0)} ms`), `${name}: rule 5, no Jump to latest`).toEqual([]);
}

// ---------------------------------------------------------------------------------------------
// Fixture conversations on the scripted backend.

type Host = {
  /** The principal that writes the replies. */
  kind: 'ticket' | 'chief' | 'manager';
  id: string;
  label: string;
  conversation: string;
  /** The owner's send route, which starts each later turn. */
  sendPath: string;
};

async function ticketHost(server: ServerHandle, conversation: string, title: string, workerType = 'coding'): Promise<Host> {
  const id: string = (
    await cli(server, ['ticket', 'create', '--input-json', '-'], {
      stdin: JSON.stringify({ worker_type: workerType, title, brief: 'Agreed brief.', manager: { kind: 'chief', id: 'chief' } }),
      settleBrief: true,
    })
  ).id;
  await post(server, `/api/test/signals/${conversation}/start`);
  await bindConversation(server, 'ticket', id, conversation);
  return { kind: 'ticket', id, label: title, conversation, sendPath: `/api/tickets/${id}/conversation/send` };
}

async function chiefHost(server: ServerHandle, conversation: string): Promise<Host> {
  await post(server, `/api/test/signals/${conversation}/start`);
  await bindConversation(server, 'agent', 'chief_of_staff', conversation);
  return { kind: 'chief', id: 'chief', label: 'Chief of Staff', conversation, sendPath: '/api/chief/conversation/send' };
}

async function managerHost(server: ServerHandle, conversation: string): Promise<Host> {
  const project = await api.post(server, '/api/projects', { name: 'Open at the bottom' });
  const manager = await api.post(server, '/api/managers', { title: 'Open at the bottom', project_id: project.id });
  const id = String(manager.id);
  await post(server, `/api/test/signals/${conversation}/start`);
  await bindConversation(server, 'agent', `manager_${id}`, conversation);
  return { kind: 'manager', id, label: 'Open at the bottom', conversation, sendPath: `/api/managers/${id}/conversation/send` };
}

const reply = (turn: number) =>
  [
    `Reply ${turn}. ` + 'The viewport keeps the newest line in place while the record loads and the bodies settle. '.repeat(8),
    '',
    '- The first point the reply makes, written out at some length.',
    '- The second point, which also takes most of a line.',
    '- The third point, about the placement of the newest line.',
    '- The fourth point, about the bodies that arrive late.',
    '- The fifth point closes the list.',
    '',
    `That is the end of reply ${turn}.`,
  ].join('\n');

const chartReply = [
  'The week, counted by day:',
  '',
  '```panels-chart',
  JSON.stringify({
    kind: 'bar',
    title: 'Tickets closed per day',
    series: [{ name: 'Closed', points: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'].map((label, index) => ({ label, value: 3 + index * 2 })) }],
  }),
  '```',
].join('\n');

const imageReply = `The render is below.\n\n![Render](${IMAGE_URL})`;

function bigReply(): string {
  const sections: string[] = [];
  for (let section = 1; sections.join('\n\n').length < 70 * 1024; section++)
    sections.push(`## Section ${section}\n\n` + `Section ${section} carries a long paragraph of findings so the message passes sixty-four kilobytes. `.repeat(12));
  return sections.join('\n\n');
}

/** A tool call that starts and finishes. */
const tool = (id: string, kind: string, title: string, detail: string) => [
  { kind: 'tool_call_started', tool_call_id: id, title, tool_kind: kind, detail },
  { kind: 'tool_call_finished', tool_call_id: id, tool_call_status: 'completed', detail: `${title}: done` },
];

/** The work of one turn: narration, a run of three tool calls, narration, one tool call, narration. */
async function playWork(server: ServerHandle, host: Host, turn: number): Promise<void> {
  const call = `${host.conversation}-${turn}`;
  await post(server, `/api/test/signals/${host.conversation}/backend-news`, {
    events: [
      { kind: 'AgentMessageCompleted', text: `Turn ${turn}: I will read the viewport and its tests first.` },
      ...tool(`${call}-read`, 'read', 'Read the viewport', 'web/src/components/conversation/viewport/ConversationViewport.svelte'),
      ...tool(`${call}-search`, 'search', 'Search for the placement', 'rg landTheOpen web/src'),
      ...tool(`${call}-edit`, 'edit', 'Edit the placement', 'web/src/components/conversation/viewport/placement.ts'),
      { kind: 'AgentMessageCompleted', text: `Turn ${turn}: the placement is edited. Now the checks.` },
      ...tool(`${call}-check`, 'execute', 'Run the checks', 'npm --prefix web run check'),
      { kind: 'AgentMessageCompleted', text: `Turn ${turn}: the checks pass.` },
    ],
  });
}

/** One whole turn. The first turn is the one the scripted start already opened. */
async function playTurn(server: ServerHandle, host: Host, turn: number, text: string, options: { finish?: boolean } = {}): Promise<void> {
  if (turn > 1)
    await post(server, host.sendPath, { sender_label: 'Owner', mode: 'queue', content: [{ piece: 'text', text: `Question ${turn}: what changed, and what is next?` }] });
  await playWork(server, host, turn);
  await post(server, `/api/test/signals/${host.conversation}/message`, { kind: host.kind, id: host.id, label: host.label, text });
  if (options.finish !== false) await post(server, `/api/test/signals/${host.conversation}/finish`);
}

/** Four settled turns, then a last turn whose reply is `last`. */
async function playConversation(server: ServerHandle, host: Host, last: string = reply(5)): Promise<void> {
  for (let turn = 1; turn <= 4; turn++) await playTurn(server, host, turn, reply(turn));
  await playTurn(server, host, 5, last);
}

/** A solid PNG of a fixed size, so an image with no stated dimensions has a real one. */
function solidPng(width: number, height: number): Buffer {
  const table = Array.from({ length: 256 }, (_, n) => {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    return c >>> 0;
  });
  const crc = (bytes: Buffer) => {
    let c = 0xffffffff;
    for (const byte of bytes) c = table[(c ^ byte) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  };
  const chunk = (type: string, data: Buffer) => {
    const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const sum = Buffer.alloc(4);
    sum.writeUInt32BE(crc(body));
    return Buffer.concat([length, body, sum]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8; // bit depth
  header[9] = 2; // RGB
  const row = Buffer.alloc(1 + width * 3);
  for (let x = 0; x < width; x++) row.set([70, 110, 160], 1 + x * 3);
  const pixels = deflateSync(Buffer.concat(Array.from({ length: height }, () => row)));
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', pixels), chunk('IEND', Buffer.alloc(0))]);
}

const TEST_IMAGE = solidPng(1200, 900);
const ticketScreen = (ticket: string) => `[data-screen="ticket"][data-ticket-id="${ticket}"]`;
const expand = (page: Page) => page.locator('[data-conversation-expand]').click({ timeout: WAIT_MS });
/** Wait until the open thread is drawn and visible, as the reader's tail has delivered it. */
const threadShown = (page: Page) =>
  page.waitForFunction(() => {
    const thread = document.querySelector<HTMLElement>('[data-conversation-thread]');
    return thread !== null && thread.clientHeight > 0 && getComputedStyle(thread).visibility === 'visible' && thread.querySelector('[data-conversation-view-key]') !== null;
  }, undefined, { timeout: WAIT_MS });

// ---------------------------------------------------------------------------------------------

for (const lens of ['full', 'focus'] as const) {
  test(`every open lands at the bottom on its first frame (${lens})`, async ({ signalServer: server, newContext }, testInfo) => {
    test.setTimeout(300_000);
    const work = await ticketHost(server, `open-bottom-work-${lens}`, 'Open at the bottom');
    const owned = await ticketHost(server, `open-bottom-owned-${lens}`, 'Open by itself', 'initiative_review');
    const chief = await chiefHost(server, `open-bottom-chief-${lens}`);
    const manager = await managerHost(server, `open-bottom-manager-${lens}`);
    const chart = await ticketHost(server, `open-bottom-chart-${lens}`, 'Chart at the bottom');
    const image = await ticketHost(server, `open-bottom-image-${lens}`, 'Image at the bottom');
    const big = await ticketHost(server, `open-bottom-big-${lens}`, 'Long Markdown at the bottom');
    for (const host of [work, owned, chief, manager]) await playConversation(server, host);
    await playConversation(server, chart, chartReply);
    await playConversation(server, image, imageReply);
    await playConversation(server, big, bigReply());
    // A Ticket waiting on its owner opens its conversation when the Ticket opens.
    await bindConversation(server, 'feedback', owned.id);

    const desktop = await measuredContext(newContext, lens);
    const phone = await measuredContext(newContext, lens, { viewport: { width: 390, height: 844 } });
    const appWindow = await measuredContext(newContext, lens, { desktopWindow: true });

    type Case = { name: string; open: () => Promise<{ page: Page; state: ProbeState }> };
    const fromDock = (context: BrowserContext, route: string, ready: string) => async () => {
      const page = await openScreen(context, server, route, ready);
      await dockReady(page);
      return { page, state: await measure(page, () => expand(page)) };
    };
    const byItself = (route: string, ready: string) => async () => {
      const page = await openScreen(desktop, server, route, ready, { autostart: PROBE });
      return { page, state: await measure(page, null) };
    };
    const cases: Case[] = [
      { name: 'ticket-dock', open: fromDock(desktop, `#/workspace/${work.id}`, ticketScreen(work.id)) },
      { name: 'ticket-direct-link', open: byItself(`#/workspace/${work.id}?conversation=${work.conversation}`, ticketScreen(work.id)) },
      { name: 'ticket-user-owned', open: byItself(`#/workspace/${owned.id}`, ticketScreen(owned.id)) },
      { name: 'chief-dock', open: fromDock(desktop, '#/workspace/chief-of-staff', '[data-conversation-rest-bar]') },
      { name: 'manager-dock', open: fromDock(desktop, `#/workspace/manager/${manager.id}`, '[data-conversation-rest-bar]') },
      { name: 'ticket-dock-390px', open: fromDock(phone, `#/workspace/${work.id}`, ticketScreen(work.id)) },
      { name: 'ticket-dock-desktop-window', open: fromDock(appWindow, `#/workspace/${work.id}`, ticketScreen(work.id)) },
      { name: 'chart-reply', open: fromDock(desktop, `#/workspace/${chart.id}`, ticketScreen(chart.id)) },
      { name: 'image-reply', open: fromDock(desktop, `#/workspace/${image.id}`, ticketScreen(image.id)) },
      { name: 'markdown-70kb-reply', open: fromDock(desktop, `#/workspace/${big.id}`, ticketScreen(big.id)) },
    ];
    for (const { name, open } of cases) {
      const errors = testInfo.errors.length;
      const { page, state } = await open();
      await expectOpenAtBottom(testInfo, `${lens}-${name}`, state);
      if (name === 'image-reply') {
        // The image must have loaded inside the window for the case to prove anything.
        const loaded = await page.locator(`img[src="${IMAGE_URL}"]`).evaluateAll((images) => images.some((image) => (image as HTMLImageElement).naturalHeight > 0));
        expect.soft(loaded, `${lens}-${name}: the image loaded`).toBe(true);
      }
      await captureIfFailed(page, testInfo, errors, `${lens}-${name}`);
      await page.close();
    }

    if (lens === 'full') {
      // Live output is spooled only while a Full transcript watches a running turn, so one page
      // watches while the output streams. The measured open is a second page.
      const live = await ticketHost(server, 'open-bottom-live', 'Live output at the bottom');
      for (let turn = 1; turn <= 3; turn++) await playTurn(server, live, turn, reply(turn));
      await post(server, live.sendPath, { sender_label: 'Owner', mode: 'queue', content: [{ piece: 'text', text: 'Question 4: keep going.' }] });
      await playWork(server, live, 4);
      const watcher = await openScreen(desktop, server, `#/workspace/${live.id}`, ticketScreen(live.id));
      await dockReady(watcher);
      await expand(watcher);
      await threadShown(watcher);
      await watcher.waitForTimeout(500);
      for (let part = 1; part <= 6; part++) {
        await post(server, `/api/test/signals/${live.conversation}/backend-news`, {
          events: [{ kind: 'AgentMessageDelta', text_delta: `Live output part ${part}. ` + 'The answer is still being written into the thread. '.repeat(4) + '\n\n' }],
        });
      }
      await expect(watcher.locator('[data-conversation-row="streaming"]')).toContainText('Live output part 6', { timeout: WAIT_MS });
      await watcher.close();
      const errors = testInfo.errors.length;
      const page = await openScreen(desktop, server, `#/workspace/${live.id}`, ticketScreen(live.id));
      await dockReady(page);
      const state = await measure(page, () => expand(page));
      await expectOpenAtBottom(testInfo, 'full-live-output', state);
      await expect.soft(page.locator('[data-conversation-row="streaming"]'), 'full-live-output: the live output is drawn').toContainText('Live output part 6');
      await captureIfFailed(page, testInfo, errors, 'full-live-output');
      await page.close();
    }
  });
}

test('every way back to a conversation lands where it should', async ({ signalServer: server, newContext }, testInfo) => {
  test.setTimeout(300_000);
  const work = await ticketHost(server, 'open-bottom-return', 'Return to the bottom');
  await playConversation(server, work);
  const long = await ticketHost(server, 'open-bottom-restoration', 'Restoration falls back');
  // More items than one history page holds, so a line near the top is outside the tail.
  for (let message = 1; message <= 110; message++)
    await post(server, `/api/test/signals/${long.conversation}/message`, { kind: 'ticket', id: long.id, label: long.label, text: `Message ${message}. ` + 'A line of the long record. '.repeat(6) });
  await post(server, `/api/test/signals/${long.conversation}/finish`);

  const context = await measuredContext(newContext, 'full');
  const thread = (page: Page) => page.locator('[data-conversation-thread]');
  const atBottom = (page: Page) =>
    page
      .waitForFunction(() => {
        const element = document.querySelector<HTMLElement>('[data-conversation-thread]');
        return element !== null && element.clientHeight > 0 && element.scrollHeight - element.clientHeight - element.scrollTop <= 2;
      }, undefined, { timeout: WAIT_MS })
      .catch(() => undefined);
  const scrollUp = async (page: Page, pixels: number) => {
    await thread(page).hover();
    await page.mouse.wheel(0, -pixels);
    await page.waitForTimeout(700);
  };
  const hideTab = async (page: Page) => {
    await page.evaluate(() => window.__setDocumentHidden!(true));
    await page.waitForTimeout(300);
  };
  let replies = 0;
  const replyWhileAway = async (page: Page) => {
    replies += 1;
    await post(server, `/api/test/signals/${work.conversation}/message`, { kind: 'ticket', id: work.id, label: work.label, text: `Away reply ${replies}. ` + reply(10 + replies) });
    await page.waitForTimeout(1000);
  };

  const page = await openScreen(context, server, `#/workspace/${work.id}`, ticketScreen(work.id), { visibilityControl: true });
  await dockReady(page);
  await expand(page);
  await threadShown(page);
  await atBottom(page);
  await page.waitForTimeout(500);

  // (a) A tab that was at the bottom returns to the bottom, with what arrived while away.
  let errors = testInfo.errors.length;
  await hideTab(page);
  await replyWhileAway(page);
  await expectOpenAtBottom(testInfo, 'tab-return-at-bottom', await returnToTab(page));
  await expect.soft(thread(page).getByText('Away reply 1.'), 'tab-return-at-bottom: the reply that arrived while away is shown').toBeVisible();
  await captureIfFailed(page, testInfo, errors, 'tab-return-at-bottom');

  // (b) A tab the reader had scrolled up returns where it was, not at the bottom.
  errors = testInfo.errors.length;
  await scrollUp(page, 900);
  const held = await thread(page).evaluate((element) => {
    const box = element.getBoundingClientRect();
    const item = Array.from(element.querySelectorAll<HTMLElement>('[data-conversation-view-key]')).find((node) => node.getBoundingClientRect().bottom > box.top + 1);
    return item ? { key: item.dataset.conversationViewKey!, top: item.getBoundingClientRect().top - box.top, distance: element.scrollHeight - element.clientHeight - element.scrollTop } : null;
  });
  expect.soft(held?.distance ?? 0, 'tab-return-scrolled-up: the reader scrolled away from the bottom').toBeGreaterThan(300);
  await hideTab(page);
  await replyWhileAway(page);
  const kept = await returnToTab(page, { trackKey: held?.key ?? null });
  await attachFrames(testInfo, 'tab-return-scrolled-up', kept);
  expect.soft(kept.failure, `tab-return-scrolled-up: ${kept.failure}`).toBeNull();
  const moved = kept.frames.filter((frame) => !frame.visible || frame.tracked === null || Math.abs(frame.tracked - held!.top) > 2)
    .map((frame) => `${frame.t.toFixed(0)} ms: ${frame.tracked === null ? 'held line not drawn' : `held line at ${frame.tracked} px, was ${held!.top} px`}`);
  expect.soft(moved, 'tab-return-scrolled-up: the held line stays where it was').toEqual([]);
  expect.soft(kept.frames.filter((frame) => (frame.distance ?? 0) <= 2).length, 'tab-return-scrolled-up: no frame at the bottom').toBe(0);
  await captureIfFailed(page, testInfo, errors, 'tab-return-scrolled-up');

  // (c) Reopening from the dock lands at the bottom, however far up the reader had been.
  errors = testInfo.errors.length;
  await scrollUp(page, 600);
  await page.locator('[data-conversation-collapse]').click({ timeout: WAIT_MS });
  await page.locator('[data-conversation-state="rest"]').waitFor({ timeout: WAIT_MS });
  await page.waitForTimeout(400);
  await expectOpenAtBottom(testInfo, 'dock-reopen', await measure(page, () => expand(page)));
  await captureIfFailed(page, testInfo, errors, 'dock-reopen');

  // A switch of lens is a new reader, which opens at the bottom too. The old lens is on
  // screen until the click, so the reading starts at the first frame after it.
  for (const [name, choice] of [['lens-switch-to-focus', 'focus'], ['lens-switch-to-full', 'full']] as const) {
    errors = testInfo.errors.length;
    await page.waitForTimeout(500);
    const control = `[data-conversation-lens-choice="${choice}"]`;
    const state = await measure(page, () => page.locator(control).click({ timeout: WAIT_MS }), { readyWhen: `${control}[aria-pressed="true"]` });
    await expectOpenAtBottom(testInfo, name, state);
    await captureIfFailed(page, testInfo, errors, name);
  }
  await page.close();

  // (d) A return whose saved line cannot be restored shows the bottom, without sliding to it.
  errors = testInfo.errors.length;
  const restoring = await openScreen(context, server, `#/workspace/${long.id}`, ticketScreen(long.id), { visibilityControl: true });
  await dockReady(restoring);
  await expand(restoring);
  await threadShown(restoring);
  await atBottom(restoring);
  // Scroll back until the oldest row has been read in from an older page.
  for (let attempt = 0; attempt < 15 && (await restoring.locator('[data-conversation-view-key="e1"]').count()) === 0; attempt++)
    await scrollUp(restoring, 20_000);
  await scrollUp(restoring, 20_000);
  expect.soft(await restoring.locator('[data-conversation-view-key="e1"]').count(), 'restoration-fallback: the oldest row was read in').toBe(1);
  await restoring.route('**/view/pages**', (route) => route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"The history page is refused for this test."}' }));
  await hideTab(restoring);
  await restoring.waitForTimeout(500);
  const fallback = await returnToTab(restoring);
  await expectOpenAtBottom(testInfo, 'restoration-fallback', fallback, { idle: false });
  expect.soft(fallback.pageFetchesAfterStart.length, 'restoration-fallback: the return tried to restore the saved line').toBeGreaterThan(0);
  await captureIfFailed(restoring, testInfo, errors, 'restoration-fallback');
  await restoring.close();
});
