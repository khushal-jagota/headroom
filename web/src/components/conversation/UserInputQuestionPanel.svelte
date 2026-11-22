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
  import { untrack } from "svelte";
  import AskShell from "./AskShell.svelte";
  import AskOptionContent from "./AskOptionContent.svelte";

  let {
    request,
    busy = false,
    answerAvailable = true,
    note = null,
    framed = true,
    replyStrip,
    onStop,
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
    /** v10 (4): no confirm pill. A row is the action; only a question where several answers
     *  may be chosen waits for the composer's send (commit()), and commitReady says it can. */
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
  /** Where the arrow keys are among the rows. v10 (4): nothing is pre-highlighted; a row
   *  lights only under the pointer or keyboard focus. */
  let focusedOption = $state(-1);
  $effect(() => { step; request.requestId; focusedOption = -1; });

  let question = $derived(request.questions[step]);
  let currentAnswers = $derived(
    question === undefined ? [] : userInputAnswerFor(draft, question)
  );
  let chosenSomething = $derived(currentAnswers.length > 0);
  let canContinue = $derived(chosenSomething);
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

  // v10 (4): no row takes focus on its own (it would light it); numbers and arrows work at once.

  /** 1–9 choose an answer, as a permission ask's question does. Typing a number into a
   *  field means the number. */
  function onWindowKeydown(event: KeyboardEvent): void {
    if (question === undefined || busy || !answerAvailable) return;
    if (event.metaKey || event.ctrlKey || event.altKey || event.defaultPrevented) return;
    const target = event.target;
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement
      || (target instanceof HTMLElement && target.isContentEditable)) return;
    if ((event.key === "ArrowDown" || event.key === "ArrowUp") && question.options.length > 0) {
      event.preventDefault();
      const count = question.options.length;
      focusedOption = focusedOption < 0
        ? (event.key === "ArrowDown" ? 0 : count - 1)
        : (focusedOption + (event.key === "ArrowDown" ? 1 : count - 1)) % count;
      optionsElement?.querySelectorAll<HTMLButtonElement>("[data-user-input-option]")[focusedOption]?.focus({ preventScroll: true });
      return;
    }
    const option = userInputOptionForKey(question, event.key);
    if (option === null) return;
    event.preventDefault();
    choose(option);
  }

  /** v10 (4): a row is the action. Choosing one answers this question and moves to the next;
   *  the last choice sends all answers. Where several may be chosen, a row toggles and the
   *  composer's send goes on (commit()). */
  function choose(option: string): void {
    if (question === undefined || busy || !answerAvailable) return;
    const at = question.options.findIndex((each) => each.label === option);
    if (at >= 0) focusedOption = at;
    if (question.multi_select) {
      draft = toggleUserInputChoice(draft, question, option);
      return;
    }
    draft = {
      selected: { ...draft.selected, [question.question_id]: [option] },
      other: { ...draft.other, [question.question_id]: "" }
    };
    continueOrSubmit();
  }

  $effect(() => {
    commitReady = question !== undefined && question.multi_select && canContinue && !busy && answerAvailable;
  });

  /** Goes on with the answers chosen so far (a several-answer question): the composer's send. */
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
    if (answers !== null) { onSubmit?.(answers); return; }
    // A question was passed over with the pager: go back to the first one still unanswered.
    const open = request.questions.findIndex((each) => userInputAnswerFor(draft, each).length === 0);
    if (open >= 0) step = open;
  }

  /** v13 (5): the pager's forward arrow moves to the next question (answered or not); it is
   *  unavailable only on the last one. Back is unavailable only on the first. */
  function goForward(): void {
    if (busy || isLast) return;
    if (canContinue && composerAnswers && ownWords !== "") onOwnAnswerUsed?.();
    step += 1;
  }
</script>

<svelte:window onkeydown={onWindowKeydown} />

{#snippet position()}
  <!-- v4.1: no category label; the pager alone, "1 of 2 ‹ ›". -->
  {#if request.questions.length > 1}
    <span class="count">{step + 1} of {request.questions.length}</span>
    <button type="button" class="cv-icon-button cv-icon-button--small" data-user-input-back
      aria-label="Previous question" title="Previous question" disabled={busy || step === 0}
      onclick={() => (step -= 1)}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m10 4-4 4 4 4" /></svg></button>
    <button type="button" class="cv-icon-button cv-icon-button--small" data-user-input-forward
      aria-label="Next question" title="Next question" disabled={busy || isLast}
      onclick={goForward}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg></button>
  {/if}
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
          onfocus={() => (focusedOption = index)}
        >
          <AskOptionContent label={option.label} description={option.description}
            digit={index < 9 ? index + 1 : null} {selected} />
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
