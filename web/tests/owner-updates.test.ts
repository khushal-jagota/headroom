import { afterEach, describe, expect, it, vi } from "vitest";
import { get } from "svelte/store";
import { render } from "svelte/server";
import { performUpdateAction, settleUpdateStarting, updateStarting, updateActionRoute } from "../src/lib/updates/api";
import { UPDATE_CONFIRMATION_MS } from "../src/lib/updates/config";
import { atUpdateDeadline } from "../src/lib/updates/deadline";
import { updateCard, deviceUpdateWarnings, cardMachines, cardInterruption, interruptionText, machineUpdateLabel, updatePresentation, type UpdatePresentationInput } from "../src/lib/updates/logic/presentation";
import type { UpdateStatus, MachineUpdateState } from "../src/lib/updates/contracts";
import MachineRows from "../src/lib/updates/MachineRows.svelte";

const attempt = { attempt_id: "a1", from_sha: "1".repeat(40), to_sha: "2".repeat(40),
  state: "failed" as const, completed_at: new Date(0).toISOString(), detail: null, deadline_at: null, mode: "now" as const, interruption: null };
const status: UpdateStatus = { state: "available", allowed_actions: ["update"], activity: "unknown",
  client_versions: [], machines: [], available_machines: [], interruption: { stops_agents: false, agents: [], machine_ids: [] },
  available: { app_sha: "2".repeat(40) }, attempt: null, app_sha: "1".repeat(40), detail: null, deadline_at: null, mac_details: null };
const machine = (name: string, state: MachineUpdateState["state"]): MachineUpdateState => ({
  machine_id: name, name, platform: "linux", present: state !== "waiting", build_id: "old", target_build_id: "new",
  changed: true, state, attempt_id: "old", request_id: name, detail: null, can_retry: state === "failed",
});
const base: UpdatePresentationInput = { status, connection: "connected", pageIsBehind: false, starting: false, now: 1000 };
const at = (state: UpdateStatus["state"], extra: Partial<UpdateStatus> = {}) => ({ ...status, state, ...extra });

// The resolver is the one place that turns a server answer, the connection and this
// page's version into the card. Each state needs its own proof because the card has
// no other source, and a wrong answer either hides an action or claims a finished update.
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); updateStarting.set(null); });

