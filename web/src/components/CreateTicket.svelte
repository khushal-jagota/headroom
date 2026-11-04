<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { createQuery } from '@tanstack/svelte-query';
  import { queries } from '../lib/queryCatalogue';
  import { mutateJson } from '../lib/mutate';
  import { blankWorkflow, copyWorkflow, workflowError, workflowSummary, type WorkflowRecord } from '../lib/workflow';
  import type { EmployeeConfigurationSnapshot } from '../lib/types';
  import { machineName, type TicketLocale } from '../lib/ticketMachines';
  import { workspaceAddress } from '../lib/workspaceAddress';
  import WorkflowEditor from './WorkflowEditor.svelte';
  import MarkdownBlock from './MarkdownBlock.svelte';
  import ManagedLaunchDefaults from './ManagedLaunchDefaults.svelte';
  import Button from './Button.svelte';
  import ErrorLine from './ErrorLine.svelte';

  let { onClose, initialManager = '' }: { onClose: () => void; initialManager?: string } = $props();
  const presets = createQuery(() => queries.supportedWorkerTypeManifests());
  const projects = createQuery(() => queries.projects());
  const managers = createQuery(() => queries.managers());
  let dialog: HTMLDialogElement;
  let title = $state('');
  let brief = $state('');
  let selected = $state('');
  let source = $state<string | null>(null);
  let workflow = $state<WorkflowRecord>(blankWorkflow());
  let original = $state('');
  let project = $state('');
  let manager = $state(untrack(() => initialManager));
  let locale = $state<TicketLocale>('vps');
  let launch = $state<EmployeeConfigurationSnapshot>({ employee_backend: 'codex', employee_launch_model: null, employee_launch_reasoning_effort: null });
  let reviewing = $state(false);
  let saving = $state(false);
  let error = $state<unknown>(null);
  let custom = $derived(selected === '__custom__' || JSON.stringify(workflow) !== original);
  let currentManager = $derived(managers.data?.managers.find(item => item.id === manager));
  let catalogueError = $derived(presets.error ?? projects.error ?? managers.error);
  onMount(() => { dialog.showModal(); });

  function choose(value: string): void {
    selected = value;
    reviewing = false;
    error = null;
    const preset = presets.data?.worker_types.find(item => item.worker_type === value);
    source = preset?.worker_type ?? null;
    workflow = preset ? copyWorkflow(preset) : blankWorkflow();
    original = JSON.stringify(workflow);
    if (preset) launch = { employee_backend: preset.default_backend, employee_launch_model: preset.default_model, employee_launch_reasoning_effort: preset.default_reasoning_effort };
  }
  function review(): void {
    error = !title.trim() ? 'Give the Ticket a title.' : !selected ? 'Choose a preset or a custom workflow.' : workflowError(workflow);
    if (!error && (!launch.employee_backend || !launch.employee_launch_model)) error = 'Choose the Worker and model for this Ticket.';
    if (!error) reviewing = true;
  }
  async function create(): Promise<void> {
    if (saving || !reviewing) return;
    saving = true;
    error = null;
    try {
      const result = await mutateJson<{ id: string }>('/api/tickets', {
        method: 'POST', body: {
          title: title.trim(), brief, worker_type: source,
          ...(custom ? { workflow: copyWorkflow($state.snapshot(workflow)) } : {}),
          ...$state.snapshot(launch), locale,
          manager: manager ? { kind: 'manager', id: manager } : { kind: 'chief', id: 'chief' },
          ...(!manager && project ? { project_id: project } : {})
        }
      });
      onClose();
      window.location.hash = workspaceAddress({ kind: 'ticket', id: result.id });
    } catch (caught) { error = caught; }
    finally { saving = false; }
  }
  function close(): void { if (!saving) { dialog.close(); onClose(); } }
</script>

