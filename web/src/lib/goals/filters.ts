import type {GoalResponse} from './contracts';
import {selectedChecklist,checklistCounts} from './checklistCompletion';
export type NumericFilter={op:'any'|'gte'|'lte'|'eq'|'missing'|'present';value:string};
export type GoalFilters={goal:string;current:NumericFilter;target:NumericFilter;project:string;due:{from:string;to:string};state:string;completion:string};
export const emptyFilters=():GoalFilters=>({goal:'',current:{op:'any',value:''},target:{op:'any',value:''},project:'all',due:{from:'',to:''},state:'all',completion:'all'});
export const columns=[['goal','Goal'],['current','Current'],['target','Target'],['project','Project'],['due','Due'],['state','State']] as const;
export type GoalColumn=typeof columns[number][0];
export function numberMatches(value:number|undefined,filter:NumericFilter){if(filter.op==='any')return true;const valid=value!==undefined&&Number.isFinite(value);if(filter.op==='missing')return !valid;if(filter.op==='present')return valid;if(!valid)return false;const threshold=Number(filter.value);return filter.op==='gte'?value>=threshold:filter.op==='lte'?value<=threshold:value===threshold;}
export function goalMatches(goal:GoalResponse,current:number|undefined,f:GoalFilters,resolved?:{target:number|undefined}){
 const checklist=goal.kind==='binary'?selectedChecklist(goal.page,goal.primary_checklist_block_id):null;
 const counts=checklist?checklistCounts(checklist):null;
 return goal.title.toLowerCase().includes(f.goal.toLowerCase())&&numberMatches(counts?counts.checked:current,f.current)&&numberMatches(counts?counts.total:resolved?resolved.target:goal.primary_chart?.target,f.target)&&(f.project==='all'||goal.project_id===f.project)&&(f.state==='all'||goal.active===(f.state==='active'))&&(f.completion==='all'||goal.completed===(f.completion==='done'))&&(!f.due.from||!!goal.deadline&&goal.deadline>=f.due.from)&&(!f.due.to||!!goal.deadline&&goal.deadline<=f.due.to);
}
export function filterActive(key:GoalColumn,f:GoalFilters){if(key==='current'||key==='target')return f[key].op!=='any';if(key==='due')return !!(f.due.from||f.due.to);if(key==='state')return f.state!=='all'||f.completion!=='all';return key==='goal'?!!f.goal:f.project!=='all';}
