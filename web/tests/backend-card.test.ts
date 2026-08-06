import { render } from "svelte/server";
import { describe, expect, it } from "vitest";

import BackendCard from "../src/components/conversation/BackendCard.svelte";
import type {
  BackendSnapshot,
  BackendUsageResult
} from "../src/lib/conversation/wire";

function snapshot(backendKey: "codex" | "claude"): BackendSnapshot {
  return {
    backend_key: backendKey,
    installed: true,
    executable_path: `/usr/local/bin/${backendKey}`,
    version: "1.0.0",
    identity: null,
    available_models: [],
    reasoning_effort_options: [],
    update_advisory: {
      install_method: "npm_global",
      update_command: `update ${backendKey}`,
      latest_version: "1.1.0",
      update_available: true,
      detail: `${backendKey} maintenance is available.`
    },
    diagnoses: [`${backendKey} diagnosis remains visible.`]
  };
}

function usage(backendKey: "codex" | "claude", name: string): BackendUsageResult {
  return {
    backend_key: backendKey,
    outcome: "succeeded",
    detail: null,
    observed_at: null,
    windows: [
      {
        name,
        used_percent: backendKey === "codex" ? 12.5 : 72,
        resets_at: "2026-08-05T18:00:00Z"
      }
    ]
  };
}

function draw(
  backendKey: "codex" | "claude",
  values: { usageResult?: BackendUsageResult; usageError?: string } = {}
): string {
  return render(BackendCard, {
    props: {
      snapshot: snapshot(backendKey),
      onUpdate() {},
      onUsageRefresh() {},
      usageResult: values.usageResult ?? null,
      usageError: values.usageError ?? null
    }
  }).body;
}

describe("BackendCard", () => {
  it("keeps maintenance actions and diagnoses visible after a usage failure", () => {
    const body = draw("codex", { usageError: "Provider usage failed." });

    expect(body).toContain("Provider usage failed.");
    expect(body).toContain("codex maintenance is available.");
    expect(body).toContain('data-conversation-backend-update="codex"');
    expect(body).toContain("codex diagnosis remains visible.");
  });

  it("renders each provider's usage result only on that provider's card", () => {
    const codex = draw("codex", { usageResult: usage("codex", "Codex five-hour limit") });
    const claude = draw("claude", { usageResult: usage("claude", "Claude weekly limit") });

    expect(codex).toContain("Codex five-hour limit");
    expect(codex).not.toContain("Claude weekly limit");
    expect(claude).toContain("Claude weekly limit");
    expect(claude).not.toContain("Codex five-hour limit");
  });
});
