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
  import { registerCallout } from "../../lib/conversation/newestCallout";
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
  /** v4: every error is one callout: a dot and what failed, then the reason in the prose
   *  voice, then its actions. A failed turn says "This turn failed"; a message that did not
   *  get through says so; the plain cause and the next step are the reason under it. */
  let calloutHeading = $derived.by(() => {
    if (status.tone !== "error") return status.title;
    return align === "end" ? "This message was not delivered" : "This turn failed";
  });
  let calloutReason = $derived.by(() => {
    if (status.tone !== "error") return status.next ?? null;
    if (status.brief) return status.brief;
    const cause = status.title
      .replace(/^Not delivered:\s*/, "")
      .replace(/^The turn stopped because\s*/, "");
    const said = cause.charAt(0).toUpperCase() + cause.slice(1);
    // v8 (9): a message that was not delivered says only its cause ("The backend did not
    // start."); its buttons (Resend, Choose another model) say the rest.
    if (align === "end" && (actions !== undefined || status.action === "choose-model")) return said;
    return [said, status.next].filter(Boolean).join(" ");
  });
  /** v6: an older error says its short cause on one inline row: the first sentence of it. */
  let shortCause = $derived.by(() => {
    const cause = status.title.replace(/^Not delivered:\s*/, "").replace(/^The turn stopped because\s*/, "").trim();
    const said = cause.charAt(0).toUpperCase() + cause.slice(1);
    return (said.match(/^.*?[.!?](?=\s|$)/)?.[0] ?? said).replace(/[.!?]$/, "");
  });
  /** v5: only the newest error callout in the conversation keeps the filled primary; older
   *  ones draw every action unfilled. Until it is measured (and on the server) a callout counts
   *  as the newest. */
  let newest = $state(true);
  $effect(() => {
    if (root === null || shownWeight !== "callout" || status.tone !== "error") return;
    return registerCallout(root, (value) => (newest = value));
  });
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
      <svg class="c2-status-chevron" class:is-open={detailsOpen} viewBox="0 0 16 16" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg>
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

