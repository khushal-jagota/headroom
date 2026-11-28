// Diagnose one target on the COPY (loopback only): pane state, expand button actionability, view requests.
// Usage: PLAYWRIGHT_FROM=<dir with playwright> node browser_diag.mjs PORT HASH_ROUTE CONVERSATION_ID OUT_JSON
import { createRequire } from 'node:module';
import { writeFileSync } from 'node:fs';
const { chromium } = createRequire(process.env.PLAYWRIGHT_FROM + '/package.json')('playwright');
const [port, hash, conversation, out] = process.argv.slice(2);
if (port === '8767') throw new Error('never the live port');
const base = `http://127.0.0.1:${port}/`;
const browser = await chromium.launch({ headless: true });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
const net = [];
page.on('response', (r) => { const u = r.url(); if (u.includes('/api/')) net.push({ t: Date.now(), status: r.status(), url: u.replace(base, '/').slice(0, 160) }); });
const consoleMsgs = [];
page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') consoleMsgs.push(`${m.type()}: ${m.text().slice(0, 300)}`); });
page.on('pageerror', (e) => consoleMsgs.push('pageerror: ' + String(e).slice(0, 300)));
const t0 = Date.now();
await page.goto(base + '#/workspace', { waitUntil: 'load' });
await page.evaluate((h) => { location.hash = h; }, hash);
const pane = page.locator(`[data-conversation-pane][data-conversation-id="${conversation}"]`);
await pane.waitFor({ state: 'attached', timeout: 60000 });
const snap = async (label) => ({
  label, at_s: (Date.now() - t0) / 1000,
  pane: await pane.evaluate((el) => Object.fromEntries([...el.attributes].filter((a) => a.name.startsWith('data-')).map((a) => [a.name, a.value]))),
  entries: await pane.evaluate((el) => [...el.querySelectorAll('[data-conversation-entry]')].map((e) => e.getAttribute('data-conversation-entry'))),
  expand: await pane.locator('[data-conversation-expand]').evaluateAll((els) => els.map((el) => {
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return { box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)], disabled: el.disabled, aria_disabled: el.getAttribute('aria-disabled'),
      visibility: cs.visibility, display: cs.display, opacity: cs.opacity, pointer_events: cs.pointerEvents,
      top_is_self: top === el || el.contains(top), top_element: top ? top.outerHTML.slice(0, 200).replace(/>[^<]+</g, '>…<') : null,
      attrs: Object.fromEntries([...el.attributes].map((a) => [a.name, a.value.slice(0, 80)])) };
  })),
});
const states = [await snap('attached')];
await page.waitForTimeout(5000);
states.push(await snap('after_5s'));
let click = null;
try { await pane.locator('[data-conversation-expand]').first().click({ timeout: 10000 }); click = 'ok'; } catch (e) { click = String(e).slice(0, 2500); }
states.push(await snap('after_click_attempt'));
await page.waitForTimeout(20000);
states.push(await snap('after_25s'));
writeFileSync(out, JSON.stringify({ states, click, net, console: consoleMsgs.slice(0, 40) }, null, 1));
console.log(JSON.stringify({ states, click }, null, 1).slice(0, 6000));
await browser.close();
