import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

// Every production surface that shows a conversation is on the conversation system's own
// pane, and each route wires it the one way. These are read out of the sources rather than
// driven in a browser because they are about WHAT a route mounts and what it hands over —
// a question a rendered pane cannot answer, since a route that mounted the wrong thing
// would still render something.

const ticketRouteSource = await readFile(
  new URL("../src/routes/TicketRoute.svelte", import.meta.url),
  "utf8",
);
const boardRouteSource = await readFile(
  new URL("../src/routes/BoardRoute.svelte", import.meta.url),
  "utf8",
);
const appSource = await readFile(new URL("../src/App.svelte", import.meta.url), "utf8");

// --- the app itself ------------------------------------------------------------------

assert.match(appSource, /startChangeStream\(\);/);
assert.doesNotMatch(appSource, /\/api\/meta/);
assert.doesNotMatch(
  appSource,
  /capabilities|relay_chief_enabled|resolveRelayChiefFromMeta|markRelayChiefMetaError/,
);

// --- the Chief is one conversation, in both the places it appears ---------------------

const chiefOfStaffRouteSource = await readFile(
  new URL("../src/routes/ChiefOfStaffRoute.svelte", import.meta.url),
  "utf8",
);
for (const [name, source] of [
  ["BoardRoute.svelte", boardRouteSource],
  ["ChiefOfStaffRoute.svelte", chiefOfStaffRouteSource],
]) {
  assert.match(source, /<ChiefConversation \/>/, name);
  assert.doesNotMatch(
    source,
    /ChatPanel|ChiefNeutralPane|relayChief|retryRelayChiefMeta|chatGatewayStatus|\/api\/chat|\/api\/relay/,
    name,
  );
}

// --- the Ticket screen -----------------------------------------------------------------

// What the route hands the pane is the conversation the Ticket names, plus the two doors
// a person can open and close one with. Nothing is attached on arrival: the conversation
// system spawns nothing until a message is sent, which is what the old deferInitialAttach
// existed to avoid while a Ticket's configuration was still editable. The absence is
// asserted so the removal carries its reason forward instead of being quietly re-added.
assert.match(ticketRouteSource, /conversationId=\{detail\.conversation_id\}/);
assert.match(ticketRouteSource, /\/api\/tickets\/\$\{stableId\}\/conversation`/);
assert.match(ticketRouteSource, /\/api\/tickets\/\$\{stableId\}\/conversation\/reset`/);
assert.doesNotMatch(ticketRouteSource, /deferInitialAttach/);

assert.match(ticketRouteSource, /<WorkerConfigurationSetup/);
assert.match(ticketRouteSource, /contextRow=\{name === "kickoff" && kickoffCardShowsContextRow/);
assert.match(ticketRouteSource, /\/api\/tickets\/\$\{stableId\}\/employee-configuration/);
assert.match(ticketRouteSource, /mutateJson<TicketDetail>\(/);
// Reading a reply is this browser's own business, so the screen tells no server about it.
assert.doesNotMatch(ticketRouteSource, /acknowledge-completed-response/);
assert.doesNotMatch(ticketRouteSource, /pristineKickoff|employeeBackendOptions|\/employee-backend/);
assert.doesNotMatch(ticketRouteSource, /["'](?:hermes|codex|claude(?: code)?)["']/i);
assert.doesNotMatch(ticketRouteSource, /<style>|settings|employee backend|ACP backend/i);

// --- the Workspace row mark -------------------------------------------------------------

assert.match(boardRouteSource, /state: "needs-me", ariaLabel: "Needs you"/);
assert.match(boardRouteSource, /state: "current-running", ariaLabel: "Agent working"/);
assert.match(boardRouteSource, /state: "reply-seen", ariaLabel: "Agent reply seen"/);
// The mark is drawn from the record's last turn ending and this browser's own watermark.
// Nothing on the card says whether a reply was seen, because seen is not a fact about the
// Ticket.
assert.match(boardRouteSource, /readReplyWatermark\(card\.conversation_id\)/);
assert.match(boardRouteSource, /latest_turn_ended_sequence/);
assert.doesNotMatch(boardRouteSource, /agent_reply_state/);

console.log("production-surfaces.test.mjs: all assertions passed");
