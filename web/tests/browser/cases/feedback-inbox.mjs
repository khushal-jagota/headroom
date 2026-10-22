import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const repositoryRoot = join(webRoot, "..");
const suffix = process.pid;
const hostPath = join(scratchRoot, `feedback-host-${suffix}.svelte`);
const mainPath = join(scratchRoot, `feedback-main-${suffix}.ts`);
const indexPath = join(scratchRoot, `feedback-index-${suffix}.html`);

  await writeFile(hostPath, `
<script lang="ts">
  import AppWithQueryClient from "../src/AppWithQueryClient.svelte";
  import {queryClient} from "../src/lib/queryClient";
  import {connectionStatus} from "../src/lib/changeStream";
  (window as any).__connection = (state: "connected" | "reconnecting") => connectionStatus.set(state);
  let notes: any[] = [{
    id: "f_1", text: "The button moved", page_address: "#/review", page_label: "Review",
    state: "open", ticket_id: null, created_at: Date.now() / 1000,
    updated_at: Date.now() / 1000, handled_at: null
  }];
  const requests: any[] = [];
  let deployment = {state:"preparing", deployed_sha:null,target_sha:"abc",outcome:null,detail:null,valid_until:new Date(Date.now()+600000).toISOString()};
  (window as any).__deployment = async (state: string) => {
    deployment = {...deployment, state};
    await queryClient.invalidateQueries({queryKey:["deployment-status"]});
  };
  let interruptions = {accounts: [] as any[]};
  (window as any).__interruptions = async () => {
    const entry = {interruption_id:"notice",deployment_id:"deploy",conversation_id:"conv",display_name:"Draft release notes",owner_kind:"ticket",owner_id:"worker",original_status:"active",recovery_state:"awaiting_action",detail:"A deploy ended the turn. Check current work before another action.",can_restart:true};
    interruptions = {accounts: [
      {deployment_id:"deploy",capture_state:"captured",detail:null,entries:[entry,
        {...entry,interruption_id:"historical",display_name:"Earlier conversation",conversation_id:null,recovery_state:"historical",can_restart:false}]},
      {deployment_id:"gap",capture_state:"unavailable",detail:"Capture timed out.",entries:[]}
    ]};
    await queryClient.invalidateQueries({queryKey:["deployment-interruptions"]});
  };
  class QuietEventSource { constructor() { setTimeout(() => (this.onopen as any)?.({}), 0); } onopen = null; onmessage = null; onerror = null; addEventListener() {} removeEventListener() {} close() {} }
  (globalThis as any).EventSource = QuietEventSource;
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
    status, headers: { "Content-Type": "application/json" }
  });
  const response = () => ({
    open_count: notes.filter((note) => note.state === "open").length,
    open: notes.filter((note) => note.state === "open"),
    handled_groups: notes.some((note) => note.state === "handled")
      ? [{ ticket: null, notes: notes.filter((note) => note.state === "handled") }]
      : []
  });
  globalThis.fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input); const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : null;
    requests.push({ path, method, body });
    if (path.startsWith("/api/resource-body?")) {
      const key = new URL(path, location.origin).searchParams.get("key");
      if (key === "/api/deployment-status") return json({ key, epoch: "fixture", revision: "1", body: deployment });
      if (key === "/api/feedback/count") return json({ key, epoch: "fixture", revision: String(response().open_count), body: { open_count: response().open_count } });
    }
    if (path === "/api/deployment-interruptions") return json(interruptions);
    if (path === "/api/tickets/worker/restart-worker" && method === "POST") {
      interruptions.accounts[0].entries[0].recovery_state = "resumed";
      interruptions.accounts[0].entries[0].can_restart = false;
      return json({started:true,delivery_fate:"started",not_started_because:null});
    }
    if (path === "/api/review") return json({ items: [], running_worker_count: 0 });
    if (path === "/api/feedback/count" && method === "GET") return json({ open_count: response().open_count });
    if (path === "/api/feedback" && method === "GET") return json(response());
    if (path === "/api/feedback" && method === "POST") {
      if (body.text === "fail") return json({ error: { code: "offline", message: "offline" } }, 503);
      const note = { id: "f_" + (notes.length + 1), ...body, state: "open", ticket_id: null,
        created_at: Date.now() / 1000, updated_at: Date.now() / 1000, handled_at: null };
      notes = [note, ...notes]; return json(note);
    }
    const match = path.match(/^\\/api\\/feedback\\/(f_\\d+)\\/(dismiss|reopen)$/);
    if (match && method === "POST") {
      notes = notes.map((note) => note.id === match[1] ? { ...note, state: match[2] === "dismiss" ? "handled" : "open" } : note);
      return json({});
    }
    return json({ error: { code: "unexpected", message: path } }, 500);
  }) as typeof fetch;
  (window as any).__requests = () => requests;
</script>
<AppWithQueryClient />
`, "utf8");
  await writeFile(mainPath, `
import { mount } from "svelte";
import Host from "./${hostPath.split("/").at(-1)}";
import "../../assets/tokens.css";
import "../../assets/app.css";
mount(Host, { target: document.getElementById("app")! });
`, "utf8");
  await writeFile(indexPath, `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"></head><body><div id="app"></div><script type="module" src="./${mainPath.split("/").at(-1)}"></script></body></html>`, "utf8");

  return {inputs: [indexPath], arguments: [indexPath], python: fileURLToPath(new URL("./feedback-inbox.py", import.meta.url))};
}
