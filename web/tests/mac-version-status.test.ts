import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import MacStatus from "../src/components/machines/MacStatus.svelte";
import { macAvailability, type ExecutorIdentity, type MacStatus as Health } from "../src/lib/ticketMachines";

const identity: ExecutorIdentity = { protocol_version: 18, application_revision: "old-mac-revision", spool_format_version: 5, executor_incarnation: "mac", connection_generation: 1 };
const refused: Health = { identity: null, connection_state: "disconnected", last_contact_at_ms: 1000,
  refusal: { reason: "Mac needs update", peer_identity: identity, server_identity: { ...identity, application_revision: "new-server-revision", protocol_version: 19 }, refused_at_ms: 1000 } };

// Existing activity uncertainty tests do not distinguish version refusal from no
// contact. SSR proves the shared evidence component without a connected host.
describe("Mac version evidence", () => {
  it("shows both versions and labels refusal as historical, disconnected contact", () => {
    const html = render(MacStatus, { props: { health: refused } }).body;
    expect(html).toContain("Mac needs update");
    expect(html).toContain("Mac: old-mac-revi · protocol 18 · outbox 5");
    expect(html).toContain("Server: new-server-r · protocol 19 · outbox 5");
    expect(html).toContain("Last refusal:");
    expect(html).toContain("The Mac is not connected.");
    expect(html).toContain("Last contact:");
    expect(macAvailability(refused)).toBe("Mac needs update");
  });
  it("does not label an ordinary disconnection as an update refusal", () => {
    const disconnected = { ...refused, refusal: null };
    const html = render(MacStatus, { props: { health: disconnected, showDisconnected: true } }).body;
    expect(html).toContain("Mac disconnected");
    expect(html).not.toContain("Mac needs update");
    expect(html).not.toContain("protocol 18");
  });
  it("gives a compatible current connection priority over retained old evidence", () => {
    const ready = { ...refused, connection_state: "ready" as const };
    expect(render(MacStatus, { props: { health: ready, showDisconnected: true } }).body).not.toContain("Mac needs update");
    expect(macAvailability(ready)).not.toBe("Mac needs update");
  });
});
