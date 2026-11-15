<script lang="ts">
  import { copyWorkflow, type WorkflowRecord } from '../lib/workflow';
  import type { StageOwnershipMode } from '../lib/types';
  let { value, onChange, disabled = false }: {
    value: WorkflowRecord;
    onChange: (next: WorkflowRecord) => void;
    disabled?: boolean;
  } = $props();

  function updateStage(index: number, changes: Partial<WorkflowRecord['stages'][number]>): void {
    const next = copyWorkflow(value);
    next.stages[index] = { ...next.stages[index]!, ...changes };
    onChange(next);
  }
  function resultName(index: number, label: string): void {
    const next = copyWorkflow(value);
    const field = next.fields.find(field => field.id === next.stages[index]?.gating_field);
    if (field) field.label = label;
    onChange(next);
  }
  function add(): void {
    const next = copyWorkflow(value);
    let number = next.stages.length;
    while (next.stages.some(stage => stage.id === `needs_step_${number}`) || next.fields.some(field => field.id === `step_${number}`)) number += 1;
    const result = `step_${number}`;
    const index = next.stages.findIndex(stage => stage.gating_field === 'consequences' || stage.is_terminal);
    next.stages.splice(index < 0 ? next.stages.length : index, 0, {
      id: `needs_${result}`, label: 'New stage', gating_field: result,
      is_terminal: false, ownership_mode: 'worker', instructions: ''
    });
    next.fields = next.stages.flatMap(stage => stage.gating_field ? [next.fields.find(field => field.id === stage.gating_field) ?? { id: stage.gating_field, label: 'Result' }] : []);
    onChange(next);
  }
  function move(index: number, direction: number): void {
    const next = copyWorkflow(value);
    const target = index + direction;
    if (target < 0 || target >= next.stages.length || next.stages[target]?.is_terminal || next.stages[target]?.id === 'needs_brief') return;
    [next.stages[index], next.stages[target]] = [next.stages[target]!, next.stages[index]!];
    next.fields = next.stages.flatMap(stage => stage.gating_field ? next.fields.filter(field => field.id === stage.gating_field) : []);
    onChange(next);
  }
  function remove(index: number): void {
    const next = copyWorkflow(value);
    const [stage] = next.stages.splice(index, 1);
    next.fields = next.fields.filter(field => field.id !== stage?.gating_field);
    onChange(next);
  }
</script>

<div class="workflow-editor" data-workflow-editor>
  <label class="workflow-guidance">Across the workflow
    <textarea value={value.guidance ?? ''} oninput={event => onChange({ ...copyWorkflow(value), guidance: event.currentTarget.value })} {disabled} rows="3" placeholder="Instructions that apply to every stage"></textarea>
  </label>
  <ol class="workflow-stages">
    {#each value.stages as stage, index (stage.id)}
      <li class="workflow-stage" class:terminal={stage.is_terminal} data-workflow-stage={stage.id}>
        <div class="workflow-stage-head">
          <span class="workflow-number" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
          <label class="workflow-stage-name">Stage name<input value={stage.label} oninput={event => updateStage(index, { label: event.currentTarget.value })} {disabled} required /></label>
          {#if !stage.is_terminal}
            <label class="workflow-stage-owner">Who does it<select value={stage.ownership_mode ?? 'worker'} onchange={event => updateStage(index, { ownership_mode: event.currentTarget.value as StageOwnershipMode })} {disabled}><option value="worker">Worker</option><option value="user">You</option></select></label>
          {:else}<span class="workflow-finish">Complete</span>{/if}
        </div>
        {#if !stage.is_terminal}
          <div class="workflow-stage-body">
            <label>Result<input value={value.fields.find(field => field.id === stage.gating_field)?.label ?? ''} oninput={event => resultName(index, event.currentTarget.value)} {disabled} required placeholder="What this stage produces" /></label>
            <label>Instructions<textarea value={stage.instructions ?? ''} oninput={event => updateStage(index, { instructions: event.currentTarget.value })} {disabled} rows="3" placeholder="What do you want from this stage?"></textarea></label>
            {#if stage.id !== 'needs_brief'}
              <div class="workflow-stage-actions">
                <button type="button" disabled={disabled || index === 0 || value.stages[index - 1]?.id === 'needs_brief'} onclick={() => move(index, -1)} aria-label={`Move ${stage.label} up`}>↑ Move up</button>
                <button type="button" disabled={disabled || index >= value.stages.length - 2} onclick={() => move(index, 1)} aria-label={`Move ${stage.label} down`}>↓ Move down</button>
                {#if stage.gating_field !== 'consequences'}<button type="button" class="workflow-remove" {disabled} onclick={() => remove(index)} aria-label={`Remove ${stage.label}`}>Remove</button>{/if}
              </div>
            {/if}
          </div>
        {/if}
      </li>
    {/each}
  </ol>
  <button type="button" class="workflow-add" {disabled} onclick={add}>+ Add stage</button>
</div>

<style>
  .workflow-editor { display: grid; gap: var(--space-5); }
  label { display: grid; gap: var(--space-2); font-size: var(--type-xs); color: var(--text-muted); }
  input, select, textarea { width: 100%; min-width: 0; padding: var(--space-3); border: 1px solid var(--border-color); border-radius: var(--radius-sm); background: var(--surface-recessed); color: var(--text-default); font: inherit; font-size: var(--type-sm); }
  textarea { resize: vertical; line-height: 1.5; }
  input:focus, select:focus, textarea:focus { outline: var(--focus-width) solid var(--focus-ring); outline-offset: 1px; }
  .workflow-stages { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-3); }
  .workflow-stage { border: 1px solid var(--border-color); border-radius: var(--radius-md); overflow: hidden; }
  .workflow-stage-head { display: flex; align-items: center; gap: var(--space-3); padding: var(--space-4); }
  .workflow-number { font: var(--type-xs) var(--font-mono); color: var(--text-faint); }
  .workflow-stage-name { flex: 1; min-width: 0; }
  .workflow-stage-owner { width: 115px; }
  .workflow-stage-body { padding: 0 var(--space-4) var(--space-4); display: grid; gap: var(--space-4); }
  .workflow-stage-actions { display: flex; flex-wrap: wrap; gap: var(--space-3); }
  button { border: 0; background: transparent; color: var(--text-muted); font: inherit; font-size: var(--type-xs); cursor: pointer; padding: var(--space-2) 0; }
  button:hover { color: var(--text-default); }
  button:disabled { opacity: .35; cursor: default; }
  .workflow-remove { margin-left: auto; }
  .workflow-add { justify-self: start; color: var(--accent-bright); }
  .terminal .workflow-stage-head { padding-block: var(--space-3); }
  .terminal input { background: transparent; border-color: transparent; }
  .workflow-finish { font-size: var(--type-xs); color: var(--text-faint); }
  @media (max-width: 500px) { .workflow-stage-head { flex-wrap: wrap; } .workflow-stage-owner { margin-left: calc(var(--space-3) + 2ch); width: calc(100% - var(--space-3) - 2ch); } }
</style>
