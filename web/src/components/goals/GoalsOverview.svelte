<script lang="ts">
 import {createQuery,createQueries} from '@tanstack/svelte-query';
 import type {GoalResponse} from '../../lib/goals/contracts';
 import GoalFilter from './GoalFilter.svelte';
 import {columns,emptyFilters,goalMatches} from '../../lib/goals/filters';
 import {checklistCounts} from '../../lib/goals/checklistCompletion';
 import {resolveGoalPrimaryDisplay} from '../../lib/goals/primaryDisplay';
 import {queries} from '../../lib/queryCatalogue';
 import {chartData,chartHeadline,selectedTarget} from '../../lib/charts/presentation';
 import {goalQueries} from '../../lib/goals/queries';
 import {createGoalRequestSessions} from '../../lib/goals/requestSessions';
 import {goalDateText,deadlineTone,deadlineColor} from '../../lib/goals/presentation';
 import {workspaceAddress} from '../../lib/workspaceAddress';
 import GoalReading from './GoalReading.svelte';
 import {formatReading} from '../../lib/charts/presentation';
 import {goalsForHolder} from '../../lib/goals/holders';
 import GoalCard from './GoalCard.svelte';
 import ManagerHeader from '../ManagerHeader.svelte';
 import ErrorLine from '../ErrorLine.svelte';
  let {goals,all=false,holderKind='chief',holderId=null}:{goals:GoalResponse[];all?:boolean;holderKind?:'chief'|'manager';holderId?:string|null}=$props();
 const sessions=createGoalRequestSessions();
 let held=$derived(all?goals:goalsForHolder(goals,{kind:holderKind,id:holderId}).held);
 const projects=createQuery(()=>queries.projects());
 const managers=createQuery(()=>({...queries.managers(),enabled:all}));
 function goalName(goal:GoalResponse){return goal.holder_kind==='manager'&&goalsForHolder(goals,{kind:'manager',id:goal.holder_id}).sole?managers.data?.managers.find(manager=>manager.id===goal.holder_id)?.title??'Manager':goal.title;}
 const readings=createQueries(()=>({queries:goals.map(goal=>({...goalQueries.readings(goal.id),enabled:all&&goal.kind==='quantitative'}))}));
 let filters=$state(emptyFilters());
 let summaries=$derived(goals.map((goal,i)=>goal.primary_chart?chartHeadline(goal.primary_chart,chartData(goal.primary_chart,readings[i]?.data?.values??[],'all',readings[i]?.data?.charts?.[goal.primary_chart.id]),null,readings[i]?.data?.charts?.[goal.primary_chart.id]):undefined));
 let filtered=$derived.by(()=>{void managers.data;return held.filter(goal=>goalMatches({...goal,title:goalName(goal)},summaries[goals.findIndex(item=>item.id===goal.id)]?.value,filters,{target:target(goal)}));});
 /** Live targets use the same supplied summary as the current reading. */
 function target(goal:GoalResponse){const index=goals.findIndex(item=>item.id===goal.id);return goal.primary_chart?.binding||goal.primary_chart?.expectation?summaries[index]?.target:goal.primary_chart?selectedTarget(goal.primary_chart,'all'):undefined;}
 function targetText(goal:GoalResponse){
  const checklist=resolveGoalPrimaryDisplay(goal).checklist;
  if(checklist)return `${checklistCounts(checklist).total} items`;
  const value=goal.primary_chart?target(goal):undefined;
  if(value===undefined)return goal.kind==='binary'?'Done':'—';
  const direction=goal.primary_chart!.direction??(goal.primary_chart!.kind==='time-budget'?'atmost':'atleast');
  return `${direction==='atmost'?'≤':'≥'} ${formatReading(goal.primary_chart!,value)}`;
 }
 function projectName(id:string){return projects.data?.projects.find(project=>project.id===id)?.name??'';}
