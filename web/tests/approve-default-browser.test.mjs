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
import ReviewProposalCard from '../src/components/ReviewProposalCard.svelte';
import ApprovalBlock from '../src/components/ApprovalBlock.svelte';
import { buildLifecycle } from '../src/lib/lifecycle';
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const disabledLifecycle = buildLifecycle({
  worker_type: 'coding', label: 'Coding',
  stages: [
    { id: 'needs_what_changes', label: 'What Changes', gating_field: 'what_changes', is_terminal: false, ownership_mode: 'worker' },
    { id: 'needs_plan', label: 'Plan', gating_field: 'plan', is_terminal: false, ownership_mode: 'worker' },
    { id: 'done', label: 'Done', gating_field: null, is_terminal: true, ownership_mode: null }
  ],
  advance: { needs_what_changes: 'needs_plan', needs_plan: 'done' },
  fields: [{ id: 'what_changes', label: 'What Changes' }, { id: 'plan', label: 'Plan' }],
  ceiling_range: ['needs_what_changes', 'needs_plan', 'done'], default_ceiling: 'needs_plan',
  worker_profile_id: 'panels-worker-coding', default_backend: 'codex',
  default_model: null, default_reasoning_effort: null
});
let hostKind = $state<'ticket' | 'review' | 'disabled'>('ticket');
let ticketId = $state('t_coding');
let field = $state('success_condition');
(window as any).__showTicket = (id: string) => { hostKind = 'ticket'; ticketId = id; };
(window as any).__showReview = (id: string, nextField: string) => { hostKind = 'review'; ticketId = id; field = nextField; };
(window as any).__showDisabled = () => { hostKind = 'disabled'; };
(window as any).__refresh = () => Promise.all([
  client.refetchQueries({ queryKey: ['ticket'] }),
  client.refetchQueries({ queryKey: ['worker-types'] })
]);
</script>
<QueryClientProvider {client}>
  {#if hostKind === 'disabled'}
    <ApprovalBlock field="what_changes" proposalBody="Disabled" proposalCreatedAt={10}
      newStage="needs_plan" lifecycle={disabledLifecycle} disabled />
  {:else if hostKind === 'review'}
    {#key ticketId}<ReviewProposalCard {ticketId} {field} />{/key}
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
import json, sys
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
        'project_id': 'project_one', 'project': 'One', 'sprint_id': None,
        'sprint_item_id': 'item_one', 'id': ticket_id, 'title': ticket_id,
        'worker_type': worker_type, 'employee_backend': 'codex',
        'employee_launch_model': None, 'employee_launch_reasoning_effort': None,
        'employee_configuration_editable': False, 'stage': stage, 'ceiling': stage,
        'priority': 'P2', 'resolved_priority_anchors': {
            'project': {'id': 'project_one', 'name': 'One', 'priority': 'P2'},
            'sprint_item': {'id': 'item_one', 'title': 'Outcome', 'priority': 'P2'}
        },
        'ticket_status': 'awaiting_approval',
        'ceiling_holder': {'kind': 'owner', 'id': 'owner'},
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
    't_failure': ticket('t_failure', 'coding', 'needs_what_changes', 'what_changes', 'Retry me', 4),
}
approvals = []
accept_attempts = []
read_count = 0

def respond(route):
    global read_count
    path = route.request.url.split('/api/', 1)[1]
    if path.startswith('tickets?'):
        path = 'tickets/' + parse_qs(path.split('?', 1)[1])['id'][0]
    if path == 'test/replace-coding':
        tickets['t_coding']['pending_proposal'] = {
            'field': 'success_condition', 'body': 'Replacement result',
            'proposed_by': 'agent', 'created_at': 1, 'revision': 2
        }
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

    coding_block = page.locator('[data-approval-block][data-field="success_condition"]')
    expect(coding_block).to_have_count(1)
    coding_picker = coding_block.locator('[data-scope-ceiling]')
    expect(coding_picker).to_have_attribute('aria-label', 'Until What Changes · then me')
    assert not coding_block.locator('[data-accept]').is_disabled()

    # Picker cancellation still leaves the actual bound default untouched.
    coding_picker.click()
    coding_block.locator('[data-ceiling-picker-choice="done"]').click()
    expect(coding_block.get_by_role('listbox')).to_have_attribute('aria-label', 'Who holds the ceiling')
    coding_block.get_by_role('listbox').press('Escape')
    expect(coding_picker).to_have_attribute('aria-label', 'Until What Changes · then me')

    # An explicit scope and an unsaved proposal edit survive fresh response objects.
    coding_picker.click()
    coding_block.locator('[data-ceiling-picker-choice="none"]').click()
    coding_block.locator('[data-ceiling-picker-choice="chief"]').click()
    expect(coding_picker).to_have_attribute('aria-label', 'Until No further · then Chief')
    proposal = coding_block.locator('[contenteditable]')
    proposal.fill('Local edit')
    page.evaluate('window.__refresh()')
    expect(coding_picker).to_have_attribute('aria-label', 'Until No further · then Chief')
    expect(proposal).to_have_text('Local edit')

    # A replacement proposal resets both the proposal draft and the valid default.
    page.evaluate("fetch('/api/test/replace-coding', {method: 'POST'})")
    state = page.evaluate("fetch('/api/test/state').then(response => response.json())")
    assert state['created_at'] == 1
    assert state['revision'] == 2
    reads_before_replacement = state['read_count']
    page.evaluate('window.__refresh()')
    state = page.evaluate("fetch('/api/test/state').then(response => response.json())")
    assert state['read_count'] > reads_before_replacement
    expect(proposal).to_have_text('Replacement result')
    expect(coding_picker).to_have_attribute('aria-label', 'Until What Changes · then me')
    coding_block.locator('[data-accept]').click()
    for _ in range(100):
        if len(approvals) == 1: break
        page.wait_for_timeout(20)

    # Review uses the same actual default: exploration Findings advances to Answer.
    page.evaluate("window.__showReview('t_findings', 'findings')")
    findings_block = page.locator('[data-approval-block][data-field="findings"]')
    expect(findings_block).to_have_count(1)
    expect(findings_block.locator('[data-scope-ceiling]')).to_have_attribute('aria-label', 'Until Answer · then me')
    assert not findings_block.locator('[data-accept]').is_disabled()
    findings_block.locator('[data-accept]').click()
    for _ in range(100):
        if len(approvals) == 2: break
        page.wait_for_timeout(20)

    # The final proposal uses the terminal Stage without picker interaction.
    page.evaluate("window.__showTicket('t_consequences')")
    consequences_block = page.locator('[data-approval-block][data-field="consequences"]')
    expect(consequences_block).to_have_count(1)
    expect(consequences_block.locator('[data-scope-ceiling]')).to_have_attribute('aria-label', 'Until Done · then me')
    assert not consequences_block.locator('[data-accept]').is_disabled()
    consequences_block.locator('[data-accept]').click()
    for _ in range(100):
        if len(approvals) == 3: break
        page.wait_for_timeout(20)

    # A same-tick duplicate cannot pass the in-flight guard. A failed request restores
    # the action and leaves the visible default intact for a successful retry.
    page.evaluate("window.__showTicket('t_failure')")
    failure_block = page.locator('[data-approval-block][data-field="what_changes"]')
    expect(failure_block).to_have_count(1)
    failure_button = failure_block.locator('[data-accept]')
    expect(failure_block.locator('[data-scope-ceiling]')).to_have_attribute('aria-label', 'Until Plan · then me')
    failure_button.evaluate('(button) => { button.click(); button.click(); }')
    expect(failure_block).to_contain_text('approval failed')
    assert not failure_button.is_disabled()
    state = page.evaluate("fetch('/api/test/state').then(response => response.json())")
    assert state['failure_attempts'] == 1
    expect(failure_block.locator('[data-scope-ceiling]')).to_have_attribute('aria-label', 'Until Plan · then me')
    failure_button.click()
    for _ in range(100):
        if len(approvals) == 4: break
        page.wait_for_timeout(20)
    state = page.evaluate("fetch('/api/test/state').then(response => response.json())")
    assert state['failure_attempts'] == 2

    # A host-level disable still wins even though the approval scope is now valid.
    page.evaluate('window.__showDisabled()')
    disabled_block = page.locator('[data-approval-block][data-field="what_changes"]')
    expect(disabled_block.locator('[data-scope-ceiling]')).to_have_attribute('aria-label', 'Until Plan · then me')
    assert disabled_block.locator('[data-accept]').is_disabled()

    assert approvals == [
        {'ticket_id': 't_coding', 'field': 'success_condition', 'body': {
            'next_ceiling': 'needs_what_changes', 'next_holder': {'kind': 'owner', 'id': 'owner'}
        }},
        {'ticket_id': 't_findings', 'field': 'findings', 'body': {
            'next_ceiling': 'needs_answer', 'next_holder': {'kind': 'owner', 'id': 'owner'}
        }},
        {'ticket_id': 't_consequences', 'field': 'consequences', 'body': {
            'next_ceiling': 'done', 'next_holder': {'kind': 'owner', 'id': 'owner'}
        }},
        {'ticket_id': 't_failure', 'field': 'what_changes', 'body': {
            'next_ceiling': 'needs_plan', 'next_holder': {'kind': 'owner', 'id': 'owner'}
        }},
    ]
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
