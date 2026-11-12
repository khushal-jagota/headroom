import type {GoalResponse} from './contracts';
export function goalsForHolder<T extends Pick<GoalResponse,'holder_kind'|'holder_id'>>(goals:readonly T[],holder:{kind:'chief'|'manager';id:string|null}){
 const held=goals.filter(goal=>goal.holder_kind===holder.kind&&(holder.kind==='chief'||goal.holder_id===holder.id));
 return {held,sole:held.length===1?held[0]:null};
}

/** A holder's rail window asks for every goal it holds, up to this many rows. */
export const GOAL_RAIL_WINDOW_LIMIT = 60;
type GoalRailWindow = {request:{limit:number};total:number};
/** The goal list and the navigation answer are separate reads, and either can lag.
 * The window's own total comes back in the same answer as its rows, so a window
 * sized short by a lagging list is resized from that answer. */
export function goalRailWindowLimit(heldCount:number,window:GoalRailWindow|undefined):number{
 return Math.min(GOAL_RAIL_WINDOW_LIMIT,Math.max(heldCount,window?.total??0));
}
/** The answer itself reports that the window asked for fewer rows than its holder has. */
export function goalRailWindowShort(window:GoalRailWindow|undefined):boolean{
 return Boolean(window&&window.request.limit<Math.min(GOAL_RAIL_WINDOW_LIMIT,window.total));
}
