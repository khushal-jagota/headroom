import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const hostPath = join(scratchRoot, `sprint-documents-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `sprint-documents-${process.pid}.ts`);
const indexPath = join(scratchRoot, `sprint-documents-${process.pid}.html`);
  await writeFile(hostPath, `
<script lang="ts">
  import { QueryClientProvider } from "@tanstack/svelte-query";
  import { queryClient } from "../src/lib/queryClient";
  import SprintRoute from "../src/routes/SprintRoute.svelte";
  let sub = $state("tracking");
  let selectedItemId = $state<string | null>(null);
  const sprint = {
    id: "sp_test", name: "A useful historical sprint", date_start: "2026-09-01", date_end: "2026-09-07",
    primary_bet: "Original **summary**", kickoff: "Original plan. [Proof](/files/tickets/t_history/artifacts/plan.md)",
    checkpoint: "Original checkpoint", review: "Original review", created_at: 1, updated_at: 1
  };
  const requests: Array<{ path: string; method: string; body: unknown }> = [];
  const outcome = { id: "outcome_existing", title: "Ship it", deadline: null, project_id: "project_one", project: "One", created_at: 1, updated_at: 1 };
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
    status, headers: { "Content-Type": "application/json" }
  });
  globalThis.fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input);
    const method = init.method || "GET";
    requests.push({ path, method, body: init.body ? JSON.parse(String(init.body)) : null });
    if (path === "/files/tickets/t_history/artifacts/plan.md") return new Response("Historical artifact proof", { headers:{ "Content-Type":"text/markdown" } });
    if (path === "/api/sprints/sp_test/tracking") return json({
      sprint, planning_date: "2026-09-08", outcome_groups: [{ outcome, committed: true, tickets: [
        { id: "t_one", title: "Move me", stage: "needs_plan", ticket_status: "empty", project_id: "project_one", sprint_item_id: outcome.id, waiting_to_closeout: false },
        { id: "t_done", title: "Leave done", stage: "done", ticket_status: "empty", project_id: "project_one", sprint_item_id: outcome.id, waiting_to_closeout: false }
      ] }], unclassified_tickets: [
        { id: "t_loose_one", title: "Loose from One", stage: "needs_plan", ticket_status: "empty", project_id: "project_one", sprint_item_id: null, waiting_to_closeout: false },
        { id: "t_loose_two", title: "Loose from Two", stage: "done", ticket_status: "empty", project_id: "project_two", sprint_item_id: null, waiting_to_closeout: false }
      ]
    });
    // A retired live calendar and absent current catalogue must never hide this record.
    // Any accidental calendar request receives a real HTTP failure, not an empty fixture.
    if (["/api/day/today", "/api/sprint/current", "/api/projects?detail=full"].includes(path)) {
      return json({ error:{ code:"not_found", message:"No live calendar/catalogue record" } },404);
    }
    throw new Error("Unexpected historical Sprint request: " + method + " " + path);
  }) as typeof fetch;
  (window as any).saved = () => ({ sprint, requests });
  (window as any).showLegacyItem = () => { selectedItemId = outcome.id; };
</script>
<button onclick={() => sub = sub === "documents" ? "tracking" : "documents"}>Switch view</button>
<QueryClientProvider client={queryClient}><SprintRoute {sub} {selectedItemId} sprintId="sp_test" /></QueryClientProvider>
`);
  await writeFile(mainPath, `
import { mount } from "svelte";
import Host from "./${hostPath.split("/").at(-1)}";
import "../../assets/tokens.css";
import "../../assets/app.css";
mount(Host, { target: document.getElementById("app")! });
`);
  await writeFile(indexPath, `<!doctype html><html><body><div id="app"></div><script type="module" src="./${mainPath.split("/").at(-1)}"></script></body></html>`);

  return {inputs: [indexPath], arguments: [indexPath], python: fileURLToPath(new URL("./sprint-documents.py", import.meta.url))};
}
