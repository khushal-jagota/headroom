import { describe, expect, it } from "vitest";
import { buildLifecycle, preferredScopeCeilingFor, type WorkerTypeManifest } from "../src/lib/lifecycle";

function manifest(stages: string[], advance: Record<string, string>): WorkerTypeManifest {
  return {
    worker_type: "fixture", label: "Fixture",
    stages: stages.map((id, index) => ({ id, label: id, gating_field: index === stages.length - 1 ? null : id, is_terminal: index === stages.length - 1, ownership_mode: index === stages.length - 1 ? null : "worker" })),
    advance, ceiling_range: stages, default_ceiling: stages[0]!, worker_profile_id: "fixture",
    default_backend: "codex", default_model: null, default_reasoning_effort: null,
    fields: stages.slice(0, -1).map((id) => ({ id, label: id }))
  };
}

describe("preferredScopeCeilingFor", () => {
  it("keeps the immediate advanced stage for Coding and another Worker shape", () => {
    const coding = buildLifecycle(manifest(["needs_brief", "needs_success", "needs_plan", "done"], {
      needs_brief: "needs_success", needs_success: "needs_plan", needs_plan: "done"
    }));
    const research = buildLifecycle(manifest(["needs_brief", "needs_findings", "needs_writeup", "done"], {
      needs_brief: "needs_findings", needs_findings: "needs_writeup", needs_writeup: "done"
    }));
    expect(preferredScopeCeilingFor(coding, "needs_success")).toBe("needs_success");
    expect(preferredScopeCeilingFor(research, "needs_findings")).toBe("needs_findings");
  });
});
