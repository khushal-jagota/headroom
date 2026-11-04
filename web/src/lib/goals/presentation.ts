import type {GoalResponse,Block} from './contracts';
/** The server calendar owns date status. Undated values never acquire a tone. */
export function deadlineTone(deadline:string|null|undefined,completed:boolean,today:string|undefined):GoalResponse['status'] {
 if(!deadline||!today)return 'neutral';
 if(completed)return 'good';
 return deadline<today?'bad':deadline===today?'attention':'neutral';
}
export function deadlineColor(tone:GoalResponse['status']):string {
 return tone==='bad'?'var(--accent-error)':tone==='attention'?'var(--accent-attention)':tone==='good'?'var(--accent-done)':'var(--text-muted)';
}
export function goalStatusText(goal:GoalResponse):string {
 if(goal.kind!=='binary')return goal.status==='good'?'On target':goal.status==='bad'?'Below target':'No readings';
 return goal.completed?'Done':goal.status==='attention'?'Due today':goal.status==='bad'?'Overdue':'Not done';
}
/** The approved pages write a deadline as "Dec 31". Midday avoids a timezone day shift. */
export function goalDateText(deadline:string|null|undefined):string|null {
 if(!deadline)return null;
 const date=new Date(deadline+'T12:00:00');
 return Number.isNaN(date.getTime())?deadline:date.toLocaleDateString('en-US',{month:'short',day:'numeric'});
}
/** Completion dates are saved calendar dates, not timestamps in the browser's zone. */
export function goalCompletionText(goal:Pick<GoalResponse,'completed'|'completion_date'>):string {
 if(!goal.completed)return 'Incomplete';
 if(!goal.completion_date)return 'Complete · date unknown';
 const date=new Date(goal.completion_date+'T12:00:00');
 const label=Number.isNaN(date.getTime())?goal.completion_date:date.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'});
 return `Completed ${label}`;
}
export function goalOutcomeText(outcome:GoalResponse['outcome']):string {
 return {hit:'Hit',miss:'Miss',pending:'Pending',undated:'No deadline',unknown:'Result unknown'}[outcome];
}
export function updatePageBlock(blocks:Block[],id:string,update:(block:Block)=>Block):Block[]{
 return blocks.map(block=>{
  if(block.id===id)return update(block);
  if(block.type==='grid')return {...block,blocks:updatePageBlock(block.blocks,id,update)};
  if(block.type==='section')return {...block,...(block.blocks?{blocks:updatePageBlock(block.blocks,id,update)}:{}),...(block.views?{views:block.views.map(v=>({...v,blocks:updatePageBlock(v.blocks,id,update)}))}:{})};
  if(block.type==='segmented')return {...block,views:block.views.map(v=>({...v,blocks:updatePageBlock(v.blocks,id,update)}))};
  return block;
 });
}
/** The server owns its calendar. Recheck at its next local midnight and after suspension. */
export function refreshAtBoundary(timestamp:number,refresh:()=>void):()=>void {
 const timer=window.setTimeout(refresh,Math.max(0,timestamp*1000-Date.now())+50);
 const visible=()=>{if(document.visibilityState==='visible')refresh();};
 document.addEventListener('visibilitychange',visible);
 return()=>{window.clearTimeout(timer);document.removeEventListener('visibilitychange',visible);};
}
