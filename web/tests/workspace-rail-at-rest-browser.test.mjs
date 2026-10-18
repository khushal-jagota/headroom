import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { scratchDirectory } from "./support/scratch.mjs";

// One existing BoardRoute host proves the sidebar's presentation and browser-only
// risks: live movement/paint, input modality, clipping, and persistent selection.
// Domain membership and stage wording also have cheaper unit coverage.
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const stem = `workspace-rail-at-rest-${process.pid}`;
const scratchRoot = await scratchDirectory();
const host = join(scratchRoot, `${stem}.svelte`);
const main = join(scratchRoot, `${stem}.ts`);
const index = join(scratchRoot, `${stem}.html`);
const cacheDir = join(scratchRoot, `${stem}.vite`);
let server;
const startedAt = performance.now();

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
    `<html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`,
    "utf8"
  );

  server = await createServer({
    root,
    configFile: false,
    cacheDir,
    plugins: [svelte()],
    server: { host: "127.0.0.1", port: 0 },
    logLevel: "error"
  });
  await server.listen();
  const address = server.httpServer.address();
  assert.ok(address && typeof address === "object");

  const script = String.raw`
import json, os, pathlib, sys, time, re
from playwright.sync_api import sync_playwright, expect
from tests.e2e.harness import browser_failure_evidence

ORCHESTRATOR_RECAP = "Coordinate the workspace sidebar with its current shipping data paths."
LONG_TITLE = "Review the entire Worker title when the adjustable rail clips this long approval row"

def card(ticket_id, item_id, item_title, **values):
    row = {
        "id": ticket_id, "title": ticket_id, "is_backlogged": False, "priority": "P1", "deadline": None,
        "project_id": None, "project": None, "group_project_id": None, "group_project": None,
        "activity_at": 0, "has_pending_proposal": False, "ticket_status": "empty",
        "worker_type": "coding", "employee_backend": "codex", "stage": "needs_implementation",
        "stage_label": "Implementation", "gating_field": "implementation",
        "gating_field_label": "Implementation", "is_done": False, "blocked": False,
        "conversation_id": None, "waiting_to_closeout": False, "recap_preview": "A concise recap from the current projection.",
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

# One orchestrator mixes attention, ongoing work, and ineligible overlap rows.
# The other has no eligible work, so its existing quiet line must remain visible.
board = {
    "columns": [{"stage": "needs_implementation", "cards": [
        card("t_approval", "si_needs_him", "Needs him", title=LONG_TITLE, awaiting_approval=True, awaiting_reply=True, conversation_signal=dict(working=False,message_for_owner=True)),
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
        card("t_quiet", "si_quiet", "Quiet", ticket_status="blocked", blocked=True),
        card("t_done_overlap", "si_needs_him", "Needs him", stage="done", is_done=True, awaiting_approval=True),
        card("t_cancelled_overlap", "si_needs_him", "Needs him", ticket_status="cancelled", awaiting_answer=True),
        card("t_blocked_overlap", "si_needs_him", "Needs him", ticket_status="blocked", blocked=True, assigned=True),
        card("t_agent", "si_needs_him", "Needs him", ticket_status="agent", conversation_signal=dict(working=True,message_for_owner=False)),
        card("t_consequences", "si_needs_him", "Needs him", waiting_to_closeout=True),
        card("t_settled_backlog", "si_settled", "Settled work", title="Saved settled message", is_backlogged=True, awaiting_reply=True, conversation_id="backlog-settled"),
        card("t_settled_message", "si_settled", "Settled work", title="Ordinary settled message", awaiting_reply=True, conversation_id="ordinary-settled"),
    ]}],
    "sprint_items": [item("si_needs_him"), item("si_quiet"), {**item("si_settled"), "settled":True}],
}
for summary in board["sprint_items"]:
    summary["tickets"] = [row for column in board["columns"] for row in column["cards"] if row["sprint_item_id"] == summary["id"]]
    summary["title"] = summary["tickets"][0]["sprint_item_title"]
    summary["priority"] = "P1"
    summary["recap_preview"] = ORCHESTRATOR_RECAP
workers = {
    "chief_of_staff": {"label": "Chief of Staff", "needs_me": False, "awaiting_reply": False, "agent_working": False, "latest_turn_ended_sequence": 0},
    "workers": [],
}

FIELDS = ['brief', 'success_condition', 'what_changes', 'plan', 'implementation']
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
    elif path.endswith("/workspace"): result = {**workspace,"tickets":board["sprint_items"][0]["tickets"]}
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
        page.on("pageerror",lambda error:(errors.append(str(error)),print("Page error:",error,flush=True)))
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
        expect(headers).to_have_count(7)
        assert [headers.nth(i).get_attribute("data-bucket-key") for i in range(7)] == [
            "awaiting_approval", "awaiting_answer", "assigned",
            "agent", "waiting_to_closeout", "status_awaiting_approval", "empty"
        ]

        # The rows are there too, one Ticket under one header.
        for ticket_id, key in [
            ("t_approval", "awaiting_approval"),
            ("t_broken", "awaiting_approval"),
            ("t_assigned", "assigned"),
            ("t_message", "empty"),
            ("t_answer", "awaiting_answer"),
        ]:
            row = needs_him.locator('[data-ticket-id="' + ticket_id + '"]')
            expect(row).to_have_count(1)
            assert row.is_visible()
            assert needs_him.locator('[data-bucket-key="' + key + '"] [data-ticket-id="' + ticket_id + '"]').count() == 1

        # Ongoing agent work is visible beside owner attention. Ineligible rows
        # stay absent even when stale attention flags overlap.
        expect(needs_him.locator('[data-ticket-id="t_agents_approval"]')).to_have_count(1)
        for excluded in ["t_done_overlap", "t_cancelled_overlap", "t_blocked_overlap", "t_parent_blocked"]:
            expect(needs_him.locator('[data-ticket-id="'+excluded+'"]')).to_have_count(0)
        expect(needs_him.locator('[data-bucket-key="empty"]')).to_contain_text("Other")

        # An orchestrator with no eligible children uses the existing quiet line.
        quiet = page.locator('[data-sprint-item="si_quiet"]')
        assert quiet.locator("[data-bucket-key]").count() == 0
        assert quiet.locator("[data-ticket-id]").count() == 0
        expect(quiet.locator(".board-workspace-quiet-line")).to_have_text("Nothing in this list")

        # Only an exact conversation message creates a dot. Child attention does not
        # create a supervisor dot, and approval/ask/running/error rows have no mark.
        expect(needs_him.locator(".item-head .stage-mark")).to_have_count(0)
        expect(needs_him.locator('[data-ticket-id="t_message"] .stage-mark')).to_have_count(1)
        expect(needs_him.locator('[data-ticket-id="t_agent"] .stage-mark')).to_have_attribute("data-conversation-signal", "working")
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

        # Keep the global settled-message discovery above proved, then close it
        # so captures isolate the active rail's approved group structure.
        page.get_by_role("button", name="Settled (1)").click()

        # The revision preserves unread facts and clears the actual query signal
        # without recategorizing, reordering, or brightening the quiet row.
        artifact_dir = pathlib.Path(os.environ.get("PANELS_SIDEBAR_ARTIFACT_DIR", "/home/vps/Deployments/Panels/current/data/files/tickets/t_21sxtdb3/artifacts/revision"))
        artifact_dir.mkdir(parents=True,exist_ok=True)
        unread = next(r for r in board["columns"][0]["cards"] if r["id"] == "t_message")
        running = next(r for r in board["columns"][0]["cards"] if r["id"] == "t_agent")
        unread_row = needs_him.locator('[data-ticket-id="t_message"]')
        working_row = needs_him.locator('[data-ticket-id="t_agent"]')
        running["awaiting_reply"] = True
        running["conversation_signal"] = dict(working=True,message_for_owner=True)
        page.evaluate("window.__refresh()")
        expect(working_row.locator("[data-conversation-signal]")).to_have_attribute("data-conversation-signal","message")
        expect(needs_him.locator('[data-bucket-key="awaiting_reply"]')).to_have_count(0)
        expect(needs_him.locator('[data-bucket-key="empty"]')).to_contain_text("t_message")
        expect(needs_him.locator('[data-bucket-key="agent"]')).to_contain_text("t_agent")
        expect(needs_him.locator('[data-bucket-key="awaiting_approval"] [data-ticket-id="t_approval"] [data-conversation-signal]')).to_have_attribute("data-conversation-signal","message")
        def row_state(row):
            return row.evaluate("e=>({group:e.closest('[data-bucket-key]').dataset.bucketKey,tone:e.closest('[data-tone]').dataset.tone,color:getComputedStyle(e).color,order:[...e.closest('[data-bucket-key]').querySelectorAll('[data-ticket-id]')].map(x=>x.dataset.ticketId)})")
        before_unread, before_working = row_state(unread_row),row_state(working_row)
        assert before_unread["tone"] == before_working["tone"] == "quiet"
        page.wait_for_timeout(600)
        page.screenshot(path=str(artifact_dir/"sidebar-unread-quiet-working-and-needs-you.png"))
        unread["awaiting_reply"] = False
        unread["conversation_signal"] = dict(working=False,message_for_owner=False)
        approval = board["columns"][0]["cards"][0]
        approval_row = needs_him.locator('[data-ticket-id="t_approval"]')
        before_approval = row_state(approval_row)
        approval["awaiting_reply"] = False
        approval["conversation_signal"] = dict(working=False,message_for_owner=False)
        running["awaiting_reply"] = False
        running["conversation_signal"] = dict(working=True,message_for_owner=False)
        page.evaluate("window.__refresh()")
        expect(unread_row.locator(".stage-mark")).to_have_count(0)
        expect(working_row.locator("[data-conversation-signal]")).to_have_attribute("data-conversation-signal","working")
        page.wait_for_timeout(600)
        after_unread, after_working = row_state(unread_row),row_state(working_row)
        assert before_unread == after_unread
        assert before_working == after_working
        expect(approval_row.locator(".stage-mark")).to_have_count(0)
        after_approval = row_state(approval_row)
        assert before_approval == after_approval
        assert after_approval["tone"] == "attention"
        page.screenshot(path=str(artifact_dir/"sidebar-read-cleared-same-groups.png"))
        (artifact_dir/"sidebar-unread-clear-proof.json").write_text(json.dumps(dict(quiet_before=before_unread,quiet_after=after_unread,working_before=before_working,working_after=after_working,approval_before=before_approval,approval_after=after_approval),indent=2))

        # Parent hover uses the authoritative recap, with no child inventory.
        needs_him.locator(".item-head").hover()
        panel = page.locator("[data-rail-hover-panel]")
        expect(panel).to_have_attribute("aria-hidden", "false")
        expect(panel.locator(".recap")).to_have_text(ORCHESTRATOR_RECAP)
        expect(panel).not_to_contain_text(LONG_TITLE)
        for title in ["Review saved workspace changes", "Prepare the next workspace pass"]:
            expect(panel).not_to_contain_text(title)
        chief_card.locator(".item-head").hover()
        expect(panel).to_contain_text("Chief of Staff")
        expect(panel.locator(".recap")).to_have_text("Nothing in this list right now.")
        expect(panel).not_to_contain_text("Revisit workspace navigation")
        # Capture both real popup branches at desktop and compact widths.
        for viewport, label in [(dict(width=1440,height=1000),"desktop"),(dict(width=560,height=1000),"compact")]:
            page.set_viewport_size(viewport)
            for target, name, recap in [(needs_him.locator(".item-head"),"orchestrator",ORCHESTRATOR_RECAP),(chief_card.locator(".item-head"),"chief","Nothing in this list right now.")]:
                target.hover()
                expect(panel).to_have_attribute("aria-hidden","false")
                expect(panel.locator(".recap")).to_have_text(recap)
                expect(panel.locator("[data-bucket-key],.group-label,.child-title")).to_have_count(0)
                expect(panel).not_to_contain_text(LONG_TITLE)
                page.wait_for_function("getComputedStyle(document.querySelector('[data-rail-hover-panel]')).opacity === '1'")
                assert panel.bounding_box()["x"] >= 0
                assert panel.bounding_box()["x"]+panel.bounding_box()["width"] <= viewport["width"]
                page.screenshot(path=str(artifact_dir/f"sidebar-{name}-{label}-popup.png"))
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
        expect(panel).not_to_contain_text("Stages")
        expect(panel).to_contain_text("Awaiting your approval at implementation.")
        needs_him.locator(".item-head").hover()
        expect(panel.locator(".recap")).to_have_text(ORCHESTRATOR_RECAP)
        expect(panel).not_to_contain_text("Quiet ordinary Worker")
        expect(panel).not_to_contain_text("Blocked ordinary Worker")
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

        # Real keyboard modality opens the popup. A pointer click must not leave
        # a keyboard-style popup open while the mouse is outside the rail.
        page.mouse.move(1200, 950)
        page.locator("body").click(position=dict(x=1100,y=920))
        row.focus()
        page.keyboard.press("Tab")
        page.keyboard.press("Shift+Tab")
        expect(row).to_be_focused()
        expect(panel).to_have_attribute("aria-hidden", "false")
        expect(panel).to_contain_text("A concise recap from the current projection.")
        expect(panel).to_contain_text("Awaiting your approval at implementation.")
        expect(panel).not_to_contain_text("Blocked by")
        page.mouse.move(1200, 950)
        row.click()
        page.mouse.move(1200, 950)
        expect(panel).to_have_attribute("aria-hidden", "true")

        # Hover freezes structure through a live refetch. Leaving applies the latest
        # answer, and a row that rejoins a group enters below its existing members.
        ordinary = next(r for r in board["columns"][0]["cards"] if r["id"] == "t_parent_quiet")
        needs_him.locator('[data-ticket-id="t_parent_quiet"]').hover()
        expect(panel).to_have_attribute("aria-hidden","false")
        ordinary["awaiting_answer"] = True
        page.evaluate("window.__refresh()")
        expect(needs_him.locator('[data-bucket-key="empty"] [data-ticket-id="t_parent_quiet"]')).to_have_count(1)
        expect(needs_him.locator('[data-bucket-key="awaiting_answer"] [data-ticket-id="t_parent_quiet"]')).to_have_count(0)
        page.mouse.move(1200,950)
        expect(needs_him.locator('[data-bucket-key="awaiting_answer"] [data-ticket-id="t_parent_quiet"]')).to_have_count(1)
        expect(needs_him.locator('[data-bucket-key="awaiting_answer"] [data-ticket-id]').last).to_have_attribute("data-ticket-id","t_parent_quiet")
        ordinary["awaiting_answer"] = False
        page.evaluate("window.__refresh()")
        expect(needs_him.locator('[data-bucket-key="empty"] [data-ticket-id="t_parent_quiet"]')).to_have_count(1)
        ordinary["awaiting_answer"] = True
        page.evaluate("window.__refresh()")
        expect(needs_him.locator('[data-bucket-key="awaiting_answer"] [data-ticket-id]').last).to_have_attribute("data-ticket-id","t_parent_quiet")
        # Query activity never becomes a recency sort inside the group.
        ordinary["activity_at"] = 9999
        page.evaluate("window.__refresh()")
        expect(needs_him.locator('[data-bucket-key="awaiting_answer"] [data-ticket-id]').first).to_have_attribute("data-ticket-id","t_answer")

        # Width limits protect real text layout and title ellipsis, not a mock box.
        artifact_dir = pathlib.Path(os.environ.get("PANELS_SIDEBAR_ARTIFACT_DIR", "/home/vps/Deployments/Panels/current/data/files/tickets/t_21sxtdb3/artifacts/revision"))
        artifact_dir.mkdir(parents=True,exist_ok=True)
        for width in [232,520]:
            grip.focus()
            for _ in range(15): grip.press("Shift+ArrowLeft" if width == 232 else "Shift+ArrowRight")
            assert abs(left.bounding_box()["width"]-width)<2
            clipped = row.locator(".ticket-row-title")
            assert clipped.evaluate("e => getComputedStyle(e).textOverflow") == "ellipsis"
            assert clipped.evaluate("e => e.scrollWidth > e.clientWidth")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            row.hover()
            expect(panel).to_contain_text(LONG_TITLE)
            page.wait_for_function("getComputedStyle(document.querySelector('[data-rail-hover-panel]')).opacity === '1'")
            page.screenshot(path=str(artifact_dir/f"sidebar-{width}-popup.png"))

        page.set_viewport_size(dict(width=1440,height=1400))
        # Cross-parent motion must use measured geometry and interpolate both
        # row and spinner colors. Capture actual computed frames, not only endpoints.
        page.mouse.move(1200,950)
        page.locator("body").click(position=dict(x=1100,y=920))
        mover = next(r for r in board["columns"][0]["cards"] if r["id"] == "t_agent")
        source = board["sprint_items"][0]
        destination = board["sprint_items"][1]
        mover_row = page.locator('[data-ticket-id="t_agent"]').first
        mover_row.scroll_into_view_if_needed()
        page.mouse.move(1200,950)
        before_y = mover_row.bounding_box()["y"]
        before_color = mover_row.evaluate("e=>getComputedStyle(e).color")
        before_spinner = mover_row.locator(".stage-mark").evaluate("e=>getComputedStyle(e).borderTopColor")
        source["tickets"] = [r for r in source["tickets"] if r["id"] != "t_agent"]
        destination["tickets"].append(mover)
        mover["assigned"] = True
        page.evaluate("""() => {
          window.__motionEvidence=[];
          const start=performance.now();
          const sample=()=>{
            const row=document.querySelector('[data-ticket-id="t_agent"]');
            const mark=row?.querySelector('.stage-mark');
            const css=row&&getComputedStyle(row);
            const rect=row?.getBoundingClientRect();
            const animations=row?.getAnimations().map(a=>({duration:a.effect.getTiming().duration,easing:a.effect.getTiming().easing}));
            const overlaps=rect&&[...document.querySelectorAll('.worker-row')].some(other=>{const r=other.getBoundingClientRect();return other!==row&&r.top<rect.bottom&&r.bottom>rect.top&&r.left<rect.right&&r.right>rect.left;});
            const top=rect&&document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2)?.closest('[data-ticket-id]')?.getAttribute('data-ticket-id');
            window.__motionEvidence.push({ms:performance.now()-start,y:row?.getBoundingClientRect().y,
              color:css?.color,background:css?.backgroundColor,z:css?.zIndex,
              spinner:mark&&getComputedStyle(mark).borderTopColor,
              animations,overlaps,top,travelling:row?.classList.contains('rail-travelling'),
              parent:row?.closest('[data-sprint-item]')?.getAttribute('data-sprint-item')});
            if(performance.now()-start<1100)requestAnimationFrame(sample);
          };
          requestAnimationFrame(sample); void window.__refresh();
        }""")
        page.wait_for_timeout(350)
        page.screenshot(path=str(artifact_dir/"sidebar-motion-mid.png"))
        page.wait_for_timeout(1000)
        samples = page.evaluate("window.__motionEvidence")
        (artifact_dir/"sidebar-motion-samples.json").write_text(json.dumps(samples,indent=2))
        moving = [x for x in samples if x.get("travelling")]
        assert moving, "No actual cross-parent travelling frames"
        after_y = mover_row.bounding_box()["y"]
        after_color = mover_row.evaluate("e=>getComputedStyle(e).color")
        after_spinner = mover_row.locator(".stage-mark").evaluate("e=>getComputedStyle(e).borderTopColor")
        assert abs(after_y-before_y)>10
        assert any(min(before_y,after_y)+1<x["y"]<max(before_y,after_y)-1 for x in moving)
        assert before_color != after_color
        assert any(x["color"] not in [before_color,after_color] for x in moving)
        assert before_spinner != after_spinner
        assert any(x["spinner"] not in [before_spinner,after_spinner] for x in moving)
        assert all(x["background"].startswith("rgb(") for x in moving), "Traveller surface must be immediately opaque"
        assert all(int(x["z"])>0 for x in moving)
        timings=[a for x in moving for a in x.get("animations",[])]
        assert timings and all(360<=a["duration"]<=560 for a in timings)
        assert all(a["easing"] == "cubic-bezier(0.4, 0.02, 0.2, 1)" for a in timings)
        crossing=[x for x in moving if x.get("overlaps")]
        assert crossing, "No measured crossing frames"
        assert all(x.get("top")=="t_agent" for x in crossing), crossing
        page.screenshot(path=str(artifact_dir/"sidebar-motion-settled.png"))

        # A departure fades before its space closes. An arrival opens its space
        # before its row fades in; its new heading also fades instead of jumping.
        def sample_phase(name):
            page.evaluate("""() => {
              window.__phaseEvidence=[]; const start=performance.now();
              const sample=()=>{
                const row=document.querySelector('[data-ticket-id="t_agent"]');
                const card=document.querySelector('[data-sprint-item="si_quiet"]');
                const heading=card.querySelector('.bucket-label');
                window.__phaseEvidence.push({ms:performance.now()-start,
                  opacity:row&&Number(getComputedStyle(row).opacity),height:card.getBoundingClientRect().height,
                  heading:heading&&Number(getComputedStyle(heading).opacity)});
                if(performance.now()-start<900)requestAnimationFrame(sample);
              };requestAnimationFrame(sample);void window.__refresh();
            }""")
            page.wait_for_timeout(85 if name=="departure" else 220)
            page.screenshot(path=str(artifact_dir/f"sidebar-{name}-mid.png"))
            page.wait_for_timeout(950)
            values=page.evaluate("window.__phaseEvidence")
            (artifact_dir/f"sidebar-{name}-samples.json").write_text(json.dumps(values,indent=2))
            return values
        mover["blocked"] = True
        departure=sample_phase("departure")
        fading=[x for x in departure if x["opacity"] is not None and 0<x["opacity"]<1]
        assert fading
        assert max(x["height"] for x in fading)-min(x["height"] for x in fading)<1
        assert departure[-1]["opacity"] is None
        assert departure[-1]["height"]<fading[0]["height"]
        mover["blocked"] = False
        arrival=sample_phase("arrival")
        entered=[x for x in arrival if x["opacity"] is not None]
        assert any(x["opacity"]==0 for x in entered)
        assert any(0<x["opacity"]<1 for x in entered)
        assert all(abs(x["height"]-entered[-1]["height"])<1 for x in entered)
        assert any(x["heading"] is not None and 0<x["heading"]<1 for x in arrival)

        # Reduced motion must stop both CSS spinner and scripted row travel.
        page.emulate_media(reduced_motion="reduce")
        spinner = page.locator('[data-ticket-id="t_agent"] [data-conversation-signal="working"]')
        expect(spinner).to_have_count(1)
        assert spinner.evaluate("e => getComputedStyle(e).animationName") == "none"
        mover["assigned"] = False
        page.evaluate("window.__refresh()")
        expect(page.locator('[data-sprint-item="si_quiet"] [data-bucket-key="agent"] [data-ticket-id="t_agent"]')).to_have_count(1)
        assert page.locator(".approved-rail").evaluate("e=>e.getAnimations({subtree:true}).length") == 0
        mover["assigned"] = True
        page.evaluate("window.__refresh()")
        expect(page.locator('[data-sprint-item="si_quiet"] [data-bucket-key="assigned"] [data-ticket-id="t_agent"]')).to_have_count(1)
        assert page.locator(".approved-rail").evaluate("e=>e.getAnimations({subtree:true}).length") == 0
        page.mouse.move(1200,950)
        page.emulate_media(reduced_motion="no-preference")

        # A phone dispatches pointerover before touchstart. No-hover devices never
        # open from that event, and a cancelled hold does not open after 400ms.
        phone = browser.new_context(viewport=dict(width=560,height=900),is_mobile=True,has_touch=True)
        phone_page = phone.new_page()
        phone_page.route("**/api/**", respond)
        phone_page.goto(sys.argv[1])
        assert phone_page.evaluate("innerWidth") == 560
        phone_row = phone_page.locator('[data-ticket-id="t_approval"]').first
        phone_panel = phone_page.locator("[data-rail-hover-panel]")
        phone_row.dispatch_event("pointerover", dict(pointerType="touch",bubbles=True))
        phone_page.wait_for_timeout(250)
        expect(phone_panel).to_have_attribute("aria-hidden","true")
        cdp = phone.new_cdp_session(phone_page)
        bounds = phone_row.bounding_box()
        point = dict(x=bounds["x"]+20,y=bounds["y"]+bounds["height"]/2)
        def touch(kind, points): cdp.send("Input.dispatchTouchEvent", dict(type=kind,touchPoints=points))
        touch("touchStart",[point])
        phone_page.wait_for_timeout(180)
        touch("touchMove",[dict(x=point["x"]+40,y=point["y"])])
        phone_page.wait_for_timeout(300)
        expect(phone_panel).to_have_attribute("aria-hidden","true")
        touch("touchEnd",[])
        touch("touchStart",[point])
        phone_page.wait_for_timeout(440)
        expect(phone_panel).to_have_attribute("aria-hidden","false")
        phone_page.screenshot(path=str(artifact_dir/"sidebar-phone-hold.png"))
        touch("touchEnd",[])

        # The existing Settled title and unread child use this same input gate.
        phone_page.get_by_role("button",name="Settled (1)").click()
        for target in [phone_page.locator(".settled-title"),phone_page.locator('[data-message-conversation="ordinary-settled"]')]:
            phone_row.dispatch_event("pointerleave",dict(pointerType="touch"))
            target.scroll_into_view_if_needed()
            target.dispatch_event("pointerover",dict(pointerType="touch",bubbles=True))
            phone_page.wait_for_timeout(250)
            expect(phone_panel).to_have_attribute("aria-hidden","true")
            bounds=target.bounding_box()
            point=dict(x=bounds["x"]+20,y=bounds["y"]+bounds["height"]/2)
            touch("touchStart",[point])
            phone_page.wait_for_timeout(440)
            expect(phone_panel).to_have_attribute("aria-hidden","false")
            touch("touchEnd",[])

        # The actual item pane is a separate shared-popup caller. Its blocked
        # child stays in the full list and states the stage truthfully.
        phone_page.evaluate("location.hash='#/workspace/item/si_needs_him'")
        item_pane=phone_page.locator('[data-sprint-item-workspace="si_needs_him"]')
        blocked_group=item_pane.locator('[data-workspace-group="blocked"]')
        if not blocked_group.evaluate("e=>e.open"): blocked_group.locator("summary").click()
        blocked_child=item_pane.locator('[data-sprint-ticket-id="t_parent_blocked"]')
        blocked_child.scroll_into_view_if_needed()
        child_panel=phone_page.locator('[data-rail-hover-panel]').first
        blocked_child.dispatch_event("pointerover",dict(pointerType="touch",bubbles=True))
        phone_page.wait_for_timeout(250)
        expect(child_panel).to_have_attribute("aria-hidden","true")
        bounds=blocked_child.bounding_box()
        point=dict(x=bounds["x"]+20,y=bounds["y"]+bounds["height"]/2)
        touch("touchStart",[point])
        phone_page.wait_for_timeout(440)
        expect(child_panel).to_have_attribute("aria-hidden","false")
        expect(child_panel.locator(".kicker")).to_contain_text("Needs him")
        expect(child_panel).to_contain_text("Blocked at implementation.")
        expect(child_panel).to_contain_text("A concise recap from the current projection.")
        expect(child_panel).not_to_contain_text("Stages")
        expect(child_panel).not_to_contain_text("Blocked by")
        phone_page.wait_for_function("getComputedStyle(document.querySelector('[data-rail-hover-panel]')).opacity==='1'")
        phone_page.screenshot(path=str(artifact_dir/"sidebar-item-phone-popup.png"))
        touch("touchEnd",[])
        assert phone_page.evaluate("location.hash") == "#/workspace/item/si_needs_him"

        phone.close()

        # Compact destinations use the real address, selected pane and browser return.
        page.evaluate("location.hash = '#/workspace'")
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

        # Native keyboard focus uses the same popup inside the actual item pane.
        page.set_viewport_size(dict(width=1440,height=1000))
        page.evaluate("location.hash='#/workspace/item/si_needs_him'")
        blocked_group=page.locator('[data-sprint-item-workspace="si_needs_him"] [data-workspace-group="blocked"]')
        if not blocked_group.evaluate("e=>e.open"): blocked_group.locator("summary").click()
        blocked_child=page.locator('[data-sprint-item-workspace="si_needs_him"] [data-sprint-ticket-id="t_parent_blocked"]')
        blocked_child.focus()
        page.keyboard.press("Tab")
        page.keyboard.press("Shift+Tab")
        expect(blocked_child).to_be_focused()
        child_panel=page.locator('[data-rail-hover-panel]').first
        expect(child_panel).to_have_attribute("aria-hidden","false")
        expect(child_panel.locator(".kicker")).to_contain_text("Needs him")
        expect(child_panel).to_contain_text("Blocked at implementation.")
        expect(child_panel).not_to_contain_text("Stages")

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
  await Promise.all([host, main, index, cacheDir].map((path) => rm(path, { force: true, recursive: true })));
}

console.log(`workspace-rail-at-rest-browser.test.mjs: all assertions passed (${((performance.now() - startedAt) / 1000).toFixed(2)}s)`);
