import { afterEach, describe, expect, it, vi } from "vitest";
import { get } from "svelte/store";
import { render } from "svelte/server";
import UpdateActions from "../src/components/UpdateActions.svelte";
import { performUpdateAction, settleUpdateStarting, updateStarting } from "../src/lib/updates/api";
import { atUpdateDeadline } from "../src/lib/updates/deadline";
import { updateCard, updateDetails, updatePresentation, type UpdatePresentationInput } from "../src/lib/updates/logic/presentation";
import type { UpdateStatus } from "../src/lib/updates/contracts";
import type { MacStatus } from "../src/lib/ticketMachines";

const attempt = { attempt_id: "a1", from_sha: "1".repeat(40), to_sha: "2".repeat(40), mode: "now" as const,
  state: "failed" as const, detail: null, deadline_at: null };
const status: UpdateStatus = { state: "available", allowed_actions: ["update"], activity: "unknown",
  available: { app_sha: "2".repeat(40) }, attempt: null, app_sha: "1".repeat(40), detail: null, deadline_at: null, mac_details: null };
const base: UpdatePresentationInput = { status, connection: "connected", pageIsBehind: false, starting: false, now: 1000 };
const at = (state: UpdateStatus["state"], extra: Partial<UpdateStatus> = {}) => ({ ...status, state, ...extra });

// The resolver is the one place that turns a server answer, the connection and this
// page's version into the card. Each state needs its own proof because the card has
// no other source, and a wrong answer either hides an action or claims a finished update.
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); updateStarting.set(null); });

describe("the update card resolver", () => {
  it("shows each server state on a connected page", () => {
    expect(updatePresentation({ ...base, status: at("up_to_date") })).toEqual({ state: null, dimmed: false });
    expect(updatePresentation({ ...base, status: undefined })).toEqual({ state: null, dimmed: false });
    for (const state of ["available", "waiting", "updating", "failed", "mac_failed", "mac_unreachable"] as const) {
      expect(updatePresentation({ ...base, status: at(state) })).toEqual({ state, dimmed: false });
    }
  });

  it("shows Updating… at once after a press, before the server answers", () => {
    for (const state of ["available", "waiting", "failed", "mac_failed", "mac_unreachable"] as const) {
      expect(updatePresentation({ ...base, status: at(state), starting: true }).state).toBe("updating");
    }
  });

  it("offers Refresh once the server is no longer updating and the running version moved", () => {
    // A page left behind cannot write. It refreshes first, then shows a Mac failure the server keeps.
    for (const state of ["up_to_date", "available", "waiting", "mac_failed", "mac_unreachable"] as const) {
      expect(updatePresentation({ ...base, status: at(state), pageIsBehind: true }).state).toBe("complete");
    }
    // Panels rolled back, so its failure is the news.
    expect(updatePresentation({ ...base, status: at("failed"), pageIsBehind: true }).state).toBe("failed");
    // The new server answers while the Mac still updates. That is still one update.
    expect(updatePresentation({ ...base, status: at("updating"), pageIsBehind: true }).state).toBe("updating");
  });

  it("keeps a lost connection inside the update until the deadline, then says Panels is unreachable", () => {
    const updating = at("updating", { deadline_at: new Date(5000).toISOString() });
    const lost = { ...base, connection: "reconnecting" as const };
    expect(updatePresentation({ ...lost, status: updating, now: 4999 })).toEqual({ state: "updating", dimmed: true });
    expect(updatePresentation({ ...lost, status: updating, now: 5000 })).toEqual({ state: "unreachable", dimmed: true });
    expect(updatePresentation({ ...lost, status: at("updating") })).toEqual({ state: "unreachable", dimmed: true });
    expect(updatePresentation({ ...lost, status: at("available", { deadline_at: new Date(5000).toISOString() }) }))
      .toEqual({ state: "unreachable", dimmed: true });
    expect(updatePresentation({ ...lost, status: undefined })).toEqual({ state: "unreachable", dimmed: true });
  });
});

