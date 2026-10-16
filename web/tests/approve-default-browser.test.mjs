import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { writeFile, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { scratchDirectory } from './support/scratch.mjs';

// Focused real-product proof: mount the actual Ticket route and Review proposal card,
// serve fresh response objects on every read, and inspect the actual accept payloads.
const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const stem = `approve-default-${process.pid}`;
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
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
let hostKind = $state<'ticket' | 'review'>('ticket');
let ticketId = $state('t_coding');
let field = $state('success_condition');
(window as any).__showTicket = (id: string) => { hostKind = 'ticket'; ticketId = id; };
(window as any).__showReview = (id: string, nextField: string) => { hostKind = 'review'; ticketId = id; field = nextField; };
(window as any).__refresh = () => Promise.all([
  client.refetchQueries({ queryKey: ['ticket'] }),
  client.refetchQueries({ queryKey: ['worker-types'] })
]);
</script>
<QueryClientProvider {client}>
  {#if hostKind === 'review'}
    {#key ticketId}<ReviewRoute />{/key}
  {:else}
    {#key ticketId}<TicketRoute id={ticketId} />{/key}
  {/if}
</QueryClientProvider>`);
  await writeFile(main, `import { mount } from 'svelte'; import Host from './${stem}.svelte'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host, {target: document.getElementById('app')!});`);
  await writeFile(index, `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);

  server = await createServer({
    root,
    configFile: false,
    plugins: [svelte()],
    server: { host: '127.0.0.1', port: 0 },
    logLevel: 'error'
  });
  await server.listen();
  const address = server.httpServer.address();
  assert.ok(address && typeof address === 'object');

  const script = String.raw`
import json, sys, os
from pathlib import Path
from urllib.parse import parse_qs
from playwright.sync_api import sync_playwright, expect

def worker_manifest(worker_type, label, stages):
    fields = [{'id': field, 'label': stage_label} for _, stage_label, field in stages if field]
    stage_rows = [
        {'id': stage, 'label': stage_label, 'gating_field': field,
         'is_terminal': field is None, 'ownership_mode': 'worker' if field else None}
        for stage, stage_label, field in stages
    ]
    return {
        'worker_type': worker_type, 'label': label, 'stages': stage_rows,
        'advance': {stages[i][0]: stages[i + 1][0] for i in range(len(stages) - 1)},
        'ceiling_range': [stage for stage, _, _ in stages],
        'default_ceiling': stages[1][0], 'worker_profile_id': f'panels-worker-{worker_type}',
        'default_backend': 'codex', 'default_model': None,
        'default_reasoning_effort': None, 'fields': fields
    }

coding = worker_manifest('coding', 'Coding', [
    ('needs_brief', 'Brief', 'brief'),
    ('needs_success_condition', 'Success Condition', 'success_condition'),
    ('needs_what_changes', 'What Changes', 'what_changes'),
    ('needs_plan', 'Plan', 'plan'),
    ('needs_implementation', 'Implementation', 'implementation'),
    ('needs_consequences', 'Consequences', 'consequences'),
    ('done', 'Done', None),
])
exploration = worker_manifest('exploration', 'Exploration', [
    ('needs_brief', 'Brief', 'brief'),
    ('needs_understanding', 'Understanding', 'understanding'),
    ('needs_research_plan', 'Research Plan', 'research_plan'),
    ('needs_findings', 'Findings', 'findings'),
    ('needs_answer', 'Answer', 'answer'),
    ('needs_proposed_follow_up', 'Proposed Follow-up', 'proposed_follow_up'),
    ('needs_consequences', 'Consequences', 'consequences'),
    ('done', 'Done', None),
])
manifest = {'worker_types': [coding, exploration]}

def ticket(ticket_id, worker_type, stage, field, body, created_at):
    return {
        'project_id': 'project_one', 'project': 'Panels', 'sprint_id': None,
        'sprint_item_id': 'item_one', 'id': ticket_id, 'title': 'Restore compact approval',
        'worker_type': worker_type, 'employee_backend': 'codex',
        'employee_launch_model': None, 'employee_launch_reasoning_effort': None,
        'employee_configuration_editable': False, 'stage': stage, 'ceiling': stage,
        'priority': 'P2', 'resolved_priority_anchors': {
            'project': {'id': 'project_one', 'name': 'One', 'priority': 'P2'},
            'sprint_item': {'id': 'item_one', 'title': 'Outcome', 'priority': 'P2'}
        },
        'ticket_status': 'awaiting_approval',
        'ceiling_holder': {'kind': 'sprint_item', 'id': 'item_one'},
        'conversation_id': None, 'conversation_history': [], 'day_ids': [],
        'blocked': False, 'blocker_summary': {'blocked_by': []}, 'recap': 'Orientation',
        'guidance': '', 'field_values': {'brief': 'Request'},
        'pending_proposal': {
            'field': field, 'body': body, 'proposed_by': 'agent', 'created_at': created_at,
            'revision': 1
        }
    }

tickets = {
    't_coding': ticket('t_coding', 'coding', 'needs_success_condition', 'success_condition', 'Coding result', 1),
    't_findings': ticket('t_findings', 'exploration', 'needs_findings', 'findings', 'Evidence', 2),
    't_consequences': ticket('t_consequences', 'exploration', 'needs_consequences', 'consequences', 'Applied', 3),
    't_brief': ticket('t_brief', 'coding', 'needs_brief', 'brief', 'Kickoff request', 5),
    't_review_replace': ticket('t_review_replace', 'coding', 'needs_plan', 'plan', 'Original Review plan', 7),
    't_stale': ticket('t_stale', 'coding', 'needs_plan', 'plan', 'Original stale plan', 6),
    't_failure': ticket('t_failure', 'coding', 'needs_what_changes', 'what_changes', 'Retry me', 4),
}
approvals = []
scope_writes = []
review_ticket = 't_coding'
priority_route = None
accept_attempts = []
read_count = 0

def respond(route):
    global read_count, priority_route
    path = route.request.url.split('/api/', 1)[1]
    if path.startswith('tickets?'):
        path = 'tickets/' + parse_qs(path.split('?', 1)[1])['id'][0]
    if path == 'tickets/t_brief' and route.request.method == 'PATCH' and 'priority' in route.request.post_data_json:
        priority_route = route
        return
    if path == 'review':
        result = {'items': [{'ticket_id': review_ticket, 'field': tickets[review_ticket]['pending_proposal']['field'], 'title': tickets[review_ticket]['title']}], 'running_worker_count': 0}
    elif path.startswith('tickets/') and path.count('/') == 1 and route.request.method == 'PATCH':
        scope_writes.append(route.request.post_data_json)
        tickets[path.split('/')[1]].update(route.request.post_data_json)
        result = tickets[path.split('/')[1]]
    elif path == 'test/replace-coding':
        tickets['t_coding']['pending_proposal'] = {
            'field': 'success_condition', 'body': 'Replacement result',
            'proposed_by': 'agent', 'created_at': 1, 'revision': 2
        }
        result = {}
    elif path == 'test/replace-review':
        tickets['t_review_replace']['pending_proposal'].update(body='Replacement Review plan', revision=2)
        result = {}
    elif path == 'test/state':
        result = {
            'read_count': read_count,
            'created_at': tickets['t_coding']['pending_proposal']['created_at'],
            'revision': tickets['t_coding']['pending_proposal']['revision'],
            'failure_attempts': len([item for item in accept_attempts if item['ticket_id'] == 't_failure'])
        }
    elif path == 'worker-types':
        result = manifest
    elif path.startswith('tickets/') and '/accept/' in path:
        _, ticket_id, _, field = path.split('/', 3)
        body = route.request.post_data_json
        attempt = {'ticket_id': ticket_id, 'field': field, 'body': body}
        accept_attempts.append(attempt)
        if ticket_id == 't_stale' and body['expected_proposal_revision'] == 1:
            tickets[ticket_id]['pending_proposal'].update(body='New plan revision', revision=2)
            route.fulfill(status=409, content_type='application/json', body=json.dumps({'error': {'code': 'stale_proposal', 'message': 'proposal changed'}}))
            return
        if ticket_id == 't_failure' and len([item for item in accept_attempts if item['ticket_id'] == ticket_id]) == 1:
            route.fulfill(status=500, content_type='application/json', body=json.dumps({
                'error': {'code': 'test_failure', 'message': 'approval failed'}
            }))
            return
        approvals.append(attempt)
        result = {**tickets[ticket_id], 'pending_proposal': None, 'ticket_status': 'empty'}
    elif path.endswith('/conversation/start-values'):
        result = {}
    elif path.startswith('tickets/') and path.count('/') == 1:
        ticket_id = path.split('/', 1)[1]
        read_count += 1
        result = tickets[ticket_id]
    elif path == 'conversation/backends':
        result = {'backends': []}
    else:
        result = {}
    route.fulfill(status=200, content_type='application/json', body=json.dumps(result))

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.on('pageerror', lambda error: print(str(error), flush=True))
    page.route('**/api/**', respond)
    page.goto(sys.argv[1])

    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    def block():
        result = page.locator('[data-approval-block]')
        expect(result).to_have_count(1)
        return result
    def open_approval():
        block().locator('[data-accept]').click()
        expect(page.get_by_role('listbox')).to_have_attribute('aria-label', 'Ceiling stage')
    def choose(stage, reviewer='owner'):
        open_approval()
        page.locator(f'[data-ceiling-picker-choice="{stage}"]').click()
        expect(page.get_by_role('listbox')).to_have_attribute('aria-label', 'Who holds the ceiling')
        page.locator(f'[data-ceiling-picker-choice="{reviewer}"]').click()
    def wait_count(items, count):
        for _ in range(100):
            if len(items) == count: return
            page.wait_for_timeout(20)
        assert len(items) == count, items

    expect(block().locator('[data-reviewer-heading]')).to_have_text('Orchestrator')
    assert block().locator('[data-scope-ceiling], [data-ceiling-effect], .approves-mark').count() == 0
    # Capture both actual product hosts with exactly the same proposal and reviewer.
    artifact_dir = os.environ.get('PANELS_APPROVAL_ARTIFACT_DIR')
    evidence = []
    for host_kind in ['ticket', 'review']:
        if host_kind == 'review': page.evaluate("window.__showReview('t_coding', 'success_condition')")
        expect(block().locator('[data-reviewer-heading]')).to_have_text('Orchestrator')
        expect(block().locator('[contenteditable]')).to_have_text('Coding result')
        for width, label in [(1280, 'desktop'), (390, 'compact')]:
            page.set_viewport_size({'width': width, 'height': 1000})
            page.emulate_media(reduced_motion='reduce')
            if host_kind == 'ticket':
                expect(page.locator('[data-ticket-identity]')).to_have_text('Panels · Coding')
                page.locator('[data-stage-fold]').evaluate('(element) => element.open = true')
            assert page.evaluate('Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) <= innerWidth + 1')
            if artifact_dir:
                Path(artifact_dir).mkdir(parents=True, exist_ok=True)
                path = f'{host_kind}-{label}-closed.png'
                page.screenshot(path=str(Path(artifact_dir) / path), full_page=True)
                evidence.append(path)
            open_approval()
            panel = page.locator('[data-ceiling-picker-panel]')
            box = panel.bounding_box()
            assert box and box['x'] >= -1 and box['x'] + box['width'] <= width + 1
            assert panel.locator('[data-ceiling-effect], .approves-mark, .holder-detail').count() == 0
            if artifact_dir:
                path = f'{host_kind}-{label}-open.png'
                page.screenshot(path=str(Path(artifact_dir) / path), full_page=True)
                evidence.append(path)
            page.locator('[data-ceiling-part="holder"]').click()
            expect(page.get_by_role('option', name='Orchestrator', exact=True)).to_have_count(1)
            if artifact_dir:
                path = f'{host_kind}-{label}-reviewers.png'
                page.screenshot(path=str(Path(artifact_dir) / path), full_page=True)
                evidence.append(path)
            page.get_by_role('listbox').press('Escape')
            assert approvals == [] and scope_writes == []

    # Review shortcuts cannot steal listbox typeahead: s stays in this proposal,
    # and o selects the role without opening the Ticket route.
    hash_before = page.evaluate('location.hash')
    open_approval()
    page.get_by_role('listbox').press('s')
    expect(page.locator('[data-listbox-picker-active]')).to_have_attribute('data-ceiling-picker-choice', 'needs_success_condition')
    expect(page.locator('[data-review-card]')).to_have_attribute('data-ticket-id', 't_coding')
    expect(page.get_by_role('listbox')).to_be_focused()
    page.locator('[data-ceiling-part="holder"]').click()
    page.get_by_role('listbox').press('o')
    expect(page.locator('[data-listbox-picker-active]')).to_have_attribute('data-ceiling-picker-choice', 'item_one')
    expect(page.get_by_role('listbox')).to_be_focused()
    assert page.evaluate('location.hash') == hash_before
    assert approvals == [] and scope_writes == []
    page.get_by_role('listbox').press('Escape')
    page.evaluate("window.__showTicket('t_coding')")
    # Reviewer heading only reroutes; pending proposal and revision stay intact.
    block().locator('[data-reviewer-heading]').click()
    expect(page.get_by_role('listbox')).to_have_attribute('aria-label', 'Who holds the ceiling')
    page.locator('[data-ceiling-picker-choice="chief"]').click()
    wait_count(scope_writes, 1)
    assert scope_writes[-1] == {'ceiling_holder': {'kind': 'chief', 'id': 'chief'}}
    assert approvals == []
    # Opening Approve then changing only the reviewer never accepts a proposal.
    open_approval()
    page.locator('[data-ceiling-part="holder"]').click()
    page.locator('[data-ceiling-picker-choice="owner"]').click()
    wait_count(scope_writes, 2)
    assert scope_writes[-1] == {'ceiling_holder': {'kind': 'owner', 'id': 'owner'}}
    assert approvals == []
    # Same and lower boundaries never approve, even from Approve.
    for stage in ['needs_success_condition', 'needs_brief']:
        choose(stage)
    wait_count(scope_writes, 4)
    assert scope_writes[-1] == {'ceiling': 'needs_brief', 'ceiling_holder': {'kind': 'owner', 'id': 'owner'}}
    assert approvals == []
    # Draft navigation, letters, Escape, and outside cancellation send no writes.
    block().locator('[data-accept]').focus()
    block().locator('[data-accept]').press('ArrowDown')
    expect(page.get_by_role('listbox')).to_have_attribute('aria-label', 'Ceiling stage')
    page.get_by_role('listbox').press('End')
    page.get_by_role('listbox').press('Enter')
    expect(page.get_by_role('listbox')).to_have_attribute('aria-label', 'Who holds the ceiling')
    page.get_by_role('listbox').press('c')
    page.get_by_role('listbox').press('Escape')
    expect(block().locator('[data-accept]')).to_be_focused()
    open_approval()
    page.locator('[data-ceiling-part="holder"]').click()
    page.locator('[data-ceiling-part="ceiling"]').click()
    page.locator('[data-ticket-identity]').click()
    expect(page.get_by_role('listbox')).to_have_count(0)
    assert len(scope_writes) == 4 and approvals == []
    # Same revision refetch retains the local edit; replacement resets it.
    proposal = block().locator('[contenteditable]')
    proposal.fill('Local edit')
    proposal.press('Tab')
    page.evaluate('window.__refresh()')
    expect(proposal).to_have_text('Local edit')
    open_approval()
    page.locator('[data-ceiling-picker-choice="done"]').click()
    page.evaluate("fetch('/api/test/replace-coding', {method: 'POST'})")
    page.evaluate('window.__refresh()')
    expect(proposal).to_have_text('Replacement result')
    expect(page.get_by_role('listbox')).to_have_count(0)
    assert approvals == []
    choose('needs_what_changes')
    wait_count(approvals, 1)
    assert approvals[-1]['body'] == {'next_ceiling': 'needs_what_changes', 'next_holder': {'kind': 'owner', 'id': 'owner'}, 'expected_proposal_revision': 2}

    review_ticket = 't_findings'
    page.evaluate("window.__showReview('t_findings', 'findings')")
    expect(block()).to_have_attribute('data-field', 'findings')
    choose('needs_answer')
    wait_count(approvals, 2)
    assert approvals[-1]['body']['expected_proposal_revision'] == 1
    assert approvals[-1]['body']['next_ceiling'] == 'needs_answer'

    page.evaluate("window.__showTicket('t_consequences')")
    expect(block()).to_have_attribute('data-field', 'consequences')
    choose('done')
    wait_count(approvals, 3)
    assert approvals[-1]['body']['next_ceiling'] == 'done'
    # A failed request remains editable and a deliberate retry sends the same revision.
    page.evaluate("window.__showTicket('t_failure')")
    expect(block()).to_have_attribute('data-field', 'what_changes')
    proposal = block().locator('[contenteditable]')
    proposal.fill('Edited retry')
    proposal.press('Tab')
    open_approval()
    page.locator('[data-ceiling-picker-choice="needs_plan"]').click()
    page.locator('[data-ceiling-picker-choice="owner"]').evaluate('(button) => { button.click(); button.click(); }')
    expect(block()).to_contain_text('approval failed')
    assert len([item for item in accept_attempts if item['ticket_id'] == 't_failure']) == 1
    choose('needs_plan')
    wait_count(approvals, 4)
    assert approvals[-1]['body'] == {'next_ceiling': 'needs_plan', 'next_holder': {'kind': 'owner', 'id': 'owner'}, 'expected_proposal_revision': 1, 'edited_body': 'Edited retry'}
    # A stale response cannot accept a replacement; refresh starts a fresh deliberate gesture.
    page.evaluate("window.__showTicket('t_stale')")
    expect(block()).to_have_attribute('data-field', 'plan')
    choose('needs_implementation')
    expect(block()).to_contain_text('proposal changed')
    assert len(approvals) == 4
    page.evaluate('window.__refresh()')
    expect(block().locator('[contenteditable]')).to_have_text('New plan revision')
    choose('needs_implementation')
    wait_count(approvals, 5)
    assert approvals[-1]['body']['expected_proposal_revision'] == 2
    # Review also discards an open old-revision gesture before replacement can accept.
    review_ticket = 't_review_replace'
    page.evaluate("window.__showReview('t_review_replace', 'plan')")
    expect(block().locator('[contenteditable]')).to_have_text('Original Review plan')
    open_approval()
    page.locator('[data-ceiling-picker-choice="done"]').click()
    page.evaluate("fetch('/api/test/replace-review', {method: 'POST'})")
    page.evaluate('window.__refresh()')
    expect(block().locator('[contenteditable]')).to_have_text('Replacement Review plan')
    expect(page.get_by_role('listbox')).to_have_count(0)
    assert len(approvals) == 5
    # Steps retain circular marks in every current state and stop motion on request.
    step_states = []
    for status, expected in [('agent', 'current-running'), ('assigned', 'current-assigned'), ('awaiting_approval', 'current-awaiting-approval'), ('errored', 'errored')]:
        state_id = 't_steps_' + status
        tickets[state_id] = ticket(state_id, 'coding', 'needs_what_changes', 'what_changes', 'Steps state proof', 20)
        tickets[state_id]['ticket_status'] = status
        if status != 'awaiting_approval': tickets[state_id]['pending_proposal'] = None
        page.evaluate('(id) => window.__showTicket(id)', state_id)
        page.locator('[data-stage-fold]').evaluate('(element) => element.open = true')
        mark = page.locator(f'[data-stage-fold] .stage-mark--{expected}').first
        expect(mark).to_be_visible()
        page.emulate_media(reduced_motion='reduce')
        computed = mark.evaluate('(element) => { const s = getComputedStyle(element); return {width:s.width, height:s.height, radius:s.borderTopLeftRadius, animation:s.animationName}; }')
        assert computed['width'] == computed['height']
        assert float(computed['radius'].replace('px','')) >= float(computed['width'].replace('px','')) / 2
        assert computed['animation'] == 'none'
        step_states.append({'status': status, 'computed': computed})
        if artifact_dir:
            path = f'steps-{status}-reduced-motion.png'
            page.screenshot(path=str(Path(artifact_dir) / path), full_page=True)
            evidence.append(path)
        if status == 'agent':
            page.emulate_media(reduced_motion='no-preference')
            assert mark.evaluate('(element) => getComputedStyle(element).animationName') != 'none'
            page.emulate_media(reduced_motion='reduce')
    # Brief priority writes cannot race approval in Review.
    review_ticket = 't_brief'
    page.evaluate("window.__showReview('t_brief', 'brief')")
    expect(block()).to_have_attribute('data-field', 'brief')
    expect(block().locator('[data-send-back]')).to_have_count(0)
    page.get_by_label('Ticket priority', exact=True).select_option('P1')
    expect(block().locator('[data-accept]')).to_be_disabled()
    assert len(approvals) == 5
    assert priority_route is not None
    tickets['t_brief']['priority'] = 'P1'
    priority_route.fulfill(status=200, content_type='application/json', body=json.dumps(tickets['t_brief']))
    expect(block().locator('[data-accept]')).to_be_enabled()
    assert errors == [], errors
    if artifact_dir:
        (Path(artifact_dir) / 'frontend-assertions.json').write_text(json.dumps({'hosts': ['TicketRoute', 'ReviewRoute'], 'same_proposal_revision': 1, 'same_reviewer': 'Orchestrator', 'screenshots': evidence, 'browser_errors': errors, 'steps': step_states, 'approval_transactions': approvals, 'scope_transactions': scope_writes}, indent=2))
    browser.close()
`;

  const child = spawn(join(root, '..', '.venv', 'bin', 'python'), [
    '-c', script, `http://127.0.0.1:${address.port}/.test-scratch/${stem}.html`
  ], { stdio: 'inherit' });
  const code = await new Promise(resolve => child.on('exit', resolve));
  assert.equal(code, 0, 'Approve default browser proof failed');
} finally {
  await server?.close();
  await Promise.all([host, main, index].map(path => rm(path, { force: true })));
}
