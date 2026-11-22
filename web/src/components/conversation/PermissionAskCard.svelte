<script lang="ts">
  /** What the agent has stopped for, in the ask shell (AskShell) every ask wears, inside the
   * composer's own frame while it waits.
   *
   * The ask is read first: its title, then the command or request, then the answers as one
   * list of numbered rows, the same rows a question's choices are. Approve once leads as the
   * default (key 1); the standing permissions follow, and decline comes last. A row answers
   * when it is chosen, by pointer or by its number. Stopping the turn is the composer's one
   * stop button, never a button here.
   */
  import type { Snippet } from "svelte";
  import AskShell from "./AskShell.svelte";
  import {
    askChoiceForDigit,
    askQuestionChoices,
    askShape,
    permissionAnswerRows
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
    /** Whether it draws its own raised plane (the composer lends it its own frame). */
    framed?: boolean;
    /** The composer's one-line reply, under the answers. */
    replyStrip?: Snippet;
    onAnswer: (optionId: string) => void;
  } = $props();

  let shape = $derived(askShape(ask));
  let rows = $derived(shape === "question"
    ? askQuestionChoices(ask).map((choice) => ({ ...choice, emphasis: "option" as const, supplied: true }))
    : permissionAnswerRows(ask));
  let detail = $derived(readableConversationDetail(ask.detail));
  // The rail names this state "Needs your approval"; the card says it in the same words.
  let eyebrow = $derived(shape === "question" ? "Needs your answer" : "Needs your approval");
  let rawOpen = $state(false);

  function onWindowKeydown(event: KeyboardEvent): void {
    if (busy || !answerAvailable) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const target = event.target;
    // Somebody typing a number into a field means the number, not the answer.
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return;
    const digit = Number.parseInt(event.key, 10);
    const chosen = shape === "question"
      ? askChoiceForDigit(ask, digit)
      : rows.find((row) => row.shortcutDigit === digit) ?? null;
    if (chosen === null) return;
    event.preventDefault();
    onAnswer(chosen.optionId);
  }
</script>

<svelte:window onkeydown={onWindowKeydown} />

<AskShell
  kind={shape === "question" ? "question" : "permission"}
  {eyebrow}
  title={ask.title}
  {framed}
  {replyStrip}
  aria-label={shape === "question" ? "Question" : "Permission ask"}
  data-conversation-ask={ask.askId}
  data-conversation-ask-shape={shape}
>
  {#if shape !== "question" && detail}
    <!-- The one place the command is shown while it waits: the answers sit right under it. -->
    <pre class="c2-ask-detail" data-conversation-ask-detail>{detail}</pre>
  {/if}
  <div class="c2-ask-choices" class:after-detail={shape !== "question" && detail} role="group" aria-label="Answers">
    {#each rows as row, index (row.optionId)}
      <button
        type="button"
        class="c2-ask-choice"
        class:is-default={shape !== "question" && index === 0}
        data-conversation-ask-choice={row.optionId}
        data-conversation-ask-action={shape === "question" ? undefined : row.optionId}
        data-conversation-ask-emphasis={shape === "question" ? undefined : row.emphasis}
        data-conversation-ask-supplied={row.supplied ? "true" : "false"}
        data-conversation-ask-shortcut={row.shortcutDigit ?? undefined}
        aria-keyshortcuts={row.shortcutDigit === null ? undefined : String(row.shortcutDigit)}
        title={row.supplied ? undefined : "Panels supplied this answer; the backend may not take it"}
        disabled={busy || !answerAvailable}
        onclick={() => answerAvailable && onAnswer(row.optionId)}
      >
        <kbd class="c2-ask-kbd" aria-hidden="true">{row.shortcutDigit ?? ""}</kbd>
        <span class="c2-ask-choice-label">{row.label}</span>
      </button>
    {/each}
  </div>
  {#if shape === "question" && detail}
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
    gap: 2px;
    max-height: 320px;
    overflow: auto;
    margin-inline: calc(var(--cv-s2) * -1);
  }
  .c2-ask-choices.after-detail { margin-block-start: var(--cv-s3); }
  /* An answer is a row the whole width, its number key on the left: the same row a question's
     choice is. Hover is one step up. The default (approve once, key 1) wears the filled key,
     so the eye finds it first without a button shouting it. */
  .c2-ask-choice {
    display: flex;
    align-items: center;
    gap: var(--cv-s3);
    width: 100%;
    min-height: 40px;
    padding: 6px var(--cv-s3) 6px var(--cv-s2);
    border: 0;
    border-radius: var(--cv-r-md);
    background: transparent;
    color: var(--cv-t2);
    cursor: pointer;
    font: var(--cv-weight-label) var(--cv-type-ui)/var(--cv-leading-ui) var(--cv-font);
    text-align: start;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out), color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .c2-ask-choice.is-default { color: var(--cv-t1); }
  .c2-ask-choice:hover:not(:disabled) { background: var(--cv-control); color: var(--cv-t1); }
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
    background: transparent;
    box-shadow: inset 0 0 0 1px var(--cv-line-strong);
    color: var(--cv-t2);
    font: var(--cv-weight-label) var(--cv-type-caption)/1 var(--cv-font);
    font-variant-numeric: tabular-nums;
  }
  .is-default .c2-ask-kbd { background: var(--cv-primary-fill); box-shadow: none; color: var(--cv-primary-ink); }
  .c2-ask-note {
    margin: var(--cv-s3) 0 0;
    color: var(--cv-t3);
    font: var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font);
    overflow-wrap: anywhere;
  }
</style>
