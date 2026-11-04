import {render} from 'svelte/server';
import {describe,expect,it} from 'vitest';
import type {GoalResponse,GoalOutcomeSummary} from '../src/lib/goals/contracts';
import GoalOutcomeView from '../src/components/goals/GoalOutcome.svelte';
import GoalHitRate from '../src/components/goals/GoalHitRate.svelte';

function outcome(completed:boolean,completion_date:string|null,result:GoalResponse['outcome']):string{
 return render(GoalOutcomeView,{props:{goal:{completed,completion_date,outcome:result}}}).body;
}

describe('saved goal outcomes',()=>{
 it('shows completion separately from a missed deadline and preserves unknown history',()=>{
  const late=outcome(true,'2026-11-04','miss');
  expect(late).toContain('Completed Nov 4, 2026');
  expect(late).toContain('Miss');
  expect(late).not.toContain('Incomplete');
  const legacy=outcome(true,null,'unknown');
  expect(legacy).toContain('Complete · date unknown');
  expect(legacy).toContain('Result unknown');
  expect(legacy).not.toContain('Completed Nov');
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
