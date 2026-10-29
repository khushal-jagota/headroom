<script lang="ts">
 import {createQuery,createQueries} from '@tanstack/svelte-query';
 import type {GoalResponse} from '../../lib/goals/contracts';
 import GoalFilter from './GoalFilter.svelte';
 import {columns,emptyFilters,goalMatches} from '../../lib/goals/filters';
 import {selectedChecklist,checklistCounts} from '../../lib/goals/checklistCompletion';
 import {queries} from '../../lib/queryCatalogue';
 import {chartData,chartHeadline,selectedTarget} from '../../lib/charts/presentation';
 import {goalQueries} from '../../lib/goals/queries';
 import {goalStatusText,refreshAtBoundary,goalDateText} from '../../lib/goals/presentation';
 import {workspaceAddress} from '../../lib/workspaceAddress';
 import GoalReading from './GoalReading.svelte';
 import {formatReading} from '../../lib/charts/presentation';
 import GoalCard from './GoalCard.svelte';
 import ErrorLine from '../ErrorLine.svelte';
 let {all=false}:{all?:boolean}=$props();
 const goals=createQuery(()=>goalQueries.list());
 const projects=createQuery(()=>queries.projects());
 const readings=createQueries(()=>({queries:(goals.data?.goals??[]).map(goal=>({...goalQueries.readings(goal.id),enabled:all&&goal.kind==='quantitative'}))}));
 let filters=$state(emptyFilters());
 let summaries=$derived((goals.data?.goals??[]).map((goal,i)=>goal.primary_chart?chartHeadline(goal.primary_chart,chartData(goal.primary_chart,readings[i]?.data?.values??[],'all',readings[i]?.data?.charts?.[goal.primary_chart.id]),null,readings[i]?.data?.charts?.[goal.primary_chart.id]):undefined));
 let filtered=$derived((goals.data?.goals??[]).filter((goal,i)=>goalMatches(goal,summaries[i]?.value,filters,{target:target(goal)})));
 /** Live targets use the same supplied summary as the current reading. */
 function target(goal:GoalResponse){const index=goals.data?.goals.findIndex(item=>item.id===goal.id)??-1;return goal.primary_chart?.binding||goal.primary_chart?.expectation?summaries[index]?.target:goal.primary_chart?selectedTarget(goal.primary_chart,'all'):undefined;}
 function targetText(goal:GoalResponse){
  const checklist=goal.kind==='binary'?selectedChecklist(goal.page,goal.primary_checklist_block_id):null;
  if(checklist)return `${checklistCounts(checklist).total} items`;
  const value=goal.primary_chart?target(goal):undefined;
  if(value===undefined)return goal.kind==='binary'?'Done':'—';
  const direction=goal.primary_chart!.direction??(goal.primary_chart!.kind==='time-budget'?'atmost':'atleast');
  return `${direction==='atmost'?'≤':'≥'} ${formatReading(goal.primary_chart!,value)}`;
 }
 function projectName(id:string){return projects.data?.projects.find(project=>project.id===id)?.name??'';}
 $effect(()=>{if(goals.data)return refreshAtBoundary(goals.data.next_refresh_at,()=>void goals.refetch());});
</script>
<section class="goals-overview" aria-label={all?'All goals':'Active goals'}>
 {#if all}<nav class="breadcrumb" aria-label="Breadcrumb"><a href={workspaceAddress({kind:'chief'})}>Chief of Staff</a><span>/</span><span aria-current="page">All goals</span></nav>
 <header><h1>All goals</h1></header>
 {:else}<header><h2>Active goals</h2><a href={workspaceAddress({kind:'goals'})}>All goals</a></header>{/if}
 {#if goals.error}<ErrorLine error={goals.error}/>{/if}
 {#if all}
 <div class="table-scroll"><table><thead><tr>{#each columns as [key,label]}<th scope="col" class:numeric={key==='current'||key==='target'}><GoalFilter column={key} {label} {filters} projects={projects.data?.projects??[]} onApply={next=>filters=next}/></th>{/each}</tr></thead><tbody>{#each filtered as goal(goal.id)}<tr><td><a href={workspaceAddress({kind:'goal',id:goal.id})}>{goal.title}</a></td><td class="numeric"><GoalReading {goal} reading/></td><td class="numeric target-cell">{targetText(goal)}</td><td>{projectName(goal.project_id)||'—'}</td><td>{goalDateText(goal.deadline)??'—'}</td><td class="state-cell">{goal.active?'Active':'Inactive'}</td></tr>{:else}<tr><td colspan="6"><div class="empty">{goals.data?.goals.length?'No matching goals':'No goals yet'}{#if goals.data?.goals.length}<button onclick={()=>filters=emptyFilters()}>Clear filters</button>{:else}<p>Set your first goal with Chief.</p>{/if}</div></td></tr>{/each}</tbody></table></div>
 <span class="sr-only" role="status">{filtered.length} goals match the filters</span>
 {:else}<div class="goals-grid">{#each goals.data?.goals.filter(g=>g.active)??[] as goal(goal.id)}<GoalCard {goal} projectName={projectName(goal.project_id)}/>{:else}<div class="empty"><h2>No active goals</h2><p>Talk with Chief to set your next goal.</p></div>{/each}</div>{/if}
</section>
<style>
header{display:flex;align-items:center;gap:var(--space-5);margin-bottom:20px;min-height:0}h1{margin:0;font-size:28px;font-weight:600;letter-spacing:-.025em;line-height:1.2;color:var(--text-strong)}header:has(h1){margin-bottom:22px;min-height:38px}header h2{margin:0;font-size:22px;font-weight:600;letter-spacing:-.015em;line-height:1.3}header a{margin-left:auto;font-size:13px;color:var(--text-muted);text-decoration:none}.breadcrumb{display:flex;gap:10px;align-items:center;color:var(--text-faintest);font-size:13px;line-height:1.45;margin-bottom:26px}.breadcrumb a{color:inherit;text-decoration:none}.goals-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.table-scroll{overflow:auto}table{min-width:740px;table-layout:fixed;border-collapse:collapse;width:100%;text-align:left;font-size:13px}th{font-size:12px;font-weight:400;color:var(--text-faintest);padding:0 10px;height:46px;vertical-align:middle;border-bottom:1px solid var(--border-color)}th:first-child{width:254px;padding-left:8px}th:nth-child(2){width:104px}th:nth-child(3){width:114px}th:nth-child(4){width:92px}th:nth-child(5){width:88px}th:nth-child(6){width:88px}td{padding:20px 14px;border-bottom:1px solid var(--border-color);color:var(--text-muted);line-height:1.4;vertical-align:middle;font-variant-numeric:tabular-nums}td:first-child{padding-left:12px;color:var(--text-default)}tbody tr:last-child td{border-bottom:0}.numeric{text-align:right}td.numeric{color:var(--text-default)}td.target-cell,td.state-cell{color:var(--text-faintest)}td.state-cell{font-size:12px}@media (max-width:760px){td{padding:18px 12px}td:first-child{padding-left:12px}}.sr-only{position:absolute;width:1px;height:1px;clip-path:inset(50%);overflow:hidden}.empty button{display:block;margin:12px auto;color:var(--text-muted);background:none;border:0;text-decoration:underline;cursor:pointer}td a{color:var(--text-strong);text-decoration:none}.empty{padding:40px 0;color:var(--text-muted)}h2{font-size:20px;font-weight:500}.goals-overview{container-type:inline-size;margin-top:40px}.goals-overview:has(.breadcrumb){margin-top:0;padding-top:32px}@container(max-width:640px){.goals-grid{grid-template-columns:1fr}}
</style>
