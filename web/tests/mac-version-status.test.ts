import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import MacStatus from "../src/components/machines/MacStatus.svelte";
import { macActivityKnown, macPresenceLabel, macTicketActivityLabel, type ExecutorIdentity, type MacStatus as Health } from "../src/lib/ticketMachines";

const identity: ExecutorIdentity = { protocol_version: 18, application_revision: "old-mac-revision", spool_format_version: 5, executor_incarnation: "mac" };
const refused: Health = { presence: "refused", since_ms: 1000, identity: null, last_contact_at_ms: 1000,
  refusal: { reason: "Mac needs update", peer_identity: identity, server_identity: { ...identity, application_revision: "new-server-revision", protocol_version: 19 }, refused_at_ms: 1000 } };
const present: Health = { ...refused, presence: "present", refusal: null };
const now = Date.UTC(2026, 10, 9, 15, 0);
const at1402 = Date.UTC(2026, 10, 9, 14, 2);
const away = (presence: "asleep" | "not_heard_from" | "never"): Health => ({ ...present, presence, since_ms: presence === "never" ? null : at1402 });

// The lease reports presence, and each kind of absence reads as its own sentence. Sleep is
// ordinary and is told plainly, with no alarm and no claim about activity.
describe("Mac presence", () => {
  it.each([
    ["asleep today", away("asleep"), "Asleep since 14:02"],
    ["not heard from on another day", { ...away("not_heard_from"), since_ms: Date.UTC(2026, 10, 7, 14, 2) }, "Not heard from since 7 Nov, 14:02"],
    ["never connected", away("never"), "The Mac has not connected yet."],
    ["refused", refused, "Mac needs update"],
    ["present", present, null],
    ["unknown", { last_contact_at_ms: null }, null],
  ] as const)("labels %s", (_case, health, label) => {
    expect(macPresenceLabel(health, now, "UTC")).toBe(label);
  });
  it("knows Mac activity only while the remote Mac is present", () => {
    expect(macActivityKnown({ ...present, mode: "remote" })).toBe(true);
    expect(macActivityKnown({ ...present, mode: "local" })).toBe(false);
    expect(macActivityKnown({ ...away("asleep"), mode: "remote" })).toBe(false);
    expect(macActivityKnown({ ...refused, mode: "remote" })).toBe(false);
    expect(macActivityKnown({ mode: "remote", last_contact_at_ms: 1 })).toBe(false);
  });
  it("says what a Mac Ticket is doing while the Mac is away", () => {
    const working = { locale: "macos" as const, ticket_status: "agent", conversation_signal: { working: true } };
    const held = { locale: "macos" as const, ticket_status: "agent", conversation_signal: { working: false } };
    expect(macTicketActivityLabel(away("asleep"), working)).toBe("Paused: the Mac is away");
    expect(macTicketActivityLabel(away("not_heard_from"), held)).toBe("Waiting for the Mac");
    expect(macTicketActivityLabel(away("never"), { locale: "macos", agent_state: "working" })).toBe("Waiting for the Mac");
    expect(macTicketActivityLabel(away("asleep"), { locale: "macos", ticket_status: "owner" })).toBeNull();
    expect(macTicketActivityLabel(away("asleep"), { ...working, locale: "vps" })).toBeNull();
    expect(macTicketActivityLabel(present, working)).toBeNull();
    expect(macTicketActivityLabel(refused, working)).toBe("Mac needs update");
  });
});

// Existing activity uncertainty tests do not distinguish version refusal from absence.
// SSR proves the shared evidence component without a connected host.
describe("Mac version evidence", () => {
  it("shows both versions and labels refusal as historical, disconnected contact", () => {
    const html = render(MacStatus, { props: { health: refused } }).body;
    expect(html).toContain("Mac needs update");
    expect(html).toContain("Machine: old-mac-revi · protocol 18 · outbox 5");
    expect(html).toContain("Server: new-server-r · protocol 19 · outbox 5");
    expect(html).toContain("Last refusal:");
    expect(html).toContain("Machine is not connected.");
    expect(html).toContain("Last contact:");
  });
  it("refuses an old WebSocket Mac without a Mac version to show", () => {
    const html = render(MacStatus, { props: { health: { ...refused, refusal: { ...refused.refusal!, peer_identity: null } } } }).body;
    expect(html).toContain("Mac needs update");
    expect(html).not.toContain("Mac: ");
    expect(html).toContain("Server: new-server-r · protocol 19 · outbox 5");
  });
  it("shows a Mac Ticket paused while the Mac sleeps, not as an update refusal", () => {
    const ticket = { locale: "macos" as const, ticket_status: "agent", conversation_signal: { working: true } };
    const html = render(MacStatus, { props: { health: { ...away("asleep"), refusal: refused.refusal, since_ms: Date.now() }, ticket } }).body;
    expect(html).toContain("Paused: Machine is away");
    expect(html).toMatch(/Asleep since \d{2}:\d{2}</);
    expect(html).not.toContain("Mac needs update");
  });
  it("gives a present Mac priority over retained old evidence", () => {
    expect(render(MacStatus, { props: { health: { ...present, refusal: refused.refusal } } }).body).not.toContain("Mac needs update");
  });
  // VPS completion cannot stand in for Mac completion. Each actual Mac phase
  // needs its own label.
  it.each([
    ["waiting", "Mac waits to update"],
    ["downloading", "Mac downloads the update"],
    ["ready_to_restart", "Mac is ready to update"],
    ["current", "Mac is current"],
    ["failed", "Mac update failed"],
    ["reverted", "Mac returned to its previous version"],
    ["unavailable", "Mac needs a manual update"],
  ] as const)("shows %s separately from VPS completion", (phase, label) => {
    const update = { request_id: "approved", target_revision: "0123456789abcdef", phase, detail: null };
    const html = render(MacStatus, { props: { health: { ...present, update } } }).body;
    expect(html).toContain(label);
    expect(html).toContain("Target 0123456789ab");
  });
  it("keeps update failure and version-refusal evidence distinct", () => {
    const update = { request_id: "approved", target_revision: "next", phase: "failed" as const, detail: "The previous version stayed active." };
    const html = render(MacStatus, { props: { health: { ...refused, update } } }).body;
    expect(html).toContain("Mac update failed");
    expect(html).toContain("The previous version stayed active.");
    expect(html).toContain("Mac needs update");
    expect(html).toContain("Last refusal:");
    expect(html).toContain("Last contact:");
  });
});


it("keeps mapped cleanup and legacy evidence visible while the executor is present", () => {
  const html = render(MacStatus, { props: { health: {
    ...present,
    cleanup_uncertainties: [{ticket_id:"affected-ticket", conversation_id:"source", reason:"Recorded tool remains"}],
    legacy_cleanup_notice: "An older process record needs operator review",
  } } }).body;
  expect(html).toContain("Worker cleanup not proven");
  expect(html).toContain("Recorded tool remains");
  expect(html).toContain("#/workspace/affected-ticket");
  expect(html).toContain("Affected Ticket");
  expect(html).toContain("An older process record needs operator review");
});
