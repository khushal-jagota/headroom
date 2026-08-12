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

function window(kind: BackendUsageWindow["kind"], usedPercent: number): BackendUsageWindow {
  return {
    kind,
    used_percent: usedPercent,
    resets_at: "2026-08-06T18:00:00Z",
    model_id: null
  };
}

function dash(remainingPercent: number, radius = 14): string {
  const total = 2 * Math.PI * radius;
  const remaining = total * remainingPercent / 100;
  return `${remaining} ${total - remaining}`;
}

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
    expect(body).toContain("week: 10% remaining, warning, nearly spent, resets");
    expect(body).toContain('data-usage-percent="90"');
    expect(body).toContain('data-remaining-percent="10"');
    expect(body).toMatch(/class="usage-ring [^"]*spent"/);
    expect(body).toMatch(/class="usage-ring-label [^"]*" aria-hidden="true">5 hr<\/span>/);
    expect(body).toMatch(/class="usage-ring-label [^"]*" aria-hidden="true">week<\/span>/);
  });

  it("uses a readable label for model-scoped rings", () => {
    const body = render(UsageRings, {
      props: { windows, modelId: "fable", label: "Usage allowances for Fable 5", compact: true }
    }).body;

    expect(body).toContain('aria-label="Usage allowances for Fable 5"');
    expect(body).not.toContain("Usage for fable");
    expect(body).toContain("week: 24% remaining");
    expect(body).toContain('data-remaining-percent="24"');
    expect(body).not.toContain('aria-hidden="true">24</span>');
    expect(body).not.toContain("week: 10% remaining");
  });

  it("renders fresh, partial, and spent allowances as remaining values", () => {
    const fresh = render(UsageRings, {
      props: { windows: [window("five_hour", 0)] }
    }).body;
    const partial = render(UsageRings, {
      props: { windows: [window("five_hour", 25)] }
    }).body;
    const spent = render(UsageRings, {
      props: { windows: [window("five_hour", 100)] }
    }).body;

    expect(fresh).toContain('data-remaining-percent="100"');
    expect(fresh).toContain('aria-label="5 hr: 100% remaining');
    expect(fresh).toContain('aria-hidden="true">100</span>');
    expect(partial).toContain('data-remaining-percent="75"');
    expect(partial).toContain('aria-label="5 hr: 75% remaining');
    expect(partial).toContain('aria-hidden="true">75</span>');
    expect(spent).toContain('data-remaining-percent="0"');
    expect(spent).toContain('aria-label="5 hr: 0% remaining, warning, nearly spent');
    expect(spent).toContain('aria-hidden="true">0</span>');
  });

  it("grows the empty segment counter-clockwise from the top", () => {
    const fresh = render(UsageRings, {
      props: { windows: [window("five_hour", 0)] }
    }).body;
    const partial = render(UsageRings, {
      props: { windows: [window("five_hour", 25)] }
    }).body;
    const spent = render(UsageRings, {
      props: { windows: [window("five_hour", 100)] }
    }).body;

    expect(fresh).toContain(`stroke-dasharray="${dash(100)}"`);
    expect(partial).toContain(`stroke-dasharray="${dash(75)}"`);
    expect(spent).toContain(`stroke-dasharray="${dash(0)}"`);
    expect(partial).toContain('transform="rotate(-90 17 17)"');
  });
});
