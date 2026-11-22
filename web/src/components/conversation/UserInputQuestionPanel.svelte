<script lang="ts">
  import type {
    UserInputAnswers,
    UserInputQuestion
  } from "../../lib/conversation/wire";
  import {
    completeUserInputAnswers,
    emptyUserInputDraft,
    toggleUserInputChoice,
    userInputOptionForKey,
    userInputAnswerFor,
    withOwnAnswer,
    type UserInputDraft
  } from "../../lib/conversation/userInput";
  import type { Snippet } from "svelte";
  import { onMount, untrack } from "svelte";
  import AskShell from "./AskShell.svelte";

  let {
    request,
    busy = false,
    answerAvailable = true,
    note = null,
    framed = true,
    replyStrip,
    onStop,
    commitLabel = $bindable(""),
    commitReady = $bindable(false),
    ownAnswer = null,
    acceptsOwnAnswer = $bindable(false),
    onOwnAnswerUsed,
    onWriteOwnAnswer,
    onSubmit
  }: {
    request: { requestId: string; questions: readonly UserInputQuestion[] };
    busy?: boolean;
    answerAvailable?: boolean;
    note?: string | null;
    /** Whether it draws its own raised plane (the composer gives up its own for it). */
    framed?: boolean;
    /** The composer's one-line reply, under the answers. */
    replyStrip?: Snippet;
    /** Ends the turn that waits on these questions, from the header's "Stop turn". */
    onStop?: () => void;
    /** Docked in the composer (v4): what its primary pill says ("Next", "Send answers") and
     *  whether it can go on now; the pill calls commit(). */
    commitLabel?: string;
    commitReady?: boolean;
    /** The words in the composer, when the composer is where an own answer is written (it is
     *  docked right under the question). Null: the panel draws its own field for them. */
    ownAnswer?: string | null;
    /** Whether the question on screen takes an own answer, for the composer to say so. */
    acceptsOwnAnswer?: boolean;
    /** The composer's words were taken as an answer, so the composer can let them go. */
    onOwnAnswerUsed?: () => void;
    /** The person chose "Something else": the composer takes the cursor. */
    onWriteOwnAnswer?: () => void;
    onSubmit?: (answers: UserInputAnswers) => void;
  } = $props();

  let step = $state(0);
  let draft = $state<UserInputDraft>(emptyUserInputDraft());
  let rememberedRequestId = $state("");
  const uid = $props.id();

  $effect(() => {
    if (request.requestId === rememberedRequestId) return;
    rememberedRequestId = request.requestId;
    step = 0;
    draft = emptyUserInputDraft();
  });

  let question = $derived(request.questions[step]);
  let currentAnswers = $derived(
    question === undefined ? [] : userInputAnswerFor(draft, question)
  );
  let canContinue = $derived(currentAnswers.length > 0);
  let isLast = $derived(step === request.questions.length - 1);
  let optionsElement = $state<HTMLDivElement | null>(null);
  let composerAnswers = $derived(ownAnswer !== null);
  let ownWords = $derived(question === undefined ? "" : (draft.other[question.question_id] ?? "").trim());

  $effect(() => { acceptsOwnAnswer = question?.allow_other === true; });

  // The composer's words are this question's own answer while it is the place to type.
  $effect(() => {
    const words = ownAnswer;
    if (words === null) return;
    untrack(() => {
      if (question === undefined || !question.allow_other || busy || !answerAvailable) return;
      draft = withOwnAnswer(draft, question, words);
    });
  });

  /** Answers the question on screen with the composer's words and goes on, as Next would.
   *  False when this question takes no own answer, so the words stay where they are. */
  export function answerWith(words: string): boolean {
    if (question === undefined || !question.allow_other || busy || !answerAvailable || words.trim() === "") return false;
    draft = withOwnAnswer(draft, question, words);
    continueOrSubmit();
    return true;
  }

  // The decision is where the person's attention goes: the first answer takes focus when the
  // ask appears, unless they are already somewhere else on the page.
  onMount(() => {
    if (document.activeElement === null || document.activeElement === document.body) {
      optionsElement?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus({ preventScroll: true });
    }
  });

  /** 1–9 choose an answer, as a permission ask's question does. Typing a number into a
   *  field means the number. */
  function onWindowKeydown(event: KeyboardEvent): void {
    if (question === undefined || busy || !answerAvailable) return;
    if (event.metaKey || event.ctrlKey || event.altKey || event.defaultPrevented) return;
    const target = event.target;
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement
      || (target instanceof HTMLElement && target.isContentEditable)) return;
    const option = userInputOptionForKey(question, event.key);
    if (option === null) return;
    event.preventDefault();
    choose(option);
  }

  /** A tile chooses (or, where several may be chosen, toggles); the one primary goes on. */
  function choose(option: string): void {
    if (question === undefined || busy || !answerAvailable) return;
    draft = toggleUserInputChoice(draft, question, option);
  }

  let primaryLabel = $derived(busy && isLast ? "Sending…" : isLast ? (request.questions.length > 1 ? "Send answers" : "Send answer") : "Next");
  $effect(() => {
    commitLabel = primaryLabel;
    commitReady = question !== undefined && canContinue && !busy && answerAvailable;
  });

  /** Goes on, as the primary does: the composer's pill calls this. */
  export function commit(): void { continueOrSubmit(); }

  function writeOther(value: string): void {
    if (question === undefined || busy || !answerAvailable) return;
    draft = {
      selected: question.multi_select
        ? draft.selected
        : { ...draft.selected, [question.question_id]: [] },
      other: { ...draft.other, [question.question_id]: value }
    };
  }

  /** Enter in the Other field answers with it, as it would in any one-line form. */
  function otherKeydown(event: KeyboardEvent): void {
    if (event.key !== "Enter" || event.isComposing || event.shiftKey) return;
    event.preventDefault();
    continueOrSubmit();
  }

  function continueOrSubmit(): void {
    if (!canContinue || question === undefined || busy || !answerAvailable) return;
    if (composerAnswers && ownWords !== "") onOwnAnswerUsed?.();
    if (!isLast) {
      step += 1;
      return;
    }
    const answers = completeUserInputAnswers(draft, request.questions);
    if (answers !== null) onSubmit?.(answers);
  }
