import type { OrchestratorChildFilter } from "./types";

/** Active retains all unfinished children. Backlog is an explicit subset. */
export function filterOrchestratorChildren<T extends { stage: string; is_backlogged: boolean }>(
  tickets: readonly T[], filter: OrchestratorChildFilter
): T[] {
  return tickets.filter((ticket) => {
    if (filter === "done") return ticket.stage === "done";
    if (ticket.stage === "done") return false;
    return filter !== "backlogged" || ticket.is_backlogged;
  });
}

/** Chief API order determines page membership before presentation groups reorder rows. */
export function chiefChildrenPage<T extends {id:string; created_at:number; terminal:boolean; stage:string; is_backlogged:boolean}>(
  tickets:readonly T[], filter:OrchestratorChildFilter, offset:number, limit=30
) {
  const matches=tickets.filter(ticket=>filter==='done' ? ticket.stage==='done'
    : !ticket.terminal && (filter!=='backlogged' || ticket.is_backlogged))
    .sort((left,right)=>left.created_at-right.created_at || (left.id<right.id ? -1 : left.id>right.id ? 1 : 0));
  const rows=matches.slice(offset,offset+limit);
  const before=Math.min(offset,matches.length);
  const after=Math.max(0,matches.length-offset-rows.length);
  return {tickets:rows,page:{match_count:matches.length,return_count:rows.length,limit,offset,
    omitted_before:before,omitted_after:after,complete:before===0 && after===0,
    next_offset:after ? offset+rows.length : null}};
}

/** Existing disclosure default; saved/manual choices take precedence. */
export const orchestratorGroupDefaultOpen = (key:string):boolean => key !== 'done';
