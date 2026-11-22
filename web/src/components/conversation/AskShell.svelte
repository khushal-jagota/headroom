<script lang="ts">
  /** The one shell every blocking ask wears: a question, a permission ask, a key request and
   * a proposal. An accent eyebrow says what it needs from the person, the title says what,
   * the content is the thing to decide on, and the footer ends in the one action that
   * commits. Meaning is carried by place, weight and tone, never by a coloured rule.
   *
   * It draws its own raised plane. Docked in the composer's place, the composer's box gives
   * up its own fill and edge for it (data-ask-shell), so the two never nest.
   *
   * Every ask answers with one kind of tile (.cv-ask-row, styled here for all of them): a
   * number chip on the left, a label, an optional line under it. While the turn waits on the
   * person, stopping it is "Stop turn": v10 (3) the composer draws it in the ask's footer, on
   * the left, for every ask; a shell standing alone keeps it in its header (onStop). */
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";

  let {
    kind,
    eyebrow,
    context,
    title,
    titleId,
    description = null,
    children,
    footerStart,
    footerEnd,
    replyStrip,
    onStop,
    stopDisabled = false,
    framed = true,
    ...rest
  }: {
    kind: "question" | "permission" | "credential" | "proposal";
    /** What it needs: "Needs your approval", "Needs your answer", "Needs a key". */
    eyebrow: string;
    /** Quiet facts on the eyebrow's right ("1 of 2", "Implementation · approver You"). */
    context?: Snippet;
    title: string;
    titleId?: string;
    description?: string | null;
    children?: Snippet;
    /** Anything on the left of the footer (none of the asks draws one: the turn is stopped
     *  from the composer's own stop button). */
    footerStart?: Snippet;
    /** The answers, ending in the one primary, on the right. */
    footerEnd?: Snippet;
    /** The composer's one-line reply, under the ask, when it docks in the composer's place. */
    replyStrip?: Snippet;
    /** Ends the turn that waits on this ask: drawn as the quiet last row of the answers. */
    onStop?: () => void;
    stopDisabled?: boolean;
    framed?: boolean;
  } & Omit<HTMLAttributes<HTMLElement>, "title"> = $props();
</script>

