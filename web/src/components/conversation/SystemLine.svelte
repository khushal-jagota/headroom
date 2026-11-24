<script lang="ts">
  /** The conversation's one system line (owner's review, B3): every line that says what
   *  happened rather than what anybody said. "Worked for …", "Working · 2m 14s", "Stopped
   *  before it finished", "This turn failed · …", "From Panels · …", "Steered · …",
   *  "Queued · 1 of 2", "No messages yet", and the dock's status line.
   *
   *  Inter 13/20 #8f8f8f, no box, no border, no fill. An optional mark sits in a 16 px slot:
   *  a spinner while live, otherwise a 6 px dot (#8f8f8f, or #ff8f85 for a failure only). The mark
   *  follows the words, so every line starts at x = 0, the column's one left edge, with no gutter
   *  and nothing outside the column. Actions are #d0d0d0 text.
   *
   *  The words and actions are the caller's: a line knows its layout, not its meaning. */
  import type { Snippet } from "svelte";

  let {
    mark = null,
    turning = true,
    align = "start",
    children,
    actions,
    below,
    ...attributes
  }: {
    /** The leading mark, if any. "failure" is the one red dot. */
    mark?: "spinner" | "dot" | "failure" | null;
    /** Whether a spinner turns. */
    turning?: boolean;
    /** A caption under a person's message keeps the bubble's right edge; "center" is the
     *  empty conversation's one line. Everything else starts at the column's left edge. */
    align?: "start" | "end" | "center";
    children: Snippet;
    /** Text actions on the line (Details, Resend, Choose another model): #d0d0d0, 16 apart. */
    actions?: Snippet;
    /** What the line opens onto (the backend's own words behind Details). */
    below?: Snippet;
    role?: "status" | "alert" | "separator";
    title?: string;
    [attribute: `data-${string}`]: string | undefined;
  } = $props();

</script>

<!-- No whitespace between the parts: the line's text is exactly its words ("Working · 2m"). -->
<div
  class="cv-system-line cv-system-line--{align}"
  data-conversation-system-line={align}
  {...attributes}
><div class="cv-system-line-row"><span class="cv-system-line-words">{@render children()}</span>{#if mark !== null}<span class="cv-system-line-mark" aria-hidden="true" data-conversation-system-mark={mark}>{#if mark === "spinner"}<span class="cv-system-line-spinner" class:is-turning={turning} data-conversation-live-mark></span>{:else}<span class="cv-system-line-dot" class:is-failure={mark === "failure"}></span>{/if}</span>{/if}{#if actions}<span class="cv-system-line-actions">{@render actions()}</span>{/if}</div>{#if below}{@render below()}{/if}</div>

<style>
  .cv-system-line {
    position: relative;
    display: grid;
    min-width: 0;
    max-width: 100%;
    color: var(--cv-system-ink);
    font-family: var(--cv-font);
    font-size: var(--cv-type-meta);
    line-height: var(--cv-leading-meta);
    font-weight: var(--cv-weight-body);
    font-variant-numeric: tabular-nums;
    overflow-wrap: anywhere;
  }
  .cv-system-line--start { align-self: flex-start; justify-items: start; text-align: start; }
  .cv-system-line--end { align-self: flex-end; justify-items: end; text-align: end; }
  .cv-system-line--center { align-self: center; justify-items: center; text-align: center; }
  .cv-system-line-row {
    display: flex;
    align-items: flex-start;
    column-gap: var(--cv-s2);
    min-width: 0;
    max-width: 100%;
  }
  .cv-system-line-words { min-width: 0; }
  .cv-system-line-mark {
    flex: none;
    display: inline-grid;
    place-items: center;
    width: var(--cv-mark-slot);
    height: var(--cv-leading-meta);
  }
  .cv-system-line-dot {
    display: block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--cv-system-ink);
  }
  .cv-system-line-dot.is-failure { background: var(--cv-failure-ink); }
  .cv-system-line-spinner {
    box-sizing: border-box;
    width: 12px;
    height: 12px;
    border-radius: var(--cv-r-pill);
    border: 2px solid var(--cv-spinner-track);
    border-top-color: #d9d9d9;
    transform: rotate(35deg);
  }
  .cv-system-line-spinner.is-turning { animation: cv-system-line-spin var(--cv-spin) linear infinite; }
  @keyframes cv-system-line-spin { to { transform: rotate(395deg); } }
  .cv-system-line-actions {
    flex: none;
    display: inline-flex;
    align-items: center;
    gap: var(--cv-s4);
    margin-inline-start: var(--cv-s2);
  }
  /* Actions are text on the line: no fill, no pill, #d0d0d0, brighter on hover. */
  .cv-system-line-actions :global(button) {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    min-height: 0;
    height: auto;
    /* A 24 px target on a 20 px line: the extra 2 px above and below take no space. */
    padding: 2px 0;
    margin-block: -2px;
    border: 0;
    border-radius: 0;
    background: none;
    color: var(--cv-system-action-ink);
    font: inherit;
    cursor: pointer;
  }
  .cv-system-line-actions :global(button:hover:not(:disabled)) { background: none; color: var(--cv-hover-ink); }
  .cv-system-line-actions :global(button:focus-visible) {
    outline: var(--cv-focus-width) solid var(--cv-accent);
    outline-offset: 2px;
    border-radius: var(--cv-r-sm);
  }
  .cv-system-line-actions :global(button:disabled) { color: var(--cv-system-ink); cursor: default; }
  @media (prefers-reduced-motion: reduce) {
    .cv-system-line-spinner.is-turning { animation: none; }
  }
</style>
