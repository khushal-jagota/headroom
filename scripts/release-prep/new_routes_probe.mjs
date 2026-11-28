// Probe the release's new stream framing and routes on a private test host (run via run_probe_server.mjs).
// Checks: the desktop notification stream's first frame is `retry: 1000`;
// POST /api/client-events with and without X-Panels-Client; GET /api/connection-records shows the closes.
// Usage: node new_routes_probe.mjs <base_url> <out.json>
import { writeFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
const [base, out] = process.argv.slice(2);
const device = randomUUID();
const client = { 'X-Panels-Client': JSON.stringify({ kind: 'ios', device_id: device, version: '14', build: 14 }), 'X-Panels-iOS-Build': '14' };
const results = [];
const record = (name, value) => (results.push({ name, ...value }), value);
async function call(method, path, { headers = {}, body, query } = {}) {
  const url = new URL(path, base);
  for (const [k, v] of Object.entries(query ?? {})) url.searchParams.set(k, v);
  const h = { ...headers };
  if (body !== undefined) h['Content-Type'] = 'application/json';
  const r = await fetch(url, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await r.text();
  let json = null;
  try { json = JSON.parse(text); } catch {}
  return { status: r.status, json, text: text.slice(0, 300) };
}
async function raw_stream(path, query, ms = 3000) {
  const url = new URL(path, base);
  for (const [k, v] of Object.entries(query ?? {})) url.searchParams.set(k, v);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  let text = '', status = 0, contentType = '';
  try {
    const r = await fetch(url, { headers: { ...client, Accept: 'text/event-stream' }, signal: controller.signal });
    status = r.status; contentType = r.headers.get('content-type') ?? '';
    const d = new TextDecoder();
    for await (const chunk of r.body) { text += d.decode(chunk, { stream: true }); if (text.length > 4000) break; }
  } catch (e) { if (e.name !== 'AbortError') throw e; } finally { clearTimeout(timer); controller.abort(); }
  const frames = text.split('\n\n').filter(Boolean);
  return { status, content_type: contentType, first_frame: frames[0]?.slice(0, 120) ?? null,
    starts_with_retry_1000: text.startsWith('retry: 1000\n\n'), frame_heads: frames.slice(0, 6).map((f) => f.split('\n')[0].slice(0, 40)) };
}
const send = (text) => ({ content: [{ piece: 'text', text }], sender_label: 'You', mode: 'steer',
  sender_message_id: randomUUID().toLowerCase(), sent_at_unix_milliseconds: Date.now() });
const chief = await call('POST', '/api/chief/conversation/send', { body: send('probe') });
const cid = chief.json?.conversation_id ?? null;
record('setup:chief_send', { status: chief.status, conversation_id_present: !!cid });
const view = JSON.stringify({ projection_version: 7, scope_generation: 0, locale: 'en-US', selected: null, selected_conversation_id: null,
  review_conversation_ids: [], review_skipped_keys: [], review_priority_keys: [],
  windows: [{ section: 'review', owner: null, group: 'all', child_filter: 'all', anchor: null, direction: 'after', limit: 30 }] });
const streams = {
  notifications_desktop: ['/api/notifications/desktop', {}],
};
for (const [name, [path, query, ms]] of Object.entries(streams)) record(`stream:${name}`, await raw_stream(path, query, ms));
const ev = { version: 1, events: [{ at: Date.now(), kind: 'probe_ok', probe_ms: 12, stream: 'changes' }] };
const noHeader = await call('POST', '/api/client-events', { body: ev });
record('client_events:no_header', { status: noHeader.status, body: noHeader.json ?? noHeader.text });
const ok = await call('POST', '/api/client-events', { headers: client, body: ev });
record('client_events:with_header', { status: ok.status, body: ok.json ?? ok.text });
const bad = await call('POST', '/api/client-events', { headers: client, body: { version: 2, events: [] } });
record('client_events:bad_version', { status: bad.status, body: bad.json ?? bad.text });
await new Promise((r) => setTimeout(r, 1500));
const recs = await call('GET', '/api/connection-records', { query: { device_id: device } });
const summary = (j) => j && typeof j === 'object' ? Object.fromEntries(Object.entries(j).map(([k, v]) => [k, Array.isArray(v) ? { count: v.length, first: v[0] ?? null } : v])) : j;
record('connection_records:device', { status: recs.status, body: summary(recs.json) ?? recs.text });
const all = await call('GET', '/api/connection-records');
record('connection_records:all', { status: all.status, body: summary(all.json) ?? all.text });
const over = await call('GET', '/api/connection-records', { query: { limit: '501' } });
record('connection_records:limit_501', { status: over.status });
writeFileSync(out, JSON.stringify(results, null, 1));
for (const r of results) console.log(r.name, r.status ?? '', r.starts_with_retry_1000 ?? '', JSON.stringify(r.frame_heads ?? r.body ?? '').slice(0, 200));
