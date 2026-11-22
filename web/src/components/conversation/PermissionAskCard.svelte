<script lang="ts">
  /** What the agent has stopped for, in the ask shell (AskShell) every ask wears.
   *
   * It docks in the composer's place and is deliberately a short head plus a bounded,
   * scrolling body — that bound is the whole of why it stays the composer's size instead
   * of growing to whatever the backend put in the request. Nothing is truncated and
   * nothing hides behind a hover; a structured request is laid out to be read.
   *
   * A question is not an approval, so it does not borrow approval's words. Its choices
   * are numbered and pickable by their number, and its raw request stays behind a
   * disclosure — the owner should be reading the question, not its payload.
   */
  import type { Snippet } from "svelte";
  import AskShell from "./AskShell.svelte";
  import PermissionAskActions from "./PermissionAskActions.svelte";
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
    framed = true,
    replyStrip,
    onAnswer,
    onCancelTurn
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
    /** Whether it draws its own raised plane (the composer gives up its own for it). */
    framed?: boolean;
    /** The composer's one-line reply, under the answers. */
    replyStrip?: Snippet;
    onAnswer: (optionId: string) => void;
    /** Given, the card draws its own answers in its footer (the ask shell's), Stop turn on
     *  the left and the one primary at the right end. Without it the host draws them. */
    onCancelTurn?: () => void;
  } = $props();

  let shape = $derived(askShape(ask));
  let choices = $derived(askQuestionChoices(ask));
  let detail = $derived(readableConversationDetail(ask.detail));
  // The rail names this state "Needs your approval"; the card says it in the same words.
  let eyebrow = $derived(shape === "question" ? "Needs your answer" : "Needs your approval");
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

{#snippet stopTurn()}
  {#if onCancelTurn}<PermissionAskActions {ask} {busy} {answerAvailable} part="start" {onAnswer} {onCancelTurn} />{/if}
{/snippet}
{#snippet answers()}
  {#if onCancelTurn}<PermissionAskActions {ask} {busy} {answerAvailable} part="end" {onAnswer} {onCancelTurn} />{/if}
{/snippet}

<AskShell
  kind={shape === "question" ? "question" : "permission"}
  {eyebrow}
  title={ask.title}
  {framed}
  {replyStrip}
  footerStart={onCancelTurn ? stopTurn : undefined}
  footerEnd={onCancelTurn && shape !== "question" ? answers : undefined}
  aria-label={shape === "question" ? "Question" : "Permission ask"}
  data-conversation-ask={ask.askId}
  data-conversation-ask-shape={shape}
>
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
          <kbd class="c2-ask-kbd" aria-hidden={choice.shortcutDigit === null ? "true" : undefined}>{choice.shortcutDigit ?? ""}</kbd>
          <span class="c2-ask-choice-label">{choice.label}</span>
        </button>
      {/each}
    </div>
    {#if detail}
      <button
        type="button"
        class="cv-button cv-button--ghost cv-button--small c2-ask-raw-toggle"
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
    <pre class="c2-ask-detail" data-conversation-ask-detail>{detail}</pre>
  {/if}

  {#if shape === "shapeless"}
    <p class="c2-ask-note" data-conversation-ask-fallback>
      This ask did not offer the usual answers, so Panels offers these. Stopping the turn
      always works.
    </p>
  {/if}
  {#if note}
    <p class="c2-ask-note" data-conversation-ask-note>{note}</p>
  {/if}
</AskShell>

<style>
  /* The command, or the request behind a question: one canvas well inside the raised ask,
     in mono, bounded so the ask stays the composer's size and scrolls inside instead. */
  .c2-ask-detail {
    margin: 0;
    max-height: 240px;
    overflow: auto;
    padding: 10px var(--cv-s3);
    border-radius: var(--cv-r-md);
    background: var(--cv-canvas);
    color: var(--cv-t1);
    font: var(--cv-weight-body) var(--cv-type-code)/var(--cv-leading-code) var(--cv-mono);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .c2-ask-raw-toggle { margin: var(--cv-s2) 0 0 calc(var(--cv-s2) * -1); }
  .c2-ask-raw-toggle + .c2-ask-detail { margin-block-start: var(--cv-s2); }
  .c2-ask-choices {
    display: grid;
    gap: var(--cv-s1);
    max-height: 320px;
    overflow: auto;
  }
  /* An option is a row the whole width, its number key on the left. Hover is one step up. */
  .c2-ask-choice {
    display: flex;
    align-items: center;
    gap: var(--cv-s3);
    width: 100%;
    min-height: 40px;
    padding: var(--cv-s2) var(--cv-s3) var(--cv-s2) var(--cv-s2);
    border: 0;
    border-radius: var(--cv-r-md);
    background: transparent;
    color: var(--cv-t1);
    cursor: pointer;
    font: var(--cv-weight-label) var(--cv-type-ui)/var(--cv-leading-ui) var(--cv-font);
    text-align: start;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .c2-ask-choice:hover:not(:disabled) { background: var(--cv-control); }
  .c2-ask-choice:focus-visible { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: -2px; }
  .c2-ask-choice:disabled { cursor: default; color: var(--cv-t3); }
  .c2-ask-choice-label { flex: 1; min-width: 0; overflow-wrap: anywhere; }
  .c2-ask-kbd {
    flex: none;
    display: inline-grid;
    place-items: center;
    width: 24px;
    height: 24px;
    border-radius: var(--cv-r-sm);
    background: var(--cv-control);
    color: var(--cv-t3);
    font: var(--cv-weight-label) var(--cv-type-caption)/1 var(--cv-font);
    font-variant-numeric: tabular-nums;
  }
  .c2-ask-note {
    margin: var(--cv-s3) 0 0;
    color: var(--cv-t3);
    font: var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font);
    overflow-wrap: anywhere;
  }
</style>
