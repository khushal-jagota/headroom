import { describe, expect, it } from "vitest";
import { deploymentStatusExpiry, deploymentStatusExpiryDelay, resolveDeploymentStatus, resolveStatusCard } from "../src/lib/deploymentStatus";
import type { ReleaseSnapshot } from "../src/lib/releaseMonitor";
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
  it("keeps a full day of success within the browser timer limit and expires at its boundary", () => {
    const expiry = now + 24 * 60 * 60 * 1000;
    const recovered = status({ state: "back_up", outcome: "succeeded", valid_until: new Date(expiry).toISOString() });
    const delay = deploymentStatusExpiryDelay(recovered, now);
    expect(delay).toBe(86_400_001);
    expect(delay).toBeLessThan(2 ** 31 - 1);
    expect(resolveDeploymentStatus(recovered, "connected", expiry - 1)).toBe("back_up");
    expect(deploymentStatusExpiryDelay(recovered, expiry - 1)).toBe(2);
    expect(resolveDeploymentStatus(recovered, "connected", expiry)).toBe("connected");
    expect(deploymentStatusExpiryDelay(recovered, expiry)).toBeNull();
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

const release = (changes: Partial<ReleaseSnapshot> = {}): ReleaseSnapshot => ({
  updateAvailable: false, compositionActive: false, reloadRequested: false, ...changes
});

describe("the one status card", () => {
  it("lets a failed deploy displace a ready update, which outranks the rest", () => {
    // A rolled back release still differs from the booted one, so the update prompt
    // would otherwise invite a reload into the deploy that just failed.
    expect(resolveStatusCard("problem", release({ updateAvailable: true }))).toBe("Problem");
    for (const state of ["preparing", "restarting", "back_up", "reconnecting", "connected"] as const) {
      expect(resolveStatusCard(state, release({ updateAvailable: true }))).toBe("update-ready");
    }
  });
  it("holds the update while a message is composed, and only once a reload was asked for", () => {
    const composing = release({ updateAvailable: true, compositionActive: true });
    expect(resolveStatusCard("connected", composing)).toBe("update-ready");
    expect(resolveStatusCard("connected", { ...composing, reloadRequested: true })).toBe("update-held");
    expect(resolveStatusCard("connected", release({ updateAvailable: true, reloadRequested: true }))).toBe("update-ready");
  });
  it("narrates the deploy when no update is waiting", () => {
    expect(resolveStatusCard("preparing", release())).toBe("Deploying");
    expect(resolveStatusCard("restarting", release())).toBe("Deploying");
    expect(resolveStatusCard("back_up", release())).toBe("Deployed");
    expect(resolveStatusCard("reconnecting", release())).toBe("Reconnecting");
    expect(resolveStatusCard("connected", release())).toBeNull();
  });
});
