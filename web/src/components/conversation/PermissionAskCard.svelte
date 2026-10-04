<script lang="ts">
  /** The band that says what the agent has stopped for.
   *
   * It sits on top of the composer and is deliberately one header line plus a bounded,
   * scrolling body — that bound is the whole of why it stays the composer's size instead
   * of growing to whatever the backend put in the request. Nothing is truncated and
   * nothing hides behind a hover; a structured request is laid out to be read.
   *
   * A question is not an approval, so it does not borrow approval's words. Its choices
   * are numbered and pickable by their number, and its raw request stays behind a
   * disclosure — the owner should be reading the question, not its payload.
   */
  import {
    askChoiceForDigit,
    askQuestionChoices,
    askShape
  } from "../../lib/conversation/composer";
  import { readableConversationDetail } from "../../lib/conversation/conversationDetail";
  import type { PermissionAskOption } from "../../lib/conversation/wire";

  let {
    ask,
    busy = false,
    note = null,
    onAnswer
  }: {
    ask: {
      askId: string;
      title: string;
      detail: string | null;
      options: readonly PermissionAskOption[];
    };
    busy?: boolean;
    note?: string | null;
    onAnswer: (optionId: string) => void;
  } = $props();

  let shape = $derived(askShape(ask));
  let choices = $derived(askQuestionChoices(ask));
  let detail = $derived(readableConversationDetail(ask.detail));
  let eyebrow = $derived(shape === "question" ? "question" : "pending approval");
  let rawOpen = $state(false);

  function onWindowKeydown(event: KeyboardEvent): void {
    if (shape !== "question" || busy) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const target = event.target;
    // Somebody typing a number into a field means the number, not the answer.
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return;
    const chosen = askChoiceForDigit(ask, Number.parseInt(event.key, 10));
    if (chosen === null) return;
    event.preventDefault();
    onAnswer(chosen.optionId);
  }
</script>

<svelte:window onkeydown={onWindowKeydown} />

<section
  class="c2-ask"
  aria-label={shape === "question" ? "Question" : "Permission ask"}
  data-conversation-ask={ask.askId}
  data-conversation-ask-shape={shape}
>
  <div class="c2-ask-head">
    <span class="c2-ask-eyebrow">{eyebrow}</span>
    <span class="c2-ask-title">{ask.title}</span>
  </div>

  {#if shape === "question"}
    <div class="c2-ask-choices" role="group" aria-label="Answers">
      {#each choices as choice (choice.optionId)}
        <button
          type="button"
          class="c2-ask-choice"
          data-conversation-ask-choice={choice.optionId}
          data-conversation-ask-shortcut={choice.shortcutDigit ?? undefined}
          disabled={busy}
          onclick={() => onAnswer(choice.optionId)}
        >
          <span class="c2-ask-choice-label">{choice.label}</span>
          {#if choice.shortcutDigit !== null}
            <kbd class="c2-ask-kbd">{choice.shortcutDigit}</kbd>
          {/if}
        </button>
      {/each}
    </div>
    {#if detail}
      <button
        type="button"
        class="c2-ask-raw-toggle"
        data-conversation-ask-raw-toggle
        aria-expanded={rawOpen}
        onclick={() => (rawOpen = !rawOpen)}
      >{rawOpen ? "Hide the request" : "Show the request"}</button>
      {#if rawOpen}
        <pre class="c2-ask-detail" data-conversation-ask-detail>{detail}</pre>
      {/if}
    {/if}
  {:else if detail}
    <pre class="c2-ask-detail" data-conversation-ask-detail>{detail}</pre>
  {/if}

  {#if shape === "shapeless"}
    <p class="c2-ask-note" data-conversation-ask-fallback>
      This ask did not offer the usual answers, so Panels offers these. Cancelling the turn
      always works.
    </p>
  {/if}
  {#if note}
    <p class="c2-ask-note" data-conversation-ask-note>{note}</p>
  {/if}
</section>

<style>
  .c2-ask {
    min-width: 0;
    max-width: 100%;
    display: grid;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    border-block-end: var(--border-hairline) solid var(--border-color);
    /* The recessed plane the card already uses for the well below it. The cool ink plane
       was invisible while the card itself was drawn in ink; on the card's own warm surface
       it read as a foreign rectangle. */
    background: var(--surface-recessed);
    color: var(--accent-text);
    border-start-start-radius: var(--radius-lg);
    border-start-end-radius: var(--radius-lg);
  }
  .c2-ask-head {
    display: flex;
    min-width: 0;
    flex-wrap: wrap;
    align-items: baseline;
    gap: var(--space-2);
  }
  .c2-ask-eyebrow {
    flex: none;
    color: var(--accent-bright);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-label);
    text-transform: uppercase;
  }
  .c2-ask-title {
    min-width: 0;
    overflow-wrap: anywhere;
    font-family: var(--font-ui);
    font-size: var(--type-sm);
    color: var(--accent-text);
  }
  /* The bound that keeps this band the composer's size: whatever the backend sent
     wraps and scrolls in here rather than growing the composer to fit it. */
  .c2-ask-detail {
    margin: 0;
    max-height: calc(var(--type-xs) * 12);
    overflow: auto;
    color: var(--accent-text);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .c2-ask-choices {
    display: grid;
    gap: var(--space-1);
    max-height: calc(var(--type-xs) * 18);
    overflow: auto;
  }
  .c2-ask-choice {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    width: 100%;
    background: transparent;
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-sm);
    color: var(--accent-text);
    cursor: pointer;
    font: inherit;
    font-size: var(--type-sm);
    padding: var(--space-2) var(--space-3);
    text-align: start;
  }
  .c2-ask-choice:hover,
  .c2-ask-choice:focus-visible { border-color: var(--accent-bright); }
  .c2-ask-choice:disabled { cursor: default; opacity: 0.7; }
  .c2-ask-choice-label { flex: 1; min-width: 0; overflow-wrap: anywhere; }
  .c2-ask-kbd {
    flex: none;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: var(--space-4);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-sm);
    color: var(--accent-bright);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    font-variant-numeric: tabular-nums;
    padding: 0 var(--space-1);
  }
  .c2-ask-raw-toggle {
    justify-self: start;
    background: transparent;
    border: 0;
    color: var(--accent-bright);
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    padding: 0;
  }
  .c2-ask-note {
    margin: 0;
    color: var(--accent-bright);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    overflow-wrap: anywhere;
  }
</style>
