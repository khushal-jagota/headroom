<script lang="ts">
 import {createQuery} from '@tanstack/svelte-query';
 import type {GoalResponse,GoalWriteFields} from '../../lib/goals/contracts';
 import type {InlineEditDraft} from '../../lib/inlineEditDraft';
 import {createGoalEditor} from '../../lib/goals/editing';
 import {savedChecklists} from '../../lib/goals/checklistCompletion';
 import {goalQueries} from '../../lib/goals/queries';
 import {queryClient} from '../../lib/queryClient';
 import {queries} from '../../lib/queryCatalogue';
 import {PlannerFetchError} from '../../lib/api';
 import InlineEdit from '../InlineEdit.svelte';
 import ErrorLine from '../ErrorLine.svelte';

 let {goal,mark}:{goal:GoalResponse;mark?:import('svelte').Snippet}=$props();
 const projects=createQuery(()=>queries.projects());
 type Field='title'|'why'|'project_id'|'deadline'|'active'|'primary_checklist_block_id'|'direction'|'target';
 type Edit=InlineEditDraft & {dirty:boolean;node:HTMLInputElement|HTMLSelectElement|null;accepted:GoalResponse|null;editor:ReturnType<typeof createGoalEditor>|null};
 const drafts=new Map<string,Record<Field,Edit>>();
 function goalDrafts(id:string){
  let fields=drafts.get(id);
  if(!fields){
   function field():Edit{const edit=$state<Edit>({value:null,error:null,saving:false,dirty:false,node:null,accepted:null,editor:null});return edit;}
   fields={title:field(),why:field(),project_id:field(),deadline:field(),active:field(),primary_checklist_block_id:field(),direction:field(),target:field()};
   drafts.set(id,fields);
  }
  return fields;
 }
 let fields=$derived(goalDrafts(goal.id));
 function acceptedValue(source:GoalResponse,field:Field):string{
  if(field==='direction')return source.primary_chart?.direction??(source.primary_chart?.kind==='time-budget'?'atmost':'atleast');
  if(field==='target')return source.primary_chart?.target===undefined?'':String(source.primary_chart.target);
  return String(source[field]??'');
 }
 function start(edit:Edit,replace=false){
  if(edit.accepted&&!(replace&&edit.value===null&&!edit.error&&!edit.saving))return;
  edit.dirty=false;
  edit.accepted=structuredClone($state.snapshot(goal));
  edit.editor=createGoalEditor(edit.accepted.id);
 }
 function discard(edit:Edit,field:Field){
  edit.value=null;edit.error=null;edit.dirty=false;edit.accepted=null;edit.editor=null;edit.node=null;
  return acceptedValue(goal,field);
 }
 async function write(edit:Edit,patch:GoalWriteFields){
  const accepted=edit.accepted,editor=edit.editor;
  if(!accepted||!editor)throw new Error('Focus this field before another edit.');
  try{
   const saved=await editor.write(patch,accepted.revision);
   // A previous field can save while focus enters its pristine sibling. Only
   // this known local acceptance advances that sibling's unused snapshot.
   for(const sibling of Object.values(drafts.get(saved.id)??{})){
    if(sibling!==edit&&sibling.value===null&&!sibling.dirty&&!sibling.error&&!sibling.saving&&sibling.accepted?.id===saved.id&&saved.revision>sibling.accepted.revision){
     sibling.accepted=structuredClone($state.snapshot(saved));
    }
   }
   edit.accepted=null;edit.editor=null;edit.dirty=false;
  }catch(error){
   if(error instanceof PlannerFetchError&&error.status===409){
    await queryClient.invalidateQueries({queryKey:goalQueries.detail(accepted.id).queryKey}).catch(()=>undefined);
   }
   throw error;
  }
 }
 async function saveText(edit:Edit,field:'title'|'why',raw:string){
  if(field==='title'&&!raw.trim())throw new Error('Enter a goal title.');
  await write(edit,{[field]:raw});
 }
 function input(edit:Edit,node:HTMLInputElement|HTMLSelectElement){start(edit);edit.node=node;edit.value=node.value;edit.error=null;}
 async function commit(edit:Edit,field:Field,node:HTMLInputElement|HTMLSelectElement){
  if(edit.saving||!edit.accepted||edit.value===null)return;
  if(edit.value===acceptedValue(edit.accepted,field)){discard(edit,field);return;}
  if(!node.checkValidity()){edit.error=new Error(node.validationMessage);return;}
  let patch:GoalWriteFields;
  if(field==='direction'||field==='target'){
   const chart=edit.accepted.primary_chart;
   if(!chart){edit.error=new Error('This goal no longer has a primary chart.');return;}
   const value=field==='target'?Number(edit.value):edit.value;
   if(field==='target'&&(edit.value.trim()===''||!Number.isFinite(value))){edit.error=new Error('Enter a finite success threshold.');return;}
   patch={primary_chart:{...chart,[field]:value}};
  }else if(field==='active')patch={active:edit.value==='true'};
  else if(field==='deadline')patch={deadline:edit.value||null};
  else if(field==='primary_checklist_block_id')patch={primary_checklist_block_id:edit.value||null};
  else patch={[field]:edit.value};
  edit.saving=true;edit.error=null;
  try{await write(edit,patch);edit.value=null;}catch(error){edit.error=error;}finally{edit.saving=false;}
 }
 function keydown(event:KeyboardEvent,edit:Edit,field:Field){
  const node=event.currentTarget as HTMLInputElement|HTMLSelectElement;
  if(event.key==='Escape'){
   event.preventDefault();node.value=discard(edit,field);node.blur();
  }else if(event.key==='Enter'&&node instanceof HTMLInputElement){event.preventDefault();void commit(edit,field,node);node.blur();}
 }