</script>

<svelte:window onkeydown={onWindowKeydown} />

{#snippet position()}
  {#if question?.header}<span class="topic">{question.header}</span>{/if}
  {#if request.questions.length > 1}
    {#if question?.header}<span class="sep" aria-hidden="true">·</span>{/if}
    <span class="count">{step + 1} of {request.questions.length}</span>
    <button type="button" class="cv-icon-button cv-icon-button--small" data-user-input-back
      aria-label="Previous question" title="Previous question" disabled={busy || step === 0}
      onclick={() => (step -= 1)}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m10 4-4 4 4 4" /></svg></button>
    <button type="button" class="cv-icon-button cv-icon-button--small" data-user-input-forward
      aria-label="Next question" title="Next question" disabled={busy || isLast || !canContinue}
      onclick={continueOrSubmit}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg></button>
  {/if}
{/snippet}

{#snippet primaryButton()}
  <!-- The one primary. Before there is an answer it waits as a quiet outline. -->
  <button
    type="button"
    class="cv-button cv-button--primary"
    data-user-input-continue
    title={!canContinue ? "Choose an answer, or write your own, to go on" : undefined}
    disabled={busy || !answerAvailable || !canContinue}
    onclick={continueOrSubmit}
  >{primaryLabel}</button>
{/snippet}

{#if question}
  <AskShell
    kind="question"
    eyebrow="Needs your answer"
    context={position}
    title={question.question}
    titleId={`${uid}-question`}
    description={question.multi_select ? "Choose all that apply." : null}
    {framed}
    {replyStrip}
    footerEnd={composerAnswers ? undefined : primaryButton}
    {onStop}
    stopDisabled={busy}
    data-user-input-panel
    data-user-input-request-id={request.requestId}
    aria-label="Questions from the agent"
  >
    <div
      class="cv-ask-rows options"
      role="group"
      aria-labelledby={`${uid}-question`}
      bind:this={optionsElement}
    >
      {#each question.options as option, index (option.label)}
        {@const selected = (draft.selected[question.question_id] ?? []).includes(option.label)}
        <button
          type="button"
          class="cv-ask-row"
          class:is-selected={selected}
          data-user-input-option={option.label}
          aria-pressed={selected}
          aria-keyshortcuts={index < 9 ? String(index + 1) : undefined}
          disabled={busy || !answerAvailable}
          onclick={() => choose(option.label)}
        >
          <span class="cv-ask-key" aria-hidden="true">
            {#if selected}<svg viewBox="0 0 16 16"><path d="m3.5 8.5 3 3 6-7" /></svg>{:else if index < 9}{index + 1}{/if}
          </span>
          <span class="cv-ask-words">
            <span class="cv-ask-label">{option.label}</span>
            {#if option.description}<small class="cv-ask-sub">{option.description}</small>{/if}
          </span>
        </button>
      {/each}
    </div>

    {#if question.allow_other && !composerAnswers}
      <label class="other">
        <span class="visually-hidden">Other answer</span>
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10.5 3.2a1.5 1.5 0 0 1 2.1 2.1L6 11.9l-2.8.7.7-2.8z" /></svg>
        <input
          data-user-input-other
          value={draft.other[question.question_id] ?? ""}
          placeholder="Something else…"
          disabled={busy || !answerAvailable}
          oninput={(event) => writeOther(event.currentTarget.value)}
          onkeydown={otherKeydown}
        />
      </label>
    {/if}

    {#if note}<p class="note" role="alert">{note}</p>{/if}
  </AskShell>
{:else}
  <AskShell
    kind="question"
    eyebrow="Needs your answer"
    title="This question request contained no questions."
    {framed}
    {replyStrip}
    data-user-input-panel
    data-user-input-request-id={request.requestId}
    aria-label="Questions from the agent"
  />
{/if}

<style>
  .topic { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sep { color: var(--cv-t4); }
  .count { flex: none; }
  .options { max-height: 360px; overflow: auto; }
  /* The field: a canvas well inside the raised ask, with a pen where the key would be. */
  .other {
    position: relative;
    display: flex;
    align-items: center;
    margin-block-start: var(--cv-s2);
  }
  .other svg { position: absolute; left: 13px; width: 14px; height: 14px; fill: none; stroke: var(--cv-t4); stroke-width: 1.5; stroke-linejoin: round; pointer-events: none; }
  .other input {
    width: 100%;
    box-sizing: border-box;
    min-height: 40px;
    padding: 0 var(--cv-s3) 0 44px;
    border: 1px solid var(--cv-line-strong);
    border-radius: var(--cv-r-md);
    background: var(--cv-canvas);
    color: var(--cv-t1);
    font: var(--cv-weight-body) var(--cv-type-ui)/var(--cv-leading-ui) var(--cv-font);
  }
  .other input::placeholder { color: var(--cv-placeholder); }
  .other input:focus { outline: none; border-color: var(--cv-accent-line); }
  .other input:disabled { cursor: default; color: var(--cv-t4); }
  .note { margin: var(--cv-s3) 0 0; color: var(--cv-danger); font: var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font); }
  .visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
</style>