describe("the update card resolver", () => {
  it("shows each server state on a connected page", () => {
    expect(updatePresentation({ ...base, status: at("up_to_date") })).toEqual({ state: null, dimmed: false });
    const complete = at("up_to_date", { attempt: { ...attempt, state: "done" }, app_sha: attempt.to_sha });
    expect(updatePresentation({ ...base, status: complete }).state).toBe("machines");
    expect(updateCard("machines", complete).word).toBe("Up to date");
    expect(updatePresentation({ ...base, status: undefined })).toEqual({ state: null, dimmed: false });
    for (const state of ["available", "updating", "failed", "mac_failed", "mac_unreachable"] as const) {
      expect(updatePresentation({ ...base, status: at(state) })).toEqual({ state, dimmed: false });
    }
  });

  it("shows the saved install independently of whether the latest release can be checked", () => {
    const saved = at("check_unavailable", { attempt: { ...attempt, state: "done" }, app_sha: attempt.to_sha });
    expect(updatePresentation({ ...base, status: saved }).state).toBe("machines");
    expect(updateCard("machines", saved)).toMatchObject({ word: "Update installed", detail: "Couldn’t confirm the latest version." });
    expect(updatePresentation({ ...base, status: { ...saved, app_sha: "different" } }).state).toBe("check_unavailable");
    const unknown = { ...saved, app_sha: "", attempt: { ...attempt, state: "done" as const, to_sha: "" } };
    expect(updatePresentation({ ...base, status: unknown }).state).toBe("check_unavailable");
    const failing = { ...saved, machines: [machine("Desk", "failed")] };
    expect(updateCard("machines", failing)).toMatchObject({ word: "Machine update needs attention", detail: "Couldn’t confirm the latest version." });
  });

  it("expires the same receipt on a quiet sidebar and after reloading, while retaining outstanding work", () => {
    const row = machine("Desk", "current");
    const complete = at("up_to_date", { attempt: { ...attempt, state: "done" }, app_sha: attempt.to_sha, machines: [row] });
    const show = (value: UpdateStatus, now: number, pageIsBehind = false) =>
      updatePresentation({ ...base, status: value, now, pageIsBehind }).state;
    expect(show(complete, UPDATE_CONFIRMATION_MS - 1)).toBe("machines");
    expect(show(complete, UPDATE_CONFIRMATION_MS)).toBeNull();
    expect(show(structuredClone(complete), 60_000)).toBeNull();
    expect(show(complete, 60_000, true)).toBe("refresh_needed");
    expect(show({ ...complete, attempt: { ...complete.attempt!, completed_at: undefined } }, 1000)).toBeNull();
    expect(show({ ...complete, state: "check_unavailable" }, 60_000)).toBe("check_unavailable");
    for (const state of ["failed", "waiting", "updating", "unavailable"] as const)
      expect(show({ ...complete, machines: [machine("Desk", state)] }, 60_000)).toBe("machines");
  });

  it("shows Updating… at once after a press, before the server answers", () => {
    for (const state of ["available", "failed", "mac_failed", "mac_unreachable"] as const) {
      expect(updatePresentation({ ...base, status: at(state), starting: true }).state).toBe("updating");
    }
  });

  it("offers Refresh once the server is no longer updating and the running version moved", () => {
    expect(updatePresentation({ ...base, status: at("up_to_date"), pageIsBehind: true }).state).toBe("refresh_needed");
    for (const state of ["available", "mac_failed", "mac_unreachable", "check_unavailable"] as const) {
      expect(updatePresentation({ ...base, status: at(state), pageIsBehind: true }).state).toBe(state);
    }
    const failedMachine = at("up_to_date", { machines: [machine("Mac 1", "failed")] });
    for (const pageIsBehind of [false, true]) {
      expect(updatePresentation({ ...base, status: failedMachine, pageIsBehind }).state).toBe("machines");
      expect(updateCard("machines", failedMachine).word).toBe("Machine update needs attention");
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
    const cards = {
      available: updateCard("available", status),
      updating: updateCard("updating", at("updating")),
      refresh_needed: updateCard("refresh_needed", status),
      failed: updateCard("failed", failed),
      mac_failed: updateCard("mac_failed", at("mac_failed")),
      mac_unreachable: updateCard("mac_unreachable", at("mac_unreachable")),
      unreachable: updateCard("unreachable", undefined),
    };
    expect(Object.fromEntries(Object.entries(cards).map(([state, card]) =>
      [state, [card.word, card.action?.label ?? null, card.action?.does ?? null, card.detail ?? null, Boolean(card.track)]]))).toEqual({
      available: ["Update available", "Update", "install", null, false],
      updating: ["Updating…", null, null, null, true],
      refresh_needed: ["Refresh to load this version", "Refresh", "refresh", null, false],
      failed: ["Update didn't install", "Try again", "retry", null, false],
      mac_failed: ["Machine update needs attention", null, null, null, false],
      mac_unreachable: ["Machine update needs attention", null, null, null, false],
      unreachable: ["Can't reach Panels", "Try now", "reconnect", "Trying again every few seconds", false],
    });
    // A failure the server will not retry keeps its details, without a dead button.
    expect(updateCard("failed", { ...failed, detail: "The previous release is running.", allowed_actions: [] })).toMatchObject({ action: undefined, detail: "The previous release is running." });
    expect(updateCard("available", { ...status, allowed_actions: [] }).action).toBeUndefined();
  });

  it("keeps retained targets distinct from future offers, with each machine's outcome", () => {
    const rows = [machine("desk", "failed"), machine("server", "updating"), machine("away", "waiting"),
      { ...machine("done", "current"), detail: "Allow Panels to access Files and Folders." }, machine("unsupported", "unavailable")];
    const mixed = at("up_to_date", { available: null, machines: rows });
    expect(updatePresentation({ ...base, status: mixed }).state).toBe("machines");
    const body = render(MachineRows, { props: { machines: rows, retry: () => {} } }).body;
    for (const word of ["Update failed", "Updating…", "Updates when it is back", "Done", "Needs an update to continue"])
      expect(body).toContain(word);
    expect(body).toContain('aria-label="Try again for desk"');
    expect(body).toContain("Allow Panels to access Files and Folders.");
    const offered = rows.map(row => ({ ...row, attempt_id: null, state: "available" as const, can_retry: false }));
    expect(cardMachines({ ...mixed, available: status.available, available_machines: offered })).toEqual(rows);
    expect(machineUpdateLabel({ ...offered[0], present: false })).toBe("Updates when it is back");
  });

  it("warns before the press from offered requirements and retained device versions", () => {
    const compatibility = { ios: { minimum_build: 20 }, desktop_shell: { minimum_bridge_version: 3 },
      machine: { minimum_protocol_version: 4, maximum_protocol_version: 6 }, outbox_format: 2 };
    const offered: UpdateStatus = { ...status, available: { ...status.available!, compatibility },
      machines: [machine("Office", "available")],
      client_versions: [
        { kind: "ios", device_id: "phone", version: "1", build: 19 },
        { kind: "desktop", device_id: "desk", version: "1", bridge_version: 2 },
        { kind: "machine", device_id: "Office", version: "1", protocol_version: 7, outbox_format: 2 },
      ] };
    expect(deviceUpdateWarnings(offered)).toEqual([
      "Your iPhone needs the TestFlight update first.", "Your desktop app needs an update first.",
      "Office needs an update to continue.",
    ]);
    expect(deviceUpdateWarnings({ ...offered, available: status.available })).toEqual([]);
    const compatible = offered.client_versions.map(identity => ({ ...identity, build: 20, bridge_version: 3, protocol_version: 6 }));
    expect(deviceUpdateWarnings({ ...offered, client_versions: compatible })).toEqual([]);
    expect(deviceUpdateWarnings({ ...offered, client_versions: [{ kind: "machine", device_id: "Office", version: "1", protocol_version: 3, outbox_format: 2 }] }))
      .toEqual(["Office needs an update to continue."]);
    expect(deviceUpdateWarnings({ ...offered, client_versions: [{ kind: "machine", device_id: "Office", version: "1", protocol_version: 6, outbox_format: 1 }] }))
      .toEqual(["Office needs an update to continue."]);
  });

  it("names affected agents before Update and uses the press snapshot afterwards", () => {
    const interruption = { stops_agents: true, agents: ["Release proof", "Inbox cleanup"], machine_ids: ["desk"] };
    const offered = { ...status, interruption, activity: "busy" as const };
    expect(interruptionText(cardInterruption(offered))).toBe("This update stops your agents");
    expect(cardInterruption(offered)?.agents).toEqual(["Release proof", "Inbox cleanup"]);
    expect(interruptionText(status.interruption)).toBe("Your agents keep running");
    const pressed = { ...offered, state: "updating" as const, attempt: { ...attempt, interruption: status.interruption } };
    expect(cardInterruption(pressed)).toBe(status.interruption);
    expect(cardInterruption({ ...pressed, attempt })).toBeNull();
    expect(cardInterruption(offered, "updating")).toBe(interruption);
    for (const state of ["failed", "machines", "refresh_needed", "unreachable"] as const)
      expect(cardInterruption({ ...pressed, attempt: { ...attempt, interruption } }, state)).toBeNull();
    expect(interruptionText(null)).toBeNull();
  });
});

describe("owner update actions", () => {
  it("maps each press to its contract route, naming the running version", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response("{}"));
    vi.stubGlobal("fetch", fetch);
    const withAttempt = { ...status, attempt };
    await performUpdateAction(status, { action: "install" });
    await performUpdateAction(withAttempt, { action: "retry" });

    expect(fetch.mock.calls.map(([path, init]) => [path, init?.body ? JSON.parse(String(init.body)) : null])).toEqual([
      ["/api/updates/install", { app_sha: "2".repeat(40) }],
      ["/api/updates/attempts/a1/retry", null],
    ]);
    for (const [, init] of fetch.mock.calls) expect(init?.headers).toMatchObject({ "X-Panels-App-SHA": "1".repeat(40) });
  });

  it("retries a retained machine target rather than the latest server attempt, without a global restart", async () => {
    const retained = machine("desk/one", "failed");
    const current = { ...status, attempt: { ...attempt, attempt_id: "new" }, machines: [retained] };
    const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response("{}"));
    vi.stubGlobal("fetch", fetch);
    await performUpdateAction(current, { action: "retry_machine", machine_id: retained.machine_id });
    expect(fetch.mock.calls[0][0]).toBe("/api/updates/attempts/old/machines/desk%2Fone/retry");
    expect(get(updateStarting)).toBeNull();
    expect(() => updateActionRoute({ ...current, machines: [{ ...retained, can_retry: false }] },
      { action: "retry_machine", machine_id: retained.machine_id })).toThrow("no longer available");
  });

  it("holds Updating… until the server's answer moves, and drops it on a refusal", async () => {
    let answer!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { answer = resolve; })));
    const pressed = performUpdateAction(status, { action: "install" });
    expect(get(updateStarting)).not.toBeNull();
    // A second press while the first is in flight sends nothing.
    await performUpdateAction(status, { action: "install" });
    answer(new Response("{}"));
    await pressed;
    // The attempt can land just after the route answers. The old answer keeps the press.
    settleUpdateStarting(status);
    expect(get(updateStarting)).not.toBeNull();
    settleUpdateStarting(at("updating", { attempt }));
    expect(get(updateStarting)).toBeNull();

    const refused = performUpdateAction(status, { action: "install" });
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
