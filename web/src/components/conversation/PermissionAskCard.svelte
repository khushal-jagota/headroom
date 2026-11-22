<script lang="ts">
  /** What the agent has stopped for, in the ask shell (AskShell) every ask wears, inside the
   * composer's own frame while it waits.
   *
   * The ask is read first: its title, then the command in a well, then the answers as one
   * list of numbered tiles, the same tiles a question's choices are. Allow once leads and is
   * chosen by default (key 1); the standing permissions follow, and decline comes last, a
   * tone down. v10 (4): there is no confirm pill: each row is the action, docked or not. A
   * click or its number answers at once. No row is pre-highlighted; hover and keyboard focus
   * light a row. The turn is stopped from the footer's "Stop turn" (the composer draws it).
   */
  import type { Snippet } from "svelte";
  import AskShell from "./AskShell.svelte";
  import AskOptionContent from "./AskOptionContent.svelte";
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
    /** Ends the waiting turn. v10 (3): the composer draws Stop turn in the footer, so it
     *  passes none; standing alone the shell's header still offers it. */
    onStop?: () => void;
    /** In the composer's place. v10 (4): rows answer at once either way. */
    docked?: boolean;
    onAnswer: (optionId: string) => void;
  } = $props();

  let shape = $derived(askShape(ask));
  let rows = $derived(shape === "question"
    ? askQuestionChoices(ask).map((choice) => ({ ...choice, emphasis: "option" as const, supplied: true }))
    : permissionAnswerRows(ask));
  let detail = $derived(readableConversationDetail(ask.detail));
  /** v17 (7): the command wraps only at spaces. Each run of non-space characters is one
   *  unbreakable piece, so a line never breaks inside a flag, a path or after a hyphen. */
  let detailPieces = $derived(detail === null ? [] : detail.split(/([ \n]+)/).filter(piece => piece !== ""));
  /** v9 (2): Deny is an option row (the last one), never a separate button. */
  let tiles = $derived(rows);
  // v5: a permission ask says "Needs your permission" (v16: with the dot); a question keeps
  // "Needs your answer". "Needs your approval" belongs to proposals.
  let eyebrow = $derived(shape === "question" ? "Needs your answer" : "Needs your permission");
  let rawOpen = $state(false);

  /** v10 (4): a row is the action: choosing it answers. */
  function pick(optionId: string): void {
    if (busy || !answerAvailable) return;
    onAnswer(optionId);
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
    <pre class="c2-ask-detail" data-conversation-ask-detail>{#each detailPieces as piece, index (index)}{#if /^[ \n]+$/.test(piece)}{piece}{:else}<span class="c2-ask-word">{piece}</span>{/if}{/each}</pre>
  {/if}
  <div class="cv-ask-rows c2-ask-choices" class:after-detail={shape !== "question" && detail} role="group" aria-label="Answers">
    {#each tiles as row, index (row.optionId)}
      <button
        type="button"
        class="cv-ask-row c2-ask-choice"
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
        <AskOptionContent label={row.label} digit={row.shortcutDigit} keyTag="kbd" />
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
  /* v7: the command is a code block: #1a1a1a, a 1px #2a2a2a edge, radius 12, 13/20 mono,
     padding 12 16. */
  .c2-ask-detail {
    margin: 0;
    max-height: 240px;
    overflow: auto;
    padding: 12px 16px;
    /* v11 (8): the command block's edge is #3a3a3a. */
    border: 1px solid #3a3a3a;
    border-radius: 12px;
    background: var(--cv-code-surface, #1a1a1a);
    color: #e0e0e0;
    font: var(--cv-weight-body) 13px/20px var(--cv-mono);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  /* v17 (7): the command wraps only at spaces (each word is one unbreakable piece). A single
     word wider than the block scrolls inside it instead of breaking. v18 (5): 16px side padding
     again, and mono 12.5/20, so a 74-character command stays on one line with room to the right. */
  .c2-ask-detail { padding-inline: 16px; font-size: 12.5px; line-height: 20px; overflow-wrap: normal; }
  .c2-ask-word { white-space: pre; }
  .c2-ask-raw-toggle { margin: var(--cv-s2) 0 0 calc(var(--cv-s2) * -1); }
  .c2-ask-raw-toggle + .c2-ask-detail { margin-block-start: var(--cv-s2); }
  .c2-ask-choices.after-detail { margin-block-start: 16px; }
  /* v11 (2): in a permission ask the declining row's label is the failure tone. v18 (4): the
     same red as the "This turn failed" title, #ff8f85. */
  .c2-ask-choice[data-conversation-ask-emphasis="decline"] :global(.cv-ask-label) { color: #ff8f85; }
  /* v19 (3): the first option's label (Allow once) is #ffffff 500; the others stay 400. Nothing
     is preselected: the row itself looks like every other row. */
  .c2-ask-choice[data-conversation-ask-emphasis="primary"] :global(.cv-ask-label) { color: #ffffff; font-weight: 500; }
  .c2-ask-note {
    margin: var(--cv-s3) 0 0;
    color: var(--cv-t3);
    font: var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font);
    overflow-wrap: anywhere;
  }
</style>
