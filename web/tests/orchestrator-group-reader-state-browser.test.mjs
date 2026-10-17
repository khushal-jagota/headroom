import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { writeFile, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { scratchDirectory } from "./support/scratch.mjs";

// A native details click and filter removal in one task expose queued-toggle loss.
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
import type { SprintItemWorkspaceTicket } from '../src/lib/types';
const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
const tickets = ['done','needs_implementation'].map((stage, i) => ({
 id:'t_'+i,title:stage,stage,is_backlogged:false,priority:'P2',activity_at:1,
 ticket_status:'empty',waiting_to_closeout:false,has_pending_proposal:false,gating_field:null,
 blocked:false,worker_type:'coding',day_ids:[],sprint_id:null,sprint_name:null,
 awaiting_reply:false,awaiting_answer:false,awaiting_approval:false,
 awaiting_agent_approval:false,assigned:false,agent_state:'idle'
})) as SprintItemWorkspaceTicket[];
</script>
<QueryClientProvider {client}><OrchestratorChildren principal={{kind:'sprint_item',id:'si_probe'}} {tickets}/></QueryClientProvider>`);
  await writeFile(files[1], `import {mount} from 'svelte';import Host from './${stem}.svelte';mount(Host,{target:document.body});`);
  await writeFile(files[2], `<html><body><script type="module" src="./${stem}.ts"></script></body></html>`);
  server = await createServer({root,configFile:false,plugins:[svelte()],server:{host:"127.0.0.1",port:0},logLevel:"error"});
  await server.listen();
  const script = String.raw`
import json, os, pathlib, sys
from playwright.sync_api import sync_playwright, expect
results = []
output = os.environ.get("PANELS_UI_PROOF_DIR")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for choice in sys.argv[2:] or ["open", "closed"]:
        context = browser.new_context()
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        page.goto(sys.argv[1])
        page.get_by_label("Worker state").select_option("done")
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
            const s=document.querySelector('select');s.value='active';
            s.dispatchEvent(new Event('change',{bubbles:true}));}""")
        expect(page.locator('[data-workspace-group="empty"]')).to_be_visible()
        assert page.locator('[data-workspace-group="empty"]').evaluate("n=>n.open"), "unfinished default changed"
        page.get_by_label("Worker state").select_option("done")
        expect(group).to_be_visible()
        actual = group.evaluate("n=>n.open")
        expected = choice == "open"
        results.append(dict(choice=choice,key="done",expected=expected,actual=actual))
        if output:
            path=pathlib.Path(output);path.mkdir(parents=True,exist_ok=True)
            (path/(choice+"-dom.html")).write_text(page.content())
            (path/(choice+"-aria.txt")).write_text(page.locator('body').aria_snapshot())
            context.tracing.stop(path=str(path/(choice+"-trace.zip")))
        else:
            context.tracing.stop()
        context.close()
    browser.close()
print(json.dumps(results),flush=True)
if output:
    (pathlib.Path(output)/"results.json").write_text(json.dumps(results,indent=2))
assert all(r['actual']==r['expected'] for r in results), results
`;
  const port = server.httpServer.address().port;
  const child = spawn(process.env.PANELS_TEST_PYTHON || join(root,"..",".venv","bin","python"),
    ["-c",script,`http://127.0.0.1:${port}/.test-scratch/${stem}.html`,...process.argv.slice(2)],{stdio:"inherit"});
  const code = await new Promise((resolve,reject) => {child.on("error",reject);child.on("exit",resolve);});
  assert.equal(code,0,"Reader choices must survive same-task filter removal");
} finally {
  await server?.close();
  await Promise.all(files.map(path=>rm(path,{force:true})));
}
