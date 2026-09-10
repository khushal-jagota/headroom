import { describe, expect, it } from "vitest";

import { usageIsNearlySpent, usageWindow } from "../src/lib/conversation/usagePresentation";
import type { BackendUsageWindow } from "../src/lib/conversation/wire";

const windows: BackendUsageWindow[] = [
  {
    kind: "seven_day",
    used_percent: 30,
    resets_at: "2026-09-17T12:00:00Z",
    model_id: null
  },
  {
    kind: "seven_day",
    used_percent: 91,
    resets_at: "2026-09-17T12:00:00Z",
    model_id: "spark[1m]"
  }
];

describe("backend usage presentation", () => {
  it("keeps account and model windows separate", () => {
    expect(usageWindow(windows, "seven_day")?.used_percent).toBe(30);
    expect(usageWindow(windows, "seven_day", "spark[1m]")?.used_percent).toBe(91);
    expect(usageWindow(windows, "seven_day", "gpt-reserve")).toBeNull();
  });

  it("uses provider percentages without changing their scale", () => {
    expect(usageIsNearlySpent(89)).toBe(false);
    expect(usageIsNearlySpent(90)).toBe(true);
  });
});
