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
const appCssSource = await readFile(
  new URL("../../assets/app.css", import.meta.url),
  "utf8",
);
const conversationSignalPresentationSource = await readFile(
  new URL("../src/lib/conversationSignalPresentation.ts", import.meta.url),
  "utf8",
);
const sprintRouteSource = await readFile(
  new URL("../src/routes/SprintRoute.svelte", import.meta.url),
  "utf8",
);
const backlogRouteSource = await readFile(
  new URL("../src/routes/BacklogRoute.svelte", import.meta.url),
  "utf8",
);
const ticketPriorityControlSource = await readFile(
  new URL("../src/components/TicketPriorityControl.svelte", import.meta.url),
  "utf8",
);
const priorityTileSource = await readFile(
  new URL("../src/components/PriorityTile.svelte", import.meta.url),
  "utf8",
);
const appSource = await readFile(new URL("../src/App.svelte", import.meta.url), "utf8");
const backendsRouteSource = await readFile(
  new URL("../src/routes/BackendsRoute.svelte", import.meta.url),
  "utf8",
);
const devConversationRouteSource = await readFile(
  new URL("../src/routes/DevConversationRoute.svelte", import.meta.url),
  "utf8",
);

// --- the app itself ------------------------------------------------------------------

assert.match(appSource, /startChangeStream\(\);/);
assert.doesNotMatch(appSource, /\/api\/meta/);
assert.doesNotMatch(
  appSource,
  /capabilities|relay_chief_enabled|resolveRelayChiefFromMeta|markRelayChiefMetaError/,
);

