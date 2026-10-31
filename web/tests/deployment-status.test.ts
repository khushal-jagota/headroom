import { describe, expect, it } from "vitest";
import { deploymentStatusDetail, deploymentStatusExpiry, deploymentStatusExpiryDelay, resolveDeploymentStatus, resolveStatusCard } from "../src/lib/deploymentStatus";
import type { ReleaseSnapshot } from "../src/lib/releaseMonitor";
import type { DeploymentStatus } from "../src/lib/types";

const now = Date.parse("2026-10-21T12:00:00Z");
const status = (changes: Partial<DeploymentStatus> = {}): DeploymentStatus => ({
  state: "idle", deployed_sha: null, target_sha: null, outcome: null,
  detail: null, valid_until: null, success_display_until: null, ...changes
});

describe("deployment status precedence and expiry", () => {
  it("keeps canceled updates neutral and uncertain evidence distinct from health failure", () => {
    const canceled = status({ state: "idle", outcome: "cancelled" });
    expect(resolveDeploymentStatus(canceled, "connected", now)).toBe("connected");
    expect(resolveDeploymentStatus(canceled, "reconnecting", now)).toBe("reconnecting");
    expect(resolveDeploymentStatus(status({ state: "unknown" }), "connected", now)).toBe("unknown");
  });
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
      expect(resolveDeploymentStatus(progress, "connected", now + 1000)).toBe("unknown");
      expect(deploymentStatusExpiryDelay(progress, now + 1000)).toBeNull();
      for (const valid_until of [null, "invalid", new Date(now - 1).toISOString()]) {
        const expired = status({ state, valid_until });
        expect(resolveDeploymentStatus(expired, "connected", now)).toBe("unknown");
        expect(deploymentStatusExpiryDelay(expired, now)).toBeNull();
      }
    }
  });
  it("retires only an installed report notice at a fixed deadline, including stale progress", () => {
    const deadline = now + 300_000;
    for (const state of ["unknown", "restarting"] as const) {
      const installed = status({ state, installation_confirmed: true,
        valid_until: new Date(now).toISOString(),
        unknown_display_until: new Date(deadline).toISOString() });
      expect(resolveDeploymentStatus(installed, "connected", now)).toBe("unknown");
      expect(deploymentStatusDetail(installed, "unknown")).toContain("No action is needed from you.");
      expect(deploymentStatusExpiryDelay(installed, now)).toBe(300_001);
      expect(resolveDeploymentStatus(installed, "connected", deadline - 1)).toBe("unknown");
      expect(resolveDeploymentStatus(installed, "connected", deadline)).toBe("connected");
      // A reloaded body retains the server deadline, rather than starting a new timer.
      expect(resolveDeploymentStatus({ ...installed }, "connected", deadline + 1)).toBe("connected");
      expect(resolveDeploymentStatus(installed, "reconnecting", now)).toBe("reconnecting");
      for (const unknown_display_until of [null, undefined, "invalid"]) {
        expect(resolveDeploymentStatus({ ...installed, unknown_display_until }, "connected", deadline)).toBe("unknown");
      }
      const held = { ...installed, installation_confirmed: false, work_paused: true };
      expect(resolveDeploymentStatus(held, "connected", deadline + 86_400_000)).toBe("unknown");
      expect(deploymentStatusExpiryDelay(held, now)).toBeNull();
      expect(deploymentStatusDetail(held, "unknown")).toContain("Chief review is required.");
      expect(deploymentStatusDetail(held, "unknown")).toContain("Work remains paused.");
      expect(deploymentStatusDetail({ ...held, work_paused: false }, "unknown")).not.toContain("paused");
      for (const outcome of ["failed", "rolled_back"] as const) {
        expect(resolveDeploymentStatus({ ...installed, outcome }, "connected", deadline)).toBe("problem");
      }
    }
  });
  it("requires both success deadlines and wakes at the earlier boundary", () => {
    const validUntil = now + 86_400_000;
    const displayUntil = now + 300_000;
    const recovered = status({ state: "back_up", outcome: "succeeded",
      valid_until: new Date(validUntil).toISOString(),
      success_display_until: new Date(displayUntil).toISOString() });
    expect(deploymentStatusExpiryDelay(recovered, now)).toBe(300_001);
    expect(resolveDeploymentStatus(recovered, "connected", displayUntil - 1)).toBe("back_up");
    expect(resolveDeploymentStatus(recovered, "connected", displayUntil)).toBe("connected");
    expect(deploymentStatusExpiryDelay(recovered, displayUntil)).toBeNull();
    const shorterRecord = { ...recovered, valid_until: new Date(now + 1000).toISOString() };
    expect(deploymentStatusExpiry(shorterRecord, now)).toBe(now + 1000);
    expect(resolveDeploymentStatus(shorterRecord, "connected", now + 1000)).toBe("connected");
    // Older API bodies can omit the new field. Neither absence nor malformed
    // evidence must turn record retention back into card visibility.
    for (const field of ["valid_until", "success_display_until"] as const) {
      for (const value of [null, undefined, "invalid", new Date(now).toISOString()]) {
        const unusable = { ...recovered, [field]: value } as DeploymentStatus;
        expect(resolveDeploymentStatus(unusable, "connected", now)).toBe("connected");
        expect(deploymentStatusExpiry(unusable, now)).toBeNull();
      }
    }
    // Disconnection hides success. Reconnection uses the original deadline.
    expect(resolveDeploymentStatus(recovered, "reconnecting", now)).toBe("reconnecting");
    expect(deploymentStatusExpiryDelay(recovered, now, "reconnecting")).toBeNull();
    expect(resolveDeploymentStatus(recovered, "connected", now + 1000)).toBe("back_up");
    expect(deploymentStatusExpiry(recovered, now + 1000)).toBe(displayUntil);
    expect(resolveDeploymentStatus(recovered, "reconnecting", displayUntil)).toBe("reconnecting");
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
    expect(resolveStatusCard("unknown", release({ updateAvailable: true }))).toBe("Deployment outcome unknown");
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
