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
const scopePairPickerSource = await readFile(
  new URL("../src/components/ScopePairPicker.svelte", import.meta.url),
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
const workspaceRailSource = await readFile(
  new URL("../src/lib/workspaceRail.ts", import.meta.url),
  "utf8",
);
const sprintRouteSource = await readFile(
  new URL("../src/routes/SprintRoute.svelte", import.meta.url),
  "utf8",
);
const sprintItemWorkspaceSource = await readFile(
  new URL("../src/components/SprintItemWorkspace.svelte", import.meta.url),
  "utf8",
);
const sprintPresentationSource = await readFile(
  new URL("../src/lib/sprintPresentation.ts", import.meta.url),
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

// Backend management has one production home. One explicit action refreshes both the
// catalogue and cached allowance readings. Ordinary mounts only read cached state.
assert.match(appSource, /href="#\/backends"/);
assert.match(appSource, /<BackendsRoute \/>/);
assert.match(backendsRouteSource, /await refreshBackends\(\)/);
assert.match(backendsRouteSource, /await setBackendModelEnabled\(backendKey, modelId, enabled\)/);
const backendsOnMount = backendsRouteSource.slice(
  backendsRouteSource.indexOf("onMount(() =>"),
  backendsRouteSource.indexOf("</script>"),
);
assert.doesNotMatch(backendsOnMount, /refreshBackends/);
assert.match(devConversationRouteSource, /readBackends\(\)/);
assert.doesNotMatch(devConversationRouteSource, /BackendCard|updateBackend|refreshBackends/);

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
assert.match(ticketRouteSource, /<TicketConversationHistory/);
assert.match(ticketRouteSource, /bind:selectedPastConversationId/);
assert.match(ticketRouteSource, /conversationId=\{selectedConversationId\}/);
assert.match(ticketRouteSource, /readOnly=\{selectedPastConversationId !== null\}/);
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
assert.match(ticketRouteSource, /value="stop">then stop/);
assert.match(ticketRouteSource, /value="agent_review">then agent review/);
assert.match(ticketRouteSource, /value="user_review">then user review/);
assert.doesNotMatch(ticketRouteSource, /value="propose"|then continue/i);
assert.match(scopePairPickerSource, /value="stop">Stop/);
assert.match(scopePairPickerSource, /value="agent_review">Agent review/);
assert.match(scopePairPickerSource, /\{#if allowAgentReview\}<option value="agent_review"/);
assert.match(ticketRouteSource, /allowAgentReview=\{detail\.sprint_item_id !== null\}/);
assert.match(
  ticketRouteSource,
  /\{#if detail\.sprint_item_id !== null\}[\s\S]*?<option value="agent_review">then agent review/,
);
assert.match(scopePairPickerSource, /value="user_review">User review/);
assert.doesNotMatch(scopePairPickerSource, /value="propose"|>Continue</);
// Ticket placement edits the Project. The eyebrow states no Sprint, and its Sprint Item
// fact is a link back to that Item on the Workspace rather than a reassignment control.
assert.match(ticketRouteSource, /data-ticket-placement/);
assert.doesNotMatch(ticketRouteSource, /data-sprint-control/);
assert.match(ticketRouteSource, /data-sprint-item-control/);
assert.match(
  ticketRouteSource,
  /<a[\s\S]*?data-sprint-item-control[\s\S]*?workspaceAddress\(\{ kind: "item", id: detail\.sprint_item_id \}\)/,
);
assert.match(ticketRouteSource, /project_id: projectId, sprint_id: sprintId, sprint_item_id: sprintItemId/);

// Sprint tracking joins the Sprint, Project, and Today resources in the browser. Its
// overview contains Project folds and linked Sprint Item rows, while Ticket rows and
// work-state marks belong to the dedicated Item view.
assert.match(sprintRouteSource, /queries\.currentSprint\(\)/);
assert.match(sprintRouteSource, /queries\.projects\(\)/);
assert.match(sprintRouteSource, /queries\.todayDay\(\)/);
assert.match(sprintRouteSource, /sprintDayLabel\(sprint, current\.data\.planning_date\)/);
assert.match(sprintRouteSource, /name: "Checkpoint", meta: "day four"/);
assert.doesNotMatch(sprintRouteSource, /Mid-sprint Review|mid-sprint review/);
assert.match(sprintRouteSource, /variant="workspace-bucket"/);
assert.match(sprintRouteSource, /data-item-status=\{item\.status\}/);
assert.match(sprintRouteSource, /#\/sprint\?item=\$\{encodeURIComponent\(item\.id\)\}/);
assert.match(sprintItemWorkspaceSource, /data-sprint-item-view=\{itemId\}/);
assert.match(sprintItemWorkspaceSource, /data-sprint-ticket-id=\{ticket\.id\}/);
assert.match(sprintRouteSource, /<StageMark state=\{condition\.mark\}/);
assert.match(sprintPresentationSource, /todayTicketIds\.has\(ticket\.id\)/);
assert.match(sprintRouteSource, /data-sprint-other/);
assert.match(sprintRouteSource, /data-sprint-other-tickets/);
assert.match(sprintRouteSource, /current\.data\?\.other_tickets/);
assert.doesNotMatch(sprintRouteSource, /#\/sprint\?item=.*otherTicket/);
assert.doesNotMatch(sprintRouteSource, /<Chip/);

// --- the one priority tile ---------------------------------------------------------------

assert.match(priorityTileSource, /aria-label=\{decorative \? undefined : `Priority \$\{priority\}`\}/);
assert.match(ticketPriorityControlSource, /<PriorityTile \{priority\} decorative \/>[\s\S]*<select/);
assert.match(
  boardRouteSource,
  /<PriorityTile priority=\{item\.priority\} \/>[\s\S]*<span class="board-workspace-item-title">\{item\.title\}<\/span>/,
);
assert.doesNotMatch(
  boardRouteSource.slice(
    boardRouteSource.indexOf("{#snippet ticketRow"),
    boardRouteSource.indexOf("{/snippet}", boardRouteSource.indexOf("{#snippet ticketRow")),
  ),
  /<PriorityTile/,
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

// --- the Sprint Item Workspace rail ----------------------------------------------------

// Workspace has one board projection. It does not keep a client-side project selector
// or filter styles that can hide cards or restore the old top gap.
assert.match(boardRouteSource, /let rail = \$derived\(buildWorkspaceRail\(allCards\)\);/);
assert.doesNotMatch(boardRouteSource, /project-filter|projectMenu|selectedProject|rosterCards|All projects/);
assert.doesNotMatch(appCssSource, /board-workspace-project-filter/);
assert.match(workspaceRailSource, /"needs_user"[\s\S]*"awaiting_user_review"[\s\S]*"paired"[\s\S]*"user"/);
assert.match(workspaceRailSource, /!card\.is_done && !card\.blocked/);
assert.match(workspaceRailSource, /Number\(right\.attentionCards\.length > 0\)/);
assert.match(boardRouteSource, /data-workspace-view=\{railMode\}/);
assert.match(boardRouteSource, /\{hiddenCount\} more/);
assert.match(boardRouteSource, /data-no-item/);
assert.match(boardRouteSource, /<SprintItemWorkspace itemId=\{selectedItem\.id\}/);

console.log("production-surfaces.test.mjs: all assertions passed");
