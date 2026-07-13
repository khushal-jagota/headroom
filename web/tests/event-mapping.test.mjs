import assert from "node:assert/strict";
import { keysForEvent } from "../src/lib/eventMapping.mjs";

const FALLBACK_KINDS = [
  "state_changed",
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
  ["board", "chat:t_left", "item:si_right", "queues", "sprint:current", "ticket:t_left"].sort()
);

assert.deepEqual(
  keysForEvent({
    id: 2,
    entity_id: "t_deleted",
    kind: "ticket_deleted",
    payload: { ticket_id: "t_deleted", title: "Mistake", actor: "human" },
    created_at: 2
  }).sort(),
  ["board", "chat:t_deleted", "queues", "sprint:current", "ticket:t_deleted"].sort()
);

assert.deepEqual(
  keysForEvent({
    id: 3,
    entity_id: "t_source",
    kind: "state_changed",
    payload: {
      affected_blocked_target_ids: ["t_blocked", "si_blocked"]
    },
    created_at: 3
  }).sort(),
  [
    "board",
    "chat:t_source",
    "item:si_blocked",
    "queues",
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
    ["board", "day:2026-07-04", "day:today", "queues", "ticket:t_demo"].sort()
  );
}
