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
const startedAt = performance.now();
try {
  await writeFile(paths[0], `<script lang="ts">
import { QueryClientProvider } from '@tanstack/svelte-query';
import { tick } from 'svelte';
import BoardRoute from '../src/routes/BoardRoute.svelte';
import { parseWorkspaceAddress } from '../src/lib/workspaceAddress';
import { queryClient as client } from '../src/lib/queryClient';
client.setDefaultOptions({ queries: { retry: false } });
let address = $state(parseWorkspaceAddress(window.location.hash)!);
(window as any).__refresh = async () => { await client.invalidateQueries(); await tick(); };
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
import json, os, pathlib, sys, time
from urllib.parse import parse_qs, urlsplit
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
        day_ids=[], sprint_id=None, sprint_name=None, is_backlogged=False, **quiet)
    row.update(overrides)
    return row

focus_tickets = [ticket("t_approval", awaiting_approval=True), ticket("t_answer", awaiting_answer=True),
    ticket("t_yours", assigned=True), ticket("t_error", agent_state="errored"),
    ticket("t_empty", ticket_status=""), ticket("t_blocked", ticket_status="blocked"),
    ticket("t_agent", awaiting_reply=True, conversation_signal=dict(working=False,message_for_owner=True)),
    ticket("t_done", stage="done", ticket_status="", is_done=True, awaiting_reply=True, conversation_signal=dict(working=False,message_for_owner=True))]

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
    rows = [ticket("t_archived_message", id, awaiting_reply=True, conversation_signal=dict(working=False,message_for_owner=True))] if i == 0 else []
    items.append(item(id, "Settled work %02d" % i, rows, settled=True,
        conversation_signal=dict(working=False,message_for_owner=(i == 0)), awaiting_reply=(i == 0), conversation_id="conv-settled" if i == 0 else None))
board = dict(columns=[dict(stage="needs_implementation", cards=[t for i in items for t in i["tickets"]])], sprint_items=items)
workers = dict(chief_of_staff=dict(label="Chief of Staff", needs_me=False, awaiting_reply=False,
    agent_working=False, latest_turn_ended_sequence=0, recap="Keep our shared work clear."), workers=[])
chief_rows = [ticket("t_chief_active", None), ticket("t_chief_saved", None, is_backlogged=True),
    ticket("t_chief_done", None, stage="done", is_done=True)]
recap_writes = []
requests = []

def respond(route):
    path = route.request.url.split("/api/", 1)[1].split("?")[0]
    requests.append(path)
    if path == "board": result = board
    elif path == "workers": result = workers
    elif path == "workers/chief-of-staff/recap":
        recap_writes.append(route.request.post_data_json)
        workers["chief_of_staff"]["recap"] = route.request.post_data_json["recap"]
        result = workers["chief_of_staff"]
    elif path.startswith("items/") and path.endswith("/workspace"):
        result = next(i for i in items if i["id"] == path.split("/")[1])
    elif path == "worker-types": result = dict(worker_types=[])
    elif path == "conversation/backends": result = dict(backends=[])
    elif path == "chief/conversation": result = dict(conversation_id=None)
    elif path.endswith("start-values"): result = dict(backends=[], current=None)
    elif path == "tickets" and "detail=summary" in route.request.url:
        query = parse_qs(urlsplit(route.request.url).query)
        rows = [row for row in chief_rows if (row["is_backlogged"] if query.get("is_backlogged") == ["true"] else
            row["stage"] == "done" if query.get("stage") == ["done"] else not row["is_backlogged"] and row["stage"] != "done")]
        result = dict(tickets=rows, page=dict(match_count=len(rows), return_count=len(rows), limit=30, offset=0,
            omitted_before=0, omitted_after=0, complete=True, next_offset=None))
    elif path == "tickets":
        route.fulfill(status=404, content_type="application/json", body='{"detail":"Ticket not found"}')
        return
    else: result = {}
    route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

def opened(page, key):
    return page.locator('[data-workspace-group="' + key + '"]').evaluate("n => n.open")

def bottom(box): return box["y"] + box["height"]

def screenshot(page, name):
    directory = os.environ.get("PANELS_SIDEBAR_ARTIFACT_DIR") or os.environ.get("PANELS_UI_PROOF_DIR")
    if directory:
        pathlib.Path(directory).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(pathlib.Path(directory) / (name + ".png")), full_page=True)

def motion_idle(page):
    # Infinite running-status spinners are unrelated to structural rail motion.
    page.wait_for_function("""() => !document.querySelector('.approved-rail').hasAttribute('data-rail-motion-busy') && ![...document.querySelector('.approved-rail').getAnimations({subtree:true})]
        .some(a => a.playState === 'running' && a.effect.getTiming().iterations !== Infinity)""")
    page.wait_for_timeout(40)

def motion_frames(page, name, action="void window.__refresh()"):
    page.evaluate("""action => {
      window.__settledFrames = []; const start = performance.now();
      const sample = () => {
        const find = s => document.querySelector(s);
        const opacity = n => n ? Number(getComputedStyle(n).opacity) : null;
        const body = find('[data-settled-scroll]');
        const card = find('[data-sprint-item="si_active_00"]');
        const row = find('[data-settled-item="si_active_00"]');
        const child = find('[data-message-conversation="conv-t_archived_message"]');
        const below = find('[data-settled-item="si_settled_01"]');
        window.__settledFrames.push({ms:performance.now()-start,
          card:opacity(card),row:opacity(row),child:opacity(child),
          body:body ? opacity(body)*opacity(body.querySelector('[data-settled-item]')) : null,height:find('.settled')?.getBoundingClientRect().height ?? 0,
          below:below?.getBoundingClientRect().y ?? null,
          chevron:getComputedStyle(find('.settled-chevron')).transform,
          count:find('[data-settled-count]')?.textContent,
          countOpacity:opacity(find('[data-settled-count]'))});
        if(performance.now()-start<850) requestAnimationFrame(sample);
      }; requestAnimationFrame(sample); (new Function(action))();
    }""", action)
    page.wait_for_timeout(90)
    if name in ["settle", "reopen", "close"]:
        screenshot(page, "settled-motion-" + name + "-early")
    page.wait_for_timeout(180)
    if name in ["settle", "reopen", "open"]:
        screenshot(page, "settled-motion-" + name + "-late")
    page.wait_for_timeout(650)
    motion_idle(page)
    values = page.evaluate("window.__settledFrames")
    directory = os.environ.get("PANELS_SIDEBAR_ARTIFACT_DIR")
    if directory:
        pathlib.Path(directory, "settled-motion-" + name + ".json").write_text(json.dumps(values, indent=2))
    return values

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
    # Sample actual transition frames: pointer exit must never reveal an accent border.
    selected = page.locator('[data-sprint-item="si_focus"]')
    selected.hover()
    page.wait_for_timeout(180)
    hovered_border = selected.evaluate("n => getComputedStyle(n).borderTopColor")
    page.mouse.move(1430, 10)
    border_frames = selected.evaluate("""n => new Promise(resolve => {
      const frames = [];
      function sample() {
        const style = getComputedStyle(n);
        frames.push({border: style.borderTopColor, focused: n.matches(':focus-within'), hovered: n.matches(':hover')});
        if (frames.length < 18) requestAnimationFrame(sample); else resolve(frames);
      }
      requestAnimationFrame(sample);
    })""")
    print("Selected card pointer-exit borders:", json.dumps(dict(hover=hovered_border, frames=border_frames)))
    colors = selected.evaluate("""n => {
      const probe = document.createElement('span'); n.append(probe);
      probe.style.borderColor = 'var(--accent-bright)'; const accent = getComputedStyle(probe).borderTopColor;
      probe.style.borderColor = 'var(--border-color)'; const neutral = getComputedStyle(probe).borderTopColor;
      probe.remove(); return {accent, neutral};
    }""")
    assert all(frame["border"] != colors["accent"] for frame in border_frames), border_frames
    assert border_frames[-1]["border"] == colors["neutral"], border_frames[-1]
    assert selected.evaluate("n => getComputedStyle(n).backgroundColor") == page.locator('[data-sprint-item="si_active_00"]').evaluate("n => getComputedStyle(n).backgroundColor")
    expect(selected.locator('.item-head')).to_have_attribute("aria-current", "page")
    selected.locator('.item-head').click()
    page.mouse.move(1430, 10)
    assert selected.evaluate("n => getComputedStyle(n).borderTopColor") != colors["accent"]
    selected.locator('.item-head').blur()
    selected.locator('.item-head').focus()
    page.keyboard.press("Tab")
    focused = page.locator(":focus")
    assert focused.evaluate("n => getComputedStyle(n).outlineStyle") != "none"
    page.keyboard.press("Shift+Tab")
    assert selected.locator('.item-head').evaluate("n => n.matches(':focus-visible')")
    assert selected.locator('.item-head').evaluate("n => getComputedStyle(n).outlineStyle") != "none"
    unfinished = ["awaiting_approval", "awaiting_answer", "assigned", "errored", "empty", "blocked", "agent"]
    for key in unfinished:
        assert opened(page, key), key + " unfinished group did not open by default"
    assert not opened(page, "done")
    assert not opened(page, "backlog")
    expect(page.get_by_label("Worker state")).to_have_count(0)
    for key in unfinished:
        count = page.locator('[data-workspace-group="' + key + '"] summary .sprint-workspace-count')
        expect(count).to_have_text("1")
        expect(count).to_be_visible()
    # The one list retains exact row facts, and Done opens independently.
    row = page.locator('[data-sprint-ticket-id="t_agent"]')
    expect(row.locator('[data-conversation-signal="message"]')).to_have_count(1)
    expect(row.locator('.stage-mark[aria-label="Unread message"]')).to_have_count(1)
    expect(row).to_have_attribute("href", "#/workspace/item/si_focus/t_agent")
    page.locator('[data-workspace-group="done"] summary').click()
    row = page.locator('[data-sprint-ticket-id="t_done"]')
    expect(row.locator('[data-conversation-signal="message"]')).to_have_count(1)
    expect(row).to_have_attribute("href", "#/workspace/item/si_focus/t_done")
    assert opened(page, "done")
    expect(page.locator('[data-sprint-ticket-id="t_yours"] .stage-mark')).to_have_count(0)
    screenshot(page, "worker-groups-defaults")

    # Reader choices survive refreshes without changing row facts.
    page.locator('[data-workspace-group="agent"] summary').click()
    focus_tickets[6]["title"] = "Updated Worker title"
    page.evaluate("window.__refresh()")
    expect(page.locator('[data-sprint-ticket-id="t_agent"] .ticket-row-title')).to_have_text("Updated Worker title")
    assert not opened(page, "agent")
    for key in ["done", "assigned"]:
        assert opened(page, key)
        count = page.locator('[data-workspace-group="' + key + '"] summary .sprint-workspace-count')
        expect(count).to_have_text("1")
        expect(count).to_be_visible()
    # A new unread fact must update exact row dots without a visible header suffix.
    focus_tickets[6]["awaiting_reply"] = False
    focus_tickets[6]["conversation_signal"] = dict(working=False,message_for_owner=False)
    page.evaluate("window.__refresh()")
    expect(page.locator('[data-sprint-ticket-id="t_agent"] .stage-mark')).to_have_count(0)
    expect(page.locator('[data-workspace-group="agent"] summary .sprint-workspace-count')).to_have_text("1")

    # One connected sequence exercises the browser-only motion boundary using
    # this existing host and fixture: no extra page, live backend, or matrix.
    motion_started = time.monotonic()
    # Keep both endpoints visible for the filmstrip, then restore the large
    # existing fixture for overflow checks below.
    all_cards = board["columns"][0]["cards"]
    motion_ids = {"si_active_00", "si_settled_00", "si_settled_01"}
    board["sprint_items"] = [i for i in items if i["id"] in motion_ids]
    board["columns"][0]["cards"] = [t for t in all_cards if t["sprint_item_id"] in motion_ids]
    page.evaluate("location.hash = '#/workspace?view=items'")
    page.evaluate("window.__refresh()")
    head = page.locator('.settled-head')
    motion_idle(page)
    opened_frames = motion_frames(page, "open", "document.querySelector('.settled-head').click()")
    assert any(0 < x["body"] < 1 for x in opened_frames if x["body"] is not None), opened_frames
    heights = [x["height"] for x in opened_frames]
    assert any(1 < h < heights[-1] - 1 for h in heights), heights
    assert len(set(x["chevron"] for x in opened_frames)) > 2
    assert head.get_attribute('aria-expanded') == 'true'
    stable = motion_frames(page, "unchanged-refresh")
    assert all(x["below"] is not None for x in stable)
    assert page.locator('[data-settled-item="si_settled_01"]').evaluate("n => getComputedStyle(n).opacity") == '1'
    assert not page.locator('.settled .rail-moving').count()

    moving_item = next(i for i in items if i["id"] == "si_active_00")
    moving_item["settled"] = True
    settled_frames = motion_frames(page, "settle")
    exiting = [x for x in settled_frames if x["card"] is not None and 0 < x["card"] < 1]
    arriving = [x for x in settled_frames if x["row"] is not None and 0 < x["row"] < 1]
    assert exiting and arriving, settled_frames
    assert max(x["ms"] for x in exiting) < min(x["ms"] for x in arriving)
    assert all(x["row"] is None for x in exiting), "Destination appeared before source departure"
    expect(page.locator('[data-settled-item="si_active_00"]')).to_have_count(1)
    expect(page.locator('[data-sprint-item="si_active_00"]')).to_have_count(0)
    expect(head).to_have_accessible_name("Settled (3)")
    assert any(0 < x["countOpacity"] < 1 for x in settled_frames if x["countOpacity"] is not None)
    moving_item["settled"] = False
    reopened_frames = motion_frames(page, "reopen")
    exiting = [x for x in reopened_frames if x["row"] is not None and 0 < x["row"] < 1]
    arriving = [x for x in reopened_frames if x["card"] is not None and 0 < x["card"] < 1]
    assert exiting and arriving, reopened_frames
    assert max(x["ms"] for x in exiting) < min(x["ms"] for x in arriving)
    expect(page.locator('[data-settled-item="si_active_00"]')).to_have_count(0)
    expect(page.locator('[data-sprint-item="si_active_00"]')).to_have_count(1)
    expect(head).to_have_accessible_name("Settled (2)")

    child_ticket = next(t for i in items for t in i["tickets"] if t["id"] == "t_archived_message")
    # The existing active Worker hover freezes global structural motion, while
    # settled child conversation facts must still update immediately.
    page.locator('[data-ticket-id="t_work_00"]').hover()
    child_ticket["awaiting_reply"] = False
    child_ticket["conversation_signal"] = dict(working=False,message_for_owner=False)
    page.evaluate("window.__refresh()")
    child = page.locator('[data-message-conversation="conv-t_archived_message"]')
    expect(child).to_have_count(1)
    expect(child.locator('.stage-mark')).to_have_count(0)
    page.mouse.move(1430,10)
    child_exit = motion_frames(page, "child-exit", "")
    assert any(x["child"] is not None and 0 < x["child"] < 1 for x in child_exit)
    assert child_exit[-1]["child"] is None
    positions = [x["below"] for x in child_exit if x["below"] is not None]
    # This survivor is anchored above the fixed footer before and after the
    # preceding child disappears. Parent height motion must not apply its
    # displacement twice and make the survivor jump away, then return.
    assert max(positions)-min(positions) < 2, positions
    child_ticket["awaiting_reply"] = True
    child_ticket["conversation_signal"] = dict(working=False,message_for_owner=True)
    child_enter = motion_frames(page, "child-enter")
    assert any(x["child"] is not None and 0 < x["child"] < 1 for x in child_enter)
    expect(page.locator('[data-message-conversation="conv-t_archived_message"]')).to_have_count(1)

    # A collapse containing focus hands it back to the persistent header.
    page.locator('[data-settled-item="si_settled_00"] .settled-title').focus()
    closed_frames = motion_frames(page, "close", "document.querySelector('.settled-head').click()")
    assert any(x["body"] is not None and 0 < x["body"] < 1 for x in closed_frames)
    assert any(1 < x["height"] < closed_frames[0]["height"] - 1 for x in closed_frames)
    expect(head).to_be_focused()
    expect(page.locator('[data-settled-item]')).to_have_count(0)
    moving_item["settled"] = True
    rapid = motion_frames(page, "rapid-refresh", """const head=document.querySelector('.settled-head');
      head.click(); setTimeout(()=>{head.click(); void window.__refresh();},40);
      setTimeout(()=>head.click(),80);""")
    expect(head).to_have_attribute('aria-expanded', 'true')
    expect(page.locator('[data-settled-item="si_active_00"]')).to_have_count(1)
    expect(head).to_have_accessible_name('Settled (3)')
    assert not page.locator('.settled .rail-moving').count()

    # Changing the OS preference during an exit cancels the current beat and
    # still drains to the requested final state without leftover inline styles.
    page.evaluate("document.querySelector('.settled-head').click()")
    page.wait_for_function("document.querySelector('.settled .rail-moving') !== null")
    page.emulate_media(reduced_motion='reduce')
    motion_idle(page)
    expect(page.locator('[data-settled-item]')).to_have_count(0)
    assert page.locator('.settled').evaluate("n => n.style.overflow === '' && n.getAnimations({subtree:true}).length === 0")
    page.evaluate("document.querySelector('.settled-head').click()")
    expect(page.locator('[data-settled-item]')).to_have_count(3)
    moving_item["settled"] = False
    page.evaluate("window.__refresh()")
    expect(page.locator('[data-sprint-item="si_active_00"]')).to_have_count(1)
    expect(head).to_have_accessible_name('Settled (2)')
    page.evaluate("document.querySelector('.settled-head').click()")
    expect(page.locator('[data-settled-item]')).to_have_count(0)
    assert page.locator('.settled').evaluate("n => n.getAnimations({subtree:true}).length") == 0
    page.emulate_media(reduced_motion='no-preference')
    board["sprint_items"] = items
    board["columns"][0]["cards"] = all_cards
    print('Added Settled connected motion sequence seconds:', round(time.monotonic()-motion_started, 2))

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
        motion_idle(page)
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
        motion_idle(page)
        assert abs(head.bounding_box()["y"] - (bottom(settled_box) - head_box["height"])) < 2
        # Footer links remain keyboard accessible and retain actual navigation targets.
        config.focus()
        page.keyboard.press("Enter")
        page.wait_for_function("location.hash === '#/settings'")
        backends.click()
        page.wait_for_function("location.hash === '#/backends'")
        page.evaluate("location.hash = '#/workspace?view=items'")
        head.click()
        motion_idle(page)
        body = page.locator("[data-settled-scroll]")
        body.evaluate("n => n.scrollTop = 0")
        expect(page.locator('[data-settled-item="si_settled_00"] .settled-title .stage-mark')).to_have_attribute("aria-label", "Unread message")
        message = page.locator('[data-message-conversation="conv-t_archived_message"]')
        expect(message.locator(".stage-mark")).to_have_attribute("aria-label", "Unread message")
        expect(message).to_have_attribute("href", "#/workspace/item/si_settled_00/t_archived_message")
        # Opening the archived message keeps its exact Worker address accessible.
        message.click()
        page.wait_for_function("location.hash === '#/workspace/item/si_settled_00/t_archived_message'")
    # The Chief and ordinary orchestrators share real column and conversation controls.
    # A component-only test cannot prove shell sizing, pointer capture or focus retention.
    page.set_viewport_size(dict(width=1440, height=1000))
    workers["chief_of_staff"]["conversation_signal"] = dict(working=True,message_for_owner=False)
    page.evaluate("location.hash = '#/workspace/chief-of-staff'")
    chief = page.locator('.chief-workspace')
    expect(chief.get_by_role("heading", level=1)).to_be_visible()
    expect(chief.get_by_role("button", name="Settle Chief of Staff")).to_have_count(0)
    expect(chief.locator('h1 [data-conversation-signal="working"]')).to_have_count(1)
    recap = chief.get_by_role("textbox", name="Orchestrator recap")
    expect(recap).to_have_text("Keep our shared work clear.")
    recap.fill("Keep the Chief and orchestrators consistent.")
    with page.expect_response("**/api/workers/chief-of-staff/recap"):
        chief.get_by_role("heading", level=1).click()
    expect(recap).to_have_text("Keep the Chief and orchestrators consistent.")
    assert recap_writes == [dict(recap="Keep the Chief and orchestrators consistent.")], recap_writes
    page.evaluate("window.__refresh()")
    expect(recap).to_have_text("Keep the Chief and orchestrators consistent.")
    pane = chief.locator('[data-conversation-pane]')
    expect(pane).to_have_attribute("data-conversation-state", "rest")
    document_column = chief.locator('.sprint-item-column').bounding_box()
    conversation_column = chief.locator('.conversation-column').bounding_box()
    assert abs(document_column["x"] - conversation_column["x"]) < 1
    assert abs(document_column["width"] - conversation_column["width"]) < 1
    page.mouse.move(1430, 10)
    expect(page.locator('[data-rail-hover-panel][aria-hidden="false"]')).to_have_count(0)
    page.wait_for_function("[...document.querySelectorAll('[data-rail-hover-panel]')].every(n => getComputedStyle(n).opacity === '0')")
    screenshot(page, "chief-desktop-rest")
    composer = chief.locator('[data-conversation-input]')
    composer.click()
    composer.fill("Draft that must survive conversation controls")
    expect(pane).to_have_attribute("data-conversation-state", "peeked")
    screenshot(page, "chief-desktop-peeked")
    chief.locator('[data-conversation-expand]').click()
    expect(pane).to_have_attribute("data-conversation-state", "opened")
    screenshot(page, "chief-desktop-opened")
    chief.locator('[data-conversation-collapse]').click()
    expect(pane).to_have_attribute("data-conversation-state", "peeked")
    composer.focus()
    page.keyboard.press("Escape")
    expect(pane).to_have_attribute("data-conversation-state", "rest")
    expect(composer).to_have_value("Draft that must survive conversation controls")
    composer.click()
    chief.get_by_role("heading", level=1).click()
    expect(pane).to_have_attribute("data-conversation-state", "rest")
    expect(composer).to_have_value("Draft that must survive conversation controls")
    # Live canonical refreshes never open the conversation for the person.
    page.evaluate("window.__refresh()")
    expect(pane).to_have_attribute("data-conversation-state", "rest")

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
        if kind in ["chief", "item"]:
            assert inspector.evaluate("n => getComputedStyle(n).padding") == "0px"
        assert abs(inspector_box["y"] - shell_box["y"]) < 1, (kind, shell_box, inspector_box)
        assert abs(inspector_box["height"] - shell_box["height"]) < 1, (kind, shell_box, inspector_box)
        assert inspector_box["x"] >= 0 and inspector_box["x"] + inspector_box["width"] <= 391
        assert bottom(inspector_box) <= 560
        screenshot(page, "compact-selected-" + kind)
        if kind == "chief":
            pane = inspector.locator('[data-conversation-pane]')
            expect(pane).to_have_attribute("data-conversation-state", "rest")
            inspector.locator('[data-conversation-input]').click()
            expect(pane).to_have_attribute("data-conversation-state", "peeked")
            inspector.locator('[data-conversation-expand]').click()
            expect(pane).to_have_attribute("data-conversation-state", "opened")
            assert pane.bounding_box()["width"] <= 390
            assert bottom(pane.bounding_box()) <= 560
            screenshot(page, "chief-compact-opened")
            inspector.locator('[data-conversation-collapse]').click()
            expect(pane).to_have_attribute("data-conversation-state", "peeked")
            page.keyboard.press("Escape")
            expect(pane).to_have_attribute("data-conversation-state", "rest")

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
console.log(`orchestrator-groups-settled-browser.test.mjs: all assertions passed (${((performance.now() - startedAt) / 1000).toFixed(2)}s)`);
