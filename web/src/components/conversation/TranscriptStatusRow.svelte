<script lang="ts">
  /** A line in the thread that says what happened, rather than anything anybody said: a
   *  turn that failed or was interrupted, a message that was not delivered, a message that
   *  steered a running turn. They are one family with two weights, and weight follows
   *  severity:
   *
   *  - a note is one 13 px line with its glyph, under a bubble or in a turn;
   *  - a callout is a block in the turn: a plain-language title, what the person can do
   *    next, the actions that really work, and the backend's own words behind Details.
   *
   *  Tone is said by the glyph and the words' colour, and by a faint wash on a failed
   *  callout. Never by a coloured edge or rule. */
  import { untrack, type Snippet } from "svelte";
  import type { TranscriptStatus } from "../../lib/conversation/transcript";
  let { status, align = "start", mark = null, weight = undefined, actions, ...attributes }: {
    status: TranscriptStatus | (Omit<TranscriptStatus, "tone"> & { tone: "quiet" });
    /** A line about a message stands under that message; a line about a turn starts where the thread does. */
    align?: "start" | "end";
    /** A different mark from the tone's own, for a line that is not a problem. */
    mark?: "steer" | null;
    /** Note or callout. By default a turn's failure or stop is a callout and the rest are notes. */
    weight?: "note" | "callout";
    actions?: Snippet;
    [attribute: `data-${string}`]: string | undefined;
    role?: "status" | "alert";
  } = $props();

  // A problem is a callout wherever it stands: a turn that failed, in the turn; a message
  // that was not delivered, under that message. One pattern for both: what stopped, what to
  // do, the one real next step as a button, and the backend's own words behind Details.
  let shownWeight = $derived(weight ?? (status.tone === "error" || (align === "start" && status.tone === "stopped") ? "callout" : "note"));
  // Open from the start when the backend's words are the only explanation there is.
  let detailsOpen = $state(untrack(() => status.detailOpen === true));
  let copied = $state(false);
  let root = $state<HTMLElement | null>(null);

  async function copyDetails(): Promise<void> {
    if (!status.detail) return;
    try {
      await navigator.clipboard.writeText(status.detail);
      copied = true;
      setTimeout(() => (copied = false), 1_500);
    } catch {
      copied = false;
    }
  }
  /** Opens the composer's model picker, where another model is chosen. */
  function chooseAnotherModel(): void {
    const trigger = root?.closest("[data-conversation-pane]")?.querySelector<HTMLButtonElement>("[data-conversation-model-picker] button[data-conversation-picker-trigger]");
    if (!trigger) return;
    trigger.focus();
    trigger.click();
  }
  const uid = $props.id();
  const detailsId = `c2-status-details-${uid}`;
</script>

