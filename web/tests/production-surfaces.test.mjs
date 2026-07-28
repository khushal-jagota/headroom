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
// a person has: saying something, and New. There is no door that makes a conversation —
// the message is what makes one — so nothing is attached on arrival and nothing is
// started on arrival either. Both absences are asserted so the removals carry their
// reasons forward instead of being quietly re-added.
assert.match(ticketRouteSource, /conversationId=\{detail\.conversation_id\}/);
assert.match(ticketRouteSource, /\/api\/tickets\/\$\{stableId\}\/conversation\/send`/);
assert.match(ticketRouteSource, /\/api\/tickets\/\$\{stableId\}\/conversation\/reset`/);
assert.doesNotMatch(ticketRouteSource, /deferInitialAttach|onStartConversation/);

assert.match(ticketRouteSource, /<WorkerConfigurationSetup/);
assert.match(ticketRouteSource, /contextRow=\{name === "kickoff" && kickoffCardShowsContextRow/);
assert.match(ticketRouteSource, /\/api\/tickets\/\$\{stableId\}\/employee-configuration/);
assert.match(ticketRouteSource, /mutateJson<TicketDetail>\(/);
// Reading a reply is this browser's own business, so the screen tells no server about it.
assert.doesNotMatch(ticketRouteSource, /acknowledge-completed-response/);
// A Ticket parked on a proposal moves to paired when its owner replies. This screen is
// the one place that knows both halves — the Ticket, and the conversation it names — so
// it is the one that says a reply happened.
assert.match(ticketRouteSource, /onMessageAccepted=\{recordHumanReply\}/);
assert.match(ticketRouteSource, /\/api\/tickets\/\$\{stableId\}\/human-reply/);
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
assert.match(boardRouteSource, /howFarThisBrowserHasRead\[card\.conversation_id\]/);
assert.match(boardRouteSource, /latest_turn_ended_sequence/);
assert.doesNotMatch(boardRouteSource, /agent_reply_state/);
// How far this browser has read is held in state and the mark reads it from there.
// Reading a conversation writes nothing a server can announce, so no refetch is coming
// to redraw the board — the state is what makes a row go quiet when you open it.
assert.match(boardRouteSource, /let howFarThisBrowserHasRead = \$state/);
assert.match(boardRouteSource, /onReplyWatermarkMoved\(rereadWhereThisBrowserHasGot\)/);
// The mark must not read storage while it draws: a plain call has nothing reactive
// about it, so a row would keep its old dot until something unrelated refetched.
assert.doesNotMatch(
  boardRouteSource.slice(boardRouteSource.indexOf("function signalPresentation")),
  /readReplyWatermark\(/,
  "the row mark reads the positions it was given, never storage"
);

console.log("production-surfaces.test.mjs: all assertions passed");
