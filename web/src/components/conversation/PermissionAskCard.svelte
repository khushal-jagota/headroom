<script lang="ts">
  /** What the agent has stopped for, in the ask shell (AskShell) every ask wears, inside the
   * composer's own frame while it waits.
   *
   * The ask is read first: its title, then the command in a well, then the answers as one
   * list of numbered tiles, the same tiles a question's choices are. Allow once leads and is
   * chosen by default (key 1); the standing permissions follow, and decline comes last, a
   * tone down. Docked in the composer (v4), a tile only chooses; the composer's primary pill
   * in the send slot ("Allow", or "Deny" when decline is chosen) commits it. Standing alone, a
   * tile answers at once. The turn is stopped from the header's "Stop turn".
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
    onStop,
    docked = false,
    commitLabel = $bindable(""),
    commitReady = $bindable(false),
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
    /** Ends the waiting turn, from the header's "Stop turn". */
    onStop?: () => void;
    /** In the composer: tiles choose, and the composer's pill commits (commit()). */
    docked?: boolean;
    /** What the composer's pill says, and whether it can commit now. */
    commitLabel?: string;
    commitReady?: boolean;
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
  /** The chosen answer while docked: the default (the first row, allow once) until another
   *  is chosen. Reset when a new ask arrives. */
  let chosenId = $state<string | null>(null);
  let chosenFor = $state("");
  $effect(() => { if (ask.askId !== chosenFor) { chosenFor = ask.askId; chosenId = null; } });
  let chosen = $derived(rows.find((row) => row.optionId === chosenId) ?? (shape === "question" ? null : rows[0] ?? null));
  $effect(() => {
    commitLabel = shape === "question" ? "Send answer" : chosen?.emphasis === "decline" ? "Deny" : "Allow";
    commitReady = chosen !== null && !busy && answerAvailable;
  });

  /** Commits the chosen answer, for the composer's pill. */
  export function commit(): void {
    if (chosen === null || busy || !answerAvailable) return;
    onAnswer(chosen.optionId);
  }

  function pick(optionId: string): void {
    if (busy || !answerAvailable) return;
    if (docked) chosenId = optionId;
    else onAnswer(optionId);
  }

  function onWindowKeydown(event: KeyboardEvent): void {
    if (busy || !answerAvailable) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const target = event.target;
    // Somebody typing a number into a field means the number, not the answer.
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return;
    const digit = Number.parseInt(event.key, 10);
    const byDigit = shape === "question"
      ? askChoiceForDigit(ask, digit)
      : rows.find((row) => row.shortcutDigit === digit) ?? null;
    if (byDigit === null) return;
    event.preventDefault();
    pick(byDigit.optionId);
  }
</script>

<svelte:window onkeydown={onWindowKeydown} />

<AskShell
  kind={shape === "question" ? "question" : "permission"}
  {eyebrow}
  title={ask.title}
  {framed}
  {replyStrip}
  {onStop}
  stopDisabled={busy}
  aria-label={shape === "question" ? "Question" : "Permission ask"}
  data-conversation-ask={ask.askId}
  data-conversation-ask-shape={shape}
>
  {#if shape !== "question" && detail}
    <!-- The one place the command is shown while it waits: the answers sit right under it. -->
    <pre class="c2-ask-detail" data-conversation-ask-detail>{detail}</pre>
  {/if}
  <div class="cv-ask-rows c2-ask-choices" class:after-detail={shape !== "question" && detail} role="group" aria-label="Answers">
    {#each rows as row, index (row.optionId)}
      <button
        type="button"
        class="cv-ask-row c2-ask-choice"
        class:is-selected={docked && chosen?.optionId === row.optionId}
        class:is-quiet={row.emphasis === "decline"}
        aria-pressed={docked ? chosen?.optionId === row.optionId : undefined}
        data-conversation-ask-choice={row.optionId}
        data-conversation-ask-action={shape === "question" ? undefined : row.optionId}
        data-conversation-ask-emphasis={shape === "question" ? undefined : row.emphasis}
        data-conversation-ask-supplied={row.supplied ? "true" : "false"}
        data-conversation-ask-shortcut={row.shortcutDigit ?? undefined}
        aria-keyshortcuts={row.shortcutDigit === null ? undefined : String(row.shortcutDigit)}
        title={row.supplied ? undefined : "Panels supplied this answer; the backend may not take it"}
        disabled={busy || !answerAvailable}
        onclick={() => pick(row.optionId)}
      >
        <kbd class="cv-ask-key" aria-hidden="true">{row.shortcutDigit ?? ""}</kbd>
        <span class="cv-ask-words"><span class="cv-ask-label">{row.label}</span></span>
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
    font: var(--cv-weight-body) 13px/var(--cv-leading-code) var(--cv-mono);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .c2-ask-raw-toggle { margin: var(--cv-s2) 0 0 calc(var(--cv-s2) * -1); }
  .c2-ask-raw-toggle + .c2-ask-detail { margin-block-start: var(--cv-s2); }
  .c2-ask-choices.after-detail { margin-block-start: var(--cv-s3); }
  .c2-ask-note {
    margin: var(--cv-s3) 0 0;
    color: var(--cv-t3);
    font: var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font);
    overflow-wrap: anywhere;
  }
</style>