<section class="cv-ask" class:is-framed={framed} data-ask-shell data-ask-kind={kind} {...rest}>
  <div class="cv-ask-body">
    <div class="cv-ask-eyebrow">
      {#if kind === "permission"}
        <!-- v5: a permission ask leads with a 12px shield in the accent, not the dot. -->
        <svg class="cv-ask-shield" data-ask-shield viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.75 13.25 3.7v3.8c0 3.3-2.2 5.5-5.25 6.75C4.95 13 2.75 10.8 2.75 7.5V3.7z" /></svg>
      {:else}
        <span class="cv-ask-dot" aria-hidden="true"></span>
      {/if}
      <span class="cv-ask-need">{eyebrow}</span>
      {#if onStop || context}
        <span class="cv-ask-context">
          {#if onStop}
            <!-- v4.1: while the turn waits on the person, stopping it is a small real button in
                 the ask's own header (stop square + "Stop turn"), the one way to stop there is. -->
            <button type="button" class="cv-ask-stop" data-conversation-stop={true} data-conversation-ask-stop
              title="Stop turn" disabled={stopDisabled} onclick={() => onStop?.()}><svg class="cv-ask-stop-mark" viewBox="0 0 16 16" aria-hidden="true"><rect x="4" y="4" width="8" height="8" rx="2" /></svg>Stop turn</button>
          {/if}
          {#if context}{@render context()}{/if}
        </span>
      {/if}
    </div>
    <h3 class="cv-ask-title" id={titleId}>{title}</h3>
    {#if description}<p class="cv-ask-description">{description}</p>{/if}
    {#if children}<div class="cv-ask-content">{@render children()}</div>{/if}
  </div>
  {#if footerStart || footerEnd}
    <div class="cv-ask-footer">
      {#if footerStart}<div class="cv-ask-footer-start">{@render footerStart()}</div>{/if}
      {#if footerEnd}<div class="cv-ask-footer-end">{@render footerEnd()}</div>{/if}
    </div>
  {/if}
  {#if replyStrip}<div class="cv-ask-reply">{@render replyStrip()}</div>{/if}
</section>

<style>
  .cv-ask {
    min-width: 0;
    width: 100%;
    box-sizing: border-box;
    color: var(--cv-t2);
    font-family: var(--cv-font);
    overflow-wrap: anywhere;
    animation: cv-ask-in var(--cv-dur-base) var(--cv-ease-out);
  }
  .cv-ask.is-framed {
    background: var(--cv-raised);
    border: 1px solid var(--cv-line-strong);
    border-radius: var(--cv-r-xl);
  }
  .cv-ask-body { padding: var(--cv-s4) 20px 0; min-width: 0; }
  .cv-ask-eyebrow {
    display: flex;
    align-items: center;
    gap: var(--cv-s2);
    min-width: 0;
    color: var(--cv-accent);
    font: var(--cv-weight-title) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font);
  }
  .cv-ask-dot { flex: none; width: 7px; height: 7px; border-radius: var(--cv-r-pill); background: var(--cv-accent); }
  .cv-ask-shield { flex: none; width: 12px; height: 12px; fill: none; stroke: var(--cv-accent); stroke-width: 1.3; stroke-linejoin: round; }
  .cv-ask-need { flex: none; }
  .cv-ask-context {
    display: inline-flex;
    align-items: center;
    gap: var(--cv-s1);
    min-width: 0;
    margin-inline-start: auto;
    color: var(--cv-t3);
    font-weight: var(--cv-weight-body);
    font-variant-numeric: tabular-nums;
  }
  /* v4.2: the pager's arrows stay in the meta tone, #a3a3a3, even when one cannot go further. */
  .cv-ask-context :global(.cv-icon-button) { color: var(--cv-t3); }
  /* v5.1: a pager arrow that cannot go further; v9 (22): #a6a6a6. */
  .cv-ask-context :global(.cv-icon-button:disabled) { color: #a6a6a6; }
  .cv-ask-context :global(.cv-icon-button:hover:not(:disabled)) { color: var(--cv-t1); }
  .cv-ask-title {
    margin: 6px 0 0;
    color: var(--cv-t1);
    font: var(--cv-weight-title) 16px/24px var(--cv-font);
  }
  .cv-ask-description {
    margin: var(--cv-s1) 0 0;
    color: var(--cv-t3);
    font: var(--cv-weight-body) var(--cv-type-ui)/22px var(--cv-font);
  }
  .cv-ask-content { margin-block-start: var(--cv-s4); min-width: 0; }
  /* The ghost on the left lines its words up with the body's 20px edge (8 + its 12). */
  .cv-ask-footer {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--cv-s2);
    padding: var(--cv-s4) var(--cv-s4) var(--cv-s4) var(--cv-s2);
  }
  .cv-ask-footer-start { display: flex; align-items: center; gap: var(--cv-s2); }
  .cv-ask-footer-end {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: flex-end;
    gap: var(--cv-s2);
    min-width: 0;
    margin-inline-start: auto;
  }
  .cv-ask-reply { padding: 0 var(--cv-s2) var(--cv-s2); }
  /* Stop turn (v4.1): a small real button, 28px high, #242424, radius 8, a 10px stop square
     and the label 13 in #d9d9d9. 16px before the pager (the context's 4px gap + 12). */
  .cv-ask-stop {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 28px;
    padding: 0 10px;
    margin-inline-end: 12px;
    border: 0;
    border-radius: 8px;
    background: #242424;
    color: #d9d9d9;
    font: var(--cv-weight-body) 13px/18px var(--cv-font);
    cursor: pointer;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out), color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .cv-ask-stop:last-child { margin-inline-end: 0; }
  .cv-ask-stop-mark { flex: none; width: 10px; height: 10px; fill: currentColor; }
  .cv-ask-stop:hover:not(:disabled) { color: var(--cv-t1); background: #2a2a2a; }
  .cv-ask-stop:focus-visible { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: 1px; }
  .cv-ask-stop:disabled { cursor: default; }

  /* --- the one answer tile every ask uses (v4) ------------------------------------------- */
  /* Tiles 6px apart, radius 12, padding 12 16 (v5): filled #262626 at rest on the #1c1c1c
     composer so they read as clickable, #2c2c2c on hover. The chosen one is #2e2e2e with a 1px
     accent edge, its number chip in the accent; the others' keycaps are #333333 with a #d9d9d9
     number. Every answer, a declining one too, is labelled in the same tone. */
  :global(.cv-ask-rows) { display: grid; gap: 6px; }
  :global(.cv-ask-row) {
    display: flex;
    align-items: center;
    gap: var(--cv-s3);
    width: 100%;
    box-sizing: border-box;
    padding: var(--cv-s3) var(--cv-s4);
    border: 0;
    border-radius: var(--cv-r-lg);
    background: #262626;
    color: var(--cv-t1);
    cursor: pointer;
    font: var(--cv-weight-body) var(--cv-type-prose)/var(--cv-leading-ui) var(--cv-font);
    text-align: start;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out), box-shadow var(--cv-dur-fast) var(--cv-ease-out);
  }
  :global(.cv-ask-row:hover:not(:disabled)) { background: #2c2c2c; }
  :global(.cv-ask-row.is-selected), :global(.cv-ask-row.is-selected:hover:not(:disabled)) { background: #2e2e2e; box-shadow: inset 0 0 0 1px rgb(188 203 240 / 55%); }
  :global(.cv-ask-row:focus-visible) { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: 2px; }
  :global(.cv-ask-row:disabled) { cursor: default; }
  :global(.cv-ask-key) {
    flex: none;
    display: inline-grid;
    place-items: center;
    width: 24px;
    height: 24px;
    box-sizing: border-box;
    border-radius: var(--cv-r-sm);
    background: #333333;
    color: #d9d9d9;
    font: var(--cv-weight-label) var(--cv-type-caption)/1 var(--cv-font);
    font-variant-numeric: tabular-nums;
  }
  :global(.cv-ask-row.is-selected .cv-ask-key) { background: var(--cv-accent); color: #121212; }
  :global(.cv-ask-key svg) { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
  :global(.cv-ask-words) { display: grid; gap: 2px; flex: 1; min-width: 0; }
  :global(.cv-ask-label) { overflow-wrap: anywhere; color: var(--cv-t1); }
  :global(.cv-ask-sub) {
    color: var(--cv-t3);
    font: var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font);
    overflow-wrap: anywhere;
  }
  /* ---- v7, inside the conversation pane only (the proposal card on the page keeps its own) ---- */
  /* Standing alone, an ask is a Surface: #1c1c1c, a 1px #2a2a2a edge, radius 20. */
  :global([data-conversation-pane]) .cv-ask.is-framed { background: #1c1c1c; border: 1px solid #2a2a2a; border-radius: 20px; }
  /* The eyebrow is a Status line: a 16px icon slot (the 8px accent dot, or the 16px shield), the
     label 13/20 500 in the accent. */
  :global([data-conversation-pane]) .cv-ask-eyebrow { min-height: 32px; font: var(--cv-weight-label) 13px/20px var(--cv-font); }
  :global([data-conversation-pane]) .cv-ask-dot { width: 8px; height: 8px; margin-inline: 4px; }
  :global([data-conversation-pane]) .cv-ask-shield { width: 16px; height: 16px; stroke-width: 1.5; }
  /* The pager: "1 of 2" is status detail (#b0b0b0); its arrows are quiet Buttons (32 round,
     #bdbdbd, hover #2a2a2a). */
  :global([data-conversation-pane]) .cv-ask-context { gap: 0; color: #b0b0b0; }
  :global([data-conversation-pane]) .cv-ask-context :global(.cv-icon-button) { width: 32px; height: 32px; border-radius: 999px; color: #bdbdbd; }
  :global([data-conversation-pane]) .cv-ask-context :global(.cv-icon-button svg) { width: 16px; height: 16px; }
  /* v13 (5): an unavailable pager arrow (back on the first question, forward on the last) is
     #5a5a5a and takes no pointer at all (no hover, no pointer cursor). */
  :global([data-conversation-pane]) .cv-ask-context :global(.cv-icon-button:disabled) { color: #5a5a5a; background: none; cursor: default; pointer-events: none; }
  :global([data-conversation-pane]) .cv-ask-context :global(.cv-icon-button:hover:not(:disabled)) { background: #2a2a2a; color: #ececec; }
  :global([data-conversation-pane]) .cv-ask-context :global(.count) { margin-inline: 0 4px; }
  :global([data-conversation-pane]) .cv-ask-title { margin-block-start: 8px; color: #ececec; }
  :global([data-conversation-pane]) .cv-ask-description { margin-block-start: 8px; color: #b0b0b0; font: var(--cv-weight-body) 13px/20px var(--cv-font); }
  /* v9 (9): Stop turn is a secondary Button: 32 tall pill, #2a2a2a, #ececec, hover #333,
     13/20 500, a 16px stop icon 6px before its word. */
  :global([data-conversation-pane]) .cv-ask-stop {
    height: 32px;
    padding: 0 14px 0 10px;
    margin-inline-end: 8px;
    border-radius: 999px;
    background: #2a2a2a;
    color: #ececec;
    font: var(--cv-weight-label) 13px/20px var(--cv-font);
  }
  :global([data-conversation-pane]) .cv-ask-stop:hover:not(:disabled) { background: #333333; color: #ececec; }
  /* Its pill's right edge stands on the option rows' right edge. */
  :global([data-conversation-pane]) .cv-ask-stop:last-child { margin-inline-end: 0; }
  :global([data-conversation-pane]) .cv-ask-stop-mark { width: 16px; height: 16px; }
  /* Option rows (part 5): min 48, #242424, radius 12, padding 12 16, 8px apart; a 24px #2e2e2e
     keycap (radius 6, 13 500); the title 15/22 #ececec and its description 13/20 #b0b0b0. The
     focused (and chosen) row: a 1px #bccbf0 edge at 55% and an accent keycap with a #141414
     digit. */
  :global([data-conversation-pane] .cv-ask-rows) { gap: 8px; }
  :global([data-conversation-pane] .cv-ask-row) { min-height: 48px; padding: 12px 16px; border-radius: 12px; background: #242424; font: var(--cv-weight-body) 15px/22px var(--cv-font); }
  /* v11 (2): on the #1c1c1c ask a row is #2a2a2a with a 1px #363636 edge (drawn inside, so the
     row keeps its size); hover and keyboard focus #303030 with the accent edge. */
  :global([data-conversation-pane] .cv-ask-row) { background: #2a2a2a; box-shadow: inset 0 0 0 1px #363636; }
  /* v10 (4): a row is the action. Under the pointer or keyboard focus (and a question's
     answer already chosen) it is #2c2c2c with the accent edge and an accent keycap. Nothing is
     lit when the pointer is not on it; the accent edge is the focus mark (no extra ring). */
  :global([data-conversation-pane] :is(.cv-ask-row.is-selected, .cv-ask-row:hover:not(:disabled), .cv-ask-row:focus-visible)) { background: #303030; box-shadow: inset 0 0 0 1px rgb(188 203 240 / 55%); }
  :global([data-conversation-pane] .cv-ask-row:focus-visible) { outline: none; }
  /* v13 (4): 1 is the default key, so the first row wears the keyboard-focus style at rest
     (#303030, the accent edge, an accent keycap) while no other row is under the pointer, has
     keyboard focus or is chosen. Deny keeps its #f08a7e label. */
  :global([data-conversation-pane] .cv-ask-rows:not(:has(.cv-ask-row.is-selected, .cv-ask-row:hover:not(:disabled), .cv-ask-row:focus-visible)) > .cv-ask-row:first-child:not(:disabled)) { background: #303030; box-shadow: inset 0 0 0 1px rgb(188 203 240 / 55%); }
  :global([data-conversation-pane] .cv-ask-rows:not(:has(.cv-ask-row.is-selected, .cv-ask-row:hover:not(:disabled), .cv-ask-row:focus-visible)) > .cv-ask-row:first-child:not(:disabled) .cv-ask-key) { background: var(--cv-accent); color: #141414; }
  :global([data-conversation-pane] .cv-ask-key) { border-radius: 6px; background: #3a3a3a; color: #d9d9d9; font: var(--cv-weight-label) 13px/1 var(--cv-font); }
  :global([data-conversation-pane] :is(.cv-ask-row.is-selected, .cv-ask-row:hover:not(:disabled), .cv-ask-row:focus-visible) .cv-ask-key) { background: var(--cv-accent); color: #141414; }
  :global([data-conversation-pane] .cv-ask-label) { color: #ececec; }
  :global([data-conversation-pane] .cv-ask-sub) { color: #b0b0b0; font: var(--cv-weight-body) 13px/20px var(--cv-font); }
  @keyframes cv-ask-in { from { opacity: 0; transform: translateY(4px); } }
  @media (prefers-reduced-motion: reduce) { .cv-ask { animation: none; } }
  @media (max-width: 560px) {
    .cv-ask-body { padding-inline: var(--cv-s4); }
    .cv-ask-footer { padding-inline: var(--cv-s1) var(--cv-s3); }
  }
</style>
