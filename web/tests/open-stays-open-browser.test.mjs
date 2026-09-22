/**
 * A server update must never close something the reader opened.
 *
 * Three surfaces failed this, each for its own reason, and each is driven here through
 * its real component with a real update pushed underneath it. A refresh is still allowed
 * to change what is in a list; it is not allowed to change what is open.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { writeFile, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { scratchDirectory } from "./support/scratch.mjs";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const stem = "open-stays-open";
const scratchRoot = await scratchDirectory();
const written = [];
let server;

async function page(name, markup) {
  const hostName = `${stem}-${name}-${process.pid}`;
  await writeFile(join(scratchRoot, `${hostName}.svelte`), markup);
  await writeFile(
    join(scratchRoot, `${hostName}.ts`),
    `import { mount } from 'svelte'; import Host from './${hostName}.svelte';` +
      ` import '../../assets/tokens.css'; import '../../assets/app.css';` +
      ` mount(Host, { target: document.getElementById('app')! });`
  );
  await writeFile(
    join(scratchRoot, `${hostName}.html`),
    `<html><body><div id="app"></div><script type="module" src="./${hostName}.ts"></script></body></html>`
  );
  written.push(
    join(scratchRoot, `${hostName}.svelte`),
    join(scratchRoot, `${hostName}.ts`),
    join(scratchRoot, `${hostName}.html`)
  );
  return `.test-scratch/${hostName}.html`;
}

try {
  // The thread, while an agent is working. Opening a tool call is the reader's hands; the
  // turn ending is the server's, and it folds the whole run away.
  const conversation = await page(
    "conversation",
    String.raw`<script lang="ts">
  import { tick } from "svelte";
  import ConversationPane from "../src/components/conversation/ConversationPane.svelte";
  import type { ConversationState } from "../src/lib/conversation/conversationState";

  const tool = (index: number) => ({
    key: "tool-" + index, kind: "tool_call", sequence: 100 + index, createdAt: 1000 + index,
    toolCallId: "tool-" + index, title: "Read file " + index, toolKind: "read",
    detail: "output line for tool " + index + "\n".repeat(12), startedDetail: null,
    cappedDetailSequence: null, status: "completed", progress: null
  });
  const working = [
    { key: "p1", kind: "prompt", sequence: 1, createdAt: 900,
      content: [{ piece: "text", text: "go" }], senderLabel: "owner", mode: "queue",
      sentAtUnixMilliseconds: 900 },
    ...[1, 2, 3, 4].map(tool)
  ];

  let rows = $state<any[]>(working);
  let running = $state(true);
  let conversationState = $state<ConversationState>("opened");
  let lens = $state<"focus" | "full">("focus");

  (window as any).__endTheTurn = () => {
    rows = [...working, { key: "end", kind: "turn_ended", sequence: 200, createdAt: 2000,
      ending: "completed", errorSummary: null, automaticCompactionResult: null }];
    running = false;
  };
  (window as any).__settle = async () => { await tick(); await tick(); };
</script>

<main style="height:720px">
  <ConversationPane
    bind:conversationState bind:lens conversationId="open-stays-open" label="Worker"
    conversationExists {rows} visibleRows={null} {running} heldPromptRows={[]}
    supportsSteer={false} backendKey="claude"
    current={{ model: "opus", reasoningEffort: "high" }}
    models={[{ model_id: "opus", display_name: "Opus", enabled: true }]}
    effortOptions={["high"]} outgoingMessages={[]} ownSenderLabel="owner"
    onSend={async () => true}
  />
</main>
<style>:global(html),:global(body),:global(#app){height:100%;margin:0}</style>`
  );

  // The Ticket screen. A stage the reader opened is rebuilt somewhere else on the page
  // when it settles, and the stage that becomes current must still open by itself.
  const ticket = await page(
    "ticket",
    `<script lang="ts">
import { QueryClient, QueryClientProvider } from '@tanstack/svelte-query';
import TicketRoute from '../src/routes/TicketRoute.svelte';
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
// What the change stream does when the server says something changed.
(window as any).__refresh = () => client.invalidateQueries();
</script>
<QueryClientProvider {client}><TicketRoute id="t_open" /></QueryClientProvider>`
  );

  // The Sprint documents. Which one is open is computed from which ones have text, so an
  // agent writing the Checkpoint recomputes it underneath the reader.
  const sprint = await page(
    "sprint",
    String.raw`<script lang="ts">
  import { QueryClientProvider } from "@tanstack/svelte-query";
  import { queryClient } from "../src/lib/queryClient";
  import SprintRoute from "../src/routes/SprintRoute.svelte";
  let sprint = {
    id: "sp_open", name: "A useful sprint", date_start: "2026-09-01", date_end: "2026-09-07",
    primary_bet: "Original summary", kickoff: "The plan we agreed", checkpoint: "", review: "",
    created_at: 1, updated_at: 1
  };
  const json = (value: unknown) =>
    new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/sprints/sp_open/tracking")
      return json({ sprint, planning_date: "2026-09-08", outcome_groups: [], unclassified_tickets: [] });
    if (path === "/api/sprints?detail=full") return json({ sprints: [sprint] });
    if (path.startsWith("/api/items?detail=summary"))
      return json({ items: [], page: { match_count: 0, return_count: 0, limit: 30, offset: 0,
        omitted_before: 0, omitted_after: 0, complete: true, next_offset: null } });
    return json({ projects: [], tickets: [] });
  }) as typeof fetch;
  (window as any).__writeTheCheckpoint = () => {
    sprint = { ...sprint, checkpoint: "Halfway, and here is where we are" };
    return queryClient.invalidateQueries();
  };
</script>
<QueryClientProvider client={queryClient}><SprintRoute sub="documents" sprintId="sp_open" /></QueryClientProvider>`
  );

  server = await createServer({
    root: webRoot, configFile: false, plugins: [svelte()],
    server: { host: "127.0.0.1", port: 0 }, logLevel: "error"
  });
  await server.listen();
  const { port } = server.httpServer.address();
  const address = (path) => `http://127.0.0.1:${port}/${path}`;

  const browserScript = String.raw`
import json, sys
from urllib.parse import parse_qs
from playwright.sync_api import sync_playwright

conversation_url, ticket_url, sprint_url = sys.argv[1:4]

FIELDS = ['brief', 'success_condition', 'what_changes', 'plan']
STAGE_OF = {f: 'needs_' + f for f in FIELDS}
stages = [dict(id=STAGE_OF[f], label=f, gating_field=f, is_terminal=False, ownership_mode='worker') for f in FIELDS]
stages.append(dict(id='done', label='Done', gating_field=None, is_terminal=True, ownership_mode=None))
advance = {STAGE_OF[FIELDS[i]]: STAGE_OF[FIELDS[i + 1]] for i in range(len(FIELDS) - 1)}
advance[STAGE_OF[FIELDS[-1]]] = 'done'
manifest = {'worker_types': [dict(worker_type='coding', label='Coding', stages=stages, advance=advance,
    ceiling_range=[s['id'] for s in stages], default_ceiling='done',
    worker_profile_id='panels-worker-coding', default_backend='claude', default_model=None,
    default_reasoning_effort=None, fields=[{'id': f, 'label': f} for f in FIELDS])]}
ticket = dict(project_id='project_one', project='One', sprint_id=None, sprint_item_id=None,
    id='t_open', title='Open ticket', worker_type='coding', employee_backend='claude',
    employee_launch_model=None, employee_launch_reasoning_effort=None,
    employee_configuration_editable=False, stage='needs_what_changes', ceiling='done', priority='P2',
    resolved_priority_anchors={'project': {'id': 'project_one', 'name': 'One', 'priority': 'P2'}, 'sprint_item': None},
    ticket_status='agent', ceiling_holder={'kind': 'owner', 'id': 'owner'}, conversation_id=None,
    conversation_history=[], day_ids=[], blocked=False, blocker_summary={'blocked_by': []},
    recap='Orientation', guidance='', pending_proposal=None,
    field_values={'brief': 'The request', 'success_condition': 'What done means', 'what_changes': 'The change'})

def serve_ticket(route):
    path = route.request.url.split('/api/', 1)[1]
    if path.startswith('tickets?'):
        path = 'tickets/' + parse_qs(path.split('?', 1)[1])['id'][0]
    if path == '__advance':
        ticket['stage'] = 'needs_plan'
        result = {'ok': True}
    elif path == 'tickets/t_open':
        result = ticket
    elif path == 'worker-types':
        result = manifest
    elif path == 'conversation/backends':
        result = {'backends': []}
    else:
        result = {}
    route.fulfill(status=200, content_type='application/json', body=json.dumps(result))

def is_open(page, selector):
    return page.locator(selector).first.evaluate('node => node.open')

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    errors = []
    try:
        # --- The thread ---------------------------------------------------------------
        page = browser.new_page(viewport={'width': 900, 'height': 760})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(conversation_url)
        page.wait_for_selector('[data-conversation-transcript]')
        page.evaluate('window.__settle()')

        page.locator('[data-conversation-work-fold]').first.click()
        row = page.locator('[data-conversation-tool="tool-2"]')
        row.first.click()
        page.evaluate('window.__settle()')
        assert row.first.get_attribute('aria-expanded') == 'true', 'the reader could not open a tool call'

        page.evaluate('window.__endTheTurn()')
        page.evaluate('window.__settle()')
        page.wait_for_timeout(200)
        page.locator('[data-conversation-turn-fold]').first.click()
        page.evaluate('window.__settle()')
        page.wait_for_timeout(100)
        reopened = page.locator('[data-conversation-tool="tool-2"]')
        assert reopened.count() == 1, 'the tool call did not come back when the turn was opened'
        assert reopened.first.get_attribute('aria-expanded') == 'true', \
            'the turn ending closed a tool call the reader had opened'
        page.close()

        # --- The Ticket screen --------------------------------------------------------
        page = browser.new_page(viewport={'width': 1200, 'height': 900})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/**', serve_ticket)
        page.goto(ticket_url)
        page.wait_for_selector('[data-field="what_changes"]')

        # The reader puts their hands on the stage they are reading.
        page.locator('[data-field="what_changes"] summary').first.click()
        page.locator('[data-field="what_changes"] summary').first.click()
        page.wait_for_timeout(100)
        assert is_open(page, '[data-field="what_changes"]')
        assert not is_open(page, '[data-field="plan"]')

        page.evaluate("() => fetch('/api/__advance')")
        page.wait_for_timeout(100)
        page.evaluate('window.__refresh()')
        page.wait_for_timeout(500)

        assert is_open(page, '[data-field="what_changes"]'), \
            'advancing the stage closed a stage the reader had opened'
        # The other half of the rule: data still opens what nobody has touched.
        assert is_open(page, '[data-field="plan"]'), \
            'the stage that became current did not open by itself'
        page.close()

        # --- The Sprint documents -----------------------------------------------------
        page = browser.new_page(viewport={'width': 1100, 'height': 900})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(sprint_url)
        page.wait_for_selector('[data-phase="kickoff"]')

        page.locator('[data-phase="kickoff"] summary').first.click()
        page.locator('[data-phase="kickoff"] summary').first.click()
        page.wait_for_timeout(100)
        assert is_open(page, '[data-phase="kickoff"]')

        page.evaluate('window.__writeTheCheckpoint()')
        page.wait_for_timeout(500)
        assert is_open(page, '[data-phase="kickoff"]'), \
            'writing the Checkpoint closed the Kickoff the reader had opened'
        page.close()
    finally:
        browser.close()
    assert not errors, errors
print('open stays open on all three surfaces')
`;
  const child = spawn(
    join(webRoot, "..", ".venv", "bin", "python"),
    ["-c", browserScript, address(conversation), address(ticket), address(sprint)],
    { stdio: "inherit" }
  );
  const code = await new Promise((resolve) => child.on("exit", resolve));
  assert.equal(code, 0, "the browser proof failed");
} finally {
  await server?.close();
  await Promise.all(written.map((path) => rm(path, { force: true })));
}
