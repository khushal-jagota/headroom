import { describe, expect, it } from "vitest";
import { deploymentStatusExpiry, deploymentStatusExpiryDelay, resolveDeploymentStatus } from "../src/lib/deploymentStatus";
import type { DeploymentStatus } from "../src/lib/types";

const now = Date.parse("2026-10-21T12:00:00Z");
const status = (changes: Partial<DeploymentStatus> = {}): DeploymentStatus => ({
  state: "idle", deployed_sha: null, target_sha: null, outcome: null,
  detail: null, valid_until: null, ...changes
});

describe("deployment status precedence and expiry", () => {
  it("keeps failed deployments visible over fresh progress, recovery, and disconnection", () => {
    for (const outcome of ["failed", "rolled_back"] as const) {
      for (const state of ["preparing", "restarting", "back_up"] as const) {
        const failed = status({ state, outcome, valid_until: new Date(now + 1000).toISOString() });
        expect(resolveDeploymentStatus(failed, "connected", now)).toBe("problem");
        expect(resolveDeploymentStatus(failed, "reconnecting", now)).toBe("problem");
        expect(deploymentStatusExpiryDelay(failed, now)).toBeNull();
      }
    }
    expect(resolveDeploymentStatus(status({ state: "problem" }), "reconnecting", now)).toBe("problem");
  });
  it("expires progress at its boundary and schedules one wake just after it", () => {
    for (const state of ["preparing", "restarting"] as const) {
      const progress = status({ state, valid_until: new Date(now + 1000).toISOString() });
      expect(resolveDeploymentStatus(progress, "connected", now)).toBe(state);
      expect(deploymentStatusExpiry(progress, now)).toBe(now + 1000);
      expect(deploymentStatusExpiryDelay(progress, now)).toBe(1001);
      expect(resolveDeploymentStatus(progress, "connected", now + 1000)).toBe("reconnecting");
      expect(deploymentStatusExpiryDelay(progress, now + 1000)).toBeNull();
      for (const valid_until of [null, "invalid", new Date(now - 1).toISOString()]) {
        const expired = status({ state, valid_until });
        expect(resolveDeploymentStatus(expired, "connected", now)).toBe("reconnecting");
        expect(deploymentStatusExpiryDelay(expired, now)).toBeNull();
      }
    }
  });
  it("requires a live connection for recovery and returns to connection status after expiry", () => {
    const recovered = status({ state: "back_up", outcome: "succeeded", valid_until: new Date(now + 1000).toISOString() });
    expect(resolveDeploymentStatus(recovered, "connected", now)).toBe("back_up");
    expect(resolveDeploymentStatus(recovered, "reconnecting", now)).toBe("reconnecting");
    expect(resolveDeploymentStatus(recovered, "connected", now + 1000)).toBe("connected");
    expect(resolveDeploymentStatus(status(), "connected", now)).toBe("connected");
    expect(resolveDeploymentStatus(undefined, "connected", now)).toBe("reconnecting");
    expect(deploymentStatusExpiryDelay(status(), now)).toBeNull();
  });
});