</script>

<header class="goal-heading">
 {#each [fields] as editFields (goal.id)}
 {#snippet failure(field:Field)}
  {@const edit=editFields[field]}
  {#if edit.error}
   <div class="field-error">
    <ErrorLine error={edit.error}/>
    <div class="error-actions"><button disabled={edit.saving} onpointerdown={e=>e.preventDefault()} onclick={()=>{if(edit.node)void commit(edit,field,edit.node);}}>Retry save</button><button disabled={edit.saving} onpointerdown={e=>e.preventDefault()} onclick={()=>discard(edit,field)}>Discard edit</button></div>
   </div>
  {/if}
 {/snippet}
 <div class="goal-context">
  <label class="metadata">Project
   <select aria-label="Project" value={editFields.project_id.value??goal.project_id} disabled={editFields.project_id.saving} onfocus={()=>start(editFields.project_id,true)} onchange={e=>{input(editFields.project_id,e.currentTarget);void commit(editFields.project_id,'project_id',e.currentTarget);}} onkeydown={e=>keydown(e,editFields.project_id,'project_id')}>
    {#if !(projects.data?.projects??[]).some(project=>project.id===goal.project_id)}<option value={goal.project_id}>Project</option>{/if}
    {#each projects.data?.projects??[] as project}<option value={project.id}>{project.name}</option>{/each}
   </select>
  </label>
  <label class="metadata">By
   <input type="date" aria-label="By date" value={editFields.deadline.value??goal.deadline??''} disabled={editFields.deadline.saving} onfocus={()=>start(editFields.deadline,true)} oninput={e=>input(editFields.deadline,e.currentTarget)} onblur={e=>void commit(editFields.deadline,'deadline',e.currentTarget)} onkeydown={e=>keydown(e,editFields.deadline,'deadline')}/>
  </label>
  <label class="metadata">State
   <select aria-label="State" value={editFields.active.value??String(goal.active)} disabled={editFields.active.saving} onfocus={()=>start(editFields.active,true)} onchange={e=>{input(editFields.active,e.currentTarget);void commit(editFields.active,'active',e.currentTarget);}} onkeydown={e=>keydown(e,editFields.active,'active')}><option value="true">Active</option><option value="false">Inactive</option></select>
  </label>
  <span class="signal">{@render mark?.()}</span>
 </div>
 {#each ['project_id','deadline','active'] as field}{@render failure(field as Field)}{/each}
 <h1 oninput={()=>editFields.title.dirty=true}><InlineEdit value={goal.title} draft={editFields.title} placeholder="Goal title…" ariaLabel="Goal title" onStart={()=>start(editFields.title,true)} onCancel={()=>discard(editFields.title,'title')} onSave={raw=>saveText(editFields.title,'title',raw)}/></h1>
 <div class="why" oninput={()=>editFields.why.dirty=true}><InlineEdit value={goal.why} draft={editFields.why} multiline placeholder="Why this goal matters…" ariaLabel="Why this goal matters" onStart={()=>start(editFields.why,true)} onCancel={()=>discard(editFields.why,'why')} onSave={raw=>saveText(editFields.why,'why',raw)}/></div>
 {#if goal.kind==='binary'}
  <label class="threshold">Completion display
   <select aria-label="Completion display" value={editFields.primary_checklist_block_id.value??goal.primary_checklist_block_id??''} disabled={editFields.primary_checklist_block_id.saving} onfocus={()=>start(editFields.primary_checklist_block_id,true)} onchange={e=>{input(editFields.primary_checklist_block_id,e.currentTarget);void commit(editFields.primary_checklist_block_id,'primary_checklist_block_id',e.currentTarget);}} onkeydown={e=>keydown(e,editFields.primary_checklist_block_id,'primary_checklist_block_id')}>
    <option value="">Binary completion</option>
    {#each savedChecklists(goal.page) as checklist,index(checklist.id)}<option value={checklist.id}>{checklist.title?.trim()||`Checklist ${index+1}`}</option>{/each}
   </select>
  </label>
  {@render failure('primary_checklist_block_id')}
 {/if}
 {#if goal.kind==='quantitative'&&goal.primary_chart}
  <div class="threshold">
   <label>Success threshold
    <select aria-label="Threshold direction" value={editFields.direction.value??acceptedValue(goal,'direction')} disabled={editFields.direction.saving} onfocus={()=>start(editFields.direction,true)} onchange={e=>{input(editFields.direction,e.currentTarget);void commit(editFields.direction,'direction',e.currentTarget);}} onkeydown={e=>keydown(e,editFields.direction,'direction')}><option value="atleast">At least</option><option value="atmost">At most</option></select>
   </label>
   <input aria-label="Success threshold" type="number" required step="any" value={editFields.target.value??acceptedValue(goal,'target')} disabled={editFields.target.saving} onfocus={()=>start(editFields.target,true)} oninput={e=>input(editFields.target,e.currentTarget)} onblur={e=>void commit(editFields.target,'target',e.currentTarget)} onkeydown={e=>keydown(e,editFields.target,'target')}/>
   <span>{goal.primary_chart.unit??goal.primary_chart.format?.suffix??''}</span>
  </div>
  {#each ['direction','target'] as field}{@render failure(field as Field)}{/each}
 {/if}
 {/each}
</header>

<style>
 .error-actions{display:flex;gap:var(--space-3);margin-top:var(--space-2);margin-bottom:var(--space-3)}.error-actions button{border:0;background:none;padding:var(--space-1);color:var(--accent-bright);font:inherit;font-size:12px;cursor:pointer}.error-actions button:disabled{opacity:.65}.goal-heading{margin-bottom:28px}.goal-context{display:flex;align-items:center;gap:var(--space-4);margin-bottom:14px;color:var(--text-faint);font-size:12px;font-weight:500;line-height:1.4;flex-wrap:wrap}.signal{margin-left:auto}h1{font-size:34px;font-weight:600;line-height:1.18;letter-spacing:-.03em;margin:0;max-width:750px;color:var(--text-strong)}.why{font-size:14px;font-weight:500;line-height:1.6;color:var(--text-faint);margin-top:var(--space-3);max-width:650px}.why :global(.ed){min-height:1.6em;white-space:pre-wrap}label{display:flex;align-items:center;gap:var(--space-2);min-width:0}.metadata{color:var(--text-faintest)}input,select{font:inherit;background:transparent;color:var(--text-muted);border:0;border-radius:var(--radius-sm);padding:var(--space-1);max-width:100%;min-width:0}select{cursor:pointer}select option{background:var(--surface-1);color:var(--text-default)}input:focus,select:focus{outline:var(--border-hairline) solid var(--accent-bright);outline-offset:var(--space-1)}input:disabled,select:disabled{opacity:.65}.threshold{display:flex;align-items:center;gap:var(--space-3);flex-wrap:wrap;margin-top:var(--space-5);color:var(--text-muted);font-size:13px}.threshold input{width:110px}h1,.why{overflow-wrap:anywhere}@container (max-width:640px){.goal-context{gap:var(--space-2)}h1{font-size:30px}.metadata select{max-width:220px}.threshold label{flex-wrap:wrap}}
</style>
