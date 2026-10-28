import type {GoalResponse,Block} from './contracts';
export function goalStatusText(goal:GoalResponse):string {
 if(goal.kind!=='binary')return goal.status==='good'?'On target':goal.status==='bad'?'Below target':'No readings';
 return goal.completed?'Done':goal.status==='attention'?'Due today':goal.status==='bad'?'Overdue':'Not done';
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