<dialog bind:this={dialog} class="create-ticket-dialog" aria-labelledby="create-ticket-title" oncancel={event => { event.preventDefault(); close(); }}>
  <div class="create-ticket-content" data-create-ticket>
    <header class="create-ticket-head"><div><span class="eyebrow">New Ticket</span><h1 id="create-ticket-title">Define the work.</h1></div><button type="button" class="close" aria-label="Close new Ticket" disabled={saving} onclick={close}>×</button></header>
    {#if catalogueError}<ErrorLine error={catalogueError} />{/if}
    {#if error}<ErrorLine {error} />{/if}
    {#if reviewing}
      <section class="ticket-review" data-create-ticket-review>
        <h2>{title}</h2>
        {#if brief}<MarkdownBlock text={brief} />{/if}
        <p class="review-meta">{custom ? 'Custom workflow' : presets.data?.worker_types.find(item => item.worker_type === source)?.label} · {currentManager?.title ?? 'Chief of Staff'} · {machineName(locale)}</p>
        <p class="review-sequence">{workflowSummary(workflow)}</p>
        {#if workflow.guidance}<div class="guide"><h3>Across the workflow</h3><MarkdownBlock text={workflow.guidance} /></div>{/if}
        <ol>{#each workflow.stages as stage}<li><h3>{stage.label}</h3>{#if !stage.is_terminal}<p class="review-meta">{stage.ownership_mode === 'user' ? 'You' : 'Worker'} · {workflow.fields.find(field => field.id === stage.gating_field)?.label}</p>{/if}{#if stage.instructions}<MarkdownBlock text={stage.instructions} />{/if}</li>{/each}</ol>
      </section>
      <footer><Button disabled={saving} onclick={() => { reviewing = false; error = null; }}>← Edit</Button><Button variant="primary" disabled={saving} onclick={() => void create()} data-create-ticket-submit>{saving ? 'Creating…' : 'Create Ticket'}</Button></footer>
    {:else}
      <form onsubmit={event => { event.preventDefault(); review(); }}>
        <fieldset disabled={saving}>
          <label>Title<input bind:value={title} required placeholder="What needs to happen?" data-create-title /></label>
          <label>Brief<textarea bind:value={brief} rows="3" placeholder="The job, its purpose, and its boundaries"></textarea></label>
          <div class="placement"><label>Project<select value={currentManager?.project_id ?? project} onchange={event => project = event.currentTarget.value} disabled={Boolean(manager)}><option value="">No project</option>{#each projects.data?.projects ?? [] as item}<option value={item.id}>{item.name}</option>{/each}</select></label><label>Manager<select bind:value={manager}><option value="">Chief of Staff</option>{#each managers.data?.managers.filter(item => !item.settled) ?? [] as item}<option value={item.id}>{item.title}</option>{/each}</select></label><label>Machine<select bind:value={locale}><option value="vps">VPS</option><option value="macos">Mac</option></select></label></div>
          <section class="workflow-selection"><h2>Workflow</h2><label>Start from<select value={selected} onchange={event => choose(event.currentTarget.value)} required data-create-preset><option value="" disabled>Choose a preset or custom stages</option>{#each presets.data?.worker_types ?? [] as preset}<option value={preset.worker_type}>{preset.label}</option>{/each}<option value="__custom__">Custom stages</option></select></label></section>
          {#if selected}
            <WorkflowEditor value={workflow} onChange={next => { workflow = next; error = null; }} />
            <ManagedLaunchDefaults label="Ticket" value={launch} onSave={async next => { launch = next; return next; }} />
          {/if}
        </fieldset>
        <footer><Button onclick={close}>Cancel</Button><button type="submit" class="button button--primary" disabled={!selected} data-review-ticket>Review Ticket →</button></footer>
      </form>
    {/if}
  </div>
</dialog>

<style>
  dialog { width: min(760px, calc(100vw - var(--space-5))); max-height: calc(100dvh - var(--space-5)); margin: auto; padding: 0; border: 1px solid var(--border-color); border-radius: var(--radius-lg); background: var(--surface-1); color: var(--text-default); box-shadow: var(--shadow-float); }
  dialog::backdrop { background: var(--scrim); }
  .create-ticket-content { padding: var(--space-6); }
  .create-ticket-head { display: flex; justify-content: space-between; align-items: start; margin-bottom: var(--space-6); }
  .eyebrow { font: var(--type-xs) var(--font-mono); color: var(--text-faint); }
  h1 { font-size: var(--type-display); font-weight: 500; letter-spacing: var(--tracking-title); margin-top: var(--space-2); }
  h2 { font-size: var(--type-md); font-weight: 500; margin-bottom: var(--space-3); }
  h3 { font-size: var(--type-sm); font-weight: 500; }
  .close { background: none; border: 0; color: var(--text-muted); font: inherit; font-size: var(--type-lg); padding: var(--space-2); cursor: pointer; }
  form, fieldset { display: grid; gap: var(--space-5); }
  fieldset { border: 0; padding: 0; margin: 0; min-width: 0; }
  label { display: grid; gap: var(--space-2); color: var(--text-muted); font-size: var(--type-xs); }
  input, select, textarea { width: 100%; min-width: 0; padding: var(--space-3); border: 1px solid var(--border-color); border-radius: var(--radius-sm); background: var(--surface-recessed); color: var(--text-default); font: inherit; font-size: var(--type-sm); }
  textarea { resize: vertical; line-height: 1.5; }
  input:focus, select:focus, textarea:focus { outline: 1px solid var(--accent-bright); outline-offset: 1px; }
  .placement { display: grid; grid-template-columns: 1fr 1fr 90px; gap: var(--space-3); }
  .workflow-selection { padding-top: var(--space-4); border-top: 1px solid var(--border-color); }
  footer { margin-top: var(--space-6); display: flex; justify-content: space-between; align-items: center; gap: var(--space-3); }
  .ticket-review p { white-space: pre-wrap; font-size: var(--type-sm); line-height: 1.6; }
  .review-meta { color: var(--text-muted); margin-block: var(--space-2); }
  .review-sequence { padding-block: var(--space-4); color: var(--accent-bright); }
  .ticket-review ol { padding-left: var(--space-5); display: grid; gap: var(--space-5); }
  .guide { margin-block: var(--space-4); }
  @media(max-width: 520px) { .create-ticket-content { padding: var(--space-4); } .placement { grid-template-columns: 1fr; } }
</style>
