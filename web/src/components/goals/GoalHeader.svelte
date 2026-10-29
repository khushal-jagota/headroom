<script lang="ts">
 import {createQuery} from '@tanstack/svelte-query';
 import {tick} from 'svelte';
 import type {GoalResponse} from '../../lib/goals/contracts';
 import {goalDateText} from '../../lib/goals/presentation';
 import {queries} from '../../lib/queryCatalogue';
 import ErrorLine from '../ErrorLine.svelte';
 let {goal,onSave,mark}:{goal:GoalResponse;onSave:(patch:Partial<GoalResponse>,revision?:number)=>Promise<unknown>;mark?:import('svelte').Snippet}=$props();
 const projects=createQuery(()=>queries.projects());
 let draft=$state<GoalResponse|null>(null),error=$state<unknown>(null),saving=$state(false),editButton=$state<HTMLButtonElement>(),titleInput=$state<HTMLTextAreaElement>();
 async function start(){draft=structuredClone($state.snapshot(goal));if(draft.primary_chart&&!draft.primary_chart.direction)draft.primary_chart.direction=draft.primary_chart.kind==='time-budget'?'atmost':'atleast';error=null;await tick();titleInput?.focus();}
 async function cancel(){draft=null;error=null;await tick();editButton?.focus();}
 async function save(){if(!draft)return;saving=true;try{await onSave({title:draft.title,why:draft.why,deadline:draft.deadline||null,project_id:draft.project_id,active:draft.active,primary_chart:draft.primary_chart},draft.revision);await cancel();}catch(e){error=e;}finally{saving=false;}}
</script>
<svelte:window onkeydown={e=>{if(draft&&e.key==='Escape'){e.preventDefault();void cancel();}}}/>
<header class="goal-heading">
 {#if draft}
 <form onsubmit={e=>{e.preventDefault();void save();}}>
 <div class="goal-context"><label>Project<select aria-label="Project" bind:value={draft.project_id}>{#each projects.data?.projects??[] as project}<option value={project.id}>{project.name}</option>{/each}</select></label><label>By<input type="date" aria-label="By date" bind:value={draft.deadline}/></label><label>State<select aria-label="State" bind:value={draft.active}><option value={true}>Active</option><option value={false}>Inactive</option></select></label><div class="actions"><button type="button" disabled={saving} onclick={cancel}>Cancel</button><button disabled={saving}>Save</button></div></div>
 <textarea bind:this={titleInput} class="title-input" aria-label="Goal title" required rows="1" bind:value={draft.title}></textarea><textarea class="why-input" aria-label="Why this goal matters" rows="2" bind:value={draft.why}></textarea>
 {#if draft.kind==='quantitative'&&draft.primary_chart}<div class="threshold"><span>Success threshold</span><select aria-label="Threshold direction" bind:value={draft.primary_chart.direction}><option value="atleast">At least</option><option value="atmost">At most</option></select><input aria-label="Success threshold" type="number" step="any" bind:value={draft.primary_chart.target}/><span>{draft.primary_chart.unit??draft.primary_chart.format?.suffix??''}</span></div>{/if}
 {#if error}<ErrorLine {error}/>{/if}</form>
 {:else}<div class="goal-context"><span class="goal-meta"><span>{projects.data?.projects.find(p=>p.id===goal.project_id)?.name??''}</span><span aria-hidden="true">·</span><span>{goal.deadline?`By ${goalDateText(goal.deadline)}`:'No deadline'}</span></span><span class="goal-state">{goal.active?'Active':'Inactive'}</span>{@render mark?.()}<button bind:this={editButton} class="edit" aria-label="Edit goal" onclick={start}>•••</button></div><h1>{goal.title}</h1><div class="why">{goal.why}</div>{/if}
</header>
<style>
.goal-heading{margin-bottom:28px}.goal-context{display:flex;align-items:center;gap:15px;margin-bottom:14px;color:var(--text-faint);font-size:12px;font-weight:500;line-height:1.4;flex-wrap:wrap}.goal-meta{display:flex;align-items:center;gap:9px}.goal-meta span:first-child{color:var(--text-muted)}.goal-state{color:var(--text-faintest)}.edit{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:var(--radius-sm)}.edit,.actions{margin-left:auto}h1,.title-input{font-size:34px;font-weight:600;line-height:1.18;letter-spacing:-.03em;margin:0;max-width:750px;color:var(--text-strong)}.why,.why-input{font-size:14px;font-weight:500;line-height:1.6;color:var(--text-faint);margin-top:12px;max-width:650px}@container (max-width:640px){.goal-context{gap:10px}.goal-meta{gap:7px}h1,.title-input{font-size:30px}}button{border:0;background:none;color:var(--text-muted);font:inherit;cursor:pointer}label{display:flex;align-items:center;gap:8px}.actions{display:flex;gap:12px}.actions button:last-child{color:var(--accent-bright)}input,select,textarea{font-family:inherit;background:var(--surface-recessed);color:var(--text-default);border:1px solid var(--border-color);padding:6px 8px;border-radius:var(--radius-sm)}textarea{display:block;width:100%;resize:vertical}.threshold{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:24px;color:var(--text-muted);font-size:13px}.threshold input{width:110px}
</style>
