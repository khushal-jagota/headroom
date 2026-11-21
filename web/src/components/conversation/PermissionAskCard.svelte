<script lang="ts">
  /** The band that says what the agent has stopped for.
   *
   * It sits on top of the composer and is deliberately a short head plus a bounded,
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
    answerAvailable = true,
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
    answerAvailable?: boolean;
    note?: string | null;
    onAnswer: (optionId: string) => void;
  } = $props();

  let shape = $derived(askShape(ask));
  let choices = $derived(askQuestionChoices(ask));
  let detail = $derived(readableConversationDetail(ask.detail));
  // The rail names this state "Needs your approval"; the card says it in the same words.
  let eyebrow = $derived(shape === "question" ? "Question" : "Needs your approval");
  let rawOpen = $state(false);

  function onWindowKeydown(event: KeyboardEvent): void {
    if (shape !== "question" || busy || !answerAvailable) return;
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
          aria-keyshortcuts={choice.shortcutDigit === null ? undefined : String(choice.shortcutDigit)}
          disabled={busy || !answerAvailable}
          onclick={() => answerAvailable && onAnswer(choice.optionId)}
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
    <!-- The one place the command is shown while it waits: the answers sit right under it. -->
    <pre class="c2-ask-detail is-command" data-conversation-ask-detail>{detail}</pre>
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
  /* No plane of its own: the ask is the composer's content while it waits, so it stands on
     the composer's surface. A recessed band inside the box read as a box in a box. */
  .c2-ask {
    min-width: 0;
    max-width: 100%;
    display: grid;
    gap: var(--space-2);
    padding: var(--space-3) 0 var(--space-1);
    color: var(--text-default);
  }
  .c2-ask-head {
    display: grid;
    min-width: 0;
    gap: var(--space-1);
  }
  .c2-ask-eyebrow {
    color: var(--accent-bright);
    font-family: var(--font-ui);
    font-size: var(--type-xs);
    font-weight: var(--weight-title);
    letter-spacing: var(--tracking-label);
    text-transform: uppercase;
  }
  .c2-ask-title {
    min-width: 0;
    overflow-wrap: anywhere;
    font-family: var(--font-ui);
    font-size: var(--type-md);
    font-weight: var(--weight-title);
    line-height: 1.4;
    color: var(--text-strong);
  }
  /* The bound that keeps this band the composer's size: whatever the backend sent
     wraps and scrolls in here rather than growing the composer to fit it. */
  .c2-ask-detail {
    margin: 0;
    max-height: calc(var(--type-xs) * 12);
    overflow: auto;
    color: var(--text-default);
    font-family: var(--font-mono);
    font-size: var(--type-code);
    line-height: 1.55;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  /* A command reads as a command: one quiet code well, the only one on the card. */
  .c2-ask-detail.is-command,
  .c2-ask-raw-toggle[aria-expanded="true"] + .c2-ask-detail {
    padding: var(--space-2) var(--space-3);
    border-radius: var(--radius-md);
    background: var(--surface-recessed);
    color: var(--text-strong);
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
    min-height: var(--button-height);
    /* Filled, not outlined: an answer is a button, and on the composer's surface one step
       of white says so without drawing a box around each line. */
    background: var(--state-step);
    border: 0;
    border-radius: var(--radius-md);
    color: var(--text-default);
    cursor: pointer;
    font: inherit;
    font-size: var(--type-row);
    padding: var(--space-2) var(--space-3);
    text-align: start;
  }
  .c2-ask-choice:hover:not(:disabled) { background-image: var(--interaction-hover); color: var(--text-strong); }
  .c2-ask-choice:disabled { cursor: default; color: var(--text-faint); }
  .c2-ask-choice-label { flex: 1; min-width: 0; overflow-wrap: anywhere; }
  .c2-ask-kbd {
    flex: none;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: var(--space-5);
    border-radius: var(--radius-sm);
    background: var(--state-step);
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    font-variant-numeric: tabular-nums;
    line-height: 1.6;
  }
  .c2-ask-raw-toggle {
    justify-self: start;
    min-height: 24px;
    background: transparent;
    border: 0;
    border-radius: var(--radius-sm);
    color: var(--text-faint);
    cursor: pointer;
    font: inherit;
    font-size: var(--type-xs);
    padding: 0;
  }
  .c2-ask-raw-toggle:hover { color: var(--text-strong); }
  .c2-ask-note {
    margin: 0;
    color: var(--text-faint);
    font-size: var(--type-xs);
    line-height: 1.5;
    overflow-wrap: anywhere;
  }
</style>
