<script lang="ts">
 import {setContext,untrack} from 'svelte';
 import {createQuery} from '@tanstack/svelte-query';
 import {goalQueries} from '../../lib/goals/queries';
 import {resolveGoalPrimaryDisplay} from '../../lib/goals/primaryDisplay';
 import {createGoalEditor} from '../../lib/goals/editing';
 import {createGoalPageContext} from '../../lib/goals/pageContext';
 import {refreshAtBoundary} from '../../lib/goals/presentation';
 import type {createGoalRequestSessions} from '../../lib/goals/requestSessions';
 import type {GoalResponse} from '../../lib/goals/contracts';
 import {PAGE_CONTEXT,type PageContext} from '../../lib/pageKit/context';
 import PageChecklist from '../pageKit/PageChecklist.svelte';
 import ErrorLine from '../ErrorLine.svelte';
 let {initialGoal,sessions}:{initialGoal:GoalResponse;sessions:ReturnType<typeof createGoalRequestSessions>}=$props();
 // The parent keys this host by goal identity, so a late save keeps its original goal.
 const goalId=untrack(()=>initialGoal.id);
 const editor=createGoalEditor(goalId);
 const detail=createQuery(()=>goalQueries.detail(goalId));
 let goal=$derived(detail.data??initialGoal);
 let checklist=$derived(resolveGoalPrimaryDisplay(goal).checklist);
 setContext<PageContext>(PAGE_CONTEXT,createGoalPageContext({
  goalId:()=>goalId,goal:()=>goal,readings:()=>undefined,narrow:()=>true,sessions:()=>sessions,
 },{updateBlock:editor.updateBlock}));
 $effect(()=>refreshAtBoundary(goal.next_refresh_at,()=>void detail.refetch()));
</script>
{#if checklist}
 {#key checklist.id}<PageChecklist block={checklist} presentation="small" supplementary/>{/key}
{/if}
{#if detail.error}<ErrorLine error={detail.error}/>{/if}
