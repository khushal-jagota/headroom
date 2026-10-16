import { WORK_ITEM_TICKET_GROUPS, workItemTicketGroupKey, workItemTicketOrder } from "./workItemPresentation";
import type { SprintItemWorkspaceTicket } from "./types";

/** The complete page needs no duplicate message bucket. Classification alone ignores
 * replies; original attention facts remain on every row and unread count. */
export function orchestratorPageGroups(tickets: readonly SprintItemWorkspaceTicket[]) {
  const definitions = [...WORK_ITEM_TICKET_GROUPS.filter((group) => group.key !== "awaiting_reply"),
    {key:"cancelled", label:"Cancelled", quiet:true}];
  return definitions.flatMap((definition) => {
    const rows = tickets.filter((ticket) => {
      const key = ticket.stage === "cancelled" ? "cancelled"
        : workItemTicketGroupKey({...ticket, awaiting_reply:false});
      return key === definition.key;
    }).sort(workItemTicketOrder);
    return rows.length ? [{...definition, tickets:rows}] : [];
  });
}
