import { describe, expect, it, vi } from "vitest";
import { render } from "svelte/server";
import UpdateActions from "../src/components/UpdateActions.svelte";
import { cancelUpdate, requestUpdate } from "../src/lib/updates/api";
import { activeUpdateRequest, canCancelUpdate, needsActivityConfirmation, usableUpdate } from "../src/lib/updates/logic/actions";
import type { OwnerUpdateRequest, PublishedUpdate, UpdateStatus } from "../src/lib/updates/contracts";

const release: PublishedUpdate = {
  publication_run_id: 42, artifact_id: 101, proof_artifact_id: 102,
  manifest: { app_sha: "a".repeat(40), source_digest: "source", artifact_digest: "artifact" },
  archive_sha256: "archive", schema_floor: "17", expires_at: "2099-01-01T00:00:00Z"
};
const request: OwnerUpdateRequest = { request_id: "pinned", release, mode: "idle", state: "pending", created_at: "2026-10-28T00:00:00Z", detail: null };
const status: UpdateStatus = { configured: true, available: release, request: null, activity: "unknown", detail: null };

// These gaps concern exact publication identity, cancellation state, and offering
// adoption vs page refresh. Adapter and SSR checks need no connected browser host.
describe("owner-controlled updates", () => {
  it("reports cancellation neutrally and retains the next update controls", () => {
    const html = render(UpdateActions, { props: { status: { ...status,
      request: { ...request, state: "cancelled" } }, now: Date.now() } }).body;
    expect(html).toContain("Update canceled before installation. The previous version stayed active.");
    expect(html).not.toContain('role="alert"');
    expect(html).not.toContain("Update failed");
    expect(html).toContain("Update now");
  });
  it("posts the exact selected publication identity and explicit activity confirmation", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify(request)));
    vi.stubGlobal("fetch", fetch);
    await requestUpdate(release, "now", true);
    const [path, init] = fetch.mock.calls[0]!;
    expect(path).toBe("/api/updates/requests");
    expect(JSON.parse(String(init?.body))).toEqual({ publication_run_id: 42, artifact_id: 101,
      proof_artifact_id: 102, app_sha: "a".repeat(40), artifact_digest: "artifact", archive_sha256: "archive",
      mode: "now", confirm_activity: true });
    expect(needsActivityConfirmation("unknown")).toBe(true);
    expect(needsActivityConfirmation("busy")).toBe(true);
    expect(needsActivityConfirmation("idle")).toBe(false);
  });

  it("shows only pending request actions and cancels the exact request ID", async () => {
    const html = render(UpdateActions, { props: { status: { ...status, request,
      available: { ...release, manifest: { ...release.manifest, app_sha: "b".repeat(40) } } }, now: Date.now() } }).body;
    // Publication pinning is proved by durable receipt equality in the connected
    // browser case. This render proves that controls do not offer the newer release.
    expect(html).toContain("Update waits for idle");
    expect(html).not.toContain("aaaaaaa");
    expect(html).toContain("Cancel update");
    expect(html).not.toContain("Update now");
    expect(html).not.toContain("bbbbbbb");
    const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify({ ...request, state: "cancelled" })));
    vi.stubGlobal("fetch", fetch);
    await cancelUpdate(request.request_id);
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/updates/requests/pinned/cancel");
    for (const mode of ["idle", "now"] as const) {
      for (const state of ["pending", "dispatched"] as const) {
        const queued = { ...request, mode, state };
        expect(canCancelUpdate(queued)).toBe(true);
        expect(render(UpdateActions, { props: { status: { ...status, request: queued }, now: Date.now() } }).body).toContain("Cancel update");
      }
    }
    expect(canCancelUpdate({ ...request, state: "applying" })).toBe(false);
    expect(activeUpdateRequest({ ...request, state: "applying" })).toBe(true);
    expect(render(UpdateActions, { props: { status: { ...status, request: { ...request, state: "applying" } }, now: Date.now() } }).body).not.toContain("Cancel update");
  });

  it("offers adoption only for a configured unexpired release and never calls it page refresh", () => {
    const html = render(UpdateActions, { props: { status, now: Date.now() } }).body;
    expect(html).toContain("Update now");
    expect(html).toContain("Update when idle");
    expect(html).not.toContain("Refresh page");
    for (const unavailable of [{ ...status, configured: false }, { ...status, available: null },
      { ...status, available: { ...release, expires_at: "2000-01-01T00:00:00Z" } }]) {
      expect(render(UpdateActions, { props: { status: unavailable, now: Date.now() } }).body).not.toContain("Update now");
    }
    expect(usableUpdate({ ...release, expires_at: "invalid" }, Date.now())).toBe(false);
  });
});
