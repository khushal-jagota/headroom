import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { scratchDirectory } from "./support/scratch.mjs";

// Frontend browser regression: real BoardRoute and SprintItemWorkspace, controlled
// API responses, production CSS and the shell's real height chain. No backend server.
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const stem = `orchestrator-groups-settled-${process.pid}`;
const scratch = await scratchDirectory();
const paths = ["svelte", "ts", "html"].map((ext) => join(scratch, `${stem}.${ext}`));
let server;
try {
  await writeFile(paths[0], `<script lang="ts">
import { QueryClient, QueryClientProvider } from '@tanstack/svelte-query';
import BoardRoute from '../src/routes/BoardRoute.svelte';
import { parseWorkspaceAddress } from '../src/lib/workspaceAddress';
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
let address = $state(parseWorkspaceAddress(window.location.hash)!);
(window as any).__refresh = () => client.invalidateQueries();
</script>
<svelte:window onhashchange={() => address = parseWorkspaceAddress(window.location.hash) ?? address} />
<div class="shell">
  <header class="shell-mobile-head"><span class="shell-mobile-title">Panels</span></header>
  <header class="shell-nav"><nav class="shell-links">Panels · Workspace</nav></header>
  <main class="shell-content"><div class="screen"><QueryClientProvider {client}><BoardRoute {address} /></QueryClientProvider></div></main>
</div>`);
  await writeFile(paths[1], `import { mount } from 'svelte'; import Host from './${stem}.svelte'; import '../../assets/tokens.css'; import '../../assets/app.css'; mount(Host, { target: document.getElementById('app')! });`);
  await writeFile(paths[2], `<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);
  server = await createServer({ root, configFile: false, plugins: [svelte()], server: { host: "127.0.0.1", port: 0 }, logLevel: "error" });
  await server.listen();
  const { port } = server.httpServer.address();
  const script = String.raw`
import json, os, pathlib, sys
from playwright.sync_api import sync_playwright, expect

quiet = dict(awaiting_reply=False, awaiting_answer=False, awaiting_approval=False,
             awaiting_agent_approval=False, assigned=False, agent_state="idle")

def ticket(id, parent="si_focus", **overrides):
    row = dict(id=id, title=id.replace("_", " "), priority="P2", deadline=None,
        project_id=None, project=None, group_project_id=None, group_project=None,
        activity_at=1, has_pending_proposal=False, ticket_status="agent",
        worker_type="coding", employee_backend="codex", stage="needs_implementation",
        stage_label="Implementation", gating_field="implementation", gating_field_label="Implementation",
        is_done=False, blocked=False, conversation_id="conv-" + id, waiting_to_closeout=False,
        sprint_item_id=parent, sprint_item_title=parent, sprint_item_priority="P2",
        day_ids=[], sprint_id=None, sprint_name=None, **quiet)
    row.update(overrides)
    return row

focus_tickets = [ticket("t_approval", awaiting_approval=True), ticket("t_answer", awaiting_answer=True),
    ticket("t_yours", assigned=True), ticket("t_error", agent_state="errored"),
    ticket("t_empty", ticket_status=""), ticket("t_blocked", ticket_status="blocked"),
    ticket("t_agent", awaiting_reply=True),
    ticket("t_done", stage="done", ticket_status="", is_done=True, awaiting_reply=True),
    ticket("t_cancelled", stage="cancelled", ticket_status="")]

def item(id, title, rows=None, **overrides):
    row = dict(id=id, title=title, body="Shared work and owner decisions", priority="P2",
        project_id=None, project=None, created_at=1, updated_at=1, settled=False,
        conversation_id=None, conversation_history=[], tickets=rows or [], artifacts=[],
        ticket_rollup=dict(quiet), supervisor=dict(agent_key="sprint_item:" + id,
            conversation_id=None, launch_configuration=dict(employee_backend="codex",
                employee_launch_model=None, employee_launch_reasoning_effort=None)), **quiet)
    row.update(overrides)
    return row

items = [item("si_focus", "Product polish", focus_tickets)]
for i in range(24):
    id = "si_active_%02d" % i
    items.append(item(id, "Active work %02d" % i, [ticket("t_work_%02d" % i, id, assigned=True)]))
for i in range(24):
    id = "si_settled_%02d" % i
    # Separate exact unread conversations on the settled orchestrator and its child.
    rows = [ticket("t_archived_message", id, awaiting_reply=True)] if i == 0 else []
    items.append(item(id, "Settled work %02d" % i, rows, settled=True,
        awaiting_reply=(i == 0), conversation_id="conv-settled" if i == 0 else None))
board = dict(columns=[dict(stage="needs_implementation", cards=[t for i in items for t in i["tickets"]])], sprint_items=items)
workers = dict(chief_of_staff=dict(label="Chief of Staff", needs_me=False, awaiting_reply=False,
    agent_working=False, latest_turn_ended_sequence=0), workers=[])
requests = []

def respond(route):
    path = route.request.url.split("/api/", 1)[1].split("?")[0]
    requests.append(path)
    if path == "board": result = board
    elif path == "workers": result = workers
    elif path.startswith("items/") and path.endswith("/workspace"):
        result = next(i for i in items if i["id"] == path.split("/")[1])
    elif path == "worker-types": result = dict(worker_types=[])
    elif path == "conversation/backends": result = dict(backends=[])
    elif path == "chief/conversation": result = dict(conversation_id=None)
    elif path.endswith("start-values"): result = dict(backends=[], current=None)
    elif path == "tickets":
        route.fulfill(status=404, content_type="application/json", body='{"detail":"Ticket not found"}')
        return
    else: result = {}
    route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

def opened(page, key):
    return page.locator('[data-workspace-group="' + key + '"]').evaluate("n => n.open")

def bottom(box): return box["y"] + box["height"]

def screenshot(page, name):
    directory = os.environ.get("PANELS_UI_PROOF_DIR")
    if directory:
        pathlib.Path(directory).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(pathlib.Path(directory) / (name + ".png")), full_page=True)

def overflow(locator):
    return locator.evaluate("n => ({height:n.clientHeight, full:n.scrollHeight, top:n.scrollTop})")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    errors = []
    page = browser.new_page(viewport=dict(width=1440, height=1000))
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", respond)
    page.goto(sys.argv[1] + "#/workspace/item/si_focus")
    expect(page.locator('[data-workspace-group="agent"]')).to_be_visible()
    unfinished = ["awaiting_approval", "awaiting_answer", "assigned", "errored", "empty", "blocked", "agent"]
    for key in unfinished:
        assert opened(page, key), key + " unfinished group did not open by default"
    for key in ["done", "cancelled"]:
        assert not opened(page, key), key + " terminal group opened by default"
    for key in unfinished + ["done", "cancelled"]:
        count = page.locator('[data-workspace-group="' + key + '"] summary .sprint-workspace-count')
        expect(count).to_have_text("1")
        expect(count).to_be_visible()
    # The rounded Worker header suppresses only its unread suffix. Real row facts survive.
    for id in ["t_agent", "t_done"]:
        row = page.locator('[data-sprint-ticket-id="' + id + '"]')
        expect(row).to_have_attribute("data-workspace-mark", "reply")
        expect(row.locator('.stage-mark[aria-label="Unread reply"]')).to_have_count(1)
        expect(row).to_have_attribute("href", "#/workspace/item/si_focus/" + id)
    expect(page.locator('[data-sprint-ticket-id="t_yours"] .stage-mark')).to_have_count(0)
    screenshot(page, "worker-groups-defaults")

    # Reader choices survive a changed API response and ordinary query invalidation.
    page.locator('[data-workspace-group="agent"] summary').click()
    page.locator('[data-workspace-group="done"] summary').click()
    page.locator('[data-workspace-group="cancelled"] summary').click()
    focus_tickets[6]["title"] = "Updated Worker title"
    page.evaluate("window.__refresh()")
    expect(page.locator('[data-sprint-ticket-id="t_agent"] .ticket-row-title')).to_have_text("Updated Worker title")
    assert not opened(page, "agent")
    assert opened(page, "done") and opened(page, "cancelled")
    for key in ["done", "cancelled", "assigned"]:
        count = page.locator('[data-workspace-group="' + key + '"] summary .sprint-workspace-count')
        expect(count).to_have_text("1")
        expect(count).to_be_visible()
    # A new unread fact must update exact row dots without a visible header suffix.
    focus_tickets[6]["awaiting_reply"] = False
    page.evaluate("window.__refresh()")
    expect(page.locator('[data-sprint-ticket-id="t_agent"] .stage-mark')).to_have_count(0)
    expect(page.locator('[data-workspace-group="agent"] summary .sprint-workspace-count')).to_have_text("1")

    # Real rail layout at tall, short, and compact viewports with both lists overflowing.
    for label, width, height in [("tall",1440,1000), ("short",1280,480), ("compact",390,560)]:
        page.set_viewport_size(dict(width=width, height=height))
        page.goto(sys.argv[1] + "#/workspace?view=items")
        page.reload()
        head = page.locator(".settled-head")
        rail = page.locator(".board-workspace-left")
        active = page.locator("[data-active-work-scroll]")
        settled = page.locator(".settled")
        expect(head).to_have_attribute("aria-expanded", "false")
        expect(page.locator("[data-settled-count]")).to_have_text("(24)")
        expect(page.locator("[data-settled-item]")).to_have_count(0)
        footer = page.get_by_role("navigation", name="Workspace utilities")
        expect(footer).to_be_in_viewport()
        config = footer.get_by_role("link", name="Settings", exact=True)
        backends = footer.get_by_role("link", name="Usage and Backends", exact=True)
        expect(config).to_have_attribute("href", "#/settings")
        expect(backends).to_have_attribute("href", "#/backends")
        expect(head).to_have_text("Settled (24)")
        expect(head.locator("svg[aria-hidden=true]")).to_have_count(1)
        rail_box, head_box = rail.bounding_box(), head.bounding_box()
        assert head_box["y"] > rail_box["y"] + rail_box["height"] * .7
        assert bottom(head_box) <= bottom(rail_box) + 1
        assert abs(bottom(head_box) - footer.bounding_box()["y"]) < 2, (label, rail_box, head_box)
        footer_box = footer.bounding_box()
        screenshot(page, "settled-" + label + "-collapsed")

        head.click()
        expect(head).to_have_attribute("aria-expanded", "true")
        expect(page.locator("[data-settled-item]")).to_have_count(24)
        body = page.locator("[data-settled-scroll]")
        rail_box, settled_box = rail.bounding_box(), settled.bounding_box()
        padding = rail.evaluate("n => parseFloat(getComputedStyle(n).paddingTop) + parseFloat(getComputedStyle(n).paddingBottom)")
        usable = rail_box["height"] - padding - footer_box["height"]
        assert settled_box["height"] <= usable * .5 + 1, (label, usable, settled_box)
        assert bottom(settled_box) <= bottom(rail_box) + 1
        assert overflow(active)["full"] > overflow(active)["height"], label + " active fixture did not overflow"
        assert overflow(body)["full"] > overflow(body)["height"], label + " settled fixture did not overflow"
        active_box, head_box, body_box = active.bounding_box(), head.bounding_box(), body.bounding_box()
        assert bottom(active_box) <= head_box["y"] + 1
        assert bottom(head_box) <= body_box["y"] + 1
        assert head_box["y"] >= rail_box["y"]
        screenshot(page, "settled-" + label + "-expanded")

        # Last rows remain reachable independently, while the header stays in place.
        active.evaluate("n => n.scrollTop = n.scrollHeight")
        expect(active.locator('.item-head, [data-ticket-id]').last).to_be_in_viewport()
        assert overflow(body)["top"] == 0
        active_top = overflow(active)["top"]
        body.evaluate("n => n.scrollTop = n.scrollHeight")
        expect(body.locator('[data-settled-item] .settled-title').last).to_be_in_viewport()
        assert overflow(active)["top"] == active_top
        assert head.bounding_box() == head_box
        assert footer.bounding_box() == footer_box
        expect(config).to_be_in_viewport()
        expect(backends).to_be_in_viewport()
        screenshot(page, "settled-" + label + "-last-rows")
        head.click()
        expect(head).to_have_attribute("aria-expanded", "false")
        assert abs(head.bounding_box()["y"] - (bottom(settled_box) - head_box["height"])) < 2
        # Footer links remain keyboard accessible and retain actual navigation targets.
        config.focus()
        page.keyboard.press("Enter")
        page.wait_for_function("location.hash === '#/config'")
        backends.click()
        page.wait_for_function("location.hash === '#/backends'")
        page.evaluate("location.hash = '#/workspace?view=items'")
        head.click()
        body = page.locator("[data-settled-scroll]")
        body.evaluate("n => n.scrollTop = 0")
        expect(page.locator('[data-settled-item="si_settled_00"] .settled-title .stage-mark')).to_have_attribute("aria-label", "Unread reply")
        message = page.locator('[data-message-conversation="conv-t_archived_message"]')
        expect(message.locator(".stage-mark")).to_have_attribute("aria-label", "Unread reply")
        expect(message).to_have_attribute("href", "#/workspace/item/si_settled_00/t_archived_message")
        # Opening the archived message keeps its exact Worker address accessible.
        message.click()
        page.wait_for_function("location.hash === '#/workspace/item/si_settled_00/t_archived_message'")
    # Compact selection replaces the rail with the selected inspector. The scoped
    # flex rule for the rail must not override the production responsive hide rule.
    page.set_viewport_size(dict(width=390, height=560))
    for kind, address in [("item", "#/workspace/item/si_focus"),
                          ("chief", "#/workspace/chief-of-staff"),
                          ("ticket", "#/workspace/item/si_focus/t_agent")]:
        page.evaluate("hash => location.hash = hash", address)
        inspector = page.locator(".board-workspace-right--" + kind)
        expect(inspector).to_be_visible()
        expect(page.locator(".board-workspace-left")).to_be_hidden()
        expect(page.get_by_role("navigation", name="Workspace utilities", include_hidden=True)).to_be_hidden()
        expect(page.locator(".rail-grip")).to_be_hidden()
        shell_box = page.locator(".board-workspace-shell").bounding_box()
        inspector_box = inspector.bounding_box()
        assert abs(inspector_box["y"] - shell_box["y"]) < 1, (kind, shell_box, inspector_box)
        assert abs(inspector_box["height"] - shell_box["height"]) < 1, (kind, shell_box, inspector_box)
        assert inspector_box["x"] >= 0 and inspector_box["x"] + inspector_box["width"] <= 391
        assert bottom(inspector_box) <= 560
        screenshot(page, "compact-selected-" + kind)
    page.evaluate("location.hash = '#/workspace?view=items'")
    expect(page.locator(".board-workspace-left")).to_be_visible()
    expect(page.get_by_role("navigation", name="Workspace utilities")).to_be_in_viewport()
    expect(page.locator(".board-workspace-right")).to_be_hidden()
    assert not errors, errors
    browser.close()
print("orchestrator defaults, stable choices, counts/dots/navigation, and independent Settled scrolls passed")
`;
  const child = spawn(join(root, "..", ".venv", "bin", "python"), ["-c", script, `http://127.0.0.1:${port}/.test-scratch/${stem}.html`], { stdio: "inherit", env: process.env });
  const code = await new Promise((resolve) => child.on("exit", resolve));
  assert.equal(code, 0, "Orchestrator groups and Settled browser regression failed");
} finally {
  await server?.close();
  await Promise.all(paths.map((path) => rm(path, { force: true })));
}
console.log("orchestrator-groups-settled-browser.test.mjs: all assertions passed");
