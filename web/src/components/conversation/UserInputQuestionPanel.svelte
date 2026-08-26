<script lang="ts">
  import type {
    UserInputAnswers,
    UserInputQuestion
  } from "../../lib/conversation/wire";
  import {
    completeUserInputAnswers,
    emptyUserInputDraft,
    toggleUserInputChoice,
    userInputAnswerFor,
    type UserInputDraft
  } from "../../lib/conversation/userInput";

  let {
    request,
    busy = false,
    note = null,
    onSubmit,
    onCancelTurn
  }: {
    request: { requestId: string; questions: readonly UserInputQuestion[] };
    busy?: boolean;
    note?: string | null;
    onSubmit?: (answers: UserInputAnswers) => void;
    onCancelTurn?: () => void;
  } = $props();

  let step = $state(0);
  let draft = $state<UserInputDraft>(emptyUserInputDraft());
  let rememberedRequestId = $state("");

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

  function choose(option: string): void {
    if (question === undefined || busy) return;
    draft = toggleUserInputChoice(draft, question, option);
  }

  function writeOther(value: string): void {
    if (question === undefined) return;
    draft = {
      selected: question.multi_select
        ? draft.selected
        : { ...draft.selected, [question.question_id]: [] },
      other: { ...draft.other, [question.question_id]: value }
    };
  }

  function continueOrSubmit(): void {
    if (!canContinue || question === undefined || busy) return;
    if (!isLast) {
      step += 1;
      return;
    }
    const answers = completeUserInputAnswers(draft, request.questions);
    if (answers !== null) onSubmit?.(answers);
  }
</script>

<section
  class="question-panel"
  data-user-input-panel
  data-user-input-request-id={request.requestId}
  aria-label="Questions from the agent"
>
  {#if question}
    <header>
      <div class="progress">
        <span>{question.header || "Question"}</span>
        <span>{step + 1} of {request.questions.length}</span>
      </div>
      <h3>{question.question}</h3>
      {#if question.multi_select}<p class="hint">Choose all that apply.</p>{/if}
    </header>

    <div class="options" role={question.multi_select ? "group" : "radiogroup"}>
      {#each question.options as option (option.label)}
        {@const selected = (draft.selected[question.question_id] ?? []).includes(option.label)}
        <button
          type="button"
          class:selected
          data-user-input-option={option.label}
          aria-pressed={selected}
          disabled={busy}
          onclick={() => choose(option.label)}
        >
          <span>{option.label}</span>
          {#if option.description}<small>{option.description}</small>{/if}
        </button>
      {/each}
    </div>

    {#if question.allow_other}
      <label class="other">
        <span>Other</span>
        <input
          data-user-input-other
          value={draft.other[question.question_id] ?? ""}
          placeholder="Type your answer"
          disabled={busy}
          oninput={(event) => writeOther(event.currentTarget.value)}
        />
      </label>
    {/if}

    {#if note}<p class="note" role="alert">{note}</p>{/if}

    <footer>
      <button
        type="button"
        class="quiet"
        data-user-input-cancel
        disabled={busy}
        onclick={() => onCancelTurn?.()}
      >Stop</button>
      <span class="spacer"></span>
      {#if step > 0}
        <button
          type="button"
          class="quiet"
          data-user-input-back
          disabled={busy}
          onclick={() => (step -= 1)}
        >Back</button>
      {/if}
      <button
        type="button"
        class="continue"
        data-user-input-continue
        disabled={busy || !canContinue}
        onclick={continueOrSubmit}
      >{busy && isLast ? "Sending…" : isLast ? "Submit answers" : "Next"}</button>
    </footer>
  {:else}
    <p class="note" role="alert">This question request contained no questions.</p>
    <footer>
      <button type="button" class="quiet" onclick={() => onCancelTurn?.()}>Stop</button>
    </footer>
  {/if}
</section>

<style>
  .question-panel { display: grid; gap: var(--space-3); padding: var(--space-3); width: 100%; box-sizing: border-box; background: var(--surface-ink); color: var(--accent-text); border-radius: var(--radius-lg); }
  header { display: grid; gap: var(--space-1); }
  .progress { display: flex; justify-content: space-between; gap: var(--space-2); color: var(--accent-bright); font-family: var(--font-mono); font-size: var(--type-xs); }
  h3 { margin: 0; font: inherit; font-weight: 650; line-height: 1.35; }
  .hint, .note { margin: 0; color: var(--text-muted); font-size: var(--type-xs); }
  .note[role="alert"] { color: var(--accent-error); }
  .options { display: grid; gap: var(--space-1); }
  .options button { display: grid; gap: var(--space-1); width: 100%; padding: var(--space-2) var(--space-3); text-align: left; border: var(--border-hairline) solid var(--border-color); border-radius: var(--radius-sm); background: transparent; color: inherit; }
  .options button.selected { border-color: var(--accent-bright); }
  .options small { color: var(--text-muted); font-size: var(--type-xs); }
  .other { display: grid; gap: var(--space-1); font-size: var(--type-xs); color: var(--text-muted); }
  .other input { width: 100%; box-sizing: border-box; padding: var(--space-2) var(--space-3); border: var(--border-hairline) solid var(--border-color); border-radius: var(--radius-sm); background: transparent; color: var(--accent-text); font: inherit; }
  footer { display: flex; align-items: center; gap: var(--space-2); }
  .spacer { flex: 1; }
  footer button { min-height: 2rem; padding: var(--space-1) var(--space-3); border-radius: var(--radius-sm); border: var(--border-hairline) solid var(--border-color); font: inherit; }
  footer .quiet { background: transparent; color: var(--text-faint); }
  footer .continue { background: var(--accent-bright); color: var(--surface-ink); border-color: var(--accent-bright); }
  button:disabled, input:disabled { opacity: .5; cursor: default; }
</style>
