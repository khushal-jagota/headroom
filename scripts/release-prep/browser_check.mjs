// Headless browser pass on the COPY (loopback only). For each target: open its hash route, time
// until the pane's first stream snapshot and until the transcript region reports 'complete',
// then hold 2 s and require the stream still open, no connection-trouble mark, no upgrade 409.
// Screenshots hide all text and media. Usage: PLAYWRIGHT_FROM=tests/e2e node browser_check.mjs PORT TARGETS_JSONL OUT_DIR
import { createRequire } from 'node:module';
const { chromium } = createRequire(process.env.PLAYWRIGHT_FROM + '/package.json')('playwright');
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
const [port, targets_file, out] = process.argv.slice(2);
if (port === '8767') throw new Error('never the live port');
mkdirSync(out + '/screenshots', { recursive: true });
const base = `http://127.0.0.1:${port}/`;
const targets = readFileSync(targets_file, 'utf8').trim().split('\n').map((l) => JSON.parse(l));
targets.push({ kind: 'review', id: 'review' });
const route = (t) => t.kind === 'chief' ? '#/workspace/chief-of-staff' : t.kind === 'manager' ? `#/workspace/manager/${t.id}`
  : t.kind === 'ticket' ? `#/workspace/${t.id}` : '#/review';
const MASK = `*{color:transparent!important;text-shadow:none!important;caret-color:transparent!important}
img,video,canvas,svg image,picture{visibility:hidden!important}`;
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await context.addInitScript(() => {
  const log = (window.__streams = []);
  const Native = window.EventSource;
  window.EventSource = class extends Native {
    constructor(url, init) {
      super(url, init);
      const entry = { url: String(url), created: performance.now(), open: null, snapshot: null, errors: 0 };
      log.push(entry);
      this.__entry = entry;
      this.addEventListener('open', () => { entry.open ??= performance.now(); });
      this.addEventListener('snapshot', () => { entry.snapshot ??= performance.now(); });
      this.addEventListener('error', () => { entry.errors += 1; });
      window.__sources = window.__sources || [];
      window.__sources.push(this);
    }
  };
});
const page = await context.newPage();
const responses = [];
page.on('response', (r) => { const u = r.url(); if (u.includes('/api/')) responses.push({ url: u.replace(base, '/'), status: r.status(), at: Date.now() }); });
const consoleErrors = [];
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300)); });
const t0 = Date.now();
await page.goto(base + '#/workspace', { waitUntil: 'load' });
const app_load_ms = Date.now() - t0;
const results = [];
for (const t of targets) {
  const from = responses.length;
  const started = Date.now();
  await page.evaluate(() => { window.__mark = performance.now(); });
  await page.evaluate((h) => { location.hash = h; }, route(t));
  const r = { kind: t.kind, id: t.id, conversation_id: t.conversation_id ?? null };
  try {
    if (t.kind === 'review') {
      await page.locator('[data-deck-item]').first().waitFor({ state: 'visible', timeout: 60000 });
      r.complete_seconds = (Date.now() - started) / 1000;
      r.review_cards = await page.locator('[data-deck-item]').count();
      r.api_failures = responses.slice(from).filter((x) => x.status >= 400).map((x) => `${x.status} ${x.url.split('?')[0]}`);
      r.pass = !responses.slice(from).some((x) => x.status >= 500 && !x.url.startsWith('/api/conversation/backends'));
    } else {
      const pane = page.locator(`[data-conversation-pane][data-conversation-id="${t.conversation_id}"]`);
      await pane.waitFor({ state: 'attached', timeout: 60000 });
      // Chief and Manager pages show the conversation collapsed ('rest'); open it as the owner would.
      const expand = pane.locator('[data-conversation-expand]');
      r.opened_from_rest = (await pane.getAttribute('data-conversation-state')) === 'rest' && (await expand.count()) > 0;
      if (r.opened_from_rest) await expand.first().click();
      await pane.locator('[data-conversation-entry="complete"]').waitFor({ state: 'attached', timeout: 60000 });
      r.complete_seconds = (Date.now() - started) / 1000;
      const stream = await page.evaluate((id) => {
        const e = [...window.__streams].reverse().find((s) => s.url.includes(`/conversations/${id}/view/tail`) && s.created >= window.__mark);
        return e ? { snapshot_seconds: e.snapshot === null ? null : (e.snapshot - window.__mark) / 1000 } : null;
      }, t.conversation_id);
      r.stream_snapshot_seconds = stream?.snapshot_seconds ?? null;
      await page.waitForTimeout(2000);
      const held = await page.evaluate((id) => {
        const i = [...window.__streams].map((s, n) => [s, n]).reverse().find(([s]) => s.url.includes(`/conversations/${id}/view/tail`));
        if (!i) return null;
        return { ready_state: window.__sources[i[1]].readyState, errors: i[0].errors };
      }, t.conversation_id);
      r.stream_after_2s = held;
      r.connection_trouble = (await pane.locator('[data-conversation-connection]').count()) > 0;
      r.load_error = (await pane.locator('[data-conversation-entry-status="error"]').count()) > 0;
      const mine = responses.slice(from);
      r.view_http = mine.filter((x) => x.url.includes(`/conversations/${t.conversation_id}/view`) && !x.url.includes('/tail')).map((x) => x.status);
      r.upgrading_409 = mine.some((x) => x.status === 409 && x.url.includes('/view'));
      r.upgrading_text = (await page.getByText('Conversation view is upgrading').count()) > 0;
      r.other_api_failures = mine.filter((x) => x.status >= 400).map((x) => `${x.status} ${x.url.split('?')[0]}`);
      r.pass = r.stream_snapshot_seconds !== null && held?.ready_state === 1 && held.errors === 0 && !r.connection_trouble && !r.load_error && !r.upgrading_409 && !r.upgrading_text;
    }
  } catch (error) {
    r.pass = false;
    r.error = String(error).slice(0, 300);
  }
  if (t.kind !== 'manager' || results.filter((x) => x.kind === 'manager').length < 2) {
    await page.addStyleTag({ content: MASK });
    await page.screenshot({ path: `${out}/screenshots/${t.kind}-${t.id}.png` });
    await page.evaluate(() => document.querySelectorAll('style').forEach((s) => { if (s.textContent.includes('color:transparent!important')) s.remove(); }));
  }
  results.push(r);
  console.log(JSON.stringify({ kind: r.kind, id: r.id, pass: r.pass, complete: r.complete_seconds, snapshot: r.stream_snapshot_seconds }));
}
await browser.close();
writeFileSync(out + '/browser-results.json', JSON.stringify({ app_load_ms, total_seconds: (Date.now() - t0) / 1000, console_errors: consoleErrors.slice(0, 50), results }, null, 1));
