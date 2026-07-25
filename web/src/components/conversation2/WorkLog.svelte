<script lang="ts">
  /** A turn's work, sized to how much of it still matters.
   *
   * While the turn runs, one line: what is happening now. What already happened waits
   * behind a count, because a person watching an agent work wants the front of the queue,
   * not all of it. When the turn is over the whole log folds into a single quiet line —
   * the work is done, the reply is underneath it, and the log is there if you want it.
   */
  import ToolCallRow from "./ToolCallRow.svelte";
  import {
    hiddenWorkSentence,
    VISIBLE_RUNNING_WORK_ENTRIES,
    workedSentence
  } from "../../lib/conversation2/transcript";
  import type { ToolCallRow as ToolCallRowType } from "../../lib/conversation2/transcript";

  let {
    entries,
    settled = false,
    durationSeconds = null
  }: {
    entries: readonly ToolCallRowType[];
    settled?: boolean;
    durationSeconds?: number | null;
  } = $props();

  let expanded = $state(false);

  let hiddenCount = $derived(Math.max(0, entries.length - VISIBLE_RUNNING_WORK_ENTRIES));
  let visible = $derived.by(() => {
    if (settled) return expanded ? entries : [];
    if (expanded) return entries;
    return entries.slice(entries.length - VISIBLE_RUNNING_WORK_ENTRIES);
  });
  let foldLabel = $derived(
    settled
      ? `${workedSentence(durationSeconds)} · ${entries.length} tool call${entries.length === 1 ? "" : "s"}`
      : expanded
        ? "Show fewer tool calls"
        : hiddenWorkSentence(hiddenCount)
  );
  let foldVisible = $derived(settled || hiddenCount > 0);
</script>

<div
  class="c2-work"
  data-conversation2-work
  data-conversation2-work-settled={settled ? "true" : "false"}
  data-conversation2-work-expanded={expanded ? "true" : "false"}
>
  {#if settled && foldVisible}
    <button
      type="button"
      class="c2-work-fold"
      data-conversation2-work-fold
      aria-expanded={expanded}
      onclick={() => (expanded = !expanded)}
    >
      <span aria-hidden="true" class="c2-work-chevron" class:is-open={expanded}>›</span>
      <span>{foldLabel}</span>
    </button>
  {/if}

  {#if visible.length > 0}
    <div class="acp-steps" data-conversation2-work-entries>
      {#each visible as entry (entry.key)}
        <ToolCallRow row={entry} />
      {/each}
    </div>
  {/if}

  {#if !settled && foldVisible}
    <button
      type="button"
      class="c2-work-fold"
      data-conversation2-work-fold
      aria-expanded={expanded}
      onclick={() => (expanded = !expanded)}
    >
      <span aria-hidden="true" class="c2-work-chevron" class:is-open={expanded}>›</span>
      <span>{foldLabel}</span>
    </button>
  {/if}
</div>

<style>
  .c2-work { display: grid; min-width: 0; max-width: 100%; gap: var(--space-1); }
  .c2-work-fold {
    justify-self: start;
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    background: transparent;
    border: 0;
    border-radius: var(--radius-sm);
    color: var(--text-faintest);
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
    padding: var(--space-1) var(--space-2);
    font-variant-numeric: tabular-nums;
  }
  .c2-work-fold:hover { color: var(--text-muted); background: var(--surface-overlay); }
  .c2-work-chevron {
    display: inline-block;
    transition: transform var(--motion-fast) var(--motion-ease);
  }
  .c2-work-chevron.is-open { transform: rotate(90deg); }
  @media (prefers-reduced-motion: reduce) {
    .c2-work-chevron { transition: none; }
  }
</style>
