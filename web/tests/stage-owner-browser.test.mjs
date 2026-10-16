import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { writeFile, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { scratchDirectory } from './support/scratch.mjs';

// Focused real-product proof: mount the actual Config Worker page, change a Stage's
// owner in a real browser, and inspect the record the page posts back.
const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const stem = `stage-owner-${process.pid}`;
const scratchRoot = await scratchDirectory();
const host = join(scratchRoot, `${stem}.svelte`);
const main = join(scratchRoot, `${stem}.ts`);
const index = join(scratchRoot, `${stem}.html`);
let server;

try {
  await writeFile(host, `<script lang="ts">
import { QueryClientProvider } from '@tanstack/svelte-query';
import ConfigRoute from '../src/routes/ConfigRoute.svelte';
// The app's own client, because a write's invalidation goes through that one instance.
import { queryClient } from '../src/lib/queryClient';
</script>
<QueryClientProvider client={queryClient}>
  <ConfigRoute roleKind="worker" roleId="coding" />
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
import copy, json, sys
from playwright.sync_api import sync_playwright, expect

# The stored record, in the exact shape GET returns and POST takes back.
record = {
    'worker_type': 'coding',
    'label': 'Coding',
    'stages': [
        {'id': 'needs_success_condition', 'label': 'Success Condition',
         'gating_field': 'success_condition', 'is_terminal': False, 'ownership_mode': 'worker'},
        {'id': 'needs_plan', 'label': 'Plan', 'gating_field': 'plan',
         'is_terminal': False, 'ownership_mode': 'worker'},
        {'id': 'done', 'label': 'Done', 'gating_field': None,
         'is_terminal': True, 'ownership_mode': None},
    ],
    'fields': [
        {'id': 'success_condition', 'label': 'Success Condition'},
        {'id': 'plan', 'label': 'Plan'},
    ],
    'profile': {
        'specialist_skill': 'panels-worker-coding',
        'default_model': 'opus',
        'default_reasoning_effort': 'medium',
        'toolset_profile': 'coding',
        'default_backend': 'claude',
    },
}

def detail():
    return {
        'manifest': {
            'worker_type': 'coding', 'label': 'Coding',
            'stages': copy.deepcopy(record['stages']),
            'advance': {'needs_success_condition': 'needs_plan', 'needs_plan': 'done'},
            'fields': copy.deepcopy(record['fields']),
            'ceiling_range': ['needs_success_condition', 'needs_plan', 'done'],
            'default_ceiling': 'needs_plan',
            'worker_profile_id': 'panels-worker-coding', 'default_backend': 'claude',
            'default_model': 'opus', 'default_reasoning_effort': 'medium',
        },
        'settings': {
            'worker_type': 'coding',
            'specialist_skill': {'name': 'panels-worker-coding', 'description': 'Coding worker',
                                 'markdown_body': 'Body'},
            'launch_defaults': {'employee_backend': 'claude', 'employee_launch_model': 'opus',
                                'employee_launch_reasoning_effort': 'medium'},
        },
    }

posts = []
refuse_next = {'value': False}

def respond(route):
    path = route.request.url.split('/api/', 1)[1]
    if path == 'worker-types' and route.request.method == 'POST':
        body = route.request.post_data_json
        posts.append(body)
        if refuse_next['value']:
            refuse_next['value'] = False
            route.fulfill(status=409, content_type='application/json', body=json.dumps({
                'error': {'code': 'validation',
                          'message': 'removing a Stage would strand unfinished Tickets'}
            }))
            return
        record['stages'] = body['stages']
        result = body
    elif path == 'worker-types/coding':
        result = copy.deepcopy(record)
    elif path == 'workers/coding':
        result = detail()
    elif path == 'test/refuse-next':
        refuse_next['value'] = True
        result = {}
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

    table = page.locator('[data-worker-stage-table]')
    expect(table).to_have_count(1)

    # Every non-terminal Stage carries a control over the two stored modes.
    selects = table.locator('[data-stage-owner-select]')
    expect(selects).to_have_count(2)
    plan_row = table.locator('[data-worker-stage-row][data-stage="needs_plan"]')
    plan_select = plan_row.locator('[data-stage-owner-select]')
    expect(plan_select).to_have_attribute('aria-label', 'Owner for Plan')
    assert plan_select.locator('option').all_inner_texts() == ['worker', 'user']
    expect(plan_select).to_have_value('worker')

    # A terminal Stage has no control.
    done_row = table.locator('[data-worker-stage-row][data-stage="done"]')
    expect(done_row.locator('[data-terminal-owner]')).to_have_text('terminal')
    expect(done_row.locator('[data-stage-owner-select]')).to_have_count(0)

    # Changing the owner posts the whole record with one value changed.
    plan_select.select_option('user')
    for _ in range(200):
        if len(posts) == 1: break
        page.wait_for_timeout(20)
    assert len(posts) == 1, posts
    assert 'skill' not in posts[0]
    assert set(posts[0]) == {'worker_type', 'label', 'stages', 'fields', 'profile'}
    assert posts[0]['profile'] == record['profile']
    assert [(stage['id'], stage['ownership_mode']) for stage in posts[0]['stages']] == [
        ('needs_success_condition', 'worker'), ('needs_plan', 'user'), ('done', None)
    ]
    expect(plan_row.locator('[data-stage-owner]')).to_have_attribute(
        'data-stage-owner', 'user', timeout=5000
    )
    expect(plan_select).to_have_value('user')
    expect(plan_row.locator('[data-stage-owner-error]')).to_have_count(0)

    # A reload shows the stored owner, not a local memory of the change.
    page.reload()
    plan_row = page.locator('[data-worker-stage-row][data-stage="needs_plan"]')
    expect(plan_row.locator('[data-stage-owner-select]')).to_have_value('user')

    # A refused save shows the refusal on the row and reverts the control.
    page.evaluate("fetch('/api/test/refuse-next', {method: 'POST'})")
    plan_select = plan_row.locator('[data-stage-owner-select]')
    plan_select.select_option('worker')
    expect(plan_row.locator('[data-stage-owner-error]')).to_have_text(
        'removing a Stage would strand unfinished Tickets'
    )
    expect(plan_select).to_have_value('user')
    assert len(posts) == 2, posts

    # The next save after a refusal still works.
    plan_select.select_option('worker')
    expect(plan_row.locator('[data-stage-owner]')).to_have_attribute(
        'data-stage-owner', 'worker', timeout=5000
    )
    expect(plan_row.locator('[data-stage-owner-error]')).to_have_count(0)
    assert [(stage['id'], stage['ownership_mode']) for stage in record['stages']] == [
        ('needs_success_condition', 'worker'), ('needs_plan', 'worker'), ('done', None)
    ]
    # Two Stages changed in quick succession both survive: the second save reads the
    # record the first one wrote, instead of posting the value from before it.
    page.evaluate('''() => {
      const rows = document.querySelectorAll('[data-stage-owner-select]');
      for (const row of rows) {
        row.value = 'user';
        row.dispatchEvent(new Event('change', {bubbles: true}));
      }
    }''')
    for _ in range(400):
        if len(posts) == 5: break
        page.wait_for_timeout(20)
    assert [(stage['id'], stage['ownership_mode']) for stage in record['stages']] == [
        ('needs_success_condition', 'user'), ('needs_plan', 'user'), ('done', None)
    ], record['stages']
    browser.close()
`;

  const child = spawn(join(root, '..', '.venv', 'bin', 'python'), [
    '-c', script, `http://127.0.0.1:${address.port}/.test-scratch/${stem}.html`
  ], { stdio: 'inherit' });
  const code = await new Promise(resolve => child.on('exit', resolve));
  assert.equal(code, 0, 'Stage owner browser proof failed');
} finally {
  await server?.close();
  await Promise.all([host, main, index].map(path => rm(path, { force: true })));
}
