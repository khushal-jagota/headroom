import { render } from "svelte/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WorkerTypeManifest, WorkerTypesResponse } from "../src/lib/lifecycle";
import type { SkillsHomeResponse, WorkersResponse } from "../src/lib/types";
import ConfigRoute from "../src/routes/ConfigRoute.svelte";
import BackendsRoute from "../src/routes/BackendsRoute.svelte";

const reads = vi.hoisted(() => new Map<string, {
  data?: unknown;
  error: unknown;
  isFetching: boolean;
}>());
const queryEnabled = vi.hoisted(() => new Map<string, boolean | undefined>());

vi.mock("@tanstack/svelte-query", async (importOriginal) => ({
  ...await importOriginal<typeof import("@tanstack/svelte-query")>(),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  createQuery: (options: () => { queryKey: readonly unknown[]; enabled?: boolean }) => {
    const query = options();
    queryEnabled.set(String(query.queryKey[0]), query.enabled);
    return reads.get(JSON.stringify(query.queryKey)) ?? { error: null, isFetching: false };
  }
}));

const defaults = {
  employee_backend: "codex" as const,
  employee_launch_model: "saved-model",
  employee_launch_reasoning_effort: "high"
};
const workers: WorkersResponse = {
  chief_of_staff: {
    employee_id: "chief",
    label: "Chief of Staff",
    recap: "",
    skill: { name: "panels-chief-of-staff", description: "Long Chief skill description", markdown_body: "Chief body" },
    launch_defaults: defaults,
    conversation_id: null,
    needs_me: false,
    awaiting_reply: false,
    agent_working: false,
    latest_turn_ended_sequence: 0,
    owner_read_through_sequence: 0
  },
  workers: [
    { worker_type: "coding", label: "Coding", specialist_skill_name: "panels-worker-coding", launch_defaults: defaults },
    { worker_type: "research", label: "Research", specialist_skill_name: "panels-worker-research", launch_defaults: defaults }
  ]
};
const skills: SkillsHomeResponse = {
  skills: [
    { name: "panels-worker", description: "Long shared skill description", markdown_body: "Worker body" },
    { name: "panels-sprint-item-supervisor", description: "Long supervisor description", markdown_body: "Supervisor body" }
  ]
};

function manifest(workerType: string, stageIds: string[]): WorkerTypeManifest {
  return {
    worker_type: workerType,
    label: workerType,
    stages: stageIds.map((id) => ({ id, label: id, gating_field: null, is_terminal: id === "done", ownership_mode: id === "done" ? null : "worker" })),
    fields: [],
    advance: {},
    ceiling_range: stageIds,
    default_ceiling: stageIds[0],
    worker_profile_id: `panels-worker-${workerType}`,
    default_backend: "codex",
    default_model: null,
    default_reasoning_effort: null
  };
}

function setManifests(data?: WorkerTypesResponse, error: unknown = null, isFetching = false): void {
  reads.set('["worker-types","supported"]', { data, error, isFetching });
}

function index(): string {
  return render(ConfigRoute, { props: { roleKind: "index" } }).body.replace(/<!--[\s\S]*?-->/g, "");
}

beforeEach(() => {
  reads.clear();
  queryEnabled.clear();
  reads.set('["workers"]', { data: workers, error: null, isFetching: false });
  reads.set('["skills-home"]', { data: skills, error: null, isFetching: false });
});

describe("Config destinations", () => {
  it("retains the Usage header and routes Config destinations inside its actual tab", () => {
    setManifests({ worker_types: [manifest("coding", ["brief", "done"])] });
    const markup = render(BackendsRoute, { props: { tab: "config" } }).body;
    expect(markup).toContain("Usage</h1>");
    expect(markup).toMatch(/href="#\/backends\/config"[^>]*aria-current="page"/);
    for (const suffix of ["chief-of-staff", "sprint-item-supervisor", "worker-skill", "workers/coding"]) {
      expect(markup).toContain(`href="#/backends/config/${suffix}"`);
    }
    expect(markup).toContain('data-screen="config"');
    expect(markup).not.toContain("data-settings");
    expect(markup).not.toContain('href="#/settings/config');
    expect(markup).not.toContain("data-backends-refresh");
    expect(queryEnabled.get("recorded-usage")).toBe(false);
  });

  it.each([
    { roleKind: "agent" as const, roleId: "chief_of_staff" },
    { roleKind: "skill" as const, roleId: "panels-worker" },
    { roleKind: "skill" as const, roleId: "panels-sprint-item-supervisor" },
    { roleKind: "worker" as const, roleId: "coding" }
  ])("retains the Usage tab strip and back link on Config $roleId details", (selection) => {
    const markup = render(BackendsRoute, { props: { tab: "config", ...selection } }).body;
    expect(markup).toMatch(/href="#\/backends\/config"[^>]*aria-current="page"/);
    expect(markup).toMatch(/href="#\/backends\/config"[^>]*data-agents-back/);
    expect(markup).not.toContain('href="#/settings/config');
    expect(markup).not.toContain("data-settings");
  });

  it("shows the actual served Stage count, including terminal Stages, and preserves Settings detail links", () => {
    setManifests({ worker_types: [manifest("coding", ["brief", "review", "done"]), manifest("research", ["done"])] });
    const markup = index();
    expect(markup).toMatch(/3\s+Stages/);
    expect(markup).toMatch(/1\s+Stage\b/);
    expect(markup).toContain('href="#/settings/config/workers/coding"');
    expect(markup).toContain('href="#/settings/config/worker-skill"');
    expect(markup).not.toContain("Long shared skill description");
  });

  it("keeps destinations available when manifests fail and does not invent a zero count", () => {
    setManifests(undefined, { code: "network", message: "Stage catalogue could not be read." });
    const markup = index();
    expect(markup).toContain("Stage catalogue could not be read.");
    expect(markup).toContain('href="#/settings/config/workers/coding"');
    expect(markup).toContain("Stages unavailable");
    expect(markup).not.toMatch(/0\s+Stages/);
  });

  it("keeps previously read counts during a failed refresh and distinguishes a pending initial read", () => {
    setManifests({ worker_types: [manifest("coding", ["brief", "done"])] }, new Error("Refresh refused"));
    expect(index()).toMatch(/2\s+Stages/);
    setManifests(undefined, null, true);
    const markup = index();
    expect(markup).toContain("Loading Stages…");
    expect(markup).toContain('href="#/settings/config/workers/research"');
    expect(markup).not.toMatch(/0\s+Stages/);
  });
});
