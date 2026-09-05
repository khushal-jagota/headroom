import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { writeFile, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// Frontend proof with HTTP fixtures: the real Ticket route writes one document,
// and the real Review card reads that document. No live backend is involved.
const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const stem = `.guidance-${process.pid}`;
const host = join(root, 'tests', `${stem}.svelte`);
const main = join(root, 'tests', `${stem}.ts`);
const index = join(root, 'tests', `${stem}.html`);
let server;
try {
  await writeFile(host, `<script lang="ts">
import { QueryClient, QueryClientProvider } from '@tanstack/svelte-query';
import TicketRoute from '../src/routes/TicketRoute.svelte';
import ReviewProposalCard from '../src/components/ReviewProposalCard.svelte';
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
let review = $state(false);
(window as any).__showReview = () => { review = true; };
</script>
<QueryClientProvider {client}>
{#if review}<ReviewProposalCard ticketId="t_guidance" field="success" />
{:else}<TicketRoute id="t_guidance" />{/if}
</QueryClientProvider>`);
  await writeFile(main, `import { mount } from 'svelte'; import Host from './${stem}.svelte'; mount(Host, {target: document.getElementById('app')!});`);
  await writeFile(index, `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);
  server = await createServer({ root, configFile: false, plugins: [svelte()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  await server.listen();
  const address = server.httpServer.address();
  assert.ok(address && typeof address === 'object');
  const script = String.raw`
import json, sys
from playwright.sync_api import sync_playwright, expect

ticket = dict(id='t_guidance', title='Guidance ticket', worker_type='coding', employee_backend='codex', employee_launch_model=None, employee_launch_reasoning_effort=None, employee_configuration_editable=False, stage='needs_success', ceiling='needs_success', at_cap='propose', suggested_next_ceiling='done', priority='P2', resolved_priority_anchors={}, ticket_status='awaiting_approval', backend_error=None, stage_ownership_overrides={}, default_stage_ownership_mode='worker', effective_stage_ownership_mode='worker', conversation_id=None, conversation_history=[], day_ids=[], blocked=False, blocker_summary={'blocked_by': [], 'is_blocked': False}, recap='Orientation', guidance='Original **constraint**', verdict=None, trouble_notes=[], field_values={'kickoff': 'Request'}, pending_proposal={'field': 'success', 'body': 'A result', 'proposed_by': 'agent', 'created_at': 1}, archived_field_content='Old **unapproved** draft')
stages = [dict(id=s, label=l, gating_field=f, is_terminal=f is None, default_ownership_mode='worker' if f else None) for s,l,f in [('needs_kickoff','Kickoff','kickoff'),('needs_success','Success','success'),('done','Done',None)]]
manifest = {'worker_types': [dict(worker_type='coding', label='Coding', stages=stages, dropped=dict(id='dropped', label='Dropped', gating_field=None, is_terminal=True, default_ownership_mode=None), advance={'needs_kickoff': 'needs_success', 'needs_success': 'done'}, ceiling_range=['needs_kickoff','needs_success','done'], default_ceiling='needs_success', worker_profile_id='panels-worker-coding', default_backend='codex', default_model=None, default_reasoning_effort=None, fields=[{'id':'kickoff','label':'Kickoff'}, {'id':'success','label':'Success'}])]}
writes=[]
def respond(route):
    path=route.request.url.split('/api/',1)[1]
    if path == 'tickets/t_guidance/guidance':
        assert route.request.method == 'PUT'
        body=route.request.post_data_json
        assert set(body) == {'body'}
        writes.append(body)
        ticket['guidance']=body['body']
        result=ticket
    elif path == 'tickets/t_guidance/proposal':
        assert route.request.method == 'PUT'
        body=route.request.post_data_json
        assert set(body) == {'field', 'body'}
        writes.append({'proposal': body})
        ticket['pending_proposal']['body']=body['body']
        result=ticket
    elif path == 'tickets/t_guidance/value/kickoff':
        assert route.request.method == 'PUT'
        body=route.request.post_data_json
        assert set(body) == {'body'}
        writes.append({'value': body})
        ticket['field_values']['kickoff']=body['body']
        result=ticket
    elif path == 'tickets/t_guidance': result=ticket
    elif path == 'worker-types': result=manifest
    elif path == 'conversations/backends': result={'backends': []}
    elif path == 'projects': result={'projects': []}
    elif path.startswith('sprint-items'): result={'items': []}
    else: result={}
    route.fulfill(status=200, content_type='application/json', body=json.dumps(result))
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page()
    page.on('pageerror', lambda e: print(str(e), flush=True))
    page.route('**/api/**', respond)
    page.goto(sys.argv[1])
    guidance=page.locator('[data-ticket-guidance]')
    expect(guidance).to_have_count(1)
    guidance.locator('summary').click()
    expect(guidance).to_contain_text('Original constraint')
    history=page.locator('[data-ticket-history]')
    expect(history).to_have_count(1)
    assert not history.get_by_text('Old unapproved draft').is_visible()
    history.locator('summary').click()
    expect(history).to_contain_text('Old unapproved draft')
    assert history.locator('[contenteditable]').count() == 0
    assert page.locator('[data-accept]').count() == 1
    proposal=page.locator('[data-field="success"] [contenteditable]')
    proposal.click()
    proposal.fill('Edited pending result')
    page.locator('[data-field="success"] summary').click()
    page.wait_for_function("document.body.textContent.includes('Edited pending result')")
    page.locator('[data-stage-fold]').evaluate('(element) => element.open = true')
    page.locator('[data-field="kickoff"]').evaluate('(element) => element.open = true')
    saved=page.locator('[data-field="kickoff"] [contenteditable]')
    saved.click()
    saved.fill('Edited saved kickoff')
    saved.press('Tab')
    page.wait_for_function("document.body.textContent.includes('kickoff')")
    assert page.locator('summary').filter(has_text='Notes').count() == 0
    editor=guidance.locator('[contenteditable]')
    editor.click()
    editor.fill('Updated boundary')
    guidance.locator('summary').click()
    page.wait_for_function("document.querySelector('[data-ticket-guidance]').textContent.includes('Updated boundary')")
    assert writes == [
        {'proposal': {'field': 'success', 'body': 'Edited pending result'}},
        {'value': {'body': 'Edited saved kickoff'}},
        {'body': 'Updated boundary'},
    ]
    page.evaluate('window.__showReview()')
    expect(page.locator('[data-ticket-guidance]')).to_have_count(1)
    guidance=page.locator('[data-ticket-guidance]')
    guidance.locator('summary').click()
    expect(guidance).to_contain_text('Updated boundary')
    assert guidance.locator('[contenteditable]').count() == 0
    expect(page.locator('[data-approval-block][data-field="success"]')).to_contain_text('Edited pending result')
    assert page.locator('[data-accept]').count() == 1
    browser.close()
`;
  const child = spawn(join(root, '..', '.venv', 'bin', 'python'), ['-c', script, `http://127.0.0.1:${address.port}/tests/${stem}.html`], { stdio: 'inherit' });
  const code = await new Promise(resolve => child.on('exit', resolve));
  assert.equal(code, 0, 'Ticket guidance browser proof failed');
} finally {
  await server?.close();
  await Promise.all([host, main, index].map(path => rm(path, { force: true })));
}
