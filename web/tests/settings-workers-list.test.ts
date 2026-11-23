import { render } from "svelte/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WorkerTypeManifest, WorkerTypesResponse } from "../src/lib/lifecycle";
import type { WorkersResponse } from "../src/lib/types";
import WorkerSettings from "../src/components/settings/WorkerSettings.svelte";

// The Workers list reads its stage counts from a second read that can be pending or fail
// while the Worker types are known. It must never show a count it did not read.
const reads = vi.hoisted(() => new Map<string, { data?: unknown; error: unknown; isFetching: boolean }>());

vi.mock("@tanstack/svelte-query", async (importOriginal) => ({
  ...await importOriginal<typeof import("@tanstack/svelte-query")>(),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  createQuery: (options: () => { queryKey: readonly unknown[] }) =>
    reads.get(JSON.stringify(options().queryKey)) ?? { error: null, isFetching: false }
}));

const defaults = { employee_backend: "codex", employee_launch_model: "gpt-6", employee_launch_reasoning_effort: "high" };
const workers = {
  chief_of_staff: {} as WorkersResponse["chief_of_staff"],
  workers: [
    { worker_type: "research", label: "Research", specialist_skill_name: "panels-worker-research", launch_defaults: defaults },
    { worker_type: "coding", label: "Coding", specialist_skill_name: "panels-worker-coding", launch_defaults: defaults }
  ]
} satisfies WorkersResponse;

function manifest(workerType: string, stageIds: string[]): WorkerTypeManifest {
  return {
    worker_type: workerType, label: workerType,
    stages: stageIds.map((id) => ({ id, label: id, gating_field: null, is_terminal: id === "done", ownership_mode: id === "done" ? null : "worker" })),
    fields: [], advance: {}, ceiling_range: stageIds, default_ceiling: stageIds[0],
    worker_profile_id: `panels-worker-${workerType}`, default_backend: "codex", default_model: null, default_reasoning_effort: null
  };
}

function setManifests(data?: WorkerTypesResponse, error: unknown = null, isFetching = false): void {
  reads.set('["worker-types","supported"]', { data, error, isFetching });
}

function list(): string {
  return render(WorkerSettings, { props: { detail: null } }).body.replace(/<!--[\s\S]*?-->/g, "");
}

beforeEach(() => {
  reads.clear();
  reads.set('["workers"]', { data: workers, error: null, isFetching: false });
});

describe("Settings Workers list", () => {
  it("sorts by label, counts terminal stages, links each type and shows its model", () => {
    setManifests({ worker_types: [manifest("coding", ["brief", "review", "done"]), manifest("research", ["done"])] });
    const markup = list();
    expect(markup.indexOf("Coding")).toBeLessThan(markup.indexOf("Research"));
    expect(markup).toMatch(/3\s+stages/);
    expect(markup).toMatch(/1\s+stage\b/);
    expect(markup).toContain('href="#/settings/workers/coding"');
    expect(markup).toContain('data-settings-row="workers:research"');
    expect(markup).toContain("gpt-6 · high");
  });

  it("keeps the rows when the stage read fails, and does not invent a zero count", () => {
    setManifests(undefined, { code: "network", message: "Stage catalogue could not be read." });
    const markup = list();
    expect(markup).toContain("Stage catalogue could not be read.");
    expect(markup).toContain('href="#/settings/workers/coding"');
    expect(markup).toContain("Stages unavailable");
    expect(markup).not.toMatch(/0\s+stages/);
  });

  it("keeps counts already read during a failed refresh, and tells a first read from a failure", () => {
    setManifests({ worker_types: [manifest("coding", ["brief", "done"])] }, new Error("Refresh refused"));
    expect(list()).toMatch(/2\s+stages/);
    setManifests(undefined, null, true);
    const markup = list();
    expect(markup).toContain('aria-busy="true"');
    expect(markup).not.toContain("Stages unavailable");
    expect(markup).not.toMatch(/0\s+stages/);
  });
});
