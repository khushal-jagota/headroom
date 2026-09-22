import {
  WORK_ITEM_TICKET_GROUPS,
  workItemTicketGroupKey,
  type WorkItemTicketFacts,
  type WorkItemTicketGroupDefinition
} from "./workItemPresentation";

export type TicketStatusGroupDefinition = WorkItemTicketGroupDefinition;
export type TicketStatusGroupFacts = Omit<WorkItemTicketFacts, "id" | "title" | "priority">;

export const TICKET_STATUS_GROUPS = WORK_ITEM_TICKET_GROUPS;
export function ticketStatusGroupKey(ticket: TicketStatusGroupFacts) {
  return workItemTicketGroupKey({ id: "", title: "", priority: "P3", ...ticket });
}