{#snippet glyph()}
  <svg class="c2-status-mark" viewBox="0 0 16 16" aria-hidden="true">
    {#if mark === "steer"}
      <path d="M4 3v4.5a2.5 2.5 0 0 0 2.5 2.5H12M9.5 7.5 12 10l-2.5 2.5" />
    {:else if status.tone === "error" || status.tone === "warning"}
      <circle cx="8" cy="8" r="6.25" /><path d="M8 4.75v3.75" /><path d="M8 11.1v.15" />
    {:else if status.tone === "stopped"}
      <rect x="3.5" y="3.5" width="9" height="9" rx="2" class="c2-status-fill" />
    {:else}
      <circle cx="8" cy="8" r="6.25" /><path d="M5.5 8h5" />
    {/if}
  </svg>
{/snippet}

{#snippet detailsToggle()}
  {#if status.detail}
    <button type="button" class="cv-button cv-button--ghost cv-button--small c2-status-toggle" aria-expanded={detailsOpen} aria-controls={detailsId}
      data-conversation-status-details onclick={() => (detailsOpen = !detailsOpen)}>
      <span>Details</span>
      <svg class="c2-status-chevron" class:is-open={detailsOpen} viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>
    </button>
  {/if}
{/snippet}

{#snippet details()}
  {#if status.detail}
    <!-- The backend's own words: kept whole and selectable, one step behind the plain
         sentence, open from the start when they are the only explanation there is. -->
    <div class="c2-status-more" id={detailsId} hidden={!detailsOpen}>
      <span class="c2-status-detail" data-conversation-status-detail>{status.detail}</span>
      <button type="button" class="cv-icon-button cv-icon-button--small c2-status-copy" data-conversation-status-copy
        aria-label={copied ? "Details copied" : "Copy details"} title={copied ? "Copied" : "Copy details"} onclick={() => void copyDetails()}>
        <svg viewBox="0 0 16 16" aria-hidden="true">
          {#if copied}<path d="m3.5 8.5 3 3 6-7" />
          {:else}<rect x="5.5" y="5.5" width="8" height="8" rx="1.75" /><path d="M10.5 3.5v-.25A1.75 1.75 0 0 0 8.75 1.5h-5.5A1.75 1.75 0 0 0 1.5 3.25v5.5A1.75 1.75 0 0 0 3.25 10.5h.25" />{/if}
        </svg>
      </button>
    </div>
  {/if}
{/snippet}

{#if shownWeight === "callout"}
  <div bind:this={root} class="c2-status c2-status--callout c2-status--{status.tone}" class:c2-status--end={align === "end"} {...attributes}>
    <span class="c2-status-icon">{@render glyph()}</span>
    <div class="c2-status-body">
      <span class="c2-status-line"><span class="c2-status-title">{status.title}</span></span>
      {#if status.next}<span class="c2-status-next" data-conversation-status-next>{status.next}</span>{/if}
      {#if actions || status.detail || status.action === "choose-model"}
        <div class="c2-status-actions">
          {#if actions}{@render actions()}{/if}
          {#if status.action === "choose-model"}
            <button type="button" class="cv-button cv-button--primary cv-button--small" data-conversation-status-choose-model onclick={chooseAnotherModel}>Choose another model</button>
          {/if}
          {@render detailsToggle()}
        </div>
      {/if}
      {@render details()}
    </div>
  </div>
{:else}
  <div bind:this={root} class="c2-status c2-status--note c2-status--{status.tone}" class:c2-status--end={align === "end"} {...attributes}>
    <div class="c2-status-body">
      <!-- The mark rides with the sentence, so a line under a message can end on the
           message's own edge, mark and all. -->
      <span class="c2-status-line">{@render glyph()}<span class="c2-status-title">{status.title}</span></span>
      {#if status.next}<span class="c2-status-next c2-status-indent" data-conversation-status-next>{status.next}</span>{/if}
      {#if actions || status.detail}<div class="c2-status-actions c2-status-actions--note c2-status-indent">{#if actions}{@render actions()}{/if}{@render detailsToggle()}</div>{/if}
      {#if status.detail}<div class="c2-status-indent">{@render details()}</div>{/if}
    </div>
  </div>
{/if}

<style>
  .c2-status {
    min-width: 0;
    max-width: 100%;
    color: var(--cv-t3);
    font-family: var(--cv-font);
    font-size: var(--cv-type-meta);
    line-height: var(--cv-leading-meta);
    overflow-wrap: anywhere;
  }
  .c2-status-mark {
    flex: none;
    width: 14px;
    height: 14px;
    margin-block-start: 3px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .c2-status-fill { fill: currentColor; stroke: none; }
  .c2-status-body { display: grid; min-width: 0; }
  .c2-status-line { display: flex; align-items: flex-start; gap: 6px; min-width: 0; }

  /* Note: one line, its own width, under the bubble or in the turn. */
  .c2-status--note {
    align-self: flex-start;
    display: flex;
    align-items: flex-start;
    gap: var(--cv-s2);
    width: fit-content;
  }
  .c2-status--note .c2-status-body { gap: 2px; }
  .c2-status--end { align-self: flex-end; text-align: end; }
  .c2-status--end .c2-status-body { justify-items: end; }
  .c2-status-indent { padding-inline-start: 20px; }
  .c2-status--end .c2-status-indent { padding-inline-start: 0; }
  .c2-status--note .c2-status-title { color: var(--cv-t3); }
  .c2-status--note.c2-status--error { color: var(--cv-danger); }
  .c2-status--note.c2-status--error .c2-status-title { color: var(--cv-danger); }
  .c2-status--note.c2-status--warning { color: var(--cv-caution); }
  .c2-status--note.c2-status--warning .c2-status-title { color: var(--cv-caution); }
  .c2-status--note .c2-status-next { color: var(--cv-t3); }

  /* Callout: a block in the turn. The glyph column and a calm surface carry the weight;
     a failure adds only a faint wash of its colour and the colour of its glyph. */
  .c2-status--callout {
    align-self: stretch;
    display: grid;
    grid-template-columns: 20px minmax(0, 1fr);
    column-gap: 10px;
    padding: 14px var(--cv-s4);
    border: 1px solid var(--cv-line);
    border-radius: var(--cv-r-lg);
    background: var(--cv-well);
  }
  .c2-status--callout.c2-status--error {
    background: linear-gradient(var(--cv-danger-wash), var(--cv-danger-wash)), var(--cv-well);
  }
  .c2-status-icon { display: flex; justify-content: center; color: var(--cv-t2); }
  .c2-status-icon .c2-status-mark { width: 16px; height: 16px; margin-block-start: 2px; }
  .c2-status--callout.c2-status--error .c2-status-icon { color: var(--cv-danger); }
  .c2-status--callout.c2-status--warning .c2-status-icon { color: var(--cv-caution); }
  .c2-status--callout .c2-status-title {
    color: var(--cv-t1);
    font-size: var(--cv-type-ui);
    line-height: var(--cv-leading-ui);
    font-weight: var(--cv-weight-title);
  }
  .c2-status--callout .c2-status-next { margin-block-start: 2px; color: var(--cv-t3); }
  .c2-status--callout .c2-status-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--cv-s2);
    margin-block-start: var(--cv-s3);
  }
  /* A message's callout stands under that message, on its right edge, as wide as it needs. */
  .c2-status--callout.c2-status--end { align-self: flex-end; width: fit-content; max-width: 100%; text-align: start; }
  .c2-status--callout.c2-status--end .c2-status-body { justify-items: start; }

  /* Details is a quiet ghost beside the real next step, never above it. Its chevron sits
     right after its word. */
  .c2-status-toggle { gap: var(--cv-s1); padding-inline: var(--cv-s2) 6px; color: var(--cv-t3); }
  .c2-status-toggle:hover, .c2-status-toggle[aria-expanded="true"] { color: var(--cv-t1); }
  .c2-status-actions > .c2-status-toggle:first-child { margin-inline-start: calc(var(--cv-s2) * -1); }
  .c2-status-chevron {
    flex: none;
    width: 12px;
    height: 12px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
    transition: transform var(--cv-dur-expand) var(--cv-ease-out);
  }
  .c2-status-chevron.is-open { transform: rotate(90deg); }

  /* The backend's words: a canvas well with its own Copy in the corner. */
  .c2-status-more { position: relative; min-width: 0; margin-block-start: var(--cv-s2); }
  .c2-status-more[hidden] { display: none; }
  .c2-status-detail {
    display: block;
    padding: var(--cv-s2) 40px var(--cv-s2) var(--cv-s3);
    border-radius: var(--cv-r-md);
    background: var(--cv-canvas);
    color: var(--cv-t3);
    font-family: var(--cv-mono);
    font-size: var(--cv-type-output);
    line-height: var(--cv-leading-output);
    white-space: pre-wrap;
    text-align: start;
    user-select: text;
  }
  .c2-status-copy { position: absolute; top: 4px; right: 4px; }
  .c2-status--note .c2-status-detail { background: var(--cv-well); }
  .c2-status-actions--note { display: flex; align-items: center; gap: var(--cv-s2); margin-block-start: var(--cv-s1); }
  .c2-status--end .c2-status-actions--note { justify-content: flex-end; }
  @media (prefers-reduced-motion: reduce) { .c2-status-chevron { transition: none; } }
</style>