// Backend management has one production home. Provider allowance acquisition exists
// behind its explicit action only; mounting the page and the dev composer merely read the
// ordinary backend catalogue.
assert.match(appSource, /href="#\/backends"/);
assert.match(appSource, /<BackendsRoute \/>/);
assert.match(backendsRouteSource, /onUsageRefresh=\{\(\) => void runUsageRefresh/);
assert.match(backendsRouteSource, /await refreshBackendUsage\(key\)/);
const backendsOnMount = backendsRouteSource.slice(
  backendsRouteSource.indexOf("onMount(() =>"),
  backendsRouteSource.indexOf("</script>"),
);
assert.doesNotMatch(backendsOnMount, /refreshBackendUsage/);
assert.match(devConversationRouteSource, /readBackends\(\)/);
assert.doesNotMatch(devConversationRouteSource, /BackendCard|updateBackend|refreshBackendUsage/);

// --- the Chief is one conversation, in the Workspace inspector -------------------------

assert.match(boardRouteSource, /<ChiefConversation \/>/);
assert.match(boardRouteSource, /data-chief-destination/);
assert.doesNotMatch(
  boardRouteSource,
  /ChatPanel|ChiefNeutralPane|relayChief|retryRelayChiefMeta|chatGatewayStatus|\/api\/chat|\/api\/relay/,
);

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
assert.match(ticketRouteSource, /data-sprint-item-control/);
assert.match(ticketRouteSource, /\/api\/items\/\$\{encodeURIComponent\(sprintItemId\)\}\/tickets/);
assert.match(ticketRouteSource, /method: "DELETE"/);
assert.doesNotMatch(ticketRouteSource, /detail\.sprint_id|body: \{ sprint_id/);

// Sprint tracking has only Sprint Item groups. Machine-recognized Other items remain
// visible as fallbacks instead of disappearing into the old loose-Ticket section.
assert.match(sprintRouteSource, /data-item-kind=\{item\.kind\}/);
assert.match(sprintRouteSource, /item\.kind === "other"/);
assert.doesNotMatch(sprintRouteSource, /loose_tickets|Loose tickets|data-loose/);

// --- the one priority tile ---------------------------------------------------------------

assert.match(priorityTileSource, /aria-label=\{decorative \? undefined : `Priority \$\{priority\}`\}/);
assert.match(ticketPriorityControlSource, /<PriorityTile \{priority\} decorative \/>[\s\S]*<select/);
assert.match(
  boardRouteSource,
  /<PriorityTile priority=\{card\.priority\} \/>[\s\S]*<span class="list-row-title">\{card\.title\}<\/span>[\s\S]*<StageMark/,
);
assert.equal((sprintRouteSource.match(/<PriorityTile priority=/g) || []).length, 2);
assert.match(backlogRouteSource, /labelContent\(\)}<PriorityTile priority=\{p\} \/>/);
assert.doesNotMatch(
  backlogRouteSource.slice(backlogRouteSource.indexOf("{#each groups[p] as item}")),
  /<PriorityTile/,
);

// --- the Workspace row mark -------------------------------------------------------------

assert.match(conversationSignalPresentationSource, /state: "needs-me", ariaLabel: "Needs you"/);
assert.match(
  conversationSignalPresentationSource,
  /state: "current-running", ariaLabel: "Agent working"/,
);
assert.match(
  conversationSignalPresentationSource,
  /state: "reply-seen", ariaLabel: "Agent reply seen"/,
);
// The mark is drawn from the record's last turn ending and this browser's own watermark.
// Nothing on the card says whether a reply was seen, because seen is not a fact about the
// Ticket.
assert.match(boardRouteSource, /conversationSignalPresentation/);
assert.match(
  conversationSignalPresentationSource,
  /replyWatermarks\[signals\.conversation_id\]/,
);
assert.match(conversationSignalPresentationSource, /latest_turn_ended_sequence/);
assert.doesNotMatch(boardRouteSource, /agent_reply_state/);
// How far this browser has read is held in state and the mark reads it from there.
// Reading a conversation writes nothing a server can announce, so no refetch is coming
// to redraw the board — the state is what makes a row go quiet when you open it.
assert.match(boardRouteSource, /let howFarThisBrowserHasRead = \$state/);
assert.match(boardRouteSource, /onReplyWatermarkMoved\(rereadWhereThisBrowserHasGot\)/);
// The mark must not read storage while it draws: a plain call has nothing reactive
// about it, so a row would keep its old dot until something unrelated refetched.
assert.doesNotMatch(
  conversationSignalPresentationSource,
  /readReplyWatermark\(/,
  "the row mark reads the positions it was given, never storage"
);

// --- the Workspace groups --------------------------------------------------------------

// Workspace has one board projection. It does not keep a client-side project selector
// or filter styles that can hide cards or restore the old top gap.
assert.match(boardRouteSource, /let groups = \$derived\(buildGroups\(allCards\)\);/);
assert.doesNotMatch(boardRouteSource, /project-filter|projectMenu|selectedProject|rosterCards|All projects/);
assert.doesNotMatch(appCssSource, /board-workspace-project-filter/);

// Kickoff approval is the one approval subtype Workspace can classify entirely from the
// existing board card. Done and the server-projected Closeout exception keep precedence,
// then this predicate separates Kickoff from every later approval.
const groupKeySource = boardRouteSource.slice(
  boardRouteSource.indexOf("function groupKeyFor"),
  boardRouteSource.indexOf("const GROUP_ORDER"),
);
assert.match(
  groupKeySource,
  /if \(card\.is_done\) return "done";[\s\S]*if \(card\.waiting_to_closeout\) return "waiting_to_closeout";[\s\S]*card\.ticket_status === "awaiting_approval"[\s\S]*card\.gating_field === "kickoff"[\s\S]*return "waiting_for_kickoff";/,
);
assert.match(boardRouteSource, /waiting_for_kickoff: "Waiting for Kickoff"/);

const groupOrderMatch = boardRouteSource.match(
  /const GROUP_ORDER: readonly string\[\] = \[([\s\S]*?)\n  \];/,
);
assert.ok(groupOrderMatch, "Workspace declares one canonical group order");
const groupOrder = [...groupOrderMatch[1].matchAll(/"([^"]+)"/g)].map(
  (match) => match[1],
);
assert.deepEqual(groupOrder, [
  "errored",
  "needs_user",
  "waiting_for_kickoff",
  "user",
  "paired",
  "agent",
  "waiting_to_closeout",
  "awaiting_approval",
  "empty",
  "blocked",
  "done",
]);

console.log("production-surfaces.test.mjs: all assertions passed");
