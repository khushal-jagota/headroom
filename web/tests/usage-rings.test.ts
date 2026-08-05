import { render } from "svelte/server";
import { describe, expect, it } from "vitest";

import UsageRings from "../src/components/conversation/UsageRings.svelte";
import {
  USAGE_NEARLY_SPENT_PERCENT,
  usageIsNearlySpent,
  usageWindow
} from "../src/lib/conversation/usagePresentation";
import type { BackendUsageWindow } from "../src/lib/conversation/wire";

const windows: BackendUsageWindow[] = [
  {
    kind: "seven_day",
    used_percent: 90,
    resets_at: "2026-08-06T18:00:00Z",
    model_id: null
  },
  {
    kind: "seven_day",
    used_percent: 76,
    resets_at: "2026-08-06T18:00:00Z",
    model_id: "fable"
  }
];

describe("usage ring presentation", () => {
  it("keeps the warning threshold isolated and inclusive", () => {
    expect(USAGE_NEARLY_SPENT_PERCENT).toBe(90);
    expect(usageIsNearlySpent(89.9)).toBe(false);
    expect(usageIsNearlySpent(90)).toBe(true);
  });

  it("matches provider and model windows without mixing their allowances", () => {
    expect(usageWindow(windows, "seven_day")?.used_percent).toBe(90);
    expect(usageWindow(windows, "seven_day", "fable")?.used_percent).toBe(76);
    expect(usageWindow(windows, "five_hour", "fable")).toBeNull();
  });

  it("renders honest absences and spoken reset and warning context", () => {
    const body = render(UsageRings, {
      props: { windows, label: "Codex usage allowances" }
    }).body;

    expect(body).toContain('aria-label="Codex usage allowances"');
    expect(body).toContain('data-usage-window="five_hour"');
    expect(body).toContain("5 hr: nothing reported");
    expect(body).toContain("week: 90% used, warning, nearly spent, resets");
    expect(body).toMatch(/class="usage-ring [^"]*spent"/);
  });

  it("uses a readable label for model-scoped rings", () => {
    const body = render(UsageRings, {
      props: { windows, modelId: "fable", label: "Usage allowances for Fable 5", compact: true }
    }).body;

    expect(body).toContain('aria-label="Usage allowances for Fable 5"');
    expect(body).not.toContain("Usage for fable");
    expect(body).toContain("week: 76% used");
  });
});