</script>
<section class="goals-overview" aria-label={all?'All goals':'Goals'}>
 <!-- On a page the goal cards stand alone (owner): each names itself, so no 'Goals' heading. -->
 {#if all}<ManagerHeader>{#snippet title()}All goals{/snippet}</ManagerHeader>{/if}
 {#if all && managers.error}<ErrorLine error={managers.error}/>{/if}
 {#if all}
 <div class="table-scroll"><table><thead><tr>{#each columns as [key,label]}<th scope="col" class:numeric={key==='current'||key==='target'}><GoalFilter column={key} {label} {filters} projects={projects.data?.projects??[]} onApply={next=>filters=next}/></th>{/each}</tr></thead><tbody>{#each filtered as goal(goal.id)}<tr><td><a href={workspaceAddress({kind:'goal',id:goal.id})}>{goalName(goal)}</a></td><td class="numeric"><GoalReading {goal} reading/></td><td class="numeric target-cell">{targetText(goal)}</td><td>{projectName(goal.project_id)||'—'}</td><td data-deadline-tone={deadlineTone(goal.deadline,goal.completed,goal.status_date)} style:color={deadlineColor(deadlineTone(goal.deadline,goal.completed,goal.status_date))}>{goalDateText(goal.deadline)??'—'}</td><td class="state-cell">{goal.active?'Active':'Inactive'}</td></tr>{:else}{#if goals.length}<tr><td colspan="6"><div class="empty">No matching goals<button onclick={()=>filters=emptyFilters()}>Clear filters</button></div></td></tr>{/if}{/each}</tbody></table></div>
 <span class="sr-only" role="status">{filtered.length} goals match the filters</span>
 {:else}<div class="goals-grid">{#each held.filter(g=>g.active) as goal(goal.id)}<div><GoalCard {goal} {sessions} projectName={projectName(goal.project_id)}/></div>{/each}</div>{/if}
</section>
<style>
/* Two cards a row, sharing the row's height; a goal alone spans the row, never half of it. */.goals-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));align-items:stretch;gap:var(--space-3)}.goals-grid > div{min-width:0;display:flex;flex-direction:column}.goals-grid > div:only-child{grid-column:1 / -1}.goals-grid > div > :global(*){flex:1 1 auto}.table-scroll{overflow:auto}table{min-width:740px;table-layout:fixed;border-collapse:collapse;width:100%;text-align:left;font-size:var(--type-body)}th{font-size:var(--type-small);font-weight:var(--weight-small);color:var(--tone-tertiary);padding:0 10px;height:46px;vertical-align:middle;border-bottom:1px solid var(--border-color)}th:first-child{width:254px;padding-left:8px}th:nth-child(2){width:104px}th:nth-child(3){width:114px}th:nth-child(4){width:92px}th:nth-child(5){width:88px}th:nth-child(6){width:88px}td{padding:20px 14px;border-bottom:1px solid var(--border-color);color:var(--text-muted);line-height:1.4;vertical-align:middle;font-variant-numeric:tabular-nums}td:first-child{padding-left:12px;color:var(--text-default)}tbody tr:last-child td{border-bottom:0}.numeric{text-align:right}td.numeric{color:var(--text-default)}td.target-cell,td.state-cell{color:var(--text-faintest)}td.state-cell{font-size:var(--type-small)}@media (max-width:760px){td{padding:18px 12px}td:first-child{padding-left:12px}}.sr-only{position:absolute;width:1px;height:1px;clip-path:inset(50%);overflow:hidden}.empty button{display:block;margin:12px auto;color:var(--text-muted);background:none;border:0;text-decoration:underline;cursor:pointer}td a{color:var(--text-strong);text-decoration:none}.empty{padding:40px 0;color:var(--text-muted)}.goals-overview{container-type:inline-size;margin-top:var(--page-section-gap)}.goals-overview:has(:global(.manager-workspace-head)){margin-top:0}.goals-overview > :global(.manager-workspace-head){margin-bottom:var(--page-head-gap)}@container(max-width:640px){.goals-grid{grid-template-columns:1fr}}
</style>
