import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "svelte/server";
import UpdateActions from "../src/components/UpdateActions.svelte";
import { cancelUpdate, promoteUpdate, requestUpdate } from "../src/lib/updates/api";
import { usableUpdate } from "../src/lib/updates/logic/actions";
import { atUpdateDeadline } from "../src/lib/updates/deadline";
import { updatePresentation } from "../src/lib/updates/logic/presentation";
import type { OwnerUpdateRequest, PublishedUpdate, UpdateStatus } from "../src/lib/updates/contracts";

const release: PublishedUpdate = {
  publication_run_id: 42, artifact_id: 101, proof_artifact_id: 102,
  manifest: { app_sha: "a".repeat(40), source_digest: "source", artifact_digest: "artifact" },
  archive_sha256: "archive", schema_floor: "17", expires_at: "2099-01-01T00:00:00Z"
};
const request: OwnerUpdateRequest = { request_id: "pinned", release, mode: "idle", state: "pending", created_at: "2026-10-28T00:00:00Z", detail: null };
const status: UpdateStatus = { configured: true, available: release, request: null, activity: "unknown", detail: null,
  state: "available", allowed_actions: ["update"], phase: null, app_sha: "boot", deadline_at: null, restart_deadline_at: null };
const local = { updateAvailable: false, reloadRequested: false, compositionActive: false };

// These tests protect publication identity, promotion identity, and the local safety
// overlays. The connected browser proof covers native dialog and retained card DOM.
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("owner-controlled updates", () => {
  it("posts exact publication identity and promotes or cancels the pinned request", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async path => new Response(JSON.stringify(path === "/api/updates" ? { ...status, request, allowed_actions: ["update", "cancel", "update_now"] } : request)));
    vi.stubGlobal("fetch", fetch);
    await requestUpdate(release, "now", true);
    expect(fetch.mock.calls[1]?.[0]).toBe("/api/updates/requests");
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({ publication_run_id: 42, artifact_id: 101,
      proof_artifact_id: 102, app_sha: "a".repeat(40), artifact_digest: "artifact", archive_sha256: "archive",
      mode: "now", confirm_activity: true });
    await promoteUpdate(request.request_id);
    expect(fetch.mock.calls[3]?.[0]).toBe("/api/updates/requests/pinned/now");
    expect(JSON.parse(String(fetch.mock.calls[3]?.[1]?.body))).toEqual({ confirm_activity: true });
    await cancelUpdate(request.request_id);
    expect(fetch.mock.calls[5]?.[0]).toBe("/api/updates/requests/pinned/cancel");
  });

  it("puts busy confirmation in the offer and removes old inline controls and cancel notice", () => {
    const body = render(UpdateActions, { props: { status: { ...status, activity: "busy",
      request: { ...request, state: "cancelled" } }, now: Date.now() } }).body;
    expect(body).toContain("Work is running. Updating now interrupts it.");
    expect(body).toContain("When work finishes");
    expect(body).toContain("Update now");
    expect(body).not.toContain("Proceed with update");
    expect(body).not.toContain("Start update");
    expect(body).not.toContain("Update canceled");
    expect(usableUpdate({ ...release, expires_at: "invalid" }, Date.now())).toBe(false);
    expect(render(UpdateActions, { props: { status: { ...status, available: null }, now: Date.now() } }).body).not.toContain("Update now");
  });

  it("expires a retained release on time without browser timer overflow", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    vi.stubGlobal("window", globalThis);
    const expired = vi.fn();
    const cancel = atUpdateDeadline(30 * 86_400_000, expired);
    await vi.advanceTimersByTimeAsync(30 * 86_400_000 - 1);
    expect(expired).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(2);
    expect(expired).toHaveBeenCalledOnce();
    cancel();
  });

  it("only overlays server states for connection and local refresh safety", () => {
    for (const state of ["up_to_date", "available", "waiting", "updating", "restarting", "failed"] as const) {
      expect(updatePresentation({ ...status, state }, "connected", local, 0)).toBe(state);
    }
    // A server success alone does not mean this page's version changed.
    expect(updatePresentation({ ...status, state: "complete" }, "connected", local, 0)).toBe("up_to_date");
    expect(updatePresentation(status, "connected", { ...local, updateAvailable: true }, 0)).toBe("complete");
    expect(updatePresentation(status, "connected", { updateAvailable: true, compositionActive: true, reloadRequested: true }, 0)).toBe("held");
    const active = { ...status, state: "updating" as const, restart_deadline_at: new Date(5000).toISOString() };
    expect(updatePresentation(active, "reconnecting", local, 4999)).toBe("restarting");
    expect(updatePresentation(active, "reconnecting", local, 5000)).toBe("unreachable");
    expect(updatePresentation(status, "reconnecting", local, 0)).toBe("unreachable");
    // An uncommitted candidate SHA must not hide a server failure with Refresh.
    expect(updatePresentation({ ...status, state: "failed" }, "connected", { ...local, updateAvailable: true }, 0)).toBe("failed");
  });
});
