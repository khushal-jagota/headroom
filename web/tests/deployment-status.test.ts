import { describe, expect, it, vi } from "vitest";
import {
  deploymentStatusExpiryDelay,
  deploymentStatusExpiry,
  resolveDeploymentStatus
} from "../src/lib/deploymentStatus";
import type { DeploymentStatus } from "../src/lib/types";

const NOW = Date.parse("2026-07-29T12:00:00Z");
const FUTURE = "2026-07-29T12:00:10Z";
const PAST = "2026-07-29T11:59:59Z";

function status(overrides: Partial<DeploymentStatus> = {}): DeploymentStatus {
  return {
    state: "idle",
    deployed_sha: "0123456789abcdef",
    target_sha: null,
    outcome: null,
    detail: null,
    valid_until: null,
    ...overrides
  };
}

describe("deployment status resolver", () => {
  it.each([
    ["idle on a live stream", status(), "connected", "connected"],
    ["idle after transport loss", status(), "reconnecting", "reconnecting"],
    ["fresh preparation", status({ state: "preparing", valid_until: FUTURE }), "connected", "preparing"],
    ["fresh restart", status({ state: "restarting", valid_until: FUTURE }), "reconnecting", "restarting"],
    ["stale preparation", status({ state: "preparing", valid_until: PAST }), "connected", "reconnecting"],
    ["stale restart", status({ state: "restarting", valid_until: PAST }), "connected", "reconnecting"],
    ["fresh return", status({ state: "back_up", valid_until: FUTURE }), "connected", "back_up"],
    ["return with a dropped stream", status({ state: "back_up", valid_until: FUTURE }), "reconnecting", "reconnecting"],
    ["expired return", status({ state: "back_up", valid_until: PAST }), "connected", "connected"],
    ["server uncertainty", status({ state: "unknown" }), "connected", "reconnecting"],
    ["unexplained missing response", null, "connected", "reconnecting"],
    ["explicit problem", status({ state: "problem" }), "connected", "problem"],
    ["failed outcome after drop", status({ outcome: "failed" }), "reconnecting", "problem"],
    ["rollback after drop", status({ outcome: "rolled_back" }), "reconnecting", "problem"]
  ] as const)("%s resolves to %s", (_name, value, connection, expected) => {
    expect(resolveDeploymentStatus(value, connection, NOW)).toBe(expected);
  });

  it("returns a future one-shot reevaluation deadline only for expiring visible phases", () => {
    expect(deploymentStatusExpiry(status({ state: "preparing", valid_until: FUTURE }), NOW)).toBe(
      Date.parse(FUTURE)
    );
    expect(deploymentStatusExpiry(status({ state: "back_up", valid_until: PAST }), NOW)).toBeNull();
    expect(deploymentStatusExpiry(status({ state: "idle", valid_until: FUTURE }), NOW)).toBeNull();
    expect(
      deploymentStatusExpiry(
        status({ state: "restarting", outcome: "failed", valid_until: FUTURE }),
        NOW
      )
    ).toBeNull();
  });

  it("schedules a newly arrived phase from the actual later clock, not the render clock", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(NOW);
      const renderClock = Date.now();
      vi.advanceTimersByTime(10 * 60 * 1000);
      const responseClock = Date.now();
      const validUntil = new Date(responseClock + 15 * 60 * 1000).toISOString();
      const preparing = status({ state: "preparing", valid_until: validUntil });

      expect(responseClock - renderClock).toBe(10 * 60 * 1000);
      expect(deploymentStatusExpiryDelay(preparing)).toBe(15 * 60 * 1000 + 1);
      expect(resolveDeploymentStatus(preparing, "connected", responseClock)).toBe("preparing");
    } finally {
      vi.useRealTimers();
    }
  });
});
