import { describe, expect, it } from "vitest";

import {
  backendRefreshControl,
  refreshBackendSnapshots
} from "../src/lib/conversation/backendRefresh";
import type { BackendSnapshot, BackendsRefreshResult } from "../src/lib/conversation/wire";

const snapshot = {
  backend_key: "codex",
  installed: true,
  executable_path: "/bin/codex",
  version: "1.0.0",
  identity: null,
  available_models: [],
  reasoning_effort_options: [],
  cached_usage: null,
  update_advisory: null,
  diagnoses: []
} satisfies BackendSnapshot;

describe("picker backend refresh", () => {
  it("returns refreshed snapshots and joins provider outcome failures", async () => {
    const answer: BackendsRefreshResult = {
      backends: [snapshot],
      usage_outcomes: [
        { backend_key: "codex", outcome: "succeeded", detail: null },
        { backend_key: "claude", outcome: "failed", detail: "Claude usage failed." },
        { backend_key: "hermes", outcome: "unavailable", detail: "No usage source." }
      ]
    };

    await expect(refreshBackendSnapshots(async () => answer)).resolves.toEqual({
      snapshots: [snapshot],
      error: "Claude usage failed."
    });
  });

  it("keeps transport failure separate from existing snapshots", async () => {
    await expect(refreshBackendSnapshots(async () => {
      throw new Error("network stopped");
    })).resolves.toEqual({ snapshots: null, error: "network stopped" });
  });

  it("names the idle and loading states in the picker's interaction language", () => {
    expect(backendRefreshControl(false)).toEqual({ label: "Refresh", busy: false });
    expect(backendRefreshControl(true)).toEqual({ label: "Reading…", busy: true });
  });
});
