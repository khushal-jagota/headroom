import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { keysForEvent } from "../src/lib/eventMapping.mjs";

const FALLBACK_KINDS = [
  "stage_changed",
  "proposal_accepted",
  "proposal_superseded",
  "day_ticket_removed",
  "ticket_deleted",
  "ticket_created",
  "sprint_created",
  "sprint_item_created",
  "idea_created",
  "day_created",
  "project_created",
  "proposal_filed",
  "kickoff_proposal_filed",
  "kickoff_accepted",
  "approval_returned",
  "note_updated",
  "recap_updated",
  "scope_changed",
  "field_value_edited",
  "ticket_updated",
  "item_updated",
  "sprint_updated",
  "day_updated",
  "project_updated",
  "item_children_changed",
  "day_ticket_added",
  "ticket_status_changed",
  "link_added",
  "link_removed",
  "chat_session_created",
  "chat_message_recorded",
  "chat_turn_started",
  "chat_turn_updated",
  "chat_turn_finished"
];

const kinds = JSON.parse(process.env.PLANNER_EVENT_KINDS || JSON.stringify(FALLBACK_KINDS));
assert.ok(Array.isArray(kinds), "PLANNER_EVENT_KINDS must be a JSON array");
assert.ok(kinds.length > 0, "backend EventKind list is empty");

function sampleEvent(kind) {
  if (kind === "sprint_created" || kind === "sprint_updated") {
    return { id: 1, entity_id: "sp_demo", kind, payload: {}, created_at: 1 };
  }
  if (
    kind === "sprint_item_created" ||
    kind === "item_updated" ||
    kind === "item_children_changed"
  ) {
    return { id: 1, entity_id: "si_demo", kind, payload: {}, created_at: 1 };
  }
  if (kind === "idea_created") {
    return { id: 1, entity_id: "idea_demo", kind, payload: {}, created_at: 1 };
  }
  if (kind === "project_created" || kind === "project_updated") {
    return { id: 1, entity_id: "project_demo", kind, payload: {}, created_at: 1 };
  }
  if (kind.startsWith("day_") || kind === "day_created") {
    return {
      id: 1,
      entity_id: "day_2026-07-04",
      kind,
      payload: kind === "day_ticket_added" || kind === "day_ticket_removed"
        ? { ticket_id: "t_demo" }
        : {},
      created_at: 1
    };
  }
  if (kind === "link_added" || kind === "link_removed") {
    return {
      id: 1,
      entity_id: "t_demo",
      kind,
      payload: { from_id: "t_demo", to_id: "si_demo", kind: "blocks" },
      created_at: 1
    };
  }
  return { id: 1, entity_id: "t_demo", kind, payload: {}, created_at: 1 };
}

for (const kind of kinds) {
  const keys = keysForEvent(sampleEvent(kind), {
    todayId: "day_2026-07-04"
  });
  assert.ok(keys.length >= 1, `${kind} mapped to no resource keys`);
}

assert.throws(
  () =>
    keysForEvent({
      id: 1,
      entity_id: "x_demo",
      kind: "ticket_updated",
      payload: {},
      created_at: 1
    }),
  /unknown entity_id prefix/
);

assert.deepEqual(
  keysForEvent({
    id: 1,
    entity_id: "project_alpha_one",
    kind: "project_created",
    payload: {},
    created_at: 1
  }),
  ["projects"]
);

assert.deepEqual(
  keysForEvent({
    id: 1,
    entity_id: "agent_panels_chief_of_staff",
    kind: "chat_turn_started",
    payload: { turn_id: "run_demo" },
    created_at: 1
  }),
  ["chat:agent_panels_chief_of_staff"]
);

assert.deepEqual(
  keysForEvent({
    id: 1,
    entity_id: "t_left",
    kind: "link_added",
    payload: { from_id: "t_left", to_id: "si_right", kind: "blocks" },
    created_at: 1
  }).sort(),
  ["board", "chat:t_left", "item:si_right", "sprint:current", "ticket:t_left"].sort()
);

assert.deepEqual(
  keysForEvent({
    id: 2,
    entity_id: "t_deleted",
    kind: "ticket_deleted",
    payload: { ticket_id: "t_deleted", title: "Mistake", actor: "human" },
    created_at: 2
  }).sort(),
  ["board", "chat:t_deleted", "review", "sprint:current", "ticket:t_deleted"].sort()
);

assert.deepEqual(
  keysForEvent({
    id: 3,
    entity_id: "t_source",
    kind: "stage_changed",
    payload: {
      affected_blocked_target_ids: ["t_blocked", "si_blocked"]
    },
    created_at: 3
  }).sort(),
  [
    "board",
    "chat:t_source",
    "item:si_blocked",
    "review",
    "sprint:current",
    "ticket:t_blocked",
    "ticket:t_source"
  ].sort()
);

