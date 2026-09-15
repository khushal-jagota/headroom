import type { BoardCard } from "../src/lib/types";

// A board card as the server sends it, for any test that needs one. The rail test
// builds its rows from this, and the group tests use it to check that the rail and the
// Sprint Item page name the same Ticket the same way.
export function boardCard(id: string, values: Partial<BoardCard> = {}): BoardCard {
  return {
    id,
    title: id,
    priority: "P3",
    deadline: null,
    project_id: null,
    project: null,
    group_project_id: null,
    group_project: null,
    activity_at: 0,
    has_pending_proposal: false,
    ticket_status: "empty",
    backend_error: null,
    worker_type: "coding",
    employee_backend: "codex",
    stage: "needs_implementation",
    stage_label: "Implementation",
    gating_field: "implementation",
    gating_field_label: "Implementation",
    is_done: false,
    is_dropped: false,
    blocked: false,
    conversation_id: null,
    waiting_to_closeout: false,
    sprint_item_id: null,
    sprint_item_title: null,
    sprint_item_priority: null,
    awaiting_reply: false,
    awaiting_approval: false,
    assigned: false,
    agent_state: "idle",
    ...values
  };
}
