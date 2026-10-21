import { WORK_ITEM_TICKET_GROUPS, workItemTicketGroupKey, workItemTicketOrder } from "./workItemPresentation";
import type { WorkItemTicketFacts } from "./workItemPresentation";

/** The complete page needs no duplicate message bucket. Classification alone ignores
 * replies; original attention facts remain on every row and unread count. */
export function managerPageGroups<T extends WorkItemTicketFacts>(tickets: readonly T[]) {
  const definitions = WORK_ITEM_TICKET_GROUPS.filter((group) => group.key !== "awaiting_reply");
  return definitions.flatMap((definition) => {
    const rows = tickets.filter((ticket) => {
      const key = workItemTicketGroupKey({...ticket, awaiting_reply:false});
      return key === definition.key;
    }).sort(workItemTicketOrder);
    return rows.length ? [{...definition, tickets:rows}] : [];
  });
}