for (const kind of ["day_ticket_added", "day_ticket_removed"]) {
  assert.deepEqual(
    keysForEvent({
      id: 4,
      entity_id: "day_2026-07-04",
      kind,
      payload: { ticket_id: "t_demo" },
      created_at: 4
    }, { todayId: "day_2026-07-04" }).sort(),
    ["board", "day:2026-07-04", "day:today", "review", "ticket:t_demo"].sort()
  );

  const knownOffDay = keysForEvent({
    id: 5,
    entity_id: "day_2026-07-03",
    kind,
    payload: { ticket_id: "t_demo" },
    created_at: 5
  }, { todayId: "day_2026-07-04" });
  assert.ok(!knownOffDay.includes("review"), `${kind} invalidated Review for a known off-day`);

  const coldStart = keysForEvent({
    id: 6,
    entity_id: "day_2026-07-04",
    kind,
    payload: { ticket_id: "t_demo" },
    created_at: 6
  }, { todayId: null, includeTodayAlias: true });
  assert.ok(coldStart.includes("review"), `${kind} missed Review during cold start`);
}

for (const kind of [
  "stage_changed",
  "proposal_accepted",
  "proposal_superseded",
  "proposal_filed",
  "kickoff_proposal_filed",
  "kickoff_accepted",
  "approval_returned",
  "ticket_status_changed",
  "ticket_deleted"
]) {
  assert.ok(
    keysForEvent(sampleEvent(kind)).includes("review"),
    `${kind} did not invalidate Review`
  );
}

assert.ok(!keysForEvent(sampleEvent("ticket_created")).includes("review"));
assert.ok(keysForEvent(sampleEvent("proposal_filed")).includes("review"));
assert.ok(keysForEvent(sampleEvent("ticket_status_changed")).includes("review"));

for (const kind of ["sprint_item_created", "item_updated", "item_children_changed"]) {
  assert.ok(!keysForEvent(sampleEvent(kind)).includes("review"));
}

for (const kind of [
  "chat_session_created",
  "chat_message_recorded",
  "chat_turn_started",
  "chat_turn_updated",
  "chat_turn_finished",
  "note_updated",
  "recap_updated",
  "scope_changed",
  "field_value_edited",
  "link_added",
  "link_removed"
]) {
  assert.ok(!keysForEvent(sampleEvent(kind)).includes("review"), `${kind} invalidated Review`);
}

assert.ok(keysForEvent({
  id: 7,
  entity_id: "t_demo",
  kind: "ticket_updated",
  payload: { field: "title" },
  created_at: 7
}).includes("review"));
assert.ok(!keysForEvent({
  id: 8,
  entity_id: "t_demo",
  kind: "ticket_updated",
  payload: { field: "priority" },
  created_at: 8
}).includes("review"));

for (const kind of kinds) {
  assert.ok(!keysForEvent(sampleEvent(kind)).includes("queues"), `${kind} emitted queues`);
}

function between(source, start, end) {
  return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
}

const ticketRoute = readFileSync(new URL("../src/routes/TicketRoute.svelte", import.meta.url), "utf8");
const patchSource = between(ticketRoute, "function patch", "function saveScope");
const scopeSource = between(ticketRoute, "function saveScope", "function saveNote");
const noteSource = between(ticketRoute, "function saveNote", "function saveValue");
const valueSource = between(ticketRoute, "function saveValue", "function acceptField");
const acceptSource = between(ticketRoute, "function acceptField", "function writeClipboard");
const takeoverSource = between(ticketRoute, "async function takeover", "function sprintLabel");
assert.match(patchSource, /"title" in body/);
assert.match(patchSource, /reviewTicketInvalidations/);
assert.doesNotMatch(scopeSource, /reviewTicketInvalidations|"review"/);
assert.doesNotMatch(noteSource, /reviewTicketInvalidations|"review"/);
assert.doesNotMatch(valueSource, /reviewTicketInvalidations|"review"/);
assert.match(acceptSource, /reviewTicketInvalidations/);
assert.match(takeoverSource, /reviewTicketInvalidations/);
assert.match(ticketRoute, /\/recap[\s\S]*?baseTicketInvalidations/);

const backlogRoute = readFileSync(new URL("../src/routes/BacklogRoute.svelte", import.meta.url), "utf8");
assert.doesNotMatch(backlogRoute, /"queues"|"review"/);

const wsSource = readFileSync(new URL("../src/lib/ws.ts", import.meta.url), "utf8");
assert.equal((wsSource.match(/const cachedTodayId = todayId\(\)/g) || []).length, 1);
assert.match(
  wsSource,
  /keysForEvent\(plannerEvent, \{\s*todayId: cachedTodayId,\s*includeTodayAlias: cachedTodayId === null\s*\}\)/
);