{#if shownWeight === "callout" && status.tone === "error" && !newest}
  <!-- v6: only the newest error is a card with actions. An older one is one inline row: the
       red dot, "This turn failed: <short cause>", then "Details ›". No tint, no edge, no
       other actions. -->
  <div bind:this={root} class="c2-status c2-status--inline c2-status--error" data-conversation-callout-newest="false" data-conversation-error-inline {...attributes}>
    <span class="c2-status-inline-line">
      <span class="c2-status-slot" aria-hidden="true"><span class="c2-status-dot"></span></span>
      <span class="c2-status-inline-words"><span class="c2-status-inline-head">{calloutHeading}</span><span class="c2-status-sep" aria-hidden="true">·</span><span>{shortCause}</span></span>
      {@render detailsToggle()}
    </span>
    {@render details()}
  </div>
{:else if shownWeight === "callout"}
  <!-- v4.1: a callout is always the full column width and left-aligned, whatever it belongs
       to; a message's callout says which message by its heading. -->
  <div bind:this={root} class="c2-status c2-status--callout c2-status--{status.tone}" class:is-older={!newest} data-conversation-callout-newest={newest ? "true" : "false"} {...attributes}>
    <span class="c2-status-line"><span class="c2-status-slot" aria-hidden="true"><span class="c2-status-dot"></span></span><span class="c2-status-title">{calloutHeading}</span></span>
    {#if calloutReason}<p class="c2-status-reason" data-conversation-status-next>{calloutReason}</p>{/if}
    {#if actions || status.detail || status.action === "choose-model"}
      <div class="c2-status-actions">
        {#if actions}{@render actions()}{/if}
        {#if status.action === "choose-model"}
          <!-- v9 (7): with no other action it is the card's one primary; beside Resend it is
               secondary (one primary per surface). -->
          <button type="button" class="cv-button c2-status-choose" class:c2-status-main={actions === undefined} class:c2-status-second={actions !== undefined} data-conversation-status-choose-model onclick={chooseAnotherModel}>Choose another model</button>
        {/if}
        {@render detailsToggle()}
      </div>
    {/if}
    {@render details()}
  </div>
{:else}
  <div bind:this={root} class="c2-status c2-status--note c2-status--{status.tone}" class:c2-status--end={align === "end"} {...attributes}>
    <div class="c2-status-body">
      <!-- v7 status line: a 16px icon slot, the label (500 #ececec), then " · " and the
           detail (400 #b0b0b0). The mark rides with the sentence, so a line under a message
           ends on the message's own edge, mark and all. -->
      <span class="c2-status-line"><span class="c2-status-slot">{@render glyph()}</span><span class="c2-status-title">{status.title}</span>{#if status.next}<span class="c2-status-sep" aria-hidden="true">·</span><span class="c2-status-next" data-conversation-status-next>{status.next}</span>{/if}</span>
      {#if actions || status.detail}<div class="c2-status-actions c2-status-actions--note c2-status-indent">{#if actions}{@render actions()}{/if}{@render detailsToggle()}</div>{/if}
      {#if status.detail}<div class="c2-status-indent">{@render details()}</div>{/if}
    </div>
  </div>
{/if}

<style>
  /* v7: every line here is a Status line (13/20: a 16px icon slot, the label 500 #ececec, then
     " · " and the detail 400 #b0b0b0), and the newest error is a Surface card. Older errors are
     one status line with a #f08a7e dot. Actions are Buttons: primary (Resend), secondary
     (Choose another model), quiet (Details). */
  .c2-status {
    min-width: 0;
    max-width: 100%;
    color: var(--cv-detail-ink, #b0b0b0);
    font-family: var(--cv-font);
    font-size: 13px;
    line-height: 20px;
    overflow-wrap: anywhere;
  }
  .c2-status-slot { flex: none; display: inline-grid; place-items: center; width: 16px; height: 20px; }
  .c2-status-mark {
    flex: none;
    width: 16px;
    height: 16px;
    fill: none;
    stroke: var(--cv-quiet-ink, #bdbdbd);
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .c2-status-fill { fill: currentColor; stroke: none; }
  .c2-status-body { display: grid; min-width: 0; }
  .c2-status-line { display: flex; align-items: flex-start; gap: 8px; min-width: 0; }
  .c2-status-title { color: var(--cv-ink, #ececec); font-weight: var(--cv-weight-label); }
  .c2-status-sep { margin-inline: -2px; color: var(--cv-detail-ink, #b0b0b0); }
  .c2-status-next { color: var(--cv-detail-ink, #b0b0b0); font-weight: var(--cv-weight-body); }
  .c2-status-dot { display: block; width: 8px; height: 8px; border-radius: 50%; background: var(--cv-detail-ink, #b0b0b0); }
  .c2-status--error .c2-status-dot { background: #f08a7e; }

  /* A note: one status line, its own width, under the bubble or in the turn. */
  .c2-status--note { align-self: flex-start; display: flex; width: fit-content; }
  .c2-status--note .c2-status-body { gap: 8px; }
  .c2-status--end { align-self: flex-end; text-align: end; }
  .c2-status--end .c2-status-body { justify-items: end; }
  .c2-status-indent { padding-inline-start: 24px; }
  .c2-status--end .c2-status-indent { padding-inline-start: 0; }
  /* Failure is said by the #f08a7e mark alone; words stay in the status tones. */
  .c2-status--note.c2-status--error .c2-status-mark { stroke: #f08a7e; }

  /* An older error, inline: dot · "This turn failed" · the short cause, Details after it. */
  .c2-status--inline { align-self: stretch; display: grid; text-align: start; }
  .c2-status-inline-line { display: flex; align-items: center; gap: 8px; min-width: 0; min-height: 32px; }
  .c2-status-inline-words { display: flex; gap: 8px; min-width: 0; overflow: hidden; white-space: nowrap; }
  .c2-status-inline-words > :last-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  .c2-status-inline-head { flex: none; color: var(--cv-ink, #ececec); font-weight: var(--cv-weight-label); }
  .c2-status--inline .c2-status-toggle { flex: none; }

  /* The newest error: a Surface (#1c1c1c, a 1px #2a2a2a edge, radius 20), padding 16. Its
     status line, the reason in the UI voice (15/24 #ececec), then its buttons. */
  .c2-status--callout {
    align-self: stretch;
    display: grid;
    padding: 16px;
    border: 1px solid var(--cv-surface-edge, #2a2a2a);
    border-radius: var(--cv-surface-radius, 20px);
    background: var(--cv-surface, #1c1c1c);
    text-align: start;
  }
  .c2-status-reason {
    margin: 8px 0 0;
    color: var(--cv-ink, #ececec);
    font-size: 15px;
    line-height: 24px;
  }
  .c2-status--callout .c2-status-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-block-start: 16px;
  }

  /* Buttons (v7 part 2): 32 tall pills, 13/20 500, padding 0 14, a 16px icon 6px before
     its word. Primary #f2f2f2 on #141414; secondary #2a2a2a with #ececec; quiet no fill,
     #bdbdbd, hover #2a2a2a. */
  .c2-status-toggle {
    gap: 6px;
    height: 32px;
    min-height: 32px;
    padding: 0 10px 0 14px;
    border-radius: 999px;
    background: transparent;
    color: var(--cv-quiet-ink, #bdbdbd);
    font-size: 13px;
    line-height: 20px;
    font-weight: var(--cv-weight-label);
  }
  .c2-status-toggle:hover, .c2-status-toggle[aria-expanded="true"] { background: #2a2a2a; color: var(--cv-ink, #ececec); }
  :global(.c2-status-main), :global(.c2-status-second) {
    height: 32px;
    min-height: 32px;
    padding: 0 14px;
    border-radius: 999px;
    font-size: 13px;
    line-height: 20px;
    font-weight: var(--cv-weight-label);
  }
  :global(.c2-status-main) { background: #f2f2f2; color: #141414; }
  :global(.c2-status-main:hover:not(:disabled)) { background: #ffffff; color: #141414; }
  :global(.c2-status-main:disabled) { background: #2a2a2a; color: #8c8c8c; }
  :global(.c2-status-second) { background: #2a2a2a; color: var(--cv-ink, #ececec); }
  :global(.c2-status-second:hover:not(:disabled)) { background: #3a3a3a; color: var(--cv-ink, #ececec); }
  .c2-status-chevron {
    flex: none;
    width: 16px;
    height: 16px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
    transition: transform var(--cv-dur-expand) var(--cv-ease-out);
  }
  .c2-status-chevron.is-open { transform: rotate(90deg); }

  /* The backend's words: a code block (#1a1a1a, a 1px #2a2a2a edge, radius 12, 13/20 mono). */
  .c2-status-more { position: relative; min-width: 0; margin-block-start: 8px; }
  .c2-status-more[hidden] { display: none; }
  .c2-status-detail {
    display: block;
    padding: 12px 48px 12px 16px;
    border: 1px solid var(--cv-surface-edge, #2a2a2a);
    border-radius: 12px;
    background: var(--cv-code-surface, #1a1a1a);
    color: var(--cv-detail-ink, #b0b0b0);
    font-family: var(--cv-mono);
    font-size: 13px;
    line-height: 20px;
    white-space: pre-wrap;
    text-align: start;
    user-select: text;
  }
  .c2-status-copy { position: absolute; top: 4px; right: 4px; }
  .c2-status-actions--note { display: flex; align-items: center; gap: 8px; }
  .c2-status--end .c2-status-actions--note { justify-content: flex-end; }
  @media (prefers-reduced-motion: reduce) { .c2-status-chevron { transition: none; } }
</style>
