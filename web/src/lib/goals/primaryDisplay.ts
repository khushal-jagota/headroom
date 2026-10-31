import type {ChartBlock,ChecklistBlock,GoalResponse} from './contracts';
import {selectedChecklist} from './checklistCompletion';

/** Resolve saved display selection without changing legacy numeric reads. */
export function resolveGoalPrimaryDisplay(goal:GoalResponse|null|undefined):{checklist:ChecklistBlock|null;chart:ChartBlock|null}{
 if(!goal)return {checklist:null,chart:null};
 const checklist=goal.kind==='binary'?selectedChecklist(goal.page,goal.primary_checklist_block_id):null;
 return {
  checklist,
  chart:checklist?null:goal.kind==='binary'?{type:'chart',id:'completion',kind:'binary',placement:'primary'}:goal.primary_chart??null,
 };
}
