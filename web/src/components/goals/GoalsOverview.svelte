<script lang="ts">
 import {createQuery,createQueries} from '@tanstack/svelte-query';
 import GoalFilter from './GoalFilter.svelte';
 import {columns,emptyFilters,goalMatches} from '../../lib/goals/filters';
 import {queries} from '../../lib/queryCatalogue';
 import {chartData} from '../../lib/charts/presentation';
 import {goalQueries} from '../../lib/goals/queries';
 import {goalStatusText,refreshAtBoundary} from '../../lib/goals/presentation';
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
 let filtered=$derived((goals.data?.goals??[]).filter((g,i)=>goalMatches(g,g.primary_chart?chartData(g.primary_chart,readings[i]?.data?.values??[],'all').at(-1)?.values.actual:undefined,filters)));
 $effect(()=>{if(goals.data)return refreshAtBoundary(goals.data.next_refresh_at,()=>void goals.refetch());});
</script>
<section class="goals-overview" aria-label={all?'All goals':'Active goals'}>
 <header><h1>{all?'All goals':'Active goals'}</h1><a href={workspaceAddress(all?{kind:'chief'}:{kind:'goals'})}>{all?'Overview':'All goals'}</a></header>
 {#if goals.error}<ErrorLine error={goals.error}/>{/if}
 {#if all}
 <div class="table-scroll"><table><thead><tr>{#each columns as [key,label]}<th scope="col"><GoalFilter column={key} {label} {filters} projects={projects.data?.projects??[]} onApply={next=>filters=next}/></th>{/each}</tr></thead><tbody>{#each filtered as goal(goal.id)}<tr><td><a href={workspaceAddress({kind:'goal',id:goal.id})}>{goal.title}</a></td><td><GoalReading {goal} reading/></td><td>{goal.primary_chart?.target!==undefined?formatReading(goal.primary_chart,goal.primary_chart.target):goal.kind==='binary'?'Done':'—'}</td><td>{projects.data?.projects.find(project=>project.id===goal.project_id)?.name??'—'}</td><td>{goal.deadline??'—'}</td><td>{goal.active?'Active':'Inactive'}</td></tr>{:else}<tr><td colspan="6"><div class="empty">{goals.data?.goals.length?'No matching goals':'No goals yet'}{#if goals.data?.goals.length}<button onclick={()=>filters=emptyFilters()}>Clear filters</button>{:else}<p>Set your first goal with Chief.</p>{/if}</div></td></tr>{/each}</tbody></table></div>
 <span class="sr-only" role="status">{filtered.length} goals match the filters</span>
 {:else}<div class="goals-grid">{#each goals.data?.goals.filter(g=>g.active)??[] as goal(goal.id)}<GoalCard {goal}/>{:else}<div class="empty"><h2>No active goals</h2><p>Talk with Chief to set your next goal.</p></div>{/each}</div>{/if}
</section>
<style>
header{display:flex;align-items:center;gap:var(--space-5);margin-bottom:var(--space-6)}h1{margin:0;font-size:28px;font-weight:600;letter-spacing:-.025em;line-height:1.3}header a{margin-left:auto;font-size:13px;color:var(--text-muted);text-decoration:none}.goals-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.table-scroll{overflow:auto}table{min-width:740px;border-collapse:collapse;width:100%;text-align:left;font-size:13px}th{font-size:12px;font-weight:400;color:var(--text-faint)}td,th{padding:16px 12px;border-bottom:1px solid var(--border-color)}td:first-child{min-width:220px}th{padding:0 8px;height:46px}.sr-only{position:absolute;width:1px;height:1px;clip-path:inset(50%);overflow:hidden}.empty button{display:block;margin:12px auto;color:var(--text-muted);background:none;border:0;text-decoration:underline;cursor:pointer}td a{color:var(--text-strong);text-decoration:none}.empty{padding:40px 0;color:var(--text-muted)}h2{font-size:20px;font-weight:500}.goals-overview{container-type:inline-size;padding-top:32px}@container(max-width:640px){.goals-grid{grid-template-columns:1fr}}
</style>
