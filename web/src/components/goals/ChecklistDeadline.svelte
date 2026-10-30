<script lang="ts">
  import { getContext, untrack } from 'svelte';
  import type { ChecklistItem } from '../../lib/goals/contracts';
  import { createGoalEditor } from '../../lib/goals/editing';
  import { PlannerFetchError } from '../../lib/api';
  import { queryClient } from '../../lib/queryClient';
  import { goalQueries } from '../../lib/goals/queries';
  import { deadlineColor, deadlineTone, goalDateText } from '../../lib/goals/presentation';
  import type { ChecklistDeadlineCommand } from '../../lib/goals/requestSessions';
  import { PAGE_CONTEXT, type PageContext } from '../../lib/pageKit/context';

  let { blockId, item }: { blockId: string; item: ChecklistItem } = $props();
  const context = getContext<PageContext | undefined>(PAGE_CONTEXT);
  // A late request belongs to the mounted goal and item, never a later host prop.
  const goalId = untrack(() => context?.goalId);
  const capturedBlockId = untrack(() => blockId);
  const capturedItemId = untrack(() => item.id);
  const editor = goalId ? createGoalEditor(goalId) : undefined;
  const session = untrack(() => context?.checklistDeadlineSession?.(capturedBlockId, capturedItemId));
  const editable = Boolean(editor && context?.updateBlock && session);
  const uid = `item-date-${Math.random().toString(36).slice(2, 9)}`;
  let root = $state<HTMLDivElement | null>(null);
  let input = $state<HTMLInputElement | null>(null);
  let editing = $state(false);
  let revision = $state(0);
  let admissionError = $state<unknown>(null);
  $effect(() => session?.subscribe(() => revision++));
  let retained = $derived.by(() => { revision; return { draft: session?.draft, receipt: session?.current }; });
  let draft = $derived(retained.draft);
  let receipt = $derived(retained.receipt);
  let pending = $derived(receipt?.status === 'pending');
  let failed = $derived(receipt?.status === 'failed');
  let value = $derived(draft?.value ?? item.deadline ?? '');
  let tone = $derived(deadlineTone(item.deadline, item.state === 'done', context?.statusDate));
  let description = $derived(!item.deadline ? 'No deadline' : item.state === 'done' ? 'Completed' : tone === 'bad' ? 'Overdue' : tone === 'attention' ? 'Due today' : 'Due date');
  let error = $derived(admissionError ?? (failed ? receipt?.error : draft?.error));

  function begin() {
    editing = true;
    if (!session || session.current?.status === 'pending' || session.current?.status === 'failed') return;
    try { session.begin(item.deadline ?? '', context?.revision ?? 0); admissionError = null; }
    catch (error) { admissionError = error; }
  }
  function change(node: HTMLInputElement) {
    if (!session || session.current?.status === 'pending' || session.current?.status === 'failed') return;
    try {
      session.begin(item.deadline ?? '', context?.revision ?? 0);
      session.update(node.value, node.validity.badInput || !node.checkValidity() ? new Error('Enter a complete date or clear it.') : null);
      admissionError = null;
    } catch (error) { admissionError = error; }
  }
  async function submit(command: ChecklistDeadlineCommand) {
    if (!session || !editor || session.current?.status === 'pending') return;
    admissionError = null;
    try {
      await session.run(command, () => editor.updateBlock(capturedBlockId, block => {
        if (block.type !== 'checklist' || !block.items.some(candidate => candidate.id === capturedItemId)) throw new Error('This checklist item no longer exists.');
        return { ...block, items: block.items.map(candidate => candidate.id === capturedItemId ? { ...candidate, deadline: command.deadline } : candidate) };
      }, command.revision));
    } catch (error) {
      // Keep the failed draft, but refresh accepted revisions for a later explicit edit.
      if (error instanceof PlannerFetchError && error.status === 409 && goalId) {
        await Promise.allSettled([
          queryClient.invalidateQueries({ queryKey: goalQueries.detail(goalId).queryKey }),
          queryClient.invalidateQueries({ queryKey: goalQueries.list().queryKey, exact: true }),
        ]);
      }
      // Submitted failures remain in the parent-owned receipt after unmount.
      if (!session.current) admissionError = error;
    }
  }
  function save() {
    if (!session || session.current?.status === 'pending' || session.current?.status === 'failed' || admissionError) return;
    const current = session.draft;
    if (!current) return;
    if (input?.validity.badInput) {
      session.update(input.value, new Error('Enter a complete date or clear it.'));
      return;
    }
    if (current.error) return;
    if (!current.dirty || current.value === current.initialValue) { session.discard(); return; }
    void submit({ deadline: current.value || null, revision: current.revision });
  }
  function discard() {
    session?.discard();
    admissionError = null;
    if (input) input.value = item.deadline ?? '';
  }
  function clear() {
    if (!session || session.current?.status === 'pending' || session.current?.status === 'failed') return;
    try {
      session.begin(item.deadline ?? '', context?.revision ?? 0);
      session.update('');
      if (input) input.value = '';
      admissionError = null;
      save();
    } catch (error) { admissionError = error; }
  }
  function keydown(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      discard();
      input?.blur();
    } else if (event.key === 'Enter') {
      event.preventDefault();
      event.stopPropagation();
      save();
      input?.blur();
    }
  }
  function focusout(event: FocusEvent) {
    if (event.relatedTarget instanceof Node && root?.contains(event.relatedTarget)) return;
    editing = false;
    save();
  }
  function openPicker(node: HTMLInputElement) {
    if (pending || failed) return;
    if (typeof node.showPicker === 'function') try { node.showPicker(); } catch { /* Native keyboard entry remains available. */ }
  }
