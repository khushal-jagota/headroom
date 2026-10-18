import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { writeFile, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { scratchDirectory } from "./support/scratch.mjs";

// A native details click and membership change in one task expose queued-toggle loss.
// Pure helpers cannot prove browser event ordering or the real component's remount.
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const scratch = await scratchDirectory();
const stem = `group-reader-state-${process.pid}`;
const files = ["svelte", "ts", "html"].map(ext => join(scratch, `${stem}.${ext}`));
let server;
try {
  await writeFile(files[0], `<script lang="ts">
import { QueryClient, QueryClientProvider } from '@tanstack/svelte-query';
import OrchestratorChildren from '../src/components/OrchestratorChildren.svelte';
import BoardRoute from '../src/routes/BoardRoute.svelte';
import { tick } from 'svelte';
(window as any).__refresh = async () => {await client.invalidateQueries();await tick();};
const mirror = new URLSearchParams(location.search).has('mirror');
import type { SprintItemWorkspaceTicket } from '../src/lib/types';
const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
let tickets = $state(['done','needs_implementation'].map((stage, i) => ({
 id:'t_'+i,title:stage,stage,is_backlogged:false,activity_at:1,
 ticket_status:'empty',waiting_to_closeout:false,has_pending_proposal:false,gating_field:null,
 blocked:false,worker_type:'coding',day_ids:[],sprint_id:null,sprint_name:null,
 awaiting_reply:false,awaiting_answer:false,awaiting_approval:false,
 awaiting_agent_approval:false,assigned:false,agent_state:'idle'
})) as SprintItemWorkspaceTicket[]);
(window as any).__setDone = (done: boolean) => {tickets = tickets.map(t => t.id === "t_0" ? {...t,stage:done ? "done" : "needs_implementation"} : t);};
</script>
<QueryClientProvider {client}>{#if mirror}<BoardRoute address={{selection:{kind:'item',id:'si_mirror'},view:'items',openFile:null}}/>{:else}<OrchestratorChildren principal={{kind:'sprint_item',id:'si_probe'}} {tickets}/>{/if}</QueryClientProvider>`);
  await writeFile(files[1], `import {mount} from 'svelte';import Host from './${stem}.svelte';import '../../assets/tokens.css';import '../../assets/app.css';mount(Host,{target:document.body});`);
  await writeFile(files[2], `<html><body><script type="module" src="./${stem}.ts"></script></body></html>`);
  server = await createServer({root,configFile:false,plugins:[svelte()],server:{host:"127.0.0.1",port:0},logLevel:"error"});
  await server.listen();
  const script = String.raw`
import json, os, pathlib, sys
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect
from tests.e2e.harness import browser_failure_evidence
results = []
output = os.environ.get("PANELS_UI_PROOF_DIR")
project={"id":"project_mirror"}
item={"id":"si_mirror","supervisor":{"agent_key":"sprint_item:si_mirror"}}
ticket={"id":"t_mirror"}
quiet = {
    "awaiting_reply": False,
    "awaiting_answer": False,
    "awaiting_approval": False,
    "awaiting_agent_approval": False,
    "assigned": False,
    "agent_state": "idle",
    "conversation_signal": {"working": False, "message_for_owner": False},
}
mutable = {
    "awaiting_reply": False,
    "assigned": True,
    "agent_state": "working",
    "conversation_signal": {"working": True, "message_for_owner": False},
}

def attention():
    return {**quiet, **mutable}

def workspace_ticket():
    return {
        "id": ticket["id"],
        "title": "Mirror Ticket",
        "stage": "needs_brief",
        "activity_at": 20,
        "ticket_status": "empty",
        "waiting_to_closeout": False,
        "has_pending_proposal": False,
        "gating_field": "brief",
        "blocked": False,
        "worker_type": "coding",
        "day_ids": [],
        "sprint_id": None,
        "sprint_name": None,
        "conversation_id": "mirror-child-conversation",
        **attention(),
    }

def workspace_payload():
    return {
        "id": item["id"],
        "title": "Mirror Item",
        "body": "[Open proof](/files/sprint-items/si_mirror/artifacts/proof.md)",
        "project_id": project["id"],
        "project": "Mirror project",
        "created_at": 10,
        "updated_at": 10,
        "committed_sprints": [],
        "supervisor": {
            "agent_key": item["supervisor"]["agent_key"],
            "conversation_id": None,
            "launch_configuration": {},
        },
        "planning_day_id": None,
        "today_ticket_ids": [],
        "tickets": [workspace_ticket()],
        "ticket_rollup": attention(),
        "artifacts": [dict(name="proof.md",opens="artifacts/proof.md",modified_at=1,children=[]),
                dict(name="notes",opens=None,modified_at=1,children=[dict(name="one.md",opens="artifacts/notes/one.md",modified_at=1,children=[])])],
        "conversation_history": [],
        **quiet,
    }

def board_payload():
    row = {
        **workspace_ticket(),
        "deadline": None,
        "project_id": project["id"],
        "project": "Mirror project",
        "group_project_id": project["id"],
        "group_project": "Mirror project",
        "stage_label": "Brief",
        "gating_field_label": "Brief",
        "is_done": False,
        "conversation_id": "mirror-child-conversation",
        "employee_backend": "codex",
        "orchestrator": {"kind": "sprint_item", "id": item["id"]},
        "sprint_item_id": item["id"],
        "sprint_item_title": "Mirror Item",
        }
    summary = {
        "id": item["id"],
        "created_at": 10,
        "title": "Mirror Item",
        "tickets": [workspace_ticket()],
        "conversation_id": None,
        "ticket_rollup": attention(),
        **quiet,
    }
    return {"columns": [{"stage": "needs_brief", "cards": [row]}], "sprint_items": [summary]}

def mirror_reads(route):
    path=urlsplit(route.request.url).path
    if path=="/api/board": result=board_payload()
    elif path.endswith("/workspace"): result=workspace_payload()
    elif path=="/api/workers": result={"chief_of_staff":{"label":"Chief of Staff"},"workers":[]}
    elif path=="/api/backends": result={"backends":[]}
    elif path.endswith("/start-values"): result={"backend_key":"codex","model":None,"reasoning_effort":None}
    else: result={}
    route.fulfill(json=result)

errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    with browser_failure_evidence(browser, "orchestrator-group-reader-state-browser"):
        for choice in sys.argv[2:] or ["open", "closed"]:
            context = browser.new_context()
            context.tracing.start(screenshots=True,snapshots=True,sources=True)

            page = context.new_page()
            page.goto(sys.argv[1])
            group = page.locator('[data-workspace-group="done"]')
            expect(group).to_be_visible()
            assert not group.evaluate("n=>n.open"), "terminal default changed"
            if choice == "closed":
                # Establish the previously saved open choice before the rapid close.
                page.evaluate("""() => {const d=document.querySelector('[data-workspace-group="done"]');
                    window.__opened = false; d.addEventListener('toggle',()=>window.__opened=d.open,{once:true});
                    d.querySelector('summary').click();}""")
                page.wait_for_function("window.__opened === true")
            page.evaluate("""() => {const d=document.querySelector('[data-workspace-group="done"]');
                d.querySelector('summary').click();
                window.__setDone(false);}""")
            expect(page.locator('[data-workspace-group="empty"] [data-group-count]')).to_have_text("2")
            page.evaluate("window.__setDone(true)")
            assert page.locator('[data-workspace-group="empty"]').evaluate("n=>n.open"), "unfinished default changed"
            expect(group).to_be_visible()
            actual = group.evaluate("n=>n.open")
            expected = choice == "open"
            results.append(dict(choice=choice,key="done",expected=expected,actual=actual))
            assert actual == expected, results[-1]
            if output:
                path=pathlib.Path(output);path.mkdir(parents=True,exist_ok=True)
                (path/(choice+"-dom.html")).write_text(page.content())
                (path/(choice+"-aria.txt")).write_text(page.locator('body').aria_snapshot())

            context.tracing.stop()
            context.close()
        context = browser.new_context(viewport={"width":1440,"height":1000})
        context.tracing.start(screenshots=True,snapshots=True,sources=True)
        context.route("**/api/**",mirror_reads)
        context.route("**/files/**",lambda route: route.fulfill(status=200,content_type="text/markdown",body="# Deterministic proof"))
        page=context.new_page()
        page.on("pageerror",lambda error:errors.append(str(error)))
        page.goto(sys.argv[1]+"?mirror")
        left_item = page.locator(f'[data-sprint-item="{item["id"]}"]')
        right_item = page.locator(f'[data-sprint-item-workspace="{item["id"]}"]')
        left_ticket = left_item.locator(f'[data-ticket-id="{ticket["id"]}"]')
        right_ticket = right_item.locator(f'[data-sprint-ticket-id="{ticket["id"]}"]')

        assigned_group = right_item.locator('[data-workspace-group="assigned"]')
        expect(assigned_group).to_have_attribute("open", "")
        # Manual closure and reopening remain available with the new default.
        assigned_group.locator(":scope > summary").click()
        expect(assigned_group).not_to_have_attribute("open", "")
        assigned_group.locator(":scope > summary").click()
        for row in (left_ticket, right_ticket):
            row.wait_for(timeout=10_000)
            expect(row.locator('[data-conversation-signal="working"]')).to_have_count(1)
            expect(row.locator('[data-conversation-signal="message"]')).to_have_count(0)
        assert left_item.locator('[data-bucket-key="assigned"]').count() == 1
        assert right_item.locator('[data-workspace-group="assigned"]').count() == 1
        # Child work never supplies a dot on the supervisor conversation.
        assert left_item.locator(".item-head .stage-mark").count() == 0
        assert right_item.locator(".sprint-workspace-title-mark").count() == 0

        # Keep a manual closure when live refresh moves this Worker to another group,
        # then back. A full reload starts with the approved open default again.
        assigned_group.locator(":scope > summary").click()
        expect(assigned_group).not_to_have_attribute("open", "")
        mutable.update(
            awaiting_answer=True,
            awaiting_reply=True,
            agent_state="working",
            conversation_signal={"working": True, "message_for_owner": True},
        )
        page.evaluate("window.__refresh()")
        answer_group = right_item.locator('[data-workspace-group="awaiting_answer"]')
        expect(answer_group).to_have_attribute("open", "")
        expect(answer_group.locator("[data-group-count]")).to_have_text("1")
        expect(answer_group.locator("[data-group-count]")).to_be_visible()
        for row in (left_ticket, right_ticket):
            row.wait_for(timeout=10_000)
            expect(row.locator('[data-conversation-signal="message"]')).to_have_count(
                1, timeout=10_000
            )
            expect(row.locator('[data-conversation-signal="working"]')).to_have_count(0)
        assert left_item.locator(".item-head .stage-mark").count() == 0
        assert right_item.locator(".sprint-workspace-title-mark").count() == 0
        assert left_item.locator('[data-bucket-key="awaiting_answer"]').count() == 1
        assert right_item.locator('[data-workspace-group="awaiting_answer"]').count() == 1

        mutable.update(
            awaiting_answer=False,
            agent_state="idle",
            conversation_signal={"working": False, "message_for_owner": True},
        )
        page.evaluate("window.__refresh()")
        expect(assigned_group).not_to_have_attribute("open", "")
        assigned_group.locator(":scope > summary").click()
        expect(assigned_group).to_have_attribute("open", "")
        for row in (left_ticket, right_ticket):
            expect(row.locator('[data-conversation-signal="message"]')).to_have_count(
                1, timeout=10_000
            )
            expect(row.locator('[data-conversation-signal="working"]')).to_have_count(0)

        page.reload()
        expect(assigned_group).to_have_attribute("open", "")
        right_ticket.wait_for(timeout=10_000)
        for row in (left_ticket, right_ticket):
            expect(row.locator('[data-conversation-signal="message"]')).to_have_count(1)
            expect(row.locator('[data-conversation-signal="working"]')).to_have_count(0)
        # A canonical unread message masks the spinner. A read clears only the message
        # fact, so the same ongoing work appears in both mounted projections.
        mutable.update(
            agent_state="working",
            conversation_signal={"working": True, "message_for_owner": True},
        )
        page.evaluate("window.__refresh()")
        for row in (left_ticket, right_ticket):
            expect(row.locator('[data-conversation-signal="message"]')).to_have_count(1)
            expect(row.locator('[data-conversation-signal="working"]')).to_have_count(0)
        mutable.update(
            awaiting_reply=False,
            conversation_signal={"working": True, "message_for_owner": False},
        )
        page.evaluate("window.__refresh()")
        for row in (left_ticket, right_ticket):
            expect(row.locator('[data-conversation-signal="message"]')).to_have_count(0)
            expect(row.locator('[data-conversation-signal="working"]')).to_have_count(1)
        # Preserve artifact focus and keyboard access without a live-server geometry catalog.
        band=right_item.locator("[data-artifacts-band]")
        expect(band).not_to_have_attribute("open","")
        band.locator(":scope > summary").focus()
        page.keyboard.press("Enter")
        notes=band.locator('[data-artifact-folder="notes"]')
        expect(notes).to_have_attribute("aria-expanded","false")
        notes.click()
        expect(notes).to_have_attribute("aria-expanded","true")
        expect(band.locator('[data-artifact-chip="artifacts/notes/one.md"]')).to_be_visible()
        link=page.get_by_role("link",name="Open proof",exact=True)
        link.focus()
        link.click()
        preview=page.locator("[data-ticket-artifact]")
        expect(preview).to_be_visible()
        expect(preview).to_be_focused()
        preview.locator("[data-ticket-artifact-close]").click()
        expect(preview).to_have_count(0)
        chip=band.locator('[data-artifact-chip="artifacts/proof.md"]')
        chip.focus()
        chip.click()
        expect(preview).to_be_visible()
        page.keyboard.press("Escape")
        expect(preview).to_have_count(0)
        expect(chip).to_be_focused()
        context.tracing.stop()
        context.close()
        assert errors == [], errors
    browser.close()
print(json.dumps(results),flush=True)
if output:
    (pathlib.Path(output)/"results.json").write_text(json.dumps(results,indent=2))
assert all(r['actual']==r['expected'] for r in results), results
`;
  const port = server.httpServer.address().port;
  const child = spawn(process.env.PANELS_TEST_PYTHON || join(root,"..",".venv","bin","python"),
    ["-c",script,`http://127.0.0.1:${port}/.test-scratch/${stem}.html`,...process.argv.slice(2)],{stdio:"inherit",cwd:join(root,"..")});
  const code = await new Promise((resolve,reject) => {child.on("error",reject);child.on("exit",resolve);});
  assert.equal(code,0,"Reader choices must survive same-task membership change");
} finally {
  await server?.close();
  await Promise.all(files.map(path=>rm(path,{force:true})));
}
