import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const repositoryRoot = join(webRoot, "..");
const hostPath = join(scratchRoot, `ideas-shell-host-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `ideas-shell-main-${process.pid}.ts`);
const indexPath = join(scratchRoot, `ideas-shell-index-${process.pid}.html`);

  await writeFile(hostPath, `
<script lang="ts">
  import AppWithQueryClient from "../src/AppWithQueryClient.svelte";

  let ideas: any[] = [];
  const projects = [
    { id: "project_vylo", name: "Vylo" },
    { id: "project_tribe", name: "Tribe" }
  ];
  const requests: Array<{ path: string; method: string; body: any }> = [];

  class QuietEventSource {
    onopen: null | (() => void) = null;
    onmessage: null | (() => void) = null;
    onerror: null | (() => void) = null;
    constructor(_url: string) {}
    close() {}
  }
  (globalThis as any).EventSource = QuietEventSource;

  function json(value: unknown): Response {
    return new Response(JSON.stringify(value), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  }
  function page(returnCount: number, matchCount: number, offset: number) {
    const omittedBefore = Math.min(offset, matchCount);
    const omittedAfter = Math.max(matchCount - omittedBefore - returnCount, 0);
    return {
      match_count: matchCount,
      return_count: returnCount,
      limit: 30,
      offset,
      omitted_before: omittedBefore,
      omitted_after: omittedAfter,
      complete: omittedBefore === 0 && omittedAfter === 0,
      next_offset: omittedAfter === 0 ? null : omittedBefore + returnCount
    };
  }
  globalThis.fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input);
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : null;
    requests.push({ path, method, body });
    if (path === "/api/review") {
      return json({ running_worker_count: 0, awaiting_approval_count: 0 });
    }
    if (path === "/api/projects?detail=full") return json({ projects });
    if (path === "/api/worker-types" || path === "/api/worker-types?supported=true") {
      return json({
        worker_types: [
          { worker_type: "coding", label: "Coding" },
          { worker_type: "exploration", label: "Exploration" }
        ]
      });
    }
    if (path === "/api/navigation/snapshot") {
      return json((window as any).__navigationFixture);
    }
    if (path === "/api/workers") {
      return json({
        workers: [],
        chief_of_staff: {
          label: "Chief of Staff",
          skill: { description: "Chief role", markdown_body: "# Chief" },
          conversation_id: null,
          needs_me: false,
          agent_working: false,
          latest_turn_ended_sequence: 0
        }
      });
    }
    if (path === "/api/items/item_offboard/workspace-content") {
      return json({
        body: "The off-board brief remains readable.",
        artifacts: [],
        conversation_history: []
      });
    }
    if (path === "/api/items/item_offboard/conversation/start-values") {
      return json({ backend_key: "codex", model: "gpt-5", reasoning_effort: null });
    }
    if (path === "/api/conversation/backends") {
      return json({ backends: [] });
    }
    if (path === "/api/feedback/count") return json({ open_count: 3 });
    if (path === "/api/skills") return json({ skills: [] });
    if (path === "/api/schedules") return json({ schedules: [] });
    if (path === "/api/notifications/settings") return json({ subjects: [], vapid_public_key: "" });
    if (path === "/api/ideas" && method === "GET") return json({ ideas });
    if (path === "/api/ideas" && method === "POST") {
      const project = projects.find((entry) => entry.id === body.project_id);
      const idea = {
        id: "idea_" + (ideas.length + 1),
        title: body.title,
        body: body.body ?? null,
        project_id: body.project_id ?? null,
        project: project?.name ?? null
      };
      ideas = [idea, ...ideas];
      return json(idea);
    }
    return new Response(JSON.stringify({ error: { code: "unexpected", message: path } }), {
      status: 500,
      headers: { "Content-Type": "application/json" }
    });
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
  await writeFile(
    indexPath,
    `<!doctype html><html><body><div id="app"></div><script type="module" src="./${mainPath.split("/").at(-1)}"></script></body></html>`,
    "utf8",
  );

  return {inputs: [indexPath], arguments: [indexPath], python: fileURLToPath(new URL("./ideas-shell.py", import.meta.url))};
}
