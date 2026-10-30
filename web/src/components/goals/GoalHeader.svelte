<script lang="ts">
 import {createQuery} from '@tanstack/svelte-query';
 import type {GoalResponse,GoalWriteFields} from '../../lib/goals/contracts';
 import type {InlineEditDraft} from '../../lib/inlineEditDraft';
 import {createGoalEditor} from '../../lib/goals/editing';
 import {deadlineTone,deadlineColor} from '../../lib/goals/presentation';
 import {goalQueries} from '../../lib/goals/queries';
 import {queryClient} from '../../lib/queryClient';
 import {queries} from '../../lib/queryCatalogue';
 import {PlannerFetchError} from '../../lib/api';
 import InlineEdit from '../InlineEdit.svelte';
 import ErrorLine from '../ErrorLine.svelte';

 let {goal,mark}:{goal:GoalResponse;mark?:import('svelte').Snippet}=$props();
 const projects=createQuery(()=>queries.projects());
 type Field='title'|'why'|'project_id'|'deadline'|'active';
 type Edit=InlineEditDraft & {dirty:boolean;node:HTMLInputElement|HTMLSelectElement|null;accepted:GoalResponse|null;editor:ReturnType<typeof createGoalEditor>|null};
 const drafts=new Map<string,Record<Field,Edit>>();
 function goalDrafts(id:string){
  let fields=drafts.get(id);
  if(!fields){
   function field():Edit{const edit=$state<Edit>({value:null,error:null,saving:false,dirty:false,node:null,accepted:null,editor:null});return edit;}
   fields={title:field(),why:field(),project_id:field(),deadline:field(),active:field()};
   drafts.set(id,fields);
  }
  return fields;
 }
 let fields=$derived(goalDrafts(goal.id));
 let projectId=$derived(fields.project_id.value??goal.project_id);
 let projectName=$derived(projects.data?.projects.find(project=>project.id===projectId)?.name??(projectId==='project_personal'?'Personal':'…'));
 let deadline=$derived(fields.deadline.value??goal.deadline??'');
 function dateText(value:string){
  return new Intl.DateTimeFormat(navigator.language,{year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(`${value||'2000-01-01'}T12:00:00`));
 }
 function openDatePicker(event:MouseEvent){
  const node=event.currentTarget as HTMLInputElement;
  // Native date segments remain the keyboard editor. Pointer and touch retain
  // access to the native picker after its decorative icon is removed.
  if(typeof node.showPicker==='function')try{node.showPicker();}catch{/* Native entry remains available if the browser refuses the picker. */}
 }
 function acceptedValue(source:GoalResponse,field:Field):string{
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
  if(field==='active')patch={active:edit.value==='true'};
  else if(field==='deadline')patch={deadline:edit.value||null};
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
  <span class="metadata">
   <span class="control-size" aria-hidden="true">{projectName}</span>
   <select aria-label="Project" value={editFields.project_id.value??goal.project_id} disabled={editFields.project_id.saving} onfocus={()=>start(editFields.project_id,true)} onchange={e=>{input(editFields.project_id,e.currentTarget);void commit(editFields.project_id,'project_id',e.currentTarget);}} onkeydown={e=>keydown(e,editFields.project_id,'project_id')}>
    {#if !(projects.data?.projects??[]).some(project=>project.id===goal.project_id)}<option value={goal.project_id}>{goal.project_id==='project_personal'?'Personal':'…'}</option>{/if}
    {#each projects.data?.projects??[] as project}<option value={project.id}>{project.name}</option>{/each}
   </select>
  </span>
  <span class="metadata deadline" class:empty={!deadline} data-deadline-tone={deadlineTone(goal.deadline,goal.completed,goal.status_date)}>
   <span class="control-size date-size" aria-hidden="true">{dateText(deadline)}</span>
   {#if !deadline}<span class="deadline-empty" aria-hidden="true">No deadline</span>{/if}
   <input type="date" style:color={deadlineColor(deadlineTone(goal.deadline,goal.completed,goal.status_date))} aria-label="By date" value={deadline} disabled={editFields.deadline.saving} onfocus={()=>start(editFields.deadline,true)} onclick={openDatePicker} oninput={e=>input(editFields.deadline,e.currentTarget)} onblur={e=>void commit(editFields.deadline,'deadline',e.currentTarget)} onkeydown={e=>keydown(e,editFields.deadline,'deadline')}/>
  </span>
  <span class="metadata">
   <span class="control-size" aria-hidden="true">{(editFields.active.value??String(goal.active))==='true'?'Active':'Inactive'}</span>
   <select aria-label="State" value={editFields.active.value??String(goal.active)} disabled={editFields.active.saving} onfocus={()=>start(editFields.active,true)} onchange={e=>{input(editFields.active,e.currentTarget);void commit(editFields.active,'active',e.currentTarget);}} onkeydown={e=>keydown(e,editFields.active,'active')}><option value="true">Active</option><option value="false">Inactive</option></select>
  </span>
  <span class="signal">{@render mark?.()}</span>
 </div>
 {#each ['project_id','deadline','active'] as field}{@render failure(field as Field)}{/each}
 <h1 oninput={()=>editFields.title.dirty=true}><InlineEdit value={goal.title} draft={editFields.title} placeholder="Goal title…" ariaLabel="Goal title" onStart={()=>start(editFields.title,true)} onCancel={()=>discard(editFields.title,'title')} onSave={raw=>saveText(editFields.title,'title',raw)}/></h1>
 <div class="why" oninput={()=>editFields.why.dirty=true}><InlineEdit value={goal.why} draft={editFields.why} multiline placeholder="Why this goal matters…" ariaLabel="Why this goal matters" onStart={()=>start(editFields.why,true)} onCancel={()=>discard(editFields.why,'why')} onSave={raw=>saveText(editFields.why,'why',raw)}/></div>
 {/each}
</header>

<style>
 .error-actions{display:flex;gap:var(--space-3);margin-top:var(--space-2);margin-bottom:var(--space-3)}
 .error-actions button{border:0;background:none;padding:var(--space-1);color:var(--accent-bright);font:inherit;font-size:12px;cursor:pointer}
 .error-actions button:disabled{opacity:.65}
 .goal-heading{margin-bottom:28px}
 .goal-context{display:flex;align-items:baseline;gap:var(--space-4);margin-bottom:14px;color:var(--text-faint);font-size:12px;font-weight:500;line-height:1.4;flex-wrap:wrap}
 .metadata{position:relative;display:inline-grid;grid-template-columns:minmax(0,1fr);align-items:baseline;min-width:0;max-width:100%}
 .control-size,.deadline-empty{grid-area:1/1;padding:var(--space-1);white-space:pre}
 .control-size{visibility:hidden}
 .deadline-empty{color:var(--text-muted);pointer-events:none}
 input,select{position:absolute;inset:0;box-sizing:border-box;width:100%;height:100%;min-width:0;font:inherit;line-height:inherit;background:transparent;color:var(--text-muted);border:0;border-radius:var(--radius-sm);padding:var(--space-1);appearance:none}
 select{cursor:pointer;text-overflow:ellipsis}
 select option{background:var(--surface-1);color:var(--text-default)}
 input:focus,select:focus{outline:var(--border-hairline) solid var(--accent-bright);outline-offset:var(--space-1)}
 input:disabled,select:disabled{opacity:.65}
 input::-webkit-calendar-picker-indicator{display:none}
 input::-webkit-datetime-edit,input::-webkit-datetime-edit-fields-wrapper,input::-webkit-datetime-edit-text,input::-webkit-datetime-edit-month-field,input::-webkit-datetime-edit-day-field,input::-webkit-datetime-edit-year-field{padding:0}
 input::-webkit-date-and-time-value{text-align:left}
 .empty:not(:focus-within) .date-size,.empty:focus-within .deadline-empty{display:none}
 .empty:not(:focus-within) input{opacity:0}
 .signal{margin-left:auto}
 h1{font-size:34px;font-weight:600;line-height:1.18;letter-spacing:-.03em;margin:0;max-width:750px;color:var(--text-strong)}
 .why{font-size:14px;font-weight:500;line-height:1.6;color:var(--text-faint);margin-top:var(--space-3);max-width:650px}
 .why :global(.ed){min-height:1.6em;white-space:pre-wrap}
 h1,.why{overflow-wrap:anywhere}
 @container (max-width:640px){.goal-context{gap:var(--space-2)}h1{font-size:30px}}
</style>
