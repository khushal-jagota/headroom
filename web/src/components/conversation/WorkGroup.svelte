<script lang="ts">
  /** One unbroken run of tool calls, sitting where it happened.
   *
   * The run is what the agent did between two things it said, so it stays between them
   * rather than being gathered up somewhere tidier. Only its newest entry is out — that
   * is what is happening — and the ones before it wait behind a count that opens this run
   * and no other. A run of one has nothing to hide and shows no count at all.
   *
   * When its turn is over the run belongs behind that turn's fold, and the turn owns
   * whether it is showing: one place to open a turn, not one per batch inside it.
   */
  import ToolCallRow from "./ToolCallRow.svelte";
  import {
    hiddenWorkSentence,
    VISIBLE_RUNNING_WORK_ENTRIES
  } from "../../lib/conversation/transcript";
  import type { ToolCallRow as ToolCallRowType } from "../../lib/conversation/transcript";

  let {
    entries,
    conversationId,
    hidden = false
  }: {
    entries: readonly ToolCallRowType[];
    /** Whose record these rows are, which is where a row's whole output is asked for. */
    conversationId: string;
    /** Its turn is settled and folded, so this run is behind the fold. */
    hidden?: boolean;
  } = $props();

  let expanded = $state(false);

  let hiddenCount = $derived(Math.max(0, entries.length - VISIBLE_RUNNING_WORK_ENTRIES));
  let visible = $derived(
    expanded ? entries : entries.slice(entries.length - VISIBLE_RUNNING_WORK_ENTRIES)
  );
  let toggleLabel = $derived(
    expanded ? "Show fewer tool calls" : hiddenWorkSentence(hiddenCount)
  );
</script>

{#if !hidden && entries.length > 0}
  <div
    class="c2-run"
    data-conversation-work-group
    data-conversation-work-expanded={expanded ? "true" : "false"}
  >
    <div class="acp-steps" data-conversation-work-entries>
      {#each visible as entry (entry.key)}
        <ToolCallRow row={entry} {conversationId} />
      {/each}
    </div>
    {#if hiddenCount > 0}
      <button
        type="button"
        class="c2-run-toggle"
        data-conversation-work-fold
        aria-expanded={expanded}
        onclick={() => (expanded = !expanded)}
      >
        <span aria-hidden="true" class="c2-run-chevron" class:is-open={expanded}>›</span>
        <span>{toggleLabel}</span>
      </button>
    {/if}
  </div>
{/if}

<style>
  /* The run hangs off the turn's head, on its own rule. */
  .c2-run {
    display: grid;
    min-width: 0;
    max-width: 100%;
    gap: var(--space-1);
    border-inline-start: var(--border-hairline) solid var(--border-color);
    margin-inline-start: var(--space-3);
    padding-inline-start: var(--space-3);
  }
  .c2-run-toggle {
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
  .c2-run-toggle:hover { color: var(--text-muted); background: var(--surface-overlay); }
  .c2-run-chevron {
    display: inline-block;
    transition: transform var(--motion-fast) var(--motion-ease);
  }
  .c2-run-chevron.is-open { transform: rotate(90deg); }
  @media (prefers-reduced-motion: reduce) {
    .c2-run-chevron { transition: none; }
  }
</style>
