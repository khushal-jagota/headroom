import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { scratchDirectory } from "./support/scratch.mjs";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const stem = `orchestrator-children-${process.pid}`;
const scratch = await scratchDirectory();
const files = [join(scratch, `${stem}.svelte`), join(scratch, `${stem}.ts`), join(scratch, `${stem}.html`)];
let server;
try {
  await writeFile(files[0], `<script lang="ts">
import { QueryClient, QueryClientProvider } from "@tanstack/svelte-query";
import { tick } from "svelte";
import OrchestratorChildren from "../src/components/OrchestratorChildren.svelte";
import type { OrchestratorPrincipal, SprintItemWorkspaceTicket } from "../src/lib/types";
const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
let principal = $state<OrchestratorPrincipal>({kind:"chief",id:"chief"});
let tickets = $state<SprintItemWorkspaceTicket[] | undefined>(undefined);
(window as any).__refresh = async () => { await client.invalidateQueries(); await tick(); };
(window as any).__setRows = (rows: SprintItemWorkspaceTicket[]) => { tickets = rows; };
(window as any).__setItem = (rows: SprintItemWorkspaceTicket[]) => { principal = {kind:"sprint_item",id:"item one"}; tickets = rows; };
</script>
<QueryClientProvider {client}><OrchestratorChildren {principal} {tickets} /></QueryClientProvider>`);
  await writeFile(files[1], `import { mount } from "svelte"; import Host from "./${stem}.svelte"; import "../../assets/tokens.css"; import "../../assets/app.css"; mount(Host,{target:document.getElementById("app")!});`);
  await writeFile(files[2], `<html><body><div id="app"></div><script type="module" src="./${stem}.ts"></script></body></html>`);
  server = await createServer({root,configFile:false,plugins:[svelte()],server:{host:"127.0.0.1",port:0},logLevel:"error"});
  await server.listen();
  const address = server.httpServer.address();
  assert.ok(address && typeof address === "object");
  const script = String.raw`
import json, os, pathlib, sys
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright, expect

def child(name, stage="needs_implementation", backlogged=False, **facts):
    row = dict(id=name,title=name,stage=stage,is_backlogged=backlogged,activity_at=1,
        ticket_status="empty",waiting_to_closeout=False,has_pending_proposal=False,gating_field=None,
        blocked=False,worker_type="coding",day_ids=[],sprint_id=None,sprint_name=None,awaiting_reply=False,
        awaiting_answer=False,awaiting_approval=False,awaiting_agent_approval=False,assigned=False,agent_state="idle")
    row.update(facts)
    return row
requests=[]
def respond(route):
    query=parse_qs(urlparse(route.request.url).query)
    requests.append(query)
    assert query["orchestrator_kind"] == ["chief"]
    offset=int(query["offset"][0]); stage=query.get("stage",[None])[0]
    if stage == "done": rows=[child("Completed workspace history", "done", True, awaiting_approval=True)]; total=1
    elif stage: rows=[child(stage,stage)]; total=1
    elif query.get("is_backlogged") == ["true"]: rows=[child("deferred",backlogged=True,awaiting_approval=True)]; total=1
    else: total=31; rows=[child(f"quiet-{i}") for i in range(offset,min(offset+30,total))]
    after=max(0,total-offset-len(rows))
    page=dict(match_count=total,return_count=len(rows),limit=30,offset=offset,omitted_before=min(offset,total),
        omitted_after=after,complete=offset==0 and after==0,next_offset=offset+len(rows) if after else None)
    route.fulfill(status=200,content_type="application/json",body=json.dumps(dict(tickets=rows,page=page)))
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport=dict(width=1100,height=900))
    page.route("**/api/tickets?**",respond)
    page.goto(sys.argv[1])
    expect(page.locator('[data-sprint-ticket-id]')).to_have_count(30)
    expect(page.get_by_role("link",name="quiet-0",exact=True)).to_have_attribute("href","#/workspace/quiet-0")
    page.get_by_role("button",name="Next",exact=True).click()
    expect(page.locator('[data-sprint-ticket-id]')).to_have_count(1)
    expect(page.get_by_role("link",name="quiet-30",exact=True)).to_be_visible()
    page.get_by_label("Worker state").select_option("backlogged")
    expect(page.get_by_role("link",name="deferred",exact=True)).to_be_visible()
    assert requests[-1]["offset"] == ["0"]
    backlog=page.locator('[data-workspace-group="backlog"]')
    expect(backlog).to_be_visible()
    expect(backlog.locator('summary')).to_contain_text("Backlog")
    expect(backlog.get_by_role("link",name="deferred",exact=True)).to_have_attribute("href","#/workspace/deferred")
    expect(page.locator('[data-workspace-group="awaiting_approval"]')).to_have_count(0)
    # A reader's explicit close survives a refetch and leaving/returning to this filter.
    backlog.locator("summary").click()
    page.wait_for_function("!document.querySelector('[data-workspace-group=backlog]').open")
    before_refresh=len(requests)
    page.evaluate("window.__refresh()")
    assert len(requests)>before_refresh, "refresh must complete a controlled API read"
    assert not backlog.evaluate("node=>node.open")
    for state,stage in [("done","done")]:
        page.get_by_label("Worker state").select_option(state)
        key="backlog" if stage == "done" else stage
        group=page.locator(f'[data-workspace-group="{key}"]')
        expect(group).to_be_visible()
        if not group.evaluate("node=>node.open"): group.locator("summary").click()
        name="Completed workspace history" if stage == "done" else stage
        expect(group.get_by_role("link",name=name,exact=True)).to_have_attribute("href","#/workspace/"+name.replace(" ","%20"))
    page.get_by_label("Worker state").select_option("backlogged")
    expect(backlog).to_be_visible()
    assert backlog.evaluate("node=>node.open"), "the manual open in Done follows the same Backlog group"
    backlog.locator("summary").click()
    page.get_by_label("Worker state").select_option("active")
    page.get_by_label("Worker state").select_option("backlogged")
    expect(backlog).to_be_visible()
    assert not backlog.evaluate("node=>node.open"), "manual closed choice survives filter removal"
    source=[child("item-quiet"),
        child("Saved owner review",backlogged=True,awaiting_approval=True,has_pending_proposal=True),
        child("Saved supervisor review",backlogged=True,awaiting_agent_approval=True,has_pending_proposal=True),
        child("Saved message",backlogged=True,awaiting_reply=True,conversation_signal=dict(working=False,message_for_owner=True)),
        child("Completed workspace history","done",True)]
    count=len(requests)
    page.evaluate("rows => window.__setItem(rows)",source)
    expect(page.get_by_label("Worker state")).to_have_value("active")
    expect(page.locator('[data-sprint-ticket-id]')).to_have_count(4)
    expect(page.get_by_role("link",name="item-quiet",exact=True)).to_have_attribute("href","#/workspace/item/item%20one/item-quiet")
    backlog=page.locator('[data-workspace-group="backlog"]')
    expect(backlog).to_be_visible()
    assert backlog.evaluate("node=>node.open"), "a new principal has its own reader choices"
    for title in ["Saved owner review", "Saved supervisor review", "Saved message"]:
        row=page.locator('[data-sprint-ticket-id="'+title+'"]')
        expect(row).to_have_count(1)
        expect(backlog.locator('[data-sprint-ticket-id="'+title+'"]')).to_be_visible()
        expect(row).to_have_accessible_name(title + (" Unread message" if title == "Saved message" else ""))
        expect(row.locator('.ticket-row-title')).to_have_text(title)
        assert row.inner_text() == title
        expect(row).to_have_attribute("href","#/workspace/item/item%20one/"+title.replace(" ","%20"))
    expect(page.locator('[data-workspace-group="awaiting_approval"], [data-workspace-group="status_awaiting_approval"]')).to_have_count(0)
    expect(backlog.locator('.stage-mark')).to_have_count(1)
    expect(backlog.locator('[data-sprint-ticket-id="Saved message"]').locator('.stage-mark')).to_have_attribute("aria-label","Unread message")
    output=os.environ.get("PANELS_UI_PROOF_DIR")
    if output:
        folder=pathlib.Path(output);folder.mkdir(parents=True,exist_ok=True)
        for label,width in [("desktop",1100),("compact",390)]:
            page.set_viewport_size(dict(width=width,height=900))
            expect(backlog.get_by_role("link",name="Saved owner review",exact=True)).to_be_visible()
            page.screenshot(path=str(folder/("backlog-full-list-"+label+".png")),full_page=True)
        (folder/"backlog-full-list-dom.html").write_text(page.content())
    # Resume changes only membership: pending owner approval becomes visible again.
    source[1]["is_backlogged"]=False
    page.evaluate("rows=>window.__setRows(rows)",source)
    expect(page.locator('[data-workspace-group="awaiting_approval"]').get_by_role("link",name="Saved owner review",exact=True)).to_be_visible()
    assert source[1]["has_pending_proposal"] is True
    page.get_by_label("Worker state").select_option("backlogged")
    expect(page.locator('[data-sprint-ticket-id]')).to_have_count(2)
    page.get_by_label("Worker state").select_option("done")
    expect(page.locator('[data-workspace-group="backlog"]')).to_be_visible()
    history=page.get_by_role("link",name="Completed workspace history",exact=True)
    expect(history).to_have_attribute("href","#/workspace/item/item%20one/Completed%20workspace%20history")
    expect(page.locator('[data-workspace-group="done"]')).to_have_count(0)
    assert len(requests)==count
    browser.close()
print("Scoped child browser checks passed")
`;
  await new Promise((resolve, reject) => {
    const child = spawn(process.env.PANELS_TEST_PYTHON || join(root,"..",".venv","bin","python"), ["-c", script, `http://127.0.0.1:${address.port}/${scratch.split("/").at(-1)}/${stem}.html`], {cwd:root,stdio:"inherit"});
    child.on("error",reject); child.on("exit",code => code===0 ? resolve() : reject(new Error(`Browser checks exited ${code}`)));
  });
} finally {
  if (server) await server.close();
  await Promise.all(files.map(file => rm(file,{force:true})));
}
