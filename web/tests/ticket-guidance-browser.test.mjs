import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { writeFile, rm, readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { scratchDirectory } from './support/scratch.mjs';

// Frontend proof with HTTP fixtures: the real Ticket route writes one document,
// and the real Review card reads that document. No live backend is involved.
const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const stem = `guidance-${process.pid}`;
const scratchRoot = await scratchDirectory();
const host = join(scratchRoot, `${stem}.svelte`);
const main = join(scratchRoot, `${stem}.ts`);
const index = join(scratchRoot, `${stem}.html`);
let server;
try {
  await writeFile(host, `<script lang="ts">
import { QueryClient, QueryClientProvider } from '@tanstack/svelte-query';
import TicketRoute from '../src/routes/TicketRoute.svelte';
import ReviewRoute from '../src/routes/ReviewRoute.svelte';
import { reviewBadgeDomMatches } from '../src/lib/navigation/render';
(window as any).__badgeProof=()=>{
 const link=document.createElement('a');const badge=document.createElement('span');link.append(badge);
 badge.className='nav-badge hidden';
 const emptyHidden=reviewBadgeDomMatches(link,0);
 badge.classList.remove('hidden');const emptyVisible=reviewBadgeDomMatches(link,0);
 badge.classList.add('hidden');badge.textContent='2';const nonemptyHidden=reviewBadgeDomMatches(link,0);
 badge.classList.remove('hidden');const positiveVisible=reviewBadgeDomMatches(link,2);
 const wrongCount=reviewBadgeDomMatches(link,1);
 badge.classList.add('hidden');const positiveHidden=reviewBadgeDomMatches(link,2);
 badge.remove();const absent=reviewBadgeDomMatches(link,0);
 return {emptyHidden,emptyVisible,nonemptyHidden,positiveVisible,wrongCount,positiveHidden,absent};
};
import ShellStatus from '../src/components/ShellStatus.svelte';
import { provideNavigationApplication } from '../src/lib/navigation/application.svelte';
const navigation=provideNavigationApplication();
(window as any).__navigation=navigation;
(window as any).__refresh=()=>navigation.owner.recover().catch(()=>undefined);
(window as any).__batch=async()=>{
 const response=await (await fetch('/api/navigation/snapshot')).json();
 const previous=navigation.owner.getState().publication!;
 const batch={...response.snapshot,base_revision:previous.continuity.revision,upserts:response.snapshot.records,deletes:[]};delete batch.records;
 const published=new Promise<void>(resolve=>{const unsubscribe=navigation.owner.subscribe(state=>{if(state.publication?.continuity.revision===batch.revision){unsubscribe();resolve();}});});
 (window as any).__navigationSource.dispatchEvent(new MessageEvent('batch',{data:JSON.stringify({wire_version:response.wire_version,authority:response.authority,batch}),lastEventId:response.cursor}));
 await published;
};
(window as any).__wait=()=>{
 const publication=navigation.owner.getState().publication!;
 return navigation.owner.waitForVisibility({...publication.continuity,version:1,coverage:'complete',witnesses:['a'.repeat(64)],signature:'synthetic'},
 {requiredObservers:navigation.captureRenderBoundaries(),timeoutMs:250});
};
import ConfigRoute from '../src/routes/ConfigRoute.svelte';
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
(window as any).__manifest=()=>client.invalidateQueries({queryKey:['worker-types']});
let review = $state(false);
let config = $state(false);
let ticketId = $state('t_guidance');
(window as any).__showReview = () => { review = true; };
(window as any).__showTicket = (id: string) => { review = false; ticketId = id; };
(window as any).__showConfig = () => { config = true; };
</script>
<QueryClientProvider {client}>
{#if config}<ConfigRoute roleKind="worker" roleId="coding" />
{:else if review}<ShellStatus connectionState="connected" /><ReviewRoute />
{:else}{#key ticketId}<TicketRoute id={ticketId} />{/key}{/if}
</QueryClientProvider>`);
  await writeFile(main, `globalThis.EventSource=class extends EventTarget{onerror=null;constructor(url){super();if(String(url).startsWith('/api/navigation/events?'))globalThis.__navigationSource=this;}close(){}}; import { mount } from 'svelte'; import Host from './${stem}.svelte'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host, {target: document.getElementById('app')!});`);
  await writeFile(index, `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);
  server = await createServer({ root, configFile: false, plugins: [svelte()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  await server.listen();
  const address = server.httpServer.address();
  assert.ok(address && typeof address === 'object');
  const fixturePython=await readFile(join(root,'tests/support/navigation/browser-fixture.py'),'utf8');
  const script = fixturePython + String.raw`
import json, sys
from urllib.parse import parse_qs
from playwright.sync_api import sync_playwright, expect

ticket = dict(project_id='project_one', project='One', sprint_id='sp_old', sprint_item_id='outcome_kept', id='t_guidance', title='Guidance ticket', worker_type='coding', employee_backend='codex', employee_launch_model=None, employee_launch_reasoning_effort=None, employee_configuration_editable=False, stage='needs_success_condition', ceiling='needs_success_condition', priority='P2', resolved_priority_anchors={'project': {'id': 'project_one', 'name': 'One', 'priority': 'P2'}, 'sprint_item': {'id': 'outcome_kept', 'title': 'Kept outcome', 'priority': 'P2'}}, ticket_status='awaiting_approval', ceiling_holder={'kind': 'chief', 'id': 'chief'}, conversation_id=None, conversation_history=[], day_ids=[], blocked=False, blocker_summary={'blocked_by': []}, recap='Orientation', guidance='Original **constraint**', field_values={'brief': 'Request'}, pending_proposal={'field': 'success_condition', 'body': 'A result', 'proposed_by': 'agent', 'created_at': 1, 'revision': 1})
stages = [dict(id=s, label=l, gating_field=f, is_terminal=f is None, ownership_mode='worker' if f else None) for s,l,f in [('needs_brief','Kickoff','brief'),('needs_success_condition','Success','success_condition'),('done','Done',None)]]
personal_stages = [dict(id=s, label=l, gating_field=f, is_terminal=f is None, ownership_mode=o) for s,l,f,o in [('needs_brief','Kickoff','brief','user'),('needs_outcome','Outcome','outcome','user'),('needs_consequences','Closeout','consequences','worker'),('done','Done',None,None)]]
manifest = {'worker_types': [dict(worker_type='coding', label='Coding', stages=stages, advance={'needs_brief': 'needs_success_condition', 'needs_success_condition': 'done'}, ceiling_range=['needs_brief','needs_success_condition','done'], default_ceiling='needs_success_condition', worker_profile_id='panels-worker-coding', default_backend='codex', default_model=None, default_reasoning_effort=None, fields=[{'id':'brief','label':'Kickoff'}, {'id':'success_condition','label':'Success'}]), dict(worker_type='personal', label='Personal', stages=personal_stages, advance={'needs_brief':'needs_outcome','needs_outcome':'needs_consequences','needs_consequences':'done'}, ceiling_range=['needs_brief','needs_outcome','needs_consequences','done'], default_ceiling='needs_brief', worker_profile_id='panels-worker-personal-task', default_backend='codex', default_model=None, default_reasoning_effort=None, fields=[{'id':'brief','label':'Kickoff'},{'id':'outcome','label':'Outcome'},{'id':'consequences','label':'Closeout'}])]}
worker_detail = {'manifest': manifest['worker_types'][0], 'settings': {'worker_type': 'coding', 'specialist_skill': {'name': 'panels-worker-coding', 'description': 'Coding worker', 'markdown_body': '# Coding'}, 'launch_defaults': {'employee_backend': 'codex', 'employee_launch_model': None, 'employee_launch_reasoning_effort': None}}}
writes=[]
placement_writes=[]
ceiling_writes=[]
approvals=[]
held_details=[]
hold_detail=False
snapshot_unavailable=False
runtime_working=False
server_revision=None
navigation_proposal=None
# This host retains all old Ticket assertions and adds the actual Review route.
def review_snapshot():
    response=navigation_response({'columns':[{'cards':[ticket]}],'sprint_items':[]},
      {'chief_of_staff':{'label':'Chief of Staff','conversation_id':None}})
    for record in response['snapshot']['records']:
        if record['kind']=='ticket':
            proposal=navigation_proposal or ticket['pending_proposal']
            record['value'].update(ceiling_holder=ticket['ceiling_holder'],gating_field='success_condition',
              proposal_field=proposal['field'] if proposal else None,proposal_created_at=proposal['created_at'] if proposal else None,proposal_revision=proposal['revision'] if proposal else 0)
        elif record['kind']=='conversation':
            record['value']['attention']['current']['signal']['working']=runtime_working
    return response

def respond(route):
    global navigation_proposal
    path=route.request.url.split('/api/',1)[1]
    # A single object is read through its collection address now: tickets?detail=full&id=X.
    if path.startswith('tickets?'):
        path='tickets/'+parse_qs(path.split('?',1)[1])['id'][0]
    if path.split('?')[0] == 'navigation/snapshot':
        if snapshot_unavailable:
            route.fulfill(status=503,content_type='application/json',body=json.dumps({'error':{'code':'unavailable','message':'Workspace unavailable','detail':None}}))
            return
        result=review_snapshot()
    elif path == 'review': raise AssertionError('competing review summary query remains')
    elif path.startswith('navigation/events'): return
    elif path == 'tickets/t_guidance':
        if hold_detail and route.request.method=='GET':
            held_details.append(route)
            return
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
    elif path == 'tickets/t_open' and route.request.method == 'PATCH':
        body=route.request.post_data_json
        ceiling_writes.append(body)
        result={**ticket, 'id': 't_open', 'ticket_status': 'empty', 'pending_proposal': None}
    elif path == 'tickets/t_personal/complete/outcome':
        assert route.request.method == 'POST'
        body=route.request.post_data_json
        writes.append({'personal_outcome': body})
        result={**ticket, 'id':'t_personal', 'worker_type':'personal', 'stage':'needs_consequences', 'ceiling':'needs_consequences', 'field_values':{'brief':'Context','outcome':body['body']}, 'pending_proposal':None, 'ticket_status':'empty', 'assigned':False}
    elif path == 'tickets/t_personal':
        # assigned is what the route reads to decide the owner may edit this Stage.
        # The server puts it on every Ticket payload, so this stand-in carries it too.
        result={**ticket, 'id':'t_personal', 'worker_type':'personal', 'stage':'needs_outcome', 'ceiling':'needs_outcome', 'field_values':{'brief':'Context'}, 'pending_proposal':None, 'ticket_status':'empty', 'assigned':True}
    elif path == 'tickets/t_kickoff':
        result={**ticket, 'id': 't_kickoff', 'stage': 'needs_brief', 'ceiling': 'needs_brief', 'ceiling_holder': {'kind': 'chief', 'id': 'chief'}, 'sprint_item_id': None, 'resolved_priority_anchors': {**ticket['resolved_priority_anchors'], 'sprint_item': None}, 'pending_proposal': {'field': 'brief', 'body': 'Opened for somebody else', 'proposed_by': 'chief', 'created_at': 1}, 'field_values': {}}
    elif path == 'tickets/t_with_session':
        result={**ticket, 'id': 't_with_session', 'conversation_id':'existing-worker-session'}
    elif path == 'tickets/t_open':
        result={**ticket, 'id': 't_open', 'ticket_status': 'empty', 'pending_proposal': None, 'ceiling_holder': {'kind': 'owner', 'id': 'owner'}}
    elif path == 'tickets/t_done':
        result={**ticket, 'id': 't_done', 'stage': 'done', 'ticket_status': 'empty', 'pending_proposal': None}
    elif path.endswith('/accept/success_condition'):
        assert route.request.method == 'POST'
        approval=route.request.post_data_json
        if server_revision is not None and approval['expected_proposal_revision']!=server_revision:
            route.fulfill(status=409,content_type='application/json',body=json.dumps({'error':{'code':'validation','message':'the visible proposal changed; read it again before accepting','detail':None}}))
            return
        approvals.append(approval)
        ticket.update(pending_proposal=None,ticket_status='empty')
        navigation_proposal=None
        result=ticket
    elif path == 'worker-types': result=manifest
    elif path == 'workers/coding': result=worker_detail
    elif path == 'conversation/backends': result={'backends': []}
    else: result={}
    route.fulfill(status=200, content_type='application/json', body=json.dumps(result))
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page()
    page.on('pageerror', lambda e: print(str(e), flush=True))
    page.route('**/api/**', respond)
    page.goto(sys.argv[1])
    assert page.evaluate('window.__badgeProof()')==dict(emptyHidden=True,emptyVisible=False,nonemptyHidden=False,positiveVisible=True,wrongCount=False,positiveHidden=False,absent=False)
    expect(page.locator('[data-project-fact]')).to_have_text('One')
    expect(page.locator('[data-ticket-identity]')).to_have_text('One · Coding')
    expect(page.locator('[data-parent-link]')).to_have_count(0)
    assert page.locator('[data-project-control], [data-outcome-control], [data-sprint-control]').count() == 0
    assert page.get_by_label('Ticket project').count() == 0
    assert page.get_by_label('Ticket outcome').count() == 0
    assert page.get_by_label('Ticket Sprint').count() == 0
    assert page.locator('[data-ticket-guidance], [data-ticket-history]').count() == 0
    assert page.get_by_text('Old unapproved draft').count() == 0
    # A later step with no Worker session cannot deliver a revision; do not offer an invalid send.
    expect(page.locator('[data-send-back]')).to_be_disabled()
    expect(page.locator('[data-return-unavailable]')).to_contain_text('No existing Worker conversation')
    # Waiting uses the same editable stage+reviewer control as running. Scope-only
    # rerouting preserves the pending body; choices past the boundary announce approval.
    parked_leash=page.locator('[data-approval-block]')
    expect(parked_leash.locator('[data-scope-ceiling]')).to_have_count(0)
    parked_leash.locator('[data-reviewer-heading]').click()
    expect(parked_leash.get_by_role('listbox')).to_have_attribute('aria-label', 'Who holds the ceiling')
    assert parked_leash.locator('[data-ceiling-picker-locked]').count() == 0
    assert not parked_leash.locator('[data-ceiling-part="ceiling"]').is_disabled()
    parked_leash.locator('[data-ceiling-part="holder"]').click()
    parked_leash.get_by_role('listbox').press('u')
    assert ceiling_writes == []
    parked_leash.get_by_role('listbox').press('Enter')
    for _ in range(100):
        if ceiling_writes: break
        page.wait_for_timeout(50)
    assert ceiling_writes == [{'ceiling_holder': {'kind': 'owner', 'id': 'owner'}}]
    assert approvals == []

    page.evaluate("window.__showTicket('t_open')")
    page.locator('[data-ticket-id="t_open"]').wait_for()
    leash=page.locator('[data-leash]')
    # No proposal parked, so the whole ceiling is here: the stage and the holder.
    expect(leash.locator('[data-scope-ceiling]')).to_have_attribute('aria-label', 'Approved until Success, then User reviews')
    assert leash.locator('select').count() == 0
    assert leash.locator('[data-scope-ceiling]').count() == 1
    assert leash.locator('[data-scope-holder]').count() == 0
    leash.locator('[data-scope-ceiling]').click()
    assert leash.locator('[data-ceiling-picker-panel]').count() == 1
    expect(leash.get_by_role('listbox')).to_have_attribute('aria-label', 'Ceiling stage')
    assert leash.locator('[data-ceiling-part="ceiling"] .picker-rail-row-detail').inner_text() == 'Success'
    assert leash.locator('[data-ceiling-part="holder"] .picker-rail-row-detail').inner_text() == 'User'
    # A stage chosen and then abandoned is a draft. Escape discards it: nothing is sent,
    # the shut trigger still reads the saved ceiling, and the list ticks it again.
    leash.get_by_role('listbox').press('End')
    leash.get_by_role('listbox').press('Enter')
    expect(leash.get_by_role('listbox')).to_have_attribute('aria-label', 'Who holds the ceiling')
    expect(leash.locator('[data-ceiling-part="ceiling"] .picker-rail-row-detail')).to_have_text('Done')
    leash.get_by_role('listbox').press('Escape')
    expect(leash.get_by_role('listbox')).to_have_count(0)
    page.wait_for_timeout(200)
    assert ceiling_writes == [{'ceiling_holder': {'kind': 'owner', 'id': 'owner'}}], ceiling_writes
    assert leash.locator('[data-scope-ceiling]').get_attribute('aria-label') == 'Approved until Success, then User reviews'
    leash.locator('[data-scope-ceiling]').click()
    expect(leash.get_by_role('listbox')).to_have_attribute('aria-label', 'Ceiling stage')
    assert leash.locator('[role="option"][aria-selected="true"]').get_attribute('data-ceiling-picker-choice') == 'needs_success_condition'
    # Each rail names its part and retains the selected draft as the other list opens.
    assert 'Approved until Success' in leash.locator('[data-scope-ceiling]').get_attribute('aria-label')
    assert 'then User reviews' in leash.locator('[data-scope-ceiling]').get_attribute('aria-label')
    leash.locator('[data-ceiling-part="holder"]').click()
    expect(leash.get_by_role('listbox')).to_have_attribute('aria-label', 'Who holds the ceiling')
    leash.locator('[data-ceiling-part="ceiling"]').click()
    expect(leash.get_by_role('listbox')).to_have_attribute('aria-label', 'Ceiling stage')
    leash.get_by_role('listbox').press('End')
    leash.get_by_role('listbox').press('Enter')
    # A stage choice still leads to the reviewer, so setting both stays one sequence.
    expect(leash.get_by_role('listbox')).to_have_attribute('aria-label', 'Who holds the ceiling')
    leash.get_by_role('listbox').press('c')
    leash.get_by_role('listbox').press('Enter')
    expect(leash.get_by_role('listbox')).to_have_count(0)
    for _ in range(100):
        if len(ceiling_writes) == 2: break
        page.wait_for_timeout(50)
    assert ceiling_writes[-1] == {'ceiling': 'done', 'ceiling_holder': {'kind': 'chief', 'id': 'chief'}}, ceiling_writes

    # A reviewer changed on its own writes the reviewer alone. The ceiling is not sent,
    # so a Ticket cannot have its ceiling moved by a reader who only changed the queue.
    leash.locator('[data-scope-ceiling]').click()
    leash.locator('[data-ceiling-part="holder"]').click()
    expect(leash.get_by_role('listbox')).to_have_attribute('aria-label', 'Who holds the ceiling')
    leash.locator('[data-ceiling-picker-choice="owner"]').click()
    expect(leash.get_by_role('listbox')).to_have_count(0)
    for _ in range(100):
        if len(ceiling_writes) == 3: break
        page.wait_for_timeout(50)
    assert ceiling_writes[-1] == {'ceiling_holder': {'kind': 'owner', 'id': 'owner'}}, ceiling_writes

    # A reviewer chosen and then abandoned is discarded too, and letters typed at one
    # half never join letters typed at the other.
    leash.locator('[data-scope-ceiling]').click()
    expect(leash.get_by_role('listbox')).to_have_attribute('aria-label', 'Ceiling stage')
    leash.get_by_role('listbox').press('d')
    leash.locator('[data-ceiling-part="holder"]').click()
    leash.get_by_role('listbox').press('c')
    assert leash.locator('[data-listbox-picker-active]').get_attribute('data-ceiling-picker-choice') == 'chief'
    leash.get_by_role('listbox').press('Escape')
    expect(leash.get_by_role('listbox')).to_have_count(0)
    page.wait_for_timeout(200)
    assert len(ceiling_writes) == 3, ceiling_writes
    assert leash.locator('[data-scope-ceiling]').get_attribute('aria-label') == 'Approved until Success, then User reviews'
    leash.locator('[data-scope-ceiling]').click()
    page.locator('.ticket-title').click()
    expect(leash.get_by_role('listbox')).to_have_count(0)
    assert page.locator('[data-copy], [data-ticket-takeover-toggle]').count() == 0
    assert placement_writes == []
    assert page.locator('.ticket-operating').count() == 0
    def assert_ticket_layout(width):
        page.set_viewport_size({'width': width, 'height': 900})
        page.wait_for_timeout(50)
        assert page.evaluate("""() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) <= window.innerWidth + 1""")
        identity_box=page.locator('[data-ticket-identity]').bounding_box()
        title_box=page.locator('.ticket-title').bounding_box()
        leash.locator('[data-scope-ceiling]').click()
        menu_box=leash.locator('[data-ceiling-picker-panel]').bounding_box()
        assert identity_box and title_box and menu_box
        assert identity_box['y'] + identity_box['height'] <= title_box['y'] + 1
        assert menu_box['x'] >= -1
        assert menu_box['x'] + menu_box['width'] <= width + 1
        assert page.evaluate("""() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) <= window.innerWidth + 1""")
        leash.get_by_role('listbox').press('Escape')
    assert_ticket_layout(1280)
    assert_ticket_layout(390)
    page.set_viewport_size({'width': 1280, 'height': 900})
    page.evaluate("window.__showTicket('t_guidance')")
    page.locator('[data-ticket-id="t_guidance"]').wait_for()
    assert page.locator('[data-accept]').count() == 1
    proposal=page.locator('[data-approval-block][data-field="success_condition"] [contenteditable]')
    proposal.click()
    proposal.fill('Edited pending result')
    proposal.press('Tab')
    page.wait_for_function("document.body.textContent.includes('Edited pending result')")
    page.locator('[data-stage-fold]').evaluate('(element) => element.open = true')
    page.locator('[data-stage-fold] [data-field="brief"]').evaluate('(element) => element.open = true')
    saved=page.locator('[data-stage-fold] [data-field="brief"] [contenteditable]')
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
    personal_outcome=page.locator('[data-worker-live-card] [data-field="outcome"] [contenteditable]')
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
    # Kickoff keeps its no-revision exception while both ceiling halves remain editable.
    kickoff_leash=page.locator('[data-approval-block]')
    expect(page.locator('[data-send-back]')).to_have_count(0)
    kickoff_leash.locator('[data-accept]').click()
    assert not kickoff_leash.locator('[data-ceiling-part="ceiling"]').is_disabled()
    kickoff_leash.locator('[data-ceiling-part="holder"]').click()
    assert [option.get_attribute('data-ceiling-picker-choice') for option in kickoff_leash.locator('[role="option"]').all()] == ['owner', 'chief']
    kickoff_leash.get_by_role('listbox').press('Escape')
    page.evaluate("window.__showTicket('t_with_session')")
    page.locator('[data-ticket-id="t_with_session"]').wait_for()
    expect(page.locator('[data-send-back]')).to_be_enabled()
    expect(page.locator('[data-return-unavailable]')).to_have_count(0)
    page.locator('[data-send-back]').click()
    expect(page.locator('[data-revision-note]')).to_be_visible()
    page.evaluate("window.__showTicket('t_done')")
    page.locator('[data-ticket-id="t_done"]').wait_for()
    assert page.locator('[data-copy]').count() == 0
    assert page.locator('.ticket-operating').count() == 0
    ticket['conversation_id']='review-worker'
    ticket['ceiling_holder']={'kind':'owner','id':'owner'}
    page.evaluate('window.__refresh()')
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
    # Review uses the same two-part gesture and reviewer-only route as Ticket.
    approve_row=page.locator('[data-approval-block][data-field="success_condition"]')
    expect(approve_row.locator('[data-scope-ceiling]')).to_have_count(0)
    approve_row.locator('[data-reviewer-heading]').click()
    expect(approve_row.get_by_role('listbox')).to_have_attribute('aria-label', 'Who holds the ceiling')
    assert [option.get_attribute('data-ceiling-picker-choice') for option in approve_row.locator('[role="option"]').all()] == ['owner', 'chief', 'outcome_kept']
    expect(approve_row.get_by_role('option', name='Orchestrator', exact=True)).to_have_count(1)
    approve_row.get_by_role('listbox').press('Escape')
    # Unchanged tuple publication retains local edits, selected reviewer and component state.
    retained_editor=page.locator('[data-approval-block] [data-edit]')
    retained_editor.click()
    retained_editor.fill('Retained local draft')
    retained_editor.press('Tab')
    page.evaluate('window.__batch()')
    expect(retained_editor).to_contain_text('Retained local draft')
    # New tuple below must reset this draft, not carry it into a replacement.
    approve_row.locator('[data-accept]').click()
    expect(approve_row.get_by_role('listbox')).to_have_attribute('aria-label', 'Ceiling stage')
    approve_row.locator('[data-ceiling-picker-choice="done"]').click()
    expect(approve_row.get_by_role('listbox')).to_have_attribute('aria-label', 'Who holds the ceiling')
    approve_row.locator('[data-ceiling-picker-choice="chief"]').click()
    for _ in range(100):
        if approvals: break
        page.wait_for_timeout(50)
    assert len(approvals) == 1
    assert approvals[0]['next_ceiling'] == 'done'
    assert approvals[0]['next_holder'] == {'kind': 'chief', 'id': 'chief'}
    assert approvals[0]['expected_proposal_revision'] == 1
    expect(page.locator('[data-review-card]')).to_have_count(0)
    # A later canonical proposal is a new ask, not resurrection of the accepted one.
    ticket.update(ticket_status='awaiting_approval',pending_proposal={'field':'success_condition','body':'A result','proposed_by':'agent','created_at':2,'revision':2})
    page.evaluate('window.__refresh()')
    expect(page.locator('[data-review-card]')).to_have_count(1)
    # A title-only canonical update must not retain the old detail title.
    navigation_proposal=dict(ticket['pending_proposal'])
    old_title=ticket['title']
    ticket['title']='Canonical Review title'
    page.evaluate('window.__refresh()')
    expect(page.locator('.review-ticket-title [contenteditable]')).to_have_text('Canonical Review title')
    # Stale manifest gate changes cannot settle or drop the still-current queue.
    for stage in manifest['worker_types'][0]['stages']:
        if stage['id']=='needs_success_condition': stage['gating_field']='plan'
    page.evaluate('window.__manifest()')
    expect(page.locator('[data-review-card]')).to_have_count(1)
    # New tuple first, old delayed detail second: old body cannot be relabeled.
    navigation_proposal={**ticket['pending_proposal'],'created_at':3,'revision':3,'body':'Replacement body'}
    hold_detail=True
    page.evaluate('window.__refresh()')
    expect(page.locator('[data-review-card]')).to_have_count(0)
    for _ in range(100):
        if held_details: break
        page.wait_for_timeout(10)
    assert held_details
    held_details.pop(0).fulfill(status=200,content_type='application/json',body=json.dumps(ticket))
    expect(page.locator('[data-review-card]')).to_have_count(0)
    ticket['pending_proposal']=navigation_proposal
    hold_detail=False
    for route in held_details:
        route.fulfill(status=200,content_type='application/json',body=json.dumps(ticket))
    held_details.clear()
    expect(page.locator('[data-approval-block]')).to_contain_text('Replacement body')
    expect(page.locator('[data-approval-block]')).not_to_contain_text('A result')
    proof=page.evaluate('window.__wait()')
    assert proof['status']=='included', proof
    # A server replacement racing a gesture retains the existing revision-CAS error.
    server_revision=4
    approve_row=page.locator('[data-approval-block]')
    approve_row.locator('[data-accept]').click()
    approve_row.locator('[data-ceiling-picker-choice="done"]').click()
    approve_row.locator('[data-ceiling-picker-choice="chief"]').click()
    expect(page.locator('.error-message')).to_contain_text('visible proposal changed')
    assert len(approvals)==1
    server_revision=3
    proposal_editor=approve_row.locator('[data-edit]')
    proposal_editor.click()
    proposal_editor.fill('Edited replacement body')
    proposal_editor.press('Tab')
    approve_row.locator('[data-accept]').click()
    approve_row.locator('[data-ceiling-picker-choice="done"]').click()
    approve_row.locator('[data-ceiling-picker-choice="chief"]').click()
    for _ in range(100):
        if len(approvals)==2: break
        page.wait_for_timeout(10)
    assert approvals[1]['expected_proposal_revision']==3
    assert approvals[1]['edited_body']=='Edited replacement body'

    expect(page.locator('[data-review-card]')).to_have_count(0)
    # Runtime loss omits count only, preserving other Review content.
    runtime_working=True
    page.evaluate('window.__refresh()')
    expect(page.locator('[data-shell-presence]')).to_have_text('1 working')
    expect(page.locator('.review-empty-meta')).to_have_text('1 agent in progress')
    snapshot_unavailable=True
    page.evaluate('window.__refresh()')
    expect(page.locator('[data-shell-presence]')).to_have_count(0)
    assert 'working' not in page.locator('[data-shell-status]').get_attribute('aria-label')
    expect(page.locator('[data-review-empty]')).to_contain_text('There is nothing to review right now.')
    expect(page.locator('.review-empty-meta')).to_have_count(0)
    snapshot_unavailable=False
    runtime_working=False
    page.evaluate('window.__refresh()')
    expect(page.locator('[data-shell-presence]')).to_have_text('0 working')
    expect(page.locator('.review-empty-meta')).to_have_text('0 agents in progress')
    # Declared ownership is Config's to change. Every non-terminal Stage carries one
    # owner control, and the Stage table carries nothing else that edits.
    page.evaluate('window.__showConfig()')
    stage_table=page.locator('[data-worker-stage-table]')
    stage_table.locator('[data-worker-stage-row]').first.wait_for()
    editable_rows = stage_table.locator('[data-worker-stage-row][data-terminal="false"]').count()
    assert editable_rows > 0
    assert stage_table.locator('[data-stage-owner-select]').count() == editable_rows
    assert stage_table.locator('select').count() == editable_rows
    assert stage_table.locator('input, [contenteditable]').count() == 0
    browser.close()
`;
  const child = spawn(join(root, '..', '.venv', 'bin', 'python'), ['-c', script, `http://127.0.0.1:${address.port}/.test-scratch/${stem}.html`], { stdio: 'inherit' });
  const code = await new Promise(resolve => child.on('exit', resolve));
  assert.equal(code, 0, 'Ticket guidance browser proof failed');
} finally {
  await server?.close();
  await Promise.all([host, main, index].map(path => rm(path, { force: true })));
}
