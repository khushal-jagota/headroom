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
import OrchestratorChildren from "../src/components/OrchestratorChildren.svelte";
import type { OrchestratorPrincipal, SprintItemWorkspaceTicket } from "../src/lib/types";
const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
let principal = $state<OrchestratorPrincipal>({kind:"chief",id:"chief"});
let tickets = $state<SprintItemWorkspaceTicket[] | undefined>(undefined);
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
import json, sys
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright, expect

def child(name, stage="needs_implementation", backlogged=False):
    return dict(id=name,title=name,stage=stage,is_backlogged=backlogged,priority="P2",activity_at=1,
        ticket_status="empty",waiting_to_closeout=False,has_pending_proposal=False,gating_field=None,
        blocked=False,worker_type="coding",day_ids=[],sprint_id=None,sprint_name=None,awaiting_reply=False,
        awaiting_answer=False,awaiting_approval=False,awaiting_agent_approval=False,assigned=False,agent_state="idle")
requests=[]
def respond(route):
    query=parse_qs(urlparse(route.request.url).query)
    requests.append(query)
    assert query["orchestrator_kind"] == ["chief"]
    offset=int(query["offset"][0]); stage=query.get("stage",[None])[0]
    if stage: rows=[child(stage,stage)]; total=1
    elif query.get("is_backlogged") == ["true"]: rows=[child("deferred",backlogged=True)]; total=1
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
    for state,stage in [("done","done"),("retired","cancelled")]:
        page.get_by_label("Worker state").select_option(state)
        group=page.locator(f'[data-workspace-group="{stage}"]')
        expect(group).to_be_visible()
        group.locator("summary").click()
        expect(group.get_by_role("link",name=stage,exact=True)).to_be_visible()
    source=[child("item-quiet"),child("item-deferred",backlogged=True),child("item-old","cancelled")]
    count=len(requests)
    page.evaluate("rows => window.__setItem(rows)",source)
    expect(page.get_by_label("Worker state")).to_have_value("active")
    expect(page.locator('[data-sprint-ticket-id]')).to_have_count(2)
    expect(page.get_by_role("link",name="item-quiet",exact=True)).to_have_attribute("href","#/workspace/item/item%20one/item-quiet")
    page.get_by_label("Worker state").select_option("retired")
    expect(page.locator('[data-sprint-ticket-id]')).to_have_count(1)
    page.get_by_label("Worker state").select_option("done")
    expect(page.get_by_text("No Workers",exact=True)).to_be_visible()
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
