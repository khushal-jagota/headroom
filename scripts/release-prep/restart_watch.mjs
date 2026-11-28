// Keep one page open on the Chief on the COPY (loopback only) and record, every 100 ms, what the shell
// shows: status card (connection state, status state, word), navigation-health line, dimming, the Chief
// pane's connection mark and its tail stream state. Only changes are kept, with wall-clock times (ms).
// Masked screenshots on the first few changes. Ends after DURATION_S, or earlier when STOP_FILE exists.
// Usage: PLAYWRIGHT_FROM=<dir> node restart_watch.mjs PORT CHIEF_CONVERSATION_ID DURATION_S OUT_DIR [STOP_FILE]
import { createRequire } from 'node:module';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
const { chromium } = createRequire(process.env.PLAYWRIGHT_FROM + '/package.json')('playwright');
const [port, chief, duration, out, stopFile] = process.argv.slice(2);
if (port === '8767') throw new Error('never the live port');
mkdirSync(out + '/screenshots', { recursive: true });
const base = `http://127.0.0.1:${port}/`;
const MASK = `*{color:transparent!important;text-shadow:none!important}img,video,canvas{visibility:hidden!important}
[data-shell-status] *{color:revert!important}`;
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await context.addInitScript(() => {
  const log = (window.__streams = []);
  const Native = window.EventSource;
  window.EventSource = class extends Native {
    constructor(url, init) { super(url, init); const e = { url: String(url), source: this, opens: 0, errors: 0 }; log.push(e);
      this.addEventListener('open', () => { e.opens += 1; }); this.addEventListener('error', () => { e.errors += 1; }); }
  };
});
const page = await context.newPage();
const failed = [];
page.on('requestfailed', (r) => { if (r.url().includes('/api/')) failed.push({ t: Date.now(), url: r.url().replace(base, '/').split('?')[0], error: r.failure()?.errorText }); });
await page.goto(base + '#/workspace/chief-of-staff', { waitUntil: 'load' });
const pane = page.locator(`[data-conversation-pane][data-conversation-id="${chief}"]`);
await pane.waitFor({ state: 'attached', timeout: 60000 });
if ((await pane.getAttribute('data-conversation-state')) === 'rest') await pane.locator('[data-conversation-expand]').first().click({ timeout: 30000 });
await pane.locator('[data-conversation-entry="complete"]').waitFor({ state: 'attached', timeout: 60000 });
const read = (id) => page.evaluate((id) => {
  const slot = document.querySelector('[data-shell-status]');
  const card = document.querySelector('[data-connection-status]');
  const nav = document.querySelector('[data-navigation-health]');
  const main = document.querySelector('main.shell-content');
  const p = document.querySelector(`[data-conversation-pane][data-conversation-id="${id}"]`);
  const tails = window.__streams.filter((s) => s.url.includes(`/conversations/${id}/view/tail`));
  const tail = tails.at(-1);
  const changes = window.__streams.filter((s) => s.url.includes('/api/changes')).at(-1);
  return {
    card_open: !!slot?.hasAttribute('data-open'),
    connection_state: card?.getAttribute('data-connection-state') ?? null,
    status_state: card?.getAttribute('data-status-state') ?? null,
    word: card?.querySelector('.status-word')?.textContent?.trim() ?? null,
    detail: card?.querySelector('.status-detail')?.textContent?.trim() ?? null,
    navigation: nav ? nav.textContent.trim() : null,
    dimmed: !!main?.classList.contains('update-dimmed'),
    updating_class: !!main?.classList.contains('update-in-progress'),
    pane_connection_mark: p ? p.querySelectorAll('[data-conversation-connection]').length : null,
    pane_entry: p?.querySelector('[data-conversation-entry]')?.getAttribute('data-conversation-entry') ?? null,
    tail_streams: tails.length, tail_ready_state: tail ? tail.source.readyState : null,
    changes_ready_state: changes ? changes.source.readyState : null,
  };
}, id);
const timeline = [];
let last = null, shots = 0;
const end = Date.now() + Number(duration) * 1000;
while (Date.now() < end && !(stopFile && existsSync(stopFile))) {
  let s;
  try { s = await read(chief); } catch (e) { s = { read_error: String(e).slice(0, 200) }; }
  const key = JSON.stringify(s);
  if (key !== last) {
    timeline.push({ t: Date.now(), ...s });
    last = key;
    if (shots < 12) {
      try { await page.addStyleTag({ content: MASK }); await page.screenshot({ path: `${out}/screenshots/${String(shots).padStart(2, '0')}-${Date.now()}.png` }); shots += 1;
        await page.evaluate(() => document.querySelectorAll('style').forEach((x) => { if (x.textContent.includes('color:transparent!important')) x.remove(); })); } catch {}
    }
  }
  await page.waitForTimeout(100);
}
writeFileSync(out + '/restart-watch.json', JSON.stringify({ started: timeline[0]?.t, ended: Date.now(), timeline, failed_requests: failed.slice(0, 100) }, null, 1));
for (const e of timeline) console.log(new Date(e.t).toISOString(), JSON.stringify({ ...e, t: undefined }));
await browser.close();