describe("the update card", () => {
  it("uses the approved words and only offers what the server allows", () => {
    const failed = at("failed", { attempt, allowed_actions: ["retry"] });
    const mac = at("mac_failed", { attempt, allowed_actions: ["retry_mac"] });
    const cards = {
      available: updateCard("available", status),
      waiting: updateCard("waiting", at("waiting")),
      updating: updateCard("updating", at("updating")),
      complete: updateCard("complete", status),
      failed: updateCard("failed", failed),
      mac_failed: updateCard("mac_failed", mac),
      mac_unreachable: updateCard("mac_unreachable", { ...mac, state: "mac_unreachable" }),
      unreachable: updateCard("unreachable", undefined),
    };
    expect(Object.fromEntries(Object.entries(cards).map(([state, card]) =>
      [state, [card.word, card.action?.label ?? null, card.action?.does ?? null, card.info ?? null, Boolean(card.track)]]))).toEqual({
      available: ["Update available", "Update", "offer", null, false],
      waiting: ["Update waits for work to finish", "View", "waiting", null, false],
      updating: ["Updating…", null, null, null, true],
      complete: ["Update complete", "Refresh", "refresh", null, false],
      failed: ["Update didn't install", "Try again", "retry", "failed", false],
      mac_failed: ["Mac didn't update", "Try again", "retry_mac", "mac_failed", false],
      mac_unreachable: ["Can't reach the Mac", "Try again", "retry_mac", "mac_unreachable", false],
      unreachable: ["Can't reach Panels", "Try now", "reconnect", null, false],
    });
    // A failure the server will not retry keeps its details, without a dead button.
    expect(updateCard("failed", { ...failed, allowed_actions: [] })).toMatchObject({ action: undefined, info: "failed" });
    expect(updateCard("available", { ...status, allowed_actions: [] }).action).toBeUndefined();
  });

  it("keeps Mac diagnostics in the details, with plain facts for each failure", () => {
    const identity = { protocol_version: 7, application_revision: "3f9a2c1d00", spool_format_version: 5, executor_incarnation: "mac" };
    const refused: MacStatus = { presence: "refused", since_ms: 0, identity: null, last_contact_at_ms: 1000 - 14 * 60_000,
      refusal: { reason: "Mac needs update", peer_identity: identity, server_identity: { ...identity, protocol_version: 8 }, refused_at_ms: 0 } };
    expect(updateDetails("failed", at("failed", { attempt, detail: "The new version did not pass its health check." }), 1000)).toEqual({
      title: "Update didn't install", facts: [
        ["What happened", "The new version did not pass its health check."],
        ["Panels", "11111111 (previous)"], ["Tried", "22222222"], ["Mac", "Not changed"]] });
    expect(updateDetails("mac_unreachable", at("mac_unreachable", { attempt, app_sha: "2".repeat(40), mac_details: refused }), 1000)).toEqual({
      title: "Can't reach the Mac", facts: [
        ["What happened", "The Mac did not check in during the update. It can be asleep or offline."],
        ["Panels", "22222222 (updated)"], ["Mac", "3f9a2c1d"], ["Last contact", "14 minutes ago"],
        ["Protocol", "Mac 7 · server 8"], ["Outbox format", "Mac 5 · server 5"], ["Refused", "Mac needs update"]] });
    const reverted: MacStatus = { presence: "present", since_ms: 0, identity, last_contact_at_ms: 1000,
      update: { request_id: "m1", target_revision: "next", phase: "reverted", detail: "The new Mac runtime did not start." } };
    expect(updateDetails("mac_failed", at("mac_failed", { mac_details: reverted }), 1000).facts).toEqual([
      ["What happened", "The new Mac runtime did not start."], ["Panels", "11111111 (updated)"], ["Mac", "3f9a2c1d (previous)"],
      ["Last contact", "just now"], ["Protocol", "Mac 7"], ["Outbox format", "Mac 5"]]);
  });

  it("puts the approved offer text and activity note in the dialog", () => {
    const body = render(UpdateActions, { props: { status: { ...status, activity: "busy" }, now: 0 } }).body;
    expect(body).toContain("A new version is ready for Panels and the Mac. Panels restarts, then you refresh to finish.");
    expect(body).toContain("Work is running. Updating now interrupts it.");
    expect(body).toContain("When work finishes");
    expect(body).toContain("Update now");
    expect(render(UpdateActions, { props: { status: { ...status, activity: "idle" }, now: 0 } }).body).not.toContain("When work finishes");
  });
});

describe("owner update actions", () => {
  it("maps each press to its contract route, naming the running version", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response("{}"));
    vi.stubGlobal("fetch", fetch);
    const withAttempt = { ...status, attempt };
    await performUpdateAction(status, { action: "install", mode: "now" });
    await performUpdateAction(status, { action: "install", mode: "idle" });
    await performUpdateAction(withAttempt, { action: "cancel" });
    await performUpdateAction(withAttempt, { action: "now" });
    await performUpdateAction(withAttempt, { action: "retry" });
    await performUpdateAction(withAttempt, { action: "retry_mac" });
    expect(fetch.mock.calls.map(([path, init]) => [path, init?.body ? JSON.parse(String(init.body)) : null])).toEqual([
      ["/api/updates/install", { app_sha: "2".repeat(40), mode: "now", confirm_activity: true }],
      ["/api/updates/install", { app_sha: "2".repeat(40), mode: "idle", confirm_activity: false }],
      ["/api/updates/attempts/a1/cancel", null],
      ["/api/updates/attempts/a1/now", { confirm_activity: true }],
      ["/api/updates/attempts/a1/retry", null],
      ["/api/updates/attempts/a1/retry-mac", null],
    ]);
    for (const [, init] of fetch.mock.calls) expect(init?.headers).toMatchObject({ "X-Panels-App-SHA": "1".repeat(40) });
  });

  it("holds Updating… until the server's answer moves, and drops it on a refusal", async () => {
    let answer!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { answer = resolve; })));
    const pressed = performUpdateAction(status, { action: "install", mode: "now" });
    expect(get(updateStarting)).not.toBeNull();
    // A second press while the first is in flight sends nothing.
    await performUpdateAction(status, { action: "install", mode: "now" });
    answer(new Response("{}"));
    await pressed;
    // The attempt can land just after the route answers. The old answer keeps the press.
    settleUpdateStarting(status);
    expect(get(updateStarting)).not.toBeNull();
    settleUpdateStarting(at("updating", { attempt }));
    expect(get(updateStarting)).toBeNull();

    const refused = performUpdateAction(status, { action: "install", mode: "now" });
    answer(new Response(JSON.stringify({ detail: "The selected release is no longer ready." }), { status: 409 }));
    await expect(refused).rejects.toThrow("The selected release is no longer ready.");
    expect(get(updateStarting)).toBeNull();
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
  });

  it("expires a deadline on time without browser timer overflow", async () => {
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
});
