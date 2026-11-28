// Drives a private Panels server with requests shaped like the installed iPhone build (INSTALLED_BUILD,
// default 16, the floor), like the next build (NEXT_BUILD, default 17), and with the build below the
// floor for the update-required refusal. Run it with run_probe_server.mjs against a server from the live
// commit and one from the candidate, each on a fresh database, then compare the two JSON files.
// Usage: [INSTALLED_BUILD=16 NEXT_BUILD=17] node ios_probe.mjs <base_url> <out.json>
import { writeFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';

const [base, out] = process.argv.slice(2);
const INSTALLED = Number(process.env.INSTALLED_BUILD ?? 16);
const NEXT = Number(process.env.NEXT_BUILD ?? 17);
const BELOW = INSTALLED - 1;
const device = randomUUID();
const native = (build) => ({
  'X-Panels-iOS-Build': String(build),
  'X-Panels-Client': JSON.stringify({ kind: 'ios', device_id: device, version: String(build), build }),
});
const results = [];
const record = (name, value) => {
  results.push({ name, ...value });
  return value;
};

async function call(method, path, { build = null, body, query } = {}) {
  const url = new URL(path, base);
  for (const [k, v] of Object.entries(query ?? {})) url.searchParams.set(k, v);
  const headers = { ...(build === null ? {} : native(build)) };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await response.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {}
  return { status: response.status, json, text };
}

/** Recursive key shape, so base and candidate can be compared without volatile values. */
function shape(value) {
  if (Array.isArray(value)) return value.length ? [shape(value[0])] : [];
  if (value && typeof value === 'object')
    return Object.fromEntries(Object.keys(value).sort().map((k) => [k, shape(value[k])]));
  return value === null ? 'null' : typeof value;
}

const view = (sections) =>
  JSON.stringify({
    projection_version: 7,
    scope_generation: 0,
    locale: 'en-US',
    selected: null,
    selected_conversation_id: null,
    review_conversation_ids: [],
    review_skipped_keys: [],
    review_priority_keys: [],
    windows: sections,
  });
const window = (section, owner = null) => ({
  section,
  owner,
  group: 'all',
  child_filter: 'all',
  anchor: null,
  direction: 'after',
  limit: 30,
});
const send = (text, conversation_id = null) => ({
  ...(conversation_id === null ? {} : { conversation_id }),
  content: [{ piece: 'text', text }],
  sender_label: 'You',
  mode: 'steer',
  sender_message_id: randomUUID().toLowerCase(),
  sent_at_unix_milliseconds: Date.now(),
});

// Setup as a headerless local owner (not a native request).
const project = (await call('POST', '/api/projects', { body: { name: 'Native probe' } })).json;
const manager = (await call('POST', '/api/managers', { body: { title: 'Probe Manager', project_id: project.id } })).json;
const ticket = (
  await call('POST', '/api/tickets', {
    body: {
      title: 'Probe Ticket',
      worker_type: 'coding',
      project_id: project.id,
      brief: 'Agreed brief.',
      manager: { kind: 'manager', id: manager.id },
    },
  })
).json;

// Native reads at the installed build.
const reads = {
  meta: ['/api/meta'],
  managers: ['/api/managers', { detail: 'summary', limit: '30', offset: '0', settled: 'false' }],
  tickets: ['/api/tickets', { detail: 'summary', limit: '30', offset: '0', is_backlogged: 'false' }],
  ticket_workspace: [`/api/tickets/${ticket.id}/workspace`],
  manager_workspace: [`/api/managers/${manager.id}/workspace`],
  chief_start_values: ['/api/chief/conversation/start-values'],
  manager_start_values: [`/api/managers/${manager.id}/conversation/start-values`],
  ticket_start_values: [`/api/tickets/${ticket.id}/conversation/start-values`],
  navigation_snapshot: [
    '/api/navigation/view/snapshot',
    { view: view([window('rail_parents'), window('review'), window('children', { kind: 'chief', id: 'chief' })]) },
  ],
  executor_health: ['/api/executor/health'],
  updates: ['/api/updates'],
  machines: ['/api/machines'],
  notification_settings: ['/api/notifications/settings'],
};
for (const [name, [path, query]] of Object.entries(reads)) {
  const r = await call('GET', path, { build: INSTALLED, query });
  record(`read:${name}`, { status: r.status, shape: shape(r.json) });
}

// One action per owner: the installed build send bodies (with sender_message_id, snake_case).
const sends = {
  chief: '/api/chief/conversation/send',
  manager: `/api/managers/${manager.id}/conversation/send`,
  ticket: `/api/tickets/${ticket.id}/conversation/send`,
};
const conversations = {};
for (const [owner, path] of Object.entries(sends)) {
  const body = send(`Hello ${owner} from the installed build`);
  const r = await call('POST', path, { build: INSTALLED, body });
  conversations[owner] = { id: r.json?.conversation_id ?? null, sender_message_id: body.sender_message_id };
  record(`send:${owner}`, { status: r.status, fate: r.json?.fate ?? null, shape: shape(r.json) });
}

// Conversation output: detail, view snapshot, receipts, tail on connect and reconnect.
for (const [owner, { id, sender_message_id }] of Object.entries(conversations)) {
  if (!id) continue;
  const detail = await call('GET', `/api/conversation/conversations/${id}`, { build: INSTALLED });
  record(`conversation:${owner}:detail`, {
    status: detail.status,
    held_prompts: detail.json?.held_prompts?.length ?? null,
    machine_id_present: typeof detail.json?.machine_id === 'string',
    machine_id: detail.json?.machine_id ?? null,
    shape: shape(detail.json),
  });
  const snapshot = await call('GET', `/api/conversation/conversations/${id}/view`, {
    build: INSTALLED,
    query: { lens: 'focus', transcript: 'true' },
  });
  record(`conversation:${owner}:view`, { status: snapshot.status, shape: shape(snapshot.json) });
  const receipts = await call('GET', `/api/conversation/conversations/${id}/view/receipts`, {
    build: INSTALLED,
    query: { message_ids: JSON.stringify([sender_message_id]) },
  });
  record(`conversation:${owner}:receipts`, {
    status: receipts.status,
    fates: receipts.json?.receipts?.map((r) => r.fate) ?? null,
    shape: shape(receipts.json),
  });
}

// Review decision (the installed build ReviewApproval, sorted snake_case keys, nils omitted).
const decision = {
  decision: 'approve',
  expected_proposal_revision: 1,
  field: 'brief',
  next_ceiling: 'needs_what_changes',
  next_holder: { id: 'owner', kind: 'owner' },
};
const op12 = randomUUID().toLowerCase();
const d12 = await call('POST', `/api/tickets/${ticket.id}/decision-operations/${op12}`, { build: BELOW, body: decision });
record('below_floor:decision', { status: d12.status, code: d12.json?.error?.code ?? null });
const pending12 = (await call('GET', `/api/tickets/${ticket.id}/workspace`, { build: INSTALLED })).json;
record('below_floor:decision_did_not_execute', { stage: pending12?.stage, pending_revision: pending12?.pending_proposal?.revision ?? null });
const op13 = randomUUID().toLowerCase();
const d13 = await call('POST', `/api/tickets/${ticket.id}/decision-operations/${op13}`, { build: INSTALLED, body: decision });
record('decision:approve', { status: d13.status, receipt_contract: d13.json?.receipt_contract ?? null, outcome: d13.json?.receipt?.outcome ?? null, shape: shape(d13.json) });
const lookup = await call('GET', `/api/tickets/${ticket.id}/decision-operations/${op13}`, { build: INSTALLED });
record('decision:lookup', { status: lookup.status, shape: shape(lookup.json) });
const approved = (await call('GET', `/api/tickets/${ticket.id}/workspace`, { build: INSTALLED })).json;
record('decision:after', { stage: approved?.stage, ticket_status: approved?.ticket_status, approvals: approved?.field_approvals?.brief ?? null });

// Unsupported build below the floor: reads and an action are refused before they run.
const before = await call('GET', `/api/conversation/conversations/${conversations.chief.id}`, { build: INSTALLED });
const r12read = await call('GET', '/api/tickets', { build: BELOW, query: { detail: 'summary', limit: '30', offset: '0' } });
record('below_floor:read', { status: r12read.status, code: r12read.json?.error?.code ?? null });
const r12send = await call('POST', sends.chief, { build: BELOW, body: send('Must not run', conversations.chief.id) });
record('below_floor:send', { status: r12send.status, code: r12send.json?.error?.code ?? null });
const after = await call('GET', `/api/conversation/conversations/${conversations.chief.id}`, { build: INSTALLED });
record('below_floor:send_did_not_execute', {
  latest_sequence_before: before.json?.latest_sequence ?? null,
  latest_sequence_after: after.json?.latest_sequence ?? null,
  held_before: before.json?.held_prompts?.length ?? null,
  held_after: after.json?.held_prompts?.length ?? null,
});


// Update screen: machine rows decode state and detail as free strings.
const updates=await call('GET','/api/updates',{build:INSTALLED});
record('updates:values',{status:updates.status,state:updates.json?.state??null,machine_states:(updates.json?.machines??[]).map((m)=>m.state)});

// Installed VideoUploadAPI reserve/status/cancel bodies. Native bytes stay untested here.
const reservation=await call('POST','/api/conversation/video-uploads',{build:INSTALLED,body:{destination:{kind:'chief',id:'chief',expected_conversation_id:conversations.chief.id},filename:'native-probe.mov',byte_count:32}});
record('video:reserve',{status:reservation.status,shape:shape(reservation.json)});
if(reservation.json?.upload_id){
 const path='/api/conversation/video-uploads/'+reservation.json.upload_id;
 const status=await call('GET',path,{build:INSTALLED});record('video:status',{status:status.status,shape:shape(status.json)});
 const cancel=await call('DELETE',path,{build:INSTALLED});record('video:cancel',{status:cancel.status,shape:shape(cancel.json)});
}

// The next build (Live Activity and the Review read fix). Its Chief send carries owner_read_through_sequence 0
// and no conversation. Its device and Live Activity routes need the device registered first. The test host
// has no APNs config, so the device route may refuse; a refusal is recorded, not hidden.
const nextSend = { ...send('Hello chief from the next build'), owner_read_through_sequence: 0 };
const ns = await call('POST', sends.chief, { build: NEXT, body: nextSend });
record('next:send_chief_read_zero', { status: ns.status, fate: ns.json?.fate ?? null, shape: shape(ns.json) });
const device_body = { device_token: 'ab'.repeat(32), bundle_id: 'com.khushaljagota.panels', environment: 'development' };
const dev = await call('PUT', '/api/notifications/apns/devices', { build: NEXT, body: device_body });
record('next:apns_device', { status: dev.status, code: dev.json?.error?.code ?? null, shape: shape(dev.json) });
const act = await call('PUT', '/api/notifications/apns/live-activity', {
  build: NEXT,
  body: { ...device_body, push_to_start_token: 'cd'.repeat(32), update_token: null },
});
record('next:live_activity_report', { status: act.status, code: act.json?.error?.code ?? null, shape: shape(act.json) });
const ar = await call('GET', '/api/notifications/settings', { build: NEXT });
record('next:notification_settings', { status: ar.status, shape: shape(ar.json) });
writeFileSync(out, JSON.stringify(results, null, 2));
for (const r of results)
  console.log(r.name, r.status ?? '', r.fate ?? r.code ?? r.state ?? r.content_type ?? '', r.held_prompts ?? '', r.fates ? JSON.stringify(r.fates) : '');
