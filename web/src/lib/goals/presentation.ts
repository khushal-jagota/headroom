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
/** A saved goal result owns goal-level signals; checklist item dates use deadlineTone. */
export function goalDeadlineTone(goal:Pick<GoalResponse,'deadline'|'completed'|'outcome'|'status_date'>):GoalResponse['status'] {
 if(!goal.deadline)return 'neutral';
 if(goal.outcome==='hit')return 'good';
 if(goal.outcome==='miss')return 'bad';
 if(goal.completed)return 'neutral';
 return deadlineTone(goal.deadline,false,goal.status_date);
}
export function goalResultTone(goal:Pick<GoalResponse,'deadline'|'completed'|'outcome'|'status_date'>):GoalResponse['status'] {
 return goalDeadlineTone(goal);
}
export function goalStatusText(goal:GoalResponse):string {
 if(goal.completed){
  if(goal.outcome==='hit')return 'Completed · Hit';
  if(goal.outcome==='miss')return 'Completed late · Miss';
  return goal.outcome==='unknown'?'Complete · result unknown':'Complete';
 }
 if(goal.outcome==='miss')return 'Open · Miss';
 return goal.deadline===goal.status_date?'Open · Due today':'Open';
}
/** The approved pages write a deadline as "Dec 31". Midday avoids a timezone day shift. */
export function goalDateText(deadline:string|null|undefined):string|null {
 if(!deadline)return null;
 const date=new Date(deadline+'T12:00:00');
 return Number.isNaN(date.getTime())?deadline:date.toLocaleDateString('en-US',{month:'short',day:'numeric'});
}
/** Completion dates are saved calendar dates, not timestamps in the browser's zone. */
export function goalCompletionText(goal:Pick<GoalResponse,'completed'|'completion_date'>):string {
 if(!goal.completed)return 'Open';
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
