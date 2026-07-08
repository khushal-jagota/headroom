import assert from "node:assert/strict";
import { keysForEvent } from "../src/lib/eventMapping.mjs";

const FALLBACK_KINDS = [
  "state_changed",
  "proposal_accepted",
  "proposal_superseded",
  "day_ticket_removed",
  "ticket_created",
  "sprint_created",
  "sprint_item_created",
  "idea_created",
  "day_created",
  "proposal_filed",
  "note_updated",
  "recap_updated",
  "scope_changed",
  "field_value_edited",
  "ticket_updated",
  "item_updated",
  "sprint_updated",
  "day_updated",
  "item_status_changed",
  "day_ticket_added",
  "ticket_status_changed",
  "link_added",
  "link_removed",
  "chat_session_created"
];

const kinds = JSON.parse(process.env.PLANNER_EVENT_KINDS || JSON.stringify(FALLBACK_KINDS));
assert.ok(Array.isArray(kinds), "PLANNER_EVENT_KINDS must be a JSON array");
assert.ok(kinds.length > 0, "backend EventKind list is empty");

function sampleEvent(kind) {
  if (kind === "sprint_created" || kind === "sprint_updated") {
    return { id: 1, entity_id: "s_demo", kind, payload: {}, created_at: 1 };
  }
  if (
    kind === "sprint_item_created" ||
    kind === "item_updated" ||
    kind === "item_status_changed"
  ) {
    return { id: 1, entity_id: "si_demo", kind, payload: {}, created_at: 1 };
  }
  if (kind === "idea_created") {
    return { id: 1, entity_id: "idea_demo", kind, payload: {}, created_at: 1 };
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
      payload: { from_id: "t_demo", to_id: "si_demo", kind: "relates" },
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
    entity_id: "t_left",
    kind: "link_added",
    payload: { from_id: "t_left", to_id: "si_right", kind: "relates" },
    created_at: 1
  }).sort(),
  ["board", "chat:t_left", "item:si_right", "queues", "sprint:current", "ticket:t_left"].sort()
);
