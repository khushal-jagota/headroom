import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { rm, writeFile, readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { scratchDirectory } from "./support/scratch.mjs";

// Browser regression for address changes and delayed full child snapshots.
// Real BoardRoute rendering must preserve Item order while completion and attention change.
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const stem = `workspace-order-${process.pid}`;
const scratchRoot = await scratchDirectory();
const host = join(scratchRoot, `${stem}.svelte`);
const main = join(scratchRoot, `${stem}.ts`);
const index = join(scratchRoot, `${stem}.html`);
const cacheDir = join(scratchRoot, `${stem}.vite`);
let server;

try {
  await writeFile(host, `<script lang="ts">
import { QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
import BoardRoute from "../src/routes/BoardRoute.svelte";
import { provideNavigationApplication } from "../src/lib/navigation/application.svelte";
const navigation=provideNavigationApplication();
(window as any).__navigation=navigation;
const hoverObservers: ReturnType<typeof navigation.owner.registerObserver>[]=[];
const register=navigation.owner.registerObserver;
navigation.owner.registerObserver=(name)=>{const observer=register(name);if(name.startsWith('workspace-hover:'))hoverObservers.push(observer);return observer;};
(window as any).__startHoverWait=()=>{
 const publication=navigation.owner.getState().publication!;
 (window as any).__hoverWaitStatus='pending';
 void navigation.owner.waitForVisibility({...publication.continuity,version:1,coverage:'complete',witnesses:['a'.repeat(64)],signature:'synthetic-proof'},
 {requiredObservers:hoverObservers.slice(-1),timeoutMs:250}).then(result=>(window as any).__hoverWaitStatus=result.status);
};
(window as any).__recover=()=>navigation.owner.recover().catch(()=>undefined);
import { parseWorkspaceAddress } from "../src/lib/workspaceAddress";

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
let address = $state(parseWorkspaceAddress(window.location.hash)!);
(window as any).__refresh = async () => {
  await client.invalidateQueries();
  const response=await (await fetch('/api/navigation/snapshot')).json();
  const previous=navigation.owner.getState().publication!;
  const keys=new Set(response.snapshot.records.map((record:any)=>JSON.stringify([record.kind,record.id])));
  const batch={...response.snapshot,base_revision:previous.continuity.revision,
    upserts:response.snapshot.records,deletes:previous.records.filter(record=>!keys.has(JSON.stringify([record.kind,record.id]))).map(record=>({kind:record.kind,id:record.id}))};
  delete batch.records;
  const published=new Promise<void>(resolve=>{ const unsubscribe=navigation.owner.subscribe(state=>{if(state.publication?.continuity.revision===batch.revision){unsubscribe();resolve();}}); });
  (window as any).__navigationSource.dispatchEvent(new MessageEvent('batch',{data:JSON.stringify({wire_version:response.wire_version,authority:response.authority,batch}),lastEventId:response.cursor}));
  await published; };
</script>

<svelte:window onhashchange={() => address = parseWorkspaceAddress(window.location.hash)!} />
<QueryClientProvider {client}>
  <BoardRoute {address} />
</QueryClientProvider>
`, "utf8");
  await writeFile(
    main,
    `globalThis.EventSource = class extends EventTarget { onerror = null; constructor(url) { super(); if(String(url).startsWith('/api/navigation/events?')) globalThis.__navigationSource=this; } close() {} }; import { mount } from "svelte"; import Host from "./${stem}.svelte"; import "../../assets/tokens.css"; import "../../assets/app.css"; mount(Host, { target: document.getElementById("app")! });`,
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
    cacheDir,
    plugins: [svelte()],
    server: { host: "127.0.0.1", port: 0 },
    logLevel: "error"
  });
  await server.listen();
  const address = server.httpServer.address();
  assert.ok(address && typeof address === "object");

  const fixturePython = await readFile(join(root,"tests/support/navigation/browser-fixture.py"),"utf8");
  const script = fixturePython + String.raw`
import json, re, sys
from playwright.sync_api import sync_playwright, expect

def card(ticket_id, item_id, item_title, **values):
    row = {
        "id": ticket_id, "title": ticket_id, "priority": "P1", "deadline": None,
        "project_id": None, "project": None, "group_project_id": None, "group_project": None,
        "activity_at": 0, "has_pending_proposal": False, "ticket_status": "empty",
        "worker_type": "coding", "employee_backend": "codex", "stage": "needs_implementation",
        "stage_label": "Implementation", "gating_field": "implementation",
        "gating_field_label": "Implementation", "is_done": False, "blocked": False,
        "conversation_id": "fixture-ticket-"+ticket_id, "waiting_to_closeout": False,
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
    return {"id": item_id, "created_at": 0, "conversation_id": "fixture-item-"+item_id, "ticket_rollup": dict(quiet), **quiet}

# The old Item has only a done child on today’s board. Its full workspace also
# contains unfinished, off-day children. The board includes those children before
# opening it, and delayed detail delivery must change neither rail nor completion.
quiet = item("si_old")["ticket_rollup"]
old = card("t_done", "si_old", "Old Item", stage="done", is_done=True)
peer = card("t_peer", "si_peer", "Peer Item")
newer = card("t_newer", "si_newer", "Newer Item")
board = {"columns": [{"stage": "needs_implementation", "cards": [newer, peer, old]}],
         "sprint_items": [{**item("si_old"), "created_at": 1},
                          {**item("si_peer"), "created_at": 2},
                          {**item("si_newer"), "created_at": 3}]}
children = [old, card("t_older", "si_old", "Old Item", assigned=True, activity_at=10),
            card("t_recent", "si_old", "Old Item", assigned=True, activity_at=20)]

def workspace(item_id):
    title = {"si_old": "Old Item", "si_peer": "Peer Item", "si_newer": "Newer Item", "si_direct": "Direct Item"}[item_id]
    rows = children if item_id == "si_old" else [card("t_" + item_id, item_id, title)]
    return {**item(item_id), "title": title, "body": "", "priority": "P1",
            "project_id": None, "project": None, "created_at": {"si_old": 1, "si_peer": 2, "si_newer": 3, "si_direct": 4}[item_id],
            "updated_at": 1, "committed_sprints": [], "planning_day_id": "day_test",
            "today_ticket_ids": ["t_done"] if item_id == "si_old" else [],
            "tickets": rows, "artifacts": [], "conversation_history": [],
            "supervisor": {"agent_key": "sprint_item:" + item_id, "conversation_id": None,
                "launch_configuration": {"employee_backend": "codex", "employee_launch_model": None,
                                         "employee_launch_reasoning_effort": None}}}

for summary in board["sprint_items"]:
    detail = workspace(summary["id"])
    summary.update(title=detail["title"], priority=detail["priority"], tickets=detail["tickets"])
workers = {"chief_of_staff": {"conversation_id":"fixture-chief", "label": "Chief of Staff", "needs_me": False, "awaiting_reply": False,
    "agent_working": False, "latest_turn_ended_sequence": 0}, "workers": []}
held = []
delay_old = True
snapshot_failure = True

def fulfill(route, result):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

def respond(route):
    path = route.request.url.split("/api/", 1)[1].split("?")[0]
    if path == "navigation/snapshot":
        if snapshot_failure:
            route.fulfill(status=503, content_type="application/json", body=json.dumps({"error":{"code":"unavailable","message":"Workspace unavailable","detail":None}}))
            return
        result = navigation_response(board,workers)
    elif path == "board" or path == "workers": raise AssertionError("summary query remains active: " + path)
    elif path.endswith("/recap-preview"): raise AssertionError("unexpected preview request: " + path)
    elif path.startswith("items/") and path.endswith("/workspace"):
        item_id = path.split("/")[1]
        if item_id == "si_old" and delay_old:
            held.append(route)
            return
        result = workspace(item_id)
    # The child click exercises the actual TicketRoute boundary without inventing
    # another complete Ticket fixture. A missing Ticket still preserves its address.
    elif path == "tickets":
        route.fulfill(status=404, content_type="application/json", body='{"detail":"Ticket not found"}')
        return
    elif path == "worker-types": result = {"worker_types": []}
    elif path == "conversation/backends": result = {"backends": []}
    elif path.endswith("start-values"): result = {"backends": [], "current": None}
    else: result = {}
    fulfill(route, result)

def order(page, expected):
    expect(page.locator(".board-workspace-left [data-sprint-item]")).to_have_count(len(expected))
    try:
        page.wait_for_function("ids => JSON.stringify([...document.querySelectorAll('.board-workspace-left [data-sprint-item]')].map(n => n.dataset.sprintItem)) === JSON.stringify(ids)", arg=expected, timeout=5000)
    except Exception:
        actual = page.locator(".board-workspace-left [data-sprint-item]").evaluate_all("nodes => nodes.map(n => n.dataset.sprintItem)")
        raise AssertionError(f"Item order changed: expected {expected}, actual {actual}")

def title(page, item_id):
    return page.locator('[data-sprint-item="' + item_id + '"] .item-title')

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", respond)
    page.goto(sys.argv[1] + "#/workspace?view=items")
    expected = ["si_old", "si_peer", "si_newer"]
    expect(page.locator('.error-line .error-code')).to_have_text('unavailable')
    expect(page.locator('.error-line .error-message')).to_have_text('Workspace unavailable')
    expect(page.locator('.quiet-line',has_text='Loading workspace')).to_have_count(0)
    snapshot_failure = False
    page.evaluate('window.__recover()')
    expect(page.locator('.error-line')).to_have_count(0)
    order(page, expected)
    # Ordinary read failures retain the rail and its existing error line.
    snapshot_failure = True
    page.evaluate('window.__recover()')
    expect(page.locator('.error-line .error-code')).to_have_text('unavailable')
    order(page, expected)
    snapshot_failure = False
    page.evaluate('window.__recover()')
    expect(page.locator('.error-line')).to_have_count(0)
    order(page, expected)
    expect(title(page, "si_old")).to_have_class(re.compile(r"\bitem-title\b"))

    page.locator('[data-sprint-item="si_old"] .item-head').click()
    expect(page.locator('[data-sprint-item="si_old"] .item-head')).to_have_attribute("aria-current", "page")
    order(page, expected)
    page.wait_for_function("() => document.querySelector('[data-sprint-item-workspace=si_old]') !== null")
    # Python route interception keeps this response pending until selection is proven.
    assert len(held) == 1, "the full workspace request was not delayed"
    delay_old = False
    fulfill(held.pop(), workspace("si_old"))
    expect(title(page, "si_old")).to_have_class(re.compile(r"\bitem-title\b"))
    order(page, expected)
    rows = page.locator('[data-sprint-item="si_old"] [data-ticket-id]')
    expect(rows).to_have_count(2)
    assert [rows.nth(i).get_attribute("data-ticket-id") for i in range(2)] == ["t_recent", "t_older"]

    page.locator('[data-sprint-item="si_old"] [data-ticket-id="t_recent"]').click()
    page.wait_for_function("() => location.hash === '#/workspace/item/si_old/t_recent'")
    order(page, expected)
    expect(page.locator('[data-sprint-item="si_old"] [data-ticket-id="t_recent"]')).to_have_class(re.compile(r"\bticket-row--active\b"))
    assert page.locator('.orchestrator-card.selected').count() == 0
    page.go_back()
    expect(page.locator('[data-sprint-item="si_old"] .item-head')).to_have_attribute("aria-current", "page")
    order(page, expected)
    page.locator('[data-sprint-item="si_peer"] .item-head').click()
    order(page, expected)
    page.go_back()
    order(page, expected)
    page.reload()
    expect(title(page, "si_old")).to_have_class(re.compile(r"\bitem-title\b"))
    order(page, expected)

    # Priority still moves an Item. Child activity changes do not reorder the
    # sidebar: persistent group entry order survives invalidation and selection.
    board["sprint_items"][1]["priority"] = "P0"
    page.evaluate("window.__refresh()")
    order(page, ["si_peer", "si_old", "si_newer"])
    children[1]["activity_at"] = 30
    page.evaluate("window.__refresh()")
    expect(rows.first).to_have_attribute("data-ticket-id", "t_recent")
    children[2]["activity_at"] = 50
    page.evaluate("window.__refresh()")
    expect(rows.first).to_have_attribute("data-ticket-id", "t_recent")
    order(page, ["si_peer", "si_old", "si_newer"])

    # Completion and clearing a direct unread mark do not settle or reorder an Item.
    # Only the owner Settle action changes settlement, never child completion.
    page.goto(sys.argv[1] + "#/workspace?view=items")
    summary = board["sprint_items"][0]
    summary["tickets"] = [old]
    summary["awaiting_reply"] = True
    summary["conversation_signal"] = dict(working=False, message_for_owner=True)
    page.evaluate("window.__refresh()")
    expect(page.locator('[data-sprint-item="si_old"] .item-head .stage-mark')).to_have_attribute("data-conversation-signal", "message")
    expect(title(page, "si_old")).to_have_class(re.compile(r"\bitem-title\b"))
    order(page, ["si_peer", "si_old", "si_newer"])
    summary["awaiting_reply"] = False
    summary["conversation_signal"] = dict(working=False, message_for_owner=False)
    page.evaluate("window.__refresh()")
    expect(title(page, "si_old")).to_have_class(re.compile(r"\bitem-title\b"))
    order(page, ["si_peer", "si_old", "si_newer"])

    # A direct-linked Item absent from the board opens its pane without joining the rail.
    page.goto(sys.argv[1] + "#/workspace/item/si_direct")
    order(page, ["si_peer", "si_old", "si_newer"])
    expect(page.locator('[data-sprint-item-workspace="si_direct"]')).to_have_count(1)
    page.reload()
    order(page, ["si_peer", "si_old", "si_newer"])
    # A mounted popup can outlive its deleted/detached Worker source. It cannot
    # certify the new publication merely because its old fallback text matches.
    source_row=page.locator('[data-sprint-item="si_peer"] [data-ticket-id]').first
    source_row.evaluate("e=>{for(let p=e.parentElement;p;p=p.parentElement)if(p instanceof HTMLDetailsElement)p.open=true;}")
    source_row.focus()
    panel=page.locator('[data-rail-hover-panel]').last
    expect(panel).to_have_attribute('aria-hidden','false')
    page.evaluate('window.__startHoverWait()')
    page.wait_for_function("window.__hoverWaitStatus==='included'")
    deleted_id=source_row.get_attribute('data-ticket-id')
    source_row.evaluate('e=>e.remove()')
    for column in board['columns']:
        column['cards']=[row for row in column['cards'] if row['id']!=deleted_id]
    for summary in board['sprint_items']:
        summary['tickets']=[row for row in summary['tickets'] if row['id']!=deleted_id]
    page.evaluate('window.__refresh()')
    expect(panel).to_have_attribute('aria-hidden','false')
    page.evaluate('window.__startHoverWait()')
    page.wait_for_function("window.__hoverWaitStatus==='unknown'")
    assert not errors, errors
    browser.close()
`;
  const child = spawn(
    join(root, "..", ".venv", "bin", "python"),
    ["-c", script, `http://127.0.0.1:${address.port}/.test-scratch/${stem}.html`],
    { stdio: "inherit" }
  );
  const code = await new Promise((resolve) => child.on("exit", resolve));
  assert.equal(code, 0, "Workspace order browser regression failed");
} finally {
  await server?.close();
  await Promise.all([host, main, index, cacheDir].map((path) => rm(path, { force: true, recursive: true })));
}

console.log("workspace-order-browser.test.mjs: all assertions passed");
