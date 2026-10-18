import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { scratchDirectory } from "./support/scratch.mjs";

// Browser proof that the rail answers what needs the owner while it is at rest. A
// Sprint Item nobody has opened shows the same groups and the same rows an open one
// shows, so the answer costs no click. An Item with nothing for him shows nothing.
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const stem = `workspace-rail-at-rest-${process.pid}`;
const scratchRoot = await scratchDirectory();
const host = join(scratchRoot, `${stem}.svelte`);
const main = join(scratchRoot, `${stem}.ts`);
const index = join(scratchRoot, `${stem}.html`);
let server;

try {
  await writeFile(host, `<script lang="ts">
import { tick, onMount } from "svelte";
import { parseWorkspaceAddress } from "../src/lib/workspaceAddress";
import { QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
import BoardRoute from "../src/routes/BoardRoute.svelte";

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
(window as any).__refresh = async () => { await client.invalidateQueries(); await tick(); };
let address = $state(parseWorkspaceAddress(location.hash) ?? { selection: {kind:"none" as const},view:"items" as const,openFile:null });
onMount(() => {
  const update = () => address = parseWorkspaceAddress(location.hash) ?? {selection:{kind:"none"},view:"items",openFile:null};
  window.addEventListener("hashchange", update);
  return () => window.removeEventListener("hashchange", update);
});
</script>

<QueryClientProvider {client}>
  <BoardRoute {address} />
</QueryClientProvider>
`, "utf8");
  await writeFile(
    main,
    `import { mount } from "svelte"; import Host from "./${stem}.svelte"; import "../../assets/tokens.css"; import "../../assets/app.css"; mount(Host, { target: document.getElementById("app")! });`,
    "utf8"
  );
  await writeFile(
    index,
    `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`,
    "utf8"
  );

  server = await createServer({
    root,
    configFile: false,
    plugins: [svelte()],
    server: { host: "127.0.0.1", port: 0 },
    logLevel: "error"
  });
  await server.listen();
  const address = server.httpServer.address();
  assert.ok(address && typeof address === "object");

  const script = String.raw`
import json, os, pathlib, sys
from playwright.sync_api import sync_playwright, expect
from tests.e2e.harness import browser_failure_evidence

LONG_TITLE = "Review the entire Worker title when the adjustable rail clips this long approval row"

def card(ticket_id, item_id, item_title, **values):
    row = {
        "id": ticket_id, "title": ticket_id, "is_backlogged": False, "priority": "P1", "deadline": None,
        "project_id": None, "project": None, "group_project_id": None, "group_project": None,
        "activity_at": 0, "has_pending_proposal": False, "ticket_status": "empty",
        "worker_type": "coding", "employee_backend": "codex", "stage": "needs_implementation",
        "stage_label": "Implementation", "gating_field": "implementation",
        "gating_field_label": "Implementation", "is_done": False, "blocked": False,
        "conversation_id": None, "waiting_to_closeout": False,
        "sprint_item_id": item_id, "sprint_item_title": item_title, "sprint_item_priority": "P1",
        "awaiting_reply": False, "awaiting_answer": False, "awaiting_approval": False,
        "awaiting_agent_approval": False, "assigned": False, "agent_state": "idle",
    }
    row.update(values)
    return row

def item(item_id):
    quiet = {
        "awaiting_reply": False, "awaiting_answer": False, "awaiting_approval": False,
        "awaiting_agent_approval": False, "assigned": False, "agent_state": "idle",
    }
    return {"id": item_id, "created_at": 0, "conversation_id": None, "ticket_rollup": dict(quiet), **quiet}

# One Item holds all four of his groups plus a Ticket that is only an agent's to approve.
# The other holds nothing that is his.
board = {
    "columns": [{"stage": "needs_implementation", "cards": [
        card("t_approval", "si_needs_him", "Needs him", title=LONG_TITLE, awaiting_approval=True),
        card("t_assigned", "si_needs_him", "Needs him", assigned=True),
        card("t_message", "si_needs_him", "Needs him", awaiting_reply=True, conversation_signal=dict(working=False,message_for_owner=True)),
        card("t_answer", "si_needs_him", "Needs him", awaiting_answer=True),
        card("t_agents_approval", "si_needs_him", "Needs him",
             ticket_status="awaiting_approval", awaiting_agent_approval=True),
        # A broken worker still holding a proposal for him. It is his to approve, and the
        # Item at rest keeps the three groups, so it belongs under Needs your approval.
        card("t_broken", "si_needs_him", "Needs him", awaiting_approval=True, agent_state="errored"),
        card("t_backlog_owner", "si_needs_him", "Needs him", title="Review saved workspace changes", is_backlogged=True, awaiting_approval=True, awaiting_reply=True, conversation_id="backlog-"+"t_backlog_owner"),
        card("t_backlog_supervisor", "si_needs_him", "Needs him", title="Prepare the next workspace pass", is_backlogged=True, awaiting_agent_approval=True, awaiting_reply=True, conversation_id="backlog-supervisor"),
        card("t_chief_backlog", None, None, title="Revisit workspace navigation", is_backlogged=True, awaiting_approval=True, awaiting_reply=True, conversation_id="backlog-chief"),
        card("t_parent_quiet", "si_needs_him", "Needs him", title="Quiet ordinary Worker"),
        card("t_parent_blocked", "si_needs_him", "Needs him", title="Blocked ordinary Worker",ticket_status="blocked",blocked=True),
        card("t_quiet", "si_quiet", "Quiet", agent_state="working"),
        card("t_settled_backlog", "si_settled", "Settled work", title="Saved settled message", is_backlogged=True, awaiting_reply=True, conversation_id="backlog-settled"),
        card("t_settled_message", "si_settled", "Settled work", title="Ordinary settled message", awaiting_reply=True, conversation_id="ordinary-settled"),
    ]}],
    "sprint_items": [item("si_needs_him"), item("si_quiet"), {**item("si_settled"), "settled":True}],
}
for summary in board["sprint_items"]:
    summary["tickets"] = [row for column in board["columns"] for row in column["cards"] if row["sprint_item_id"] == summary["id"]]
    summary["title"] = summary["tickets"][0]["sprint_item_title"]
    summary["priority"] = "P1"
workers = {
    "chief_of_staff": {"label": "Chief of Staff", "needs_me": False, "awaiting_reply": False, "agent_working": False, "latest_turn_ended_sequence": 0},
    "workers": [],
}

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
    id='t_approval', title='Open ticket', worker_type='coding', employee_backend='claude',
    employee_launch_model=None, employee_launch_reasoning_effort=None,
    employee_configuration_editable=False, stage='needs_what_changes', ceiling='done', priority='P2',
    resolved_priority_anchors={'project': {'id': 'project_one', 'name': 'One', 'priority': 'P2'}, 'sprint_item': None},
    ticket_status='agent', ceiling_holder={'kind': 'owner', 'id': 'owner'}, conversation_id=None,
    conversation_history=[], day_ids=[], blocked=False, blocker_summary={'blocked_by': []},
    recap='Orientation', guidance='', pending_proposal=None,
    field_values={'brief': 'The request', 'success_condition': 'What done means', 'what_changes': 'The change'})

workspace = dict(id="si_needs_him",title="Needs him",body="The orchestrator brief",priority="P1",project_id=None,project=None,
    created_at=0,updated_at=0,supervisor=dict(agent_key="sprint_item:si_needs_him",conversation_id=None),
    tickets=[],artifacts=[],conversation_history=[],ticket_rollup={},settled=False)

def respond(route):
    path = route.request.url.split("/api/", 1)[1].split("?")[0]
    if path == "board": result = board
    elif path == "workers": result = workers
    elif path == "worker-types": result = manifest
    elif path == "backends": result = {"backends": []}
    elif path.endswith("/workspace"): result = workspace
    elif path.endswith("/start-values"): result = {"backend_key":"codex","model":None,"reasoning_effort":None}
    elif path.startswith("tickets/") or path == "tickets": result = ticket
    elif path == "chief/conversation": result = {"conversation_id":None}
    elif path == "chief/children": result = {"tickets": []}
    else: result = {}
    route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    with browser_failure_evidence(browser, "workspace-rail-at-rest-browser"):
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.context.tracing.start(screenshots=True,snapshots=True,sources=True)
        page.on("pageerror",lambda error:errors.append(str(error)))
        page.route("**/api/**", respond)
        page.goto(sys.argv[1])

        needs_him = page.locator('[data-sprint-item="si_needs_him"]')
        needs_him.wait_for()

        # Nothing is selected, and the groups are already there.
        assert page.locator('[data-sprint-item] [aria-current="page"]').count() == 0
        assert page.locator(".orchestrator-card.selected").count() == 0

        # Backlog stays outside the sidebar. An answer
        # he owes sits under the decision he owes and above the Stage that is merely his.
        headers = needs_him.locator("[data-bucket-key]")
        expect(headers).to_have_count(4)
        assert [headers.nth(i).get_attribute("data-bucket-key") for i in range(4)] == [
            "awaiting_approval", "awaiting_answer", "assigned", "awaiting_reply"
        ]
        assert [
            headers.nth(i).locator(".bucket-label").text_content() for i in range(4)
        ] == ["Needs your approval", "Needs your answer", "Yours", "Messages"]

        # The rows are there too, one Ticket under one header.
        for ticket_id, key in [
            ("t_approval", "awaiting_approval"),
            ("t_broken", "awaiting_approval"),
            ("t_assigned", "assigned"),
            ("t_message", "awaiting_reply"),
            ("t_answer", "awaiting_answer"),
        ]:
            row = needs_him.locator('[data-ticket-id="' + ticket_id + '"]')
            expect(row).to_have_count(1)
            assert row.is_visible()
            assert needs_him.locator('[data-bucket-key="' + key + '"] [data-ticket-id="' + ticket_id + '"]').count() == 1

        # A proposal held by an agent is not his, and never reaches the rail's Items.
        assert needs_him.locator('[data-ticket-id="t_agents_approval"]').count() == 0
        assert needs_him.locator('[data-bucket-key="status_awaiting_approval"]').count() == 0
        assert "Awaiting an agent's approval" not in needs_him.inner_text()

        # An Item with none of the three is its title and its mark, and nothing else.
        quiet = page.locator('[data-sprint-item="si_quiet"]')
        assert quiet.locator("[data-bucket-key]").count() == 0
        assert quiet.locator("[data-ticket-id]").count() == 0

        # Only an exact conversation message creates a dot. Child attention does not
        # create a supervisor dot, and approval/ask/running/error rows have no mark.
        expect(needs_him.locator(".item-head .stage-mark")).to_have_count(0)
        expect(needs_him.locator('[data-ticket-id="t_message"] .stage-mark')).to_have_count(1)
        expect(needs_him.locator('[data-ticket-id]:not([data-ticket-id="t_message"]) .stage-mark')).to_have_count(0)
        expect(quiet.locator(".stage-mark")).to_have_count(0)
        expect(page.locator("[data-chief-destination] .stage-mark")).to_have_count(0)

        # Chief's ask/running/error-like work facts cannot create its message dot.
        chief = workers["chief_of_staff"]
        chief.update(needs_me=True, agent_working=True)
        page.evaluate("window.__refresh()")
        expect(page.locator("[data-chief-destination] .stage-mark")).to_have_count(0)
        chief["awaiting_reply"] = True
        chief["conversation_signal"] = dict(working=False,message_for_owner=True)
        page.evaluate("window.__refresh()")
        expect(page.locator("[data-chief-destination] .stage-mark")).to_have_count(1)
        expect(page.locator("[data-chief-destination] .stage-mark")).to_have_attribute("aria-label", "Unread message")
        chief["awaiting_reply"] = False
        chief["conversation_signal"] = dict(working=False,message_for_owner=False)
        page.evaluate("window.__refresh()")
        expect(page.locator("[data-chief-destination] .stage-mark")).to_have_count(0)

        # Backlogged owner/supervisor/unread rows never re-enter active or Chief cards.
        chief_card = page.locator(".orchestrator-card.chief")
        def assert_backlog_absent():
            expect(page.locator('.approved-rail [data-bucket-key="backlog"]')).to_have_count(0)
            for ticket_id in ["t_backlog_owner", "t_backlog_supervisor", "t_chief_backlog", "t_settled_backlog"]:
                expect(page.locator('.approved-rail [data-ticket-id="'+ticket_id+'"]')).to_have_count(0)
            expect(page.locator('[data-message-conversation="backlog-settled"]')).to_have_count(0)
            expect(page.locator('[data-message-conversation="backlog-t_backlog_owner"]')).to_have_count(0)
        assert_backlog_absent()
        owner = next(r for r in board["columns"][0]["cards"] if r["id"] == "t_backlog_owner")
        owner["is_backlogged"] = False
        page.evaluate("window.__refresh()")
        expect(needs_him.locator('[data-bucket-key="awaiting_approval"] [data-ticket-id="t_backlog_owner"]')).to_have_count(1)
        assert owner["awaiting_approval"] is True
        owner["is_backlogged"] = True
        page.evaluate("window.__refresh()")
        assert_backlog_absent()
        page.get_by_role("button", name="Settled (1)").click()
        expect(page.locator('[data-message-conversation="ordinary-settled"]')).to_be_visible()
        assert_backlog_absent()

        # Parent hover has the ordinary child control and no backlogged title.
        needs_him.locator(".item-head").hover()
        panel = page.locator("[data-rail-hover-panel]")
        expect(panel).to_have_attribute("aria-hidden", "false")
        expect(panel).to_contain_text(LONG_TITLE)
        for title in ["Review saved workspace changes", "Prepare the next workspace pass"]:
            expect(panel).not_to_contain_text(title)
        chief_card.locator(".item-head").hover()
        expect(panel).to_contain_text("Chief of Staff")
        expect(panel).not_to_contain_text("Revisit workspace navigation")
        page.mouse.move(1200, 900)
        expect(panel).to_have_attribute("aria-hidden", "true")

        # The relocated rail proof keeps real keyboard/pointer/CSS and local persistence.
        page.set_viewport_size(dict(width=1440,height=1000))
        left = page.locator(".board-workspace-left")
        grip = page.get_by_role("separator",name="Resize workspace panel")
        page.wait_for_function("Math.abs(Number(document.querySelector('[role=separator]').getAttribute('aria-valuenow'))-document.querySelector('.board-workspace-left').getBoundingClientRect().width)<2")
        selected_row = needs_him.locator('[data-ticket-id="t_approval"]')
        selected_row.click()
        expect(selected_row).to_have_attribute("aria-current","page")
        selection_hash = page.evaluate("location.hash")
        grip.focus()
        expect(grip).to_be_focused()
        original = left.evaluate("e=>e.getBoundingClientRect().width")
        grip.press("ArrowRight")
        grown = left.evaluate("e=>e.getBoundingClientRect().width")
        assert grown > original
        page.reload()
        expect(grip).to_be_visible()
        assert abs(left.evaluate("e=>e.getBoundingClientRect().width")-grown)<2
        assert page.evaluate("location.hash") == selection_hash
        expect(selected_row).to_have_attribute("aria-current","page")
        bounds = grip.bounding_box()
        page.mouse.move(bounds["x"]+bounds["width"]/2,bounds["y"]+50)
        page.mouse.down()
        page.mouse.move(bounds["x"]+65,bounds["y"]+50,steps=4)
        page.mouse.up()
        assert left.evaluate("e=>e.getBoundingClientRect().width") > grown
        grip.dblclick()
        assert abs(left.evaluate("e=>e.getBoundingClientRect().width")-original)<2
        row = needs_him.locator('[data-ticket-id="t_approval"]')
        row.focus()
        expect(row).to_be_focused()
        row.hover()
        expect(panel).to_contain_text(LONG_TITLE)
        expect(panel).to_contain_text("Stages")
        expect(panel).to_contain_text("brief")
        needs_him.locator(".item-head").hover()
        expect(panel).to_contain_text("Quiet ordinary Worker")
        expect(panel).to_contain_text("Blocked ordinary Worker")
        expect(panel).not_to_contain_text("Stages")
        expect(panel).to_have_count(1)
        page.evaluate("location.hash = '#/workspace'")
        expect(selected_row).not_to_have_attribute("aria-current","page")
        page.set_viewport_size(dict(width=820,height=1000))
        row.focus()
        expect(row).to_be_focused()
        expect(row).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

        # Each popup branch consumes changed query responses while continuously open.
        page.set_viewport_size(dict(width=1440,height=1000))
        worker = board["columns"][0]["cards"][0]
        supervisor = board["sprint_items"][0]
        for target, facts in [(row,worker),(needs_him.locator(".item-head"),supervisor),(chief_card.locator(".item-head"),chief)]:
            target.hover()
            expect(panel).to_have_attribute("aria-hidden","false")
            identity = panel.element_handle()
            for signal, winner in [(dict(working=True,message_for_owner=False),"working"),(dict(working=True,message_for_owner=True),"message"),(dict(working=True,message_for_owner=False),"working"),(dict(working=False,message_for_owner=False),None)]:
                facts["conversation_signal"] = signal
                page.evaluate("window.__refresh()")
                title = panel.locator(".title")
                expect(title.locator("[data-conversation-signal]")).to_have_count(0 if winner is None else 1)
                if winner:
                    expect(title.locator("[data-conversation-signal]")).to_have_attribute("data-conversation-signal",winner)
                assert identity.evaluate("e=>e===document.querySelector('[data-rail-hover-panel]')")
                expect(panel).to_have_attribute("aria-hidden","false")

        # Compact destinations use the real address, selected pane and browser return.
        page.set_viewport_size(dict(width=560,height=1000))
        for selector, pane in [('[data-ticket-id="t_approval"]','[data-screen="ticket"]'),('[data-sprint-item="si_needs_him"] .item-head','[data-sprint-item-workspace="si_needs_him"]'),('[data-chief-destination]','.chief-workspace')]:
            target = page.locator(selector).first
            target.focus()
            expect(target).to_be_focused()
            target.click()
            expect(page.locator(pane)).to_be_visible()
            expect(left).not_to_be_visible()
            page.go_back()
            expect(left).to_be_visible()
            target.focus()
            expect(target).to_be_focused()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

        # Selection is the card. No count line stands in for the groups any more.
        assert page.locator(".board-workspace-item-line").count() == 0
        assert errors == [], errors
    browser.close()
`;
  const child = spawn(
    process.env.PANELS_TEST_PYTHON || join(root, "..", ".venv", "bin", "python"),
    ["-c", script, `http://127.0.0.1:${address.port}/.test-scratch/${stem}.html`],
    { stdio: "inherit", cwd: join(root,"..") }
  );
  const code = await new Promise((resolve) => child.on("exit", resolve));
  assert.equal(code, 0, "Workspace rail at-rest browser proof failed");
} finally {
  await server?.close();
  await Promise.all([host, main, index].map((path) => rm(path, { force: true })));
}

console.log("workspace-rail-at-rest-browser.test.mjs: all assertions passed");
