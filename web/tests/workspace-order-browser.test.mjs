import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { rm, writeFile } from "node:fs/promises";
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
let server;

try {
  await writeFile(host, `<script lang="ts">
import { QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
import BoardRoute from "../src/routes/BoardRoute.svelte";
import { parseWorkspaceAddress } from "../src/lib/workspaceAddress";

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
let address = $state(parseWorkspaceAddress(window.location.hash)!);
(window as any).__refresh = () => client.invalidateQueries();
</script>

<svelte:window onhashchange={() => address = parseWorkspaceAddress(window.location.hash)!} />
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
import json, sys
from playwright.sync_api import sync_playwright, expect

def card(ticket_id, item_id, item_title, **values):
    row = {
        "id": ticket_id, "title": ticket_id, "priority": "P1", "deadline": None,
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

# The old Item has only a done child on today’s board. Its full workspace also
# contains unfinished, off-day children. Opening it must change completion styling
# and child visibility without promoting the Item above its legitimate position.
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

workers = {"chief_of_staff": {"label": "Chief of Staff", "needs_me": False,
    "agent_working": False, "latest_turn_ended_sequence": 0}, "workers": []}
held = []
delay_old = True

def fulfill(route, result):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

def respond(route):
    path = route.request.url.split("/api/", 1)[1].split("?")[0]
    if path == "board": result = board
    elif path == "workers": result = workers
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
    return page.locator('[data-sprint-item="' + item_id + '"] .board-workspace-item-title')

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", respond)
    page.goto(sys.argv[1] + "#/workspace?view=items")
    expected = ["si_old", "si_peer", "si_newer"]
    order(page, expected)
    expect(title(page, "si_old")).to_have_class("board-workspace-item-title board-workspace-item-title--rested")

    page.locator('[data-sprint-item="si_old"] .board-workspace-item-head').click()
    expect(page.locator('[data-sprint-item="si_old"] .board-workspace-item-head')).to_have_attribute("aria-current", "page")
    order(page, expected)
    page.wait_for_function("() => document.querySelector('[data-sprint-item-workspace=si_old]') !== null")
    # Python route interception keeps this response pending until selection is proven.
    assert len(held) == 1, "the full workspace request was not delayed"
    delay_old = False
    fulfill(held.pop(), workspace("si_old"))
    expect(title(page, "si_old")).to_have_class("board-workspace-item-title")
    order(page, expected)
    rows = page.locator('[data-sprint-item="si_old"] [data-ticket-id]')
    expect(rows).to_have_count(2)
    assert [rows.nth(i).get_attribute("data-ticket-id") for i in range(2)] == ["t_recent", "t_older"]

    page.locator('[data-sprint-item="si_old"] [data-ticket-id="t_recent"]').click()
    page.wait_for_function("() => location.hash === '#/workspace/item/si_old/t_recent'")
    order(page, expected)
    assert page.locator('.board-workspace-item--selected').count() == 0
    page.go_back()
    expect(page.locator('[data-sprint-item="si_old"] .board-workspace-item-head')).to_have_attribute("aria-current", "page")
    order(page, expected)
    page.locator('[data-sprint-item="si_peer"] .board-workspace-item-head').click()
    order(page, expected)
    page.go_back()
    order(page, expected)
    page.reload()
    expect(title(page, "si_old")).to_have_class("board-workspace-item-title")
    order(page, expected)

    # A real priority edit must still move an Item. A real child activity update
    # must still reorder children inside their group, while Item age order survives.
    peer["sprint_item_priority"] = "P0"
    page.evaluate("window.__refresh()")
    order(page, ["si_peer", "si_old", "si_newer"])
    children[1]["activity_at"] = 30
    page.evaluate("window.__refresh()")
    expect(rows.first).to_have_attribute("data-ticket-id", "t_older")
    order(page, ["si_peer", "si_old", "si_newer"])

    # Clearing a read mark on a completed Item changes its attention and styling,
    # but cannot demote it behind a younger Item with the same priority.
    page.goto(sys.argv[1] + "#/workspace?view=items")
    summary = board["sprint_items"][0]
    summary["awaiting_reply"] = True
    page.evaluate("window.__refresh()")
    expect(page.locator('[data-sprint-item="si_old"] .board-workspace-stage-mark')).to_have_attribute("data-workspace-mark", "reply")
    expect(title(page, "si_old")).to_have_class("board-workspace-item-title")
    order(page, ["si_peer", "si_old", "si_newer"])
    summary["awaiting_reply"] = False
    page.evaluate("window.__refresh()")
    expect(title(page, "si_old")).to_have_class("board-workspace-item-title board-workspace-item-title--rested")
    order(page, ["si_peer", "si_old", "si_newer"])

    # A direct-linked Item absent from today's board still joins the rail in the
    # canonical priority/age position, and survives reload.
    page.goto(sys.argv[1] + "#/workspace/item/si_direct")
    order(page, ["si_peer", "si_old", "si_newer", "si_direct"])
    expect(page.locator('[data-sprint-item="si_direct"] .board-workspace-item-head')).to_have_attribute("aria-current", "page")
    page.reload()
    order(page, ["si_peer", "si_old", "si_newer", "si_direct"])
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
  await Promise.all([host, main, index].map((path) => rm(path, { force: true })));
}

console.log("workspace-order-browser.test.mjs: all assertions passed");
