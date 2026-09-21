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
let ticketId = $state('t_guidance');
(window as any).__showReview = () => { review = true; };
(window as any).__showTicket = (id: string) => { review = false; ticketId = id; };
</script>
<QueryClientProvider {client}>
{#if review}<ReviewProposalCard ticketId="t_guidance" field="success_condition" />
{:else}{#key ticketId}<TicketRoute id={ticketId} />{/key}{/if}
</QueryClientProvider>`);
  await writeFile(main, `import { mount } from 'svelte'; import Host from './${stem}.svelte'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host, {target: document.getElementById('app')!});`);
  await writeFile(index, `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);
  server = await createServer({ root, configFile: false, plugins: [svelte()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  await server.listen();
  const address = server.httpServer.address();
  assert.ok(address && typeof address === 'object');
  const script = String.raw`
import json, sys
from urllib.parse import parse_qs
from playwright.sync_api import sync_playwright, expect

ticket = dict(project_id='project_one', project='One', sprint_id='sp_old', sprint_item_id='outcome_kept', id='t_guidance', title='Guidance ticket', worker_type='coding', employee_backend='codex', employee_launch_model=None, employee_launch_reasoning_effort=None, employee_configuration_editable=False, stage='needs_success_condition', ceiling='needs_success_condition', priority='P2', resolved_priority_anchors={'project': {'id': 'project_one', 'name': 'One', 'priority': 'P2'}, 'sprint_item': {'id': 'outcome_kept', 'title': 'Kept outcome', 'priority': 'P2'}}, ticket_status='awaiting_approval', ceiling_holder={'kind': 'chief', 'id': 'chief'}, conversation_id=None, conversation_history=[], day_ids=[], blocked=False, blocker_summary={'blocked_by': []}, recap='Orientation', guidance='Original **constraint**', field_values={'brief': 'Request'}, pending_proposal={'field': 'success_condition', 'body': 'A result', 'proposed_by': 'agent', 'created_at': 1})
stages = [dict(id=s, label=l, gating_field=f, is_terminal=f is None, ownership_mode='worker' if f else None) for s,l,f in [('needs_brief','Kickoff','brief'),('needs_success_condition','Success','success_condition'),('done','Done',None)]]
personal_stages = [dict(id=s, label=l, gating_field=f, is_terminal=f is None, ownership_mode=o) for s,l,f,o in [('needs_brief','Kickoff','brief','user'),('needs_outcome','Outcome','outcome','user'),('needs_consequences','Closeout','consequences','worker'),('done','Done',None,None)]]
manifest = {'worker_types': [dict(worker_type='coding', label='Coding', stages=stages, advance={'needs_brief': 'needs_success_condition', 'needs_success_condition': 'done'}, ceiling_range=['needs_brief','needs_success_condition','done'], default_ceiling='needs_success_condition', worker_profile_id='panels-worker-coding', default_backend='codex', default_model=None, default_reasoning_effort=None, fields=[{'id':'brief','label':'Kickoff'}, {'id':'success_condition','label':'Success'}]), dict(worker_type='personal', label='Personal', stages=personal_stages, advance={'needs_brief':'needs_outcome','needs_outcome':'needs_consequences','needs_consequences':'done'}, ceiling_range=['needs_brief','needs_outcome','needs_consequences','done'], default_ceiling='needs_brief', worker_profile_id='panels-worker-personal-task', default_backend='codex', default_model=None, default_reasoning_effort=None, fields=[{'id':'brief','label':'Kickoff'},{'id':'outcome','label':'Outcome'},{'id':'consequences','label':'Closeout'}])]}
writes=[]
placement_writes=[]
ceiling_writes=[]
approvals=[]
def respond(route):
    path=route.request.url.split('/api/',1)[1]
    # A single object is read through its collection address now: tickets?detail=full&id=X.
    if path.startswith('tickets?'):
        path='tickets/'+parse_qs(path.split('?',1)[1])['id'][0]
    if path == 'tickets/t_guidance':
        if route.request.method == 'PATCH':
            body=route.request.post_data_json
            assert not ({'project_id', 'sprint_id', 'sprint_item_id'} & set(body))
            if 'guidance' in body:
                writes.append({'guidance': body['guidance']})
                ticket['guidance']=body['guidance']
            elif 'field_values' in body:
                writes.append({'value': body['field_values']})
                ticket['field_values'].update(body['field_values'])
            elif 'ceiling' in body or 'ceiling_holder' in body:
                ceiling_writes.append(body)
                ticket.update(body)
            else:
                placement_writes.append(body)
                ticket.update(body)
        result=ticket
    elif path == 'tickets/t_personal/complete/outcome':
        assert route.request.method == 'POST'
        body=route.request.post_data_json
        writes.append({'personal_outcome': body})
        result={**ticket, 'id':'t_personal', 'worker_type':'personal', 'stage':'needs_consequences', 'ceiling':'needs_consequences', 'field_values':{'brief':'Context','outcome':body['body']}, 'pending_proposal':None, 'ticket_status':'empty'}
    elif path == 'tickets/t_personal':
        result={**ticket, 'id':'t_personal', 'worker_type':'personal', 'stage':'needs_outcome', 'ceiling':'needs_outcome', 'field_values':{'brief':'Context'}, 'pending_proposal':None, 'ticket_status':'empty'}
    elif path == 'tickets/t_kickoff':
        result={**ticket, 'id': 't_kickoff', 'stage': 'needs_brief', 'ceiling': 'needs_brief', 'ceiling_holder': {'kind': 'chief', 'id': 'chief'}, 'sprint_item_id': None, 'resolved_priority_anchors': {**ticket['resolved_priority_anchors'], 'sprint_item': None}, 'pending_proposal': {'field': 'brief', 'body': 'Opened for somebody else', 'proposed_by': 'chief', 'created_at': 1}, 'field_values': {}}
    elif path == 'tickets/t_open':
        result={**ticket, 'id': 't_open', 'ticket_status': 'empty', 'pending_proposal': None, 'ceiling_holder': {'kind': 'owner', 'id': 'owner'}}
    elif path == 'tickets/t_done':
        result={**ticket, 'id': 't_done', 'stage': 'done', 'ticket_status': 'empty', 'pending_proposal': None}
    elif path.endswith('/accept/success_condition'):
        assert route.request.method == 'POST'
        approvals.append(route.request.post_data_json)
        result={**ticket, 'pending_proposal': None, 'ticket_status': 'empty'}
    elif path == 'worker-types': result=manifest
    elif path == 'conversation/backends': result={'backends': []}
    else: result={}
    route.fulfill(status=200, content_type='application/json', body=json.dumps(result))
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page()
    page.on('pageerror', lambda e: print(str(e), flush=True))
    page.route('**/api/**', respond)
    page.goto(sys.argv[1])
    expect(page.locator('[data-project-fact]')).to_have_text('One')
    expect(page.locator('[data-outcome-fact]')).to_have_text('Kept outcome')
    assert page.locator('[data-project-control], [data-outcome-control], [data-sprint-control]').count() == 0
    assert page.get_by_label('Ticket project').count() == 0
    assert page.get_by_label('Ticket outcome').count() == 0
    assert page.get_by_label('Ticket Sprint').count() == 0
    assert page.locator('[data-ticket-guidance], [data-ticket-history]').count() == 0
    assert page.get_by_text('Old unapproved draft').count() == 0
    # A parked proposal freezes how far the Ticket may go and not who is asked, so the
    # leash is here with only its holder half. This Ticket is held by the Chief, which is
    # the state that had no control at all: a proposal in an agent's queue, and no way to
    # move it to Khushal's.
    parked_leash=page.locator('[data-leash]')
    assert parked_leash.count() == 1
    assert 'then Chief' in parked_leash.locator('[data-leash-face]').inner_text()
    assert 'Until' not in parked_leash.locator('[data-leash-face]').inner_text()
    parked_leash.locator(':scope > summary').click()
    assert parked_leash.locator('[data-scope-ceiling]').count() == 0
    assert parked_leash.locator('[data-scope-holder]').count() == 1
    parked_leash.locator('[data-scope-holder]').select_option('owner')
    for _ in range(100):
        if ceiling_writes: break
        page.wait_for_timeout(50)
    assert ceiling_writes == [{'ceiling_holder': {'kind': 'owner', 'id': 'owner'}}]

    page.evaluate("window.__showTicket('t_open')")
    page.locator('[data-ticket-id="t_open"]').wait_for()
    leash=page.locator('[data-leash]')
    # No proposal parked, so the whole ceiling is here: the stage and the holder.
    assert 'Until Success' in leash.locator('[data-leash-face]').inner_text()
    assert 'then me' in leash.locator('[data-leash-face]').inner_text()
    leash.locator(':scope > summary').click()
    assert leash.locator('select').count() == 2
    assert leash.locator('[data-scope-ceiling]').count() == 1
    assert leash.locator('[data-scope-holder]').count() == 1
    assert leash.locator('button').count() == 0
    assert placement_writes == []
    def assert_ticket_layout(width):
        page.set_viewport_size({'width': width, 'height': 900})
        page.wait_for_timeout(50)
        assert page.evaluate("""() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) <= window.innerWidth + 1""")
        identity_box=page.locator('[data-ticket-identity]').bounding_box()
        title_box=page.locator('.ticket-title-row').bounding_box()
        menu_box=page.locator('.ticket-leash-menu').bounding_box()
        assert identity_box and title_box and menu_box
        assert identity_box['y'] + identity_box['height'] <= title_box['y'] + 1
        assert menu_box['x'] >= -1
        assert menu_box['x'] + menu_box['width'] <= width + 1
    assert_ticket_layout(1280)
    assert_ticket_layout(390)
    page.set_viewport_size({'width': 1280, 'height': 900})
    page.evaluate("window.__showTicket('t_guidance')")
    page.locator('[data-ticket-id="t_guidance"]').wait_for()
    assert page.locator('[data-accept]').count() == 1
    proposal=page.locator('[data-field="success_condition"] [contenteditable]')
    proposal.click()
    proposal.fill('Edited pending result')
    page.locator('[data-field="success_condition"] summary').click()
    page.wait_for_function("document.body.textContent.includes('Edited pending result')")
    page.locator('[data-stage-fold]').evaluate('(element) => element.open = true')
    page.locator('[data-field="brief"]').evaluate('(element) => element.open = true')
    saved=page.locator('[data-field="brief"] [contenteditable]')
    saved.click()
    saved.fill('Edited saved kickoff')
    saved.press('Tab')
    page.wait_for_function("document.body.textContent.includes('kickoff')")
    assert page.locator('summary').filter(has_text='Notes').count() == 0
    # The proposal edit stays in the browser until it is approved: a proposal has two
    # outcomes and no third door, so nothing is written back over the author's text.
    assert writes == [{'value': {'brief': 'Edited saved kickoff'}}]
    page.evaluate("window.__showTicket('t_personal')")
    page.locator('[data-ticket-id="t_personal"]').wait_for()
    personal_outcome=page.locator('[data-field="outcome"] [contenteditable]')
    expect(personal_outcome).to_have_count(1)
    assert page.locator('[data-field="consequences"] [contenteditable]').count() == 0
    personal_outcome.fill('Completed by user')
    personal_outcome.press('Tab')
    page.wait_for_function("document.body.textContent.includes('Completed by user')")
    assert writes[-1] == {'personal_outcome': {'body': 'Completed by user'}}
    page.evaluate("window.__showTicket('t_kickoff')")
    page.locator('[data-ticket-id="t_kickoff"]').wait_for()
    expect(page.locator('[data-project-fact]')).to_have_text('One')
    assert page.locator('[data-outcome-fact]').count() == 0
    assert page.locator('[data-copy]').count() == 0
    # A Ticket opened for somebody else parks its Kickoff in their queue. That is the
    # first moment a proposal can sit in the wrong place, so the holder half is here too.
    kickoff_leash=page.locator('[data-leash]')
    assert kickoff_leash.count() == 1
    assert kickoff_leash.locator('[data-leash-face]').inner_text().strip() == 'then Chief'
    kickoff_leash.locator(':scope > summary').click()
    assert kickoff_leash.locator('[data-scope-ceiling]').count() == 0
    assert kickoff_leash.locator('[data-scope-holder]').count() == 1
    # No Sprint Item on this one, so the Item option is absent.
    assert [option.inner_text() for option in kickoff_leash.locator('[data-scope-holder] option').all()] == ['me', 'Chief']
    page.evaluate("window.__showTicket('t_done')")
    page.locator('[data-ticket-id="t_done"]').wait_for()
    assert page.locator('[data-copy]').count() == 0
    assert 'Copy' not in page.locator('.ticket-operating').inner_text()
    page.evaluate('window.__showReview()')
    expect(page.locator('[data-ticket-guidance]')).to_have_count(1)
    guidance=page.locator('[data-ticket-guidance]')
    guidance.locator('summary').click()
    expect(guidance).to_contain_text('Original constraint')
    assert guidance.locator('[contenteditable]').count() == 0
    # Review shows what the author wrote. The edit on the Ticket page was never saved
    # over it, because an edited proposal is approved as the edit or it is nothing.
    expect(page.locator('[data-approval-block][data-field="success_condition"]')).to_contain_text('A result')
    assert page.locator('[data-accept]').count() == 1
    # Approving sets the next ceiling, so it offers the same two choices the leash does.
    # The holder used to be hard-coded to the user here, so a proposal approved in the
    # browser could only ever stay in Khushal's own queue.
    approve_row=page.locator('[data-approval-block][data-field="success_condition"] .approval-control-group')
    assert approve_row.locator('[data-scope-ceiling]').count() == 1
    holder_select=approve_row.locator('[data-scope-holder]')
    assert holder_select.input_value() == 'owner'
    assert [option.inner_text() for option in holder_select.locator('option').all()] == [
        'me', 'Chief', 'Kept outcome'
    ]
    holder_select.select_option('chief')
    page.locator('[data-approval-block][data-field="success_condition"] [data-accept]').click()
    for _ in range(100):
        if approvals: break
        page.wait_for_timeout(50)
    assert len(approvals) == 1
    assert approvals[0]['next_holder'] == {'kind': 'chief', 'id': 'chief'}
    browser.close()
`;
  const child = spawn(join(root, '..', '.venv', 'bin', 'python'), ['-c', script, `http://127.0.0.1:${address.port}/tests/${stem}.html`], { stdio: 'inherit' });
  const code = await new Promise(resolve => child.on('exit', resolve));
  assert.equal(code, 0, 'Ticket guidance browser proof failed');
} finally {
  await server?.close();
  await Promise.all([host, main, index].map(path => rm(path, { force: true })));
}
