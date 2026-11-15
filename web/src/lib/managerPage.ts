import { workItemWorkGroups } from "./workItemPresentation";
import type { WorkItemTicketFacts } from "./workItemPresentation";

/** Page membership is complete; sidebar eligibility is a separate filter. */
export function managerPageGroups<T extends WorkItemTicketFacts>(tickets: readonly T[]) {
  return workItemWorkGroups(tickets);
}
