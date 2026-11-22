<script lang="ts">
  /** One line in the thread that says what happened, rather than anything anybody said: a
   *  turn that failed or was interrupted, a message that was not delivered, a message that
   *  steered a running turn. They are one family, so they are drawn one way — the turn
   *  head's small UI voice, a mark in the line's tone, a plain sentence, what the person
   *  can do next when there is something, and the backend's exact words behind a
   *  disclosure when it said any. The tone sets the weight: a failure stands in its own
   *  calm panel, a stopped turn in a quiet pill, the rest as plain lines. */
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
  <div class="c2-status-body">
    <!-- The mark rides with the sentence, so a line under a message can end on the
         message's own edge, mark and all. -->
    <span class="c2-status-line">
      <svg class="c2-status-mark" viewBox="0 0 16 16" aria-hidden="true">
        {#if mark === "steer"}
          <path d="M4 3v4.5a2.5 2.5 0 0 0 2.5 2.5H12M9.5 7.5 12 10l-2.5 2.5" />
        {:else if status.tone === "error" || status.tone === "warning"}
          <circle cx="8" cy="8" r="6.25" /><path d="M8 4.75v3.75" /><path d="M8 11.1v.15" />
        {:else}
          <circle cx="8" cy="8" r="6.25" /><rect x="5.75" y="5.75" width="4.5" height="4.5" rx="0.75" class="c2-status-fill" />
        {/if}
      </svg>
      <span class="c2-status-title">{status.title}</span>
    </span>
    {#if status.next}<span class="c2-status-next" data-conversation-status-next>{status.next}</span>{/if}
    {#if status.detail}
      <!-- The backend's own words: kept whole and selectable, one step behind the plain
           sentence, open from the start when they are the only explanation there is. -->
      <details class="c2-status-more" open={status.detailOpen === true}>
        <summary class="c2-status-summary">
          <span>Details</span>
          <svg class="c2-status-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>
        </summary>
        <span class="c2-status-detail" data-conversation-status-detail>{status.detail}</span>
      </details>
    {/if}
  </div>
  {#if actions}<div class="c2-status-actions">{@render actions()}</div>{/if}
</div>

<style>
  .c2-status {
    align-self: flex-start;
    display: flex;
    align-items: flex-start;
    gap: var(--space-2);
    /* Its own size, wherever it stands: a panel or a pill hugs its words. */
    width: fit-content;
    min-width: 0;
    max-width: 100%;
    color: var(--text-muted);
    font-family: var(--font-ui);
    font-size: var(--type-small);
    line-height: 1.5;
    overflow-wrap: anywhere;
  }
  /* Under the person's message it ends where their bubble ends. */
  .c2-status--end { align-self: flex-end; text-align: end; }
  .c2-status--end .c2-status-body { justify-items: end; }
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
  .c2-status-line { display: flex; align-items: flex-start; gap: var(--space-2); min-width: 0; }
  .c2-status-title { color: var(--text-default); }
  /* What to do next and the disclosure sit in from the mark, under the sentence. */
  .c2-status-next, .c2-status-more { padding-inline-start: calc(14px + var(--space-2)); }
  .c2-status-next { color: var(--text-muted); }
  .c2-status-more { min-width: 0; }
  .c2-status-summary {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    min-height: 24px;
    color: var(--text-faintest);
    cursor: pointer;
    list-style: none;
    border-radius: var(--radius-sm);
  }
  .c2-status-summary::-webkit-details-marker { display: none; }
  .c2-status-summary:hover, .c2-status-summary:focus-visible { color: var(--text-muted); }
  .c2-status-summary:focus-visible { outline: var(--focus-width) solid var(--focus-ring); outline-offset: 2px; }
  .c2-status-chevron {
    flex: none;
    width: 12px;
    height: 12px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
    transition: transform var(--motion-fast) var(--motion-ease);
  }
  .c2-status-more[open] .c2-status-chevron { transform: rotate(90deg); }
  /* The backend's words in the tool voice, wrapped whole inside the line's own edges, a
     step quieter than the sentence but still body-legible (the secondary tone). */
  .c2-status-detail {
    display: block;
    padding-block: 2px var(--space-1);
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
    line-height: 1.55;
    white-space: pre-wrap;
    text-align: start;
    user-select: text;
  }
  .c2-status-actions { flex: none; display: flex; align-items: center; gap: var(--space-1); margin-block-start: -2px; }

  /* A failure is its own calm panel: a faint wash and hairline in the error colour, the
     sentence in the body tone, so it carries the weight of something that went wrong
     without shouting. Under a message it keeps the bubble's right edge, and its text
     reads from its own left edge like any panel. */
  .c2-status--error {
    max-width: min(100%, var(--page-prose-measure));
    padding: var(--space-2) var(--space-3);
    border: 1px solid color-mix(in srgb, var(--accent-error) 32%, transparent);
    border-radius: var(--radius-md);
    background: color-mix(in srgb, var(--accent-error) 8%, transparent);
    text-align: start;
  }
  .c2-status--error .c2-status-body { justify-items: start; }
  .c2-status--error .c2-status-mark { color: var(--accent-error); }
  .c2-status--warning .c2-status-mark { color: var(--accent-attention); }
  /* A turn that stopped before it finished: more than a turn's metadata, less than a
     failure. A quiet pill on the page's raised step, its mark and words a step brighter
     than a "Worked for" line. */
  .c2-status--stopped {
    padding: 2px var(--space-3) 2px var(--space-2);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-pill);
    background: var(--surface-2);
  }
  .c2-status--stopped .c2-status-mark { color: var(--text-muted); }
  .c2-status--neutral .c2-status-mark { color: var(--text-faintest); }
  .c2-status--neutral .c2-status-title { color: var(--text-muted); }
  .c2-status--quiet { color: var(--text-faintest); }
  .c2-status--quiet .c2-status-title { color: var(--text-faintest); }
  @media (prefers-reduced-motion: reduce) { .c2-status-chevron { transition: none; } }
</style>
