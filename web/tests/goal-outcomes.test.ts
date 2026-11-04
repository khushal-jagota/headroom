import {render} from 'svelte/server';
import {describe,expect,it} from 'vitest';
import type {GoalResponse,GoalOutcomeSummary} from '../src/lib/goals/contracts';
import GoalOutcomeView from '../src/components/goals/GoalOutcome.svelte';
import GoalHitRate from '../src/components/goals/GoalHitRate.svelte';
import {deadlineTone,goalDeadlineTone,goalResultTone,goalStatusText} from '../src/lib/goals/presentation';

function outcome(completed:boolean,completion_date:string|null,result:GoalResponse['outcome']):string{
 return render(GoalOutcomeView,{props:{goal:{completed,completion_date,outcome:result}}}).body;
}

describe('saved goal outcomes',()=>{
 it('shows completion separately from a missed deadline and preserves unknown history',()=>{
  const late=outcome(true,'2026-11-04','miss');
  expect(late).toContain('Completed Nov 4, 2026');
  expect(late).toContain('Miss');
  expect(late).not.toContain('Open');
  const legacy=outcome(true,null,'unknown');
  expect(legacy).toContain('Complete · date unknown');
  expect(legacy).toContain('Result unknown');
  expect(legacy).not.toContain('Completed Nov');
 });

 it('uses the recorded goal result instead of binary status or the current numeric comparison',()=>{
  const saved={deadline:'2026-07-02',status_date:'2026-07-04',status:'good',completion_date:'2026-07-03'};
  for(const kind of ['binary','quantitative'] as const){
   const late={...saved,kind,completed:true,outcome:'miss'} as GoalResponse;
   expect(goalStatusText(late)).toBe('Completed late · Miss');
   expect(goalDeadlineTone(late)).toBe('bad');
   expect(goalResultTone(late)).toBe('bad');
   const hit={...late,outcome:'hit',completion_date:'2026-07-02',status:'bad'} as GoalResponse;
   expect(goalStatusText(hit)).toBe('Completed · Hit');
   expect(goalDeadlineTone(hit)).toBe('good');
   expect(goalResultTone(hit)).toBe('good');
   const open={...late,completed:false,completion_date:null};
   expect(goalStatusText(open)).toBe('Open · Miss');
   expect(goalDeadlineTone(open)).toBe('bad');
   const pending={...open,outcome:'pending',deadline:'2026-07-05'} as GoalResponse;
   expect(goalStatusText(pending)).toBe('Open');
   expect(goalDeadlineTone(pending)).toBe('neutral');
   const legacy={...late,outcome:'unknown',completion_date:null} as GoalResponse;
   expect(goalStatusText(legacy)).toBe('Complete · result unknown');
   expect(goalDeadlineTone(legacy)).toBe('neutral');
  }
  // Checklist-item completion still colors that item's own date green.
  expect(deadlineTone('2026-07-02',true,'2026-07-04')).toBe('good');
  expect(outcome(false,null,'miss')).toContain('Open');
 });

 it('renders the server rate instead of reconstructing it from counts or visible goals',()=>{
  // The backend owns the aggregate across active and inactive goals. No filtered
  // goal list enters this component, and display rounding retains its rate.
  const summary:GoalOutcomeSummary={hits:1,misses:2,rate:1/3};
  const html=render(GoalHitRate,{props:{summary}}).body;
  expect(html).toContain('33.3%');
  expect(html).toContain('1 hit · 2 misses');
  const empty=render(GoalHitRate,{props:{summary:{hits:0,misses:0,rate:null}}}).body;
  expect(empty).toContain('—');
  expect(empty).not.toContain('0%');
 });
});
