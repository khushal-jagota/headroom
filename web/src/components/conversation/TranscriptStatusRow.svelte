<script lang="ts">
  /** One line in the thread that says what happened, rather than anything anybody said: a
   *  turn that failed or was interrupted, a message that was not delivered, a message that
   *  steered a running turn. They are one family, so they are drawn one way — the turn
   *  head's small UI voice, a mark in the line's tone, a plain sentence, and the backend's
   *  exact words beneath it, quieter, when it said any. Only the tone changes. */
  import type { Snippet } from "svelte";
  import type { TranscriptStatus } from "../../lib/conversation/transcript";
  let { status, align = "start", mark = null, actions, ...attributes }: {
    status: TranscriptStatus | (Omit<TranscriptStatus, "tone"> & { tone: "quiet" });
    /** A line about a message stands under that message; a line about a turn starts where the thread does. */
    align?: "start" | "end";
    /** A different mark from the tone's own, for a line that is not a problem. */
    mark?: "steer" | null;
    actions?: Snippet;
    [attribute: `data-${string}`]: string | undefined;
    role?: "status" | "alert";
  } = $props();
</script>

<div class="c2-status c2-status--{status.tone}" class:c2-status--end={align === "end"} {...attributes}>
  <svg class="c2-status-mark" viewBox="0 0 16 16" aria-hidden="true">
    {#if mark === "steer"}
      <path d="M4 3v4.5a2.5 2.5 0 0 0 2.5 2.5H12M9.5 7.5 12 10l-2.5 2.5" />
    {:else if status.tone === "error" || status.tone === "warning"}
      <circle cx="8" cy="8" r="6.25" /><path d="M8 4.75v3.75" /><path d="M8 11.1v.15" />
    {:else}
      <circle cx="8" cy="8" r="6.25" /><rect x="5.75" y="5.75" width="4.5" height="4.5" rx="0.75" class="c2-status-fill" />
    {/if}
  </svg>
  <div class="c2-status-body">
    <span class="c2-status-title">{status.title}</span>
    {#if status.detail}<span class="c2-status-detail" data-conversation-status-detail>{status.detail}</span>{/if}
  </div>
  {#if actions}<div class="c2-status-actions">{@render actions()}</div>{/if}
</div>

<style>
  .c2-status {
    align-self: flex-start;
    display: flex;
    align-items: flex-start;
    gap: var(--space-2);
    min-width: 0;
    max-width: 100%;
    color: var(--text-muted);
    font-family: var(--font-ui);
    font-size: var(--type-small);
    line-height: 1.5;
    overflow-wrap: anywhere;
  }
  .c2-status--end { align-self: flex-end; }
  .c2-status-mark {
    flex: none;
    width: 14px;
    height: 14px;
    /* Centred on the first line of the sentence. */
    margin-block-start: calc((1.5em - 14px) / 2);
    fill: none;
    stroke: currentColor;
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .c2-status-fill { fill: currentColor; stroke: none; }
  .c2-status-body { display: grid; gap: 2px; min-width: 0; }
  .c2-status-title { color: var(--text-default); }
  /* The backend's own words: kept whole and selectable, in the tool voice, a step quieter
     than the sentence that explains them. */
  .c2-status-detail {
    color: var(--text-faintest);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
    line-height: 1.55;
  }
  .c2-status-actions { flex: none; display: flex; align-items: center; gap: var(--space-1); margin-block-start: -2px; }
  .c2-status--error .c2-status-mark { color: var(--accent-error); }
  .c2-status--warning .c2-status-mark { color: var(--accent-attention); }
  .c2-status--neutral .c2-status-mark { color: var(--text-faintest); }
  .c2-status--neutral .c2-status-title { color: var(--text-muted); }
  .c2-status--quiet { color: var(--text-faintest); }
  .c2-status--quiet .c2-status-title { color: var(--text-faintest); }
</style>
