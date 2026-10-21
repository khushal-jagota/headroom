import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export async function prepare({ scratchRoot }) {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const webRoot = root;
  const scratch = scratchRoot;
const repositoryRoot = join(webRoot, "..");
const hostPath = join(scratchRoot, `scheduled-tasks-host-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `scheduled-tasks-main-${process.pid}.ts`);
const indexPath = join(scratchRoot, `scheduled-tasks-index-${process.pid}.html`);

  await writeFile(hostPath, `
<script lang="ts">
  import AppWithQueryClient from "../src/AppWithQueryClient.svelte";

  const projects = [{ id: "project_other", name: "Other", summary: "", created_at: 1, updated_at: 1 }, {
    id: "project_panels",
    name: "Panels",
    summary: "The Panels app",
    created_at: 1,
    updated_at: 1
  }];
  const managers = [{
    id: "si_demo",
    title: "Scheduled tasks",
    project_id: "project_panels",
    project: "Panels",
    kind: "normal"
  }];
  const workerTypes = [{ worker_type: "planning-day", label: "Retired planning", creatable: false },
    {
      worker_type: "coding",
      label: "Coding",
      stages: [],
      advance: {},
      fields: [],
      ceiling_range: [],
      default_ceiling: "needs_implementation",
      worker_profile_id: "coding",
      default_backend: "codex",
      default_model: null,
      default_reasoning_effort: null
    }
  ];
  let schedules: any[] = [{
    id: "schedule_demo",
    enabled: true,
    cadence: "daily",
    local_time: "09:00",
    title: "Existing daily task",
    worker_type: "coding",
    kickoff_note: "Use the saved schedule.",
    deadline: null,
    project_id: "project_panels",
    manager: { kind: "manager", id: "si_demo" },
    manager_id: "si_demo",
    employee_backend: null,
    employee_launch_model: null,
    blocked_by_ticket_ids: [],
    created_at: 1,
    updated_at: 1
  }];
  schedules.push({ ...schedules[0], id: "schedule_retired", enabled: false, title: "Historical planning task", cadence: "every_planning_day", worker_type: "planning-day" });
  schedules.push({ ...schedules[1], id: "schedule_retired_active", enabled: true, title: "Disable old planning task" });
  schedules.push({ ...schedules[0], id: "schedule_personal", enabled: false, title: "Historical daily personal task", worker_type: "personal" });
  const requests: Array<{ path: string; method: string; body: any }> = [];

  class QuietEventSource {
    onopen: null | (() => void) = null;
    onmessage: null | (() => void) = null;
    onerror: null | (() => void) = null;
    constructor(_url: string) {}
    close() {}
  }
  (globalThis as any).EventSource = QuietEventSource;

  function json(value: unknown, status = 200): Response {
    return new Response(JSON.stringify(value), {
      status,
      headers: { "Content-Type": "application/json" }
    });
  }
  function scheduleFromBody(id: string, body: any) {
    return {
      id,
      enabled: body.enabled,
      cadence: body.cadence,
      local_time: body.local_time,
      title: body.title,
      worker_type: body.worker_type,
      kickoff_note: body.kickoff_note,
      deadline: body.deadline,
      project_id: body.project_id,
      manager: body.manager,
      manager_id: body.manager.kind === "manager" ? body.manager.id : null,
      employee_backend: body.employee_backend,
      employee_launch_model: body.employee_launch_model,
      blocked_by_ticket_ids: body.blocked_by_ticket_ids,
      created_at: 2,
      updated_at: 2
    };
  }
  globalThis.fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input);
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(String(init.body)) : null;
    requests.push({ path, method, body });
    if (path === "/api/review") {
      return json({ items: [], running_worker_count: 0, awaiting_approval_count: 0 });
    }
    if (path.startsWith("/api/resource-body?")) {
      const key = new URL(path, location.origin).searchParams.get("key");
      if (key === "/api/worker-types" || key === "/api/worker-types?supported=true") {
        return json({ key, epoch: "fixture", revision: "manifest-1", body: { worker_types: key.endsWith("?supported=true") ? workerTypes.filter(entry => entry.creatable !== false) : workerTypes } });
      }
    }
    if (path === "/api/projects?detail=full") return json({ projects });
    if (path === "/api/managers?detail=full") return json({ managers });
    if (path === "/api/worker-types?supported=true") return json({ worker_types: workerTypes.filter(entry => entry.creatable !== false) });
    if (path === "/api/worker-types") return json({ worker_types: workerTypes });
    if (path === "/api/schedules" && method === "GET") return json({ schedules });
    if ((path === "/api/schedules" && method === "POST") || (path.startsWith("/api/schedules/") && method === "PATCH")) {
      if (["placement_mode", "manager_id", "sprint_id"].some(key => key in body)) return json({error:{message:"Retired placement field"}},422);
      if ("cadence" in body && body.cadence !== "daily") return json({error:{message:"Retired cadence"}},422);
    }
    if (path === "/api/schedules" && method === "POST") {
      if (body.title === "Reject me") {
        return json({ error: { code: "validation", message: "The schedule was rejected." } }, 422);
      }
      const created = scheduleFromBody("schedule_created", body);
      schedules = [created, ...schedules];
      return json(created);
    }
    if (path.startsWith("/api/schedules/") && method === "PATCH") {
      const id = path.split("/").at(-1);
      schedules = schedules.map((schedule) => schedule.id === id ? { ...schedule, ...body, manager_id: body.manager ? (body.manager.kind === "manager" ? body.manager.id : null) : schedule.manager_id, updated_at: 3 } : schedule);
      return json(schedules.find((schedule) => schedule.id === id));
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
  await writeFile(
    indexPath,
    `<!doctype html><html><body><div id="app"></div><script type="module" src="./${mainPath.split("/").at(-1)}"></script></body></html>`,
    "utf8",
  );

  return {inputs: [indexPath], arguments: [indexPath], python: fileURLToPath(new URL("./scheduled-tasks.py", import.meta.url))};
}
