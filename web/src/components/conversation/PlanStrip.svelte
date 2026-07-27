<script lang="ts">
  /** The agent's plan, as a count you can open into a checklist.
   *
   * A plan is one of the few things worth keeping after its turn is over: it is what the
   * agent said it would do, and how far it got. So this is fed by rows rather than by
   * frames — it survives a reload, and a conversation that never planned shows nothing
   * at all rather than an empty strip.
   *
   * Each plan row is the whole plan, so the newest one replaces the last outright. There
   * is no merging: a step that vanished from the plan is gone, not silently kept.
   */
  import {
    planProgressSentence,
    planProgressSpokenSentence
  } from "../../lib/conversation/transcript";
  import type { PlanEntry } from "../../lib/conversation/wire";

  let { entries }: { entries: readonly PlanEntry[] } = $props();

  let open = $state(false);

  let label = $derived(planProgressSentence(entries));

  function statusClass(status: string): string {
    if (status === "completed") return "done";
    if (status === "in_progress") return "now";
    return "pending";
  }

  function statusMark(status: string): string {
    if (status === "completed") return "✓";
    if (status === "in_progress") return "›";
    return "○";
  }
</script>

{#if entries.length > 0}
  <div class="c2-plan" data-conversation-plan data-conversation-plan-open={open ? "true" : "false"}>
    <button
      type="button"
      class="c2-plan-pill"
      data-conversation-plan-pill
      aria-expanded={open}
      aria-label={planProgressSpokenSentence(entries)}
      onclick={() => (open = !open)}
    >
      <span aria-hidden="true" class="c2-plan-chevron" class:is-open={open}>›</span>
      <span class="c2-plan-count">{label}</span>
    </button>
    {#if open}
      <div class="c2-plan-list" role="list" aria-label="Tasks" data-conversation-plan-list>
        {#each entries as entry, index (index)}
          <div
            class={`c2-plan-item c2-plan-item--${statusClass(entry.status)}`}
            role="listitem"
            data-conversation-plan-status={entry.status}
          >
            <span class="c2-plan-mark" aria-hidden="true">{statusMark(entry.status)}</span>
            <span class="c2-plan-text">{entry.text}</span>
          </div>
        {/each}
      </div>
    {/if}
  </div>
{/if}

<style>
  .c2-plan { display: grid; min-width: 0; max-width: 100%; gap: var(--space-1); }
  .c2-plan-pill {
    justify-self: start;
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-pill);
    background: var(--surface-raised);
    color: var(--text-muted);
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
    padding: var(--space-1) var(--space-3);
    font-variant-numeric: tabular-nums;
  }
  .c2-plan-pill:hover { color: var(--text-strong); background: var(--surface-overlay); }
  .c2-plan-chevron {
    display: inline-block;
    transition: transform var(--motion-fast) var(--motion-ease);
  }
  .c2-plan-chevron.is-open { transform: rotate(90deg); }
  .c2-plan-list {
    display: grid;
    gap: var(--space-1);
    max-height: calc(var(--type-xs) * 24);
    overflow: auto;
    padding-inline-start: var(--space-2);
  }
  .c2-plan-item {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
    min-width: 0;
    color: var(--text-muted);
    font-size: var(--type-sm);
  }
  .c2-plan-mark {
    flex: none;
    width: var(--space-4);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
  }
  .c2-plan-text { min-width: 0; overflow-wrap: anywhere; }
  .c2-plan-item--done {
    color: var(--text-faint);
    text-decoration: line-through;
    text-decoration-color: var(--text-faintest);
  }
  .c2-plan-item--done .c2-plan-mark { color: var(--accent-done); text-decoration: none; }
  .c2-plan-item--now { color: var(--text-strong); }
  .c2-plan-item--now .c2-plan-mark { color: var(--accent-bright); }
  @media (prefers-reduced-motion: reduce) {
    .c2-plan-chevron { transition: none; }
  }
</style>