</script>

{#if editable || item.deadline}
  <div class="deadline" class:editing class:undated={!item.deadline} bind:this={root} data-item-deadline={item.id} data-tone={tone} style:--deadline-color={deadlineColor(tone)} onfocusin={begin} onfocusout={focusout}>
    <div class="field">
      {#if editable}
        <span class="date-face" aria-hidden="true">
          {#if item.deadline}{goalDateText(item.deadline)}{:else}<svg viewBox="0 0 20 20"><rect x="3.5" y="5" width="13" height="11.5" rx="2"/><path d="M6.5 3.5v3M13.5 3.5v3M3.5 9h13"/></svg>{/if}
        </span>
        <input bind:this={input} type="date" aria-label={`Deadline for ${item.label}`} aria-describedby={`${uid}-status${error ? ` ${uid}-error` : ''}`} aria-invalid={Boolean(error)} value={value} disabled={pending} readonly={failed} oninput={event => change(event.currentTarget)} onclick={event => openPicker(event.currentTarget)} onkeydown={keydown}/>
        {#if editing && (value || draft?.error) && !failed}
          <button class="clear" type="button" aria-label={`Clear deadline for ${item.label}`} disabled={pending} onclick={clear}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m5 5 6 6M11 5l-6 6"/></svg></button>
        {/if}
      {:else}
        <time datetime={item.deadline ?? undefined} aria-describedby={`${uid}-status`}>{goalDateText(item.deadline)}</time>
      {/if}
    </div>
    <span class="sr" id={`${uid}-status`}>{description}</span>
    {#if error}
      <div class="error" id={`${uid}-error`} role="alert">
        <span>{failed ? 'Date was not saved. ' : ''}{String(error instanceof Error ? error.message : error)}</span>
        <div class="error-actions">
          {#if failed}<button type="button" onclick={() => { if (receipt) void submit(receipt.command); }}>Retry save</button>{/if}
          <button type="button" onclick={discard}>Discard edit</button>
        </div>
      </div>
    {/if}
  </div>
{/if}

<style>
  .deadline { display: grid; flex: 0 0 auto; justify-items: end; align-self: start; max-width: 100%; color: var(--deadline-color); font-size: var(--type-xs); font-weight: 400; }
  .field { position: relative; display: flex; align-items: center; min-height: 44px; }
  .date-face { display: flex; align-items: center; justify-content: center; min-width: 44px; padding-inline: var(--space-1); white-space: nowrap; pointer-events: none; }
  svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.35; stroke-linecap: round; stroke-linejoin: round; }
  input { position: absolute; inset: 0; width: 100%; min-width: 0; margin: 0; padding: var(--space-1); border: 0; border-radius: var(--radius-sm); background: transparent; color: inherit; font: inherit; opacity: 0; cursor: pointer; }
  input:focus { position: relative; width: 112px; opacity: 1; cursor: text; outline: var(--border-strong) solid var(--accent-bright); outline-offset: -2px; }
  .field:has(input:focus) .date-face { display: none; }
  input::-webkit-calendar-picker-indicator { display: none; }
  input:disabled { cursor: wait; }
  .clear { position: relative; z-index: 1; display: grid; width: 44px; min-height: 44px; place-items: center; margin: 0; padding: 0; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--text-muted); cursor: pointer; }
  .clear:focus-visible, .error button:focus-visible { outline: var(--border-strong) solid var(--accent-bright); outline-offset: -2px; }
  time { padding-inline: var(--space-1); white-space: nowrap; }
  .error { width: min(24ch, 100%); padding-bottom: var(--space-2); color: var(--accent-error); font-size: var(--type-xs); overflow-wrap: anywhere; }
  .error-actions { display: flex; flex-wrap: wrap; gap: var(--space-2); }
  .error button { min-height: 44px; margin: 0; padding: 0; border: 0; border-radius: var(--radius-sm); background: none; color: var(--text-muted); font: inherit; cursor: pointer; }
  .sr { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0 0 0 0); border: 0; }
</style>
