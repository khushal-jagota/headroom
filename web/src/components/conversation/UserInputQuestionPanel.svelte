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
  // One name for ending the turn on every ask: the permission ask's own words.
  import { CANCEL_TURN_ACTION } from "../../lib/conversation/composer";

  let {
    request,
    busy = false,
    answerAvailable = true,
    note = null,
    onSubmit,
    onCancelTurn
  }: {
    request: { requestId: string; questions: readonly UserInputQuestion[] };
    busy?: boolean;
    answerAvailable?: boolean;
    note?: string | null;
    onSubmit?: (answers: UserInputAnswers) => void;
    onCancelTurn?: () => void;
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

  function choose(option: string): void {
    if (question === undefined || busy || !answerAvailable) return;
    draft = toggleUserInputChoice(draft, question, option);
  }

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
    if (!isLast) {
      step += 1;
      return;
    }
    const answers = completeUserInputAnswers(draft, request.questions);
    if (answers !== null) onSubmit?.(answers);
  }
</script>

<!-- The question is the composer's content while it waits, so it stands on the
     composer's own surface: no plane, no frame, no outlined boxes inside it. -->
<section
  class="question-panel"
  data-user-input-panel
  data-user-input-request-id={request.requestId}
  aria-label="Questions from the agent"
>
  {#if question}
    <header>
      <div class="progress">
        <span class="eyebrow">{question.header || "Question"}</span>
        {#if request.questions.length > 1}
          <span class="count">{step + 1} of {request.questions.length}</span>
        {/if}
      </div>
      <h3 id={`${uid}-question`}>{question.question}</h3>
      <p class="hint" id={`${uid}-hint`}>
        {question.multi_select ? "Choose all that apply." : "Choose one."}
        {#if question.allow_other}Or write your own below.{/if}
      </p>
    </header>

    <div
      class="options"
      role="group"
      aria-labelledby={`${uid}-question`}
      aria-describedby={`${uid}-hint`}
    >
      {#each question.options as option (option.label)}
        {@const selected = (draft.selected[question.question_id] ?? []).includes(option.label)}
        <button
          type="button"
          class:selected
          data-user-input-option={option.label}
          aria-pressed={selected}
          disabled={busy || !answerAvailable}
          onclick={() => choose(option.label)}
        >
          <span
            class="mark"
            class:square={question.multi_select}
            aria-hidden="true"
          ></span>
          <span class="words">
            <span class="label">{option.label}</span>
            {#if option.description}<small>{option.description}</small>{/if}
          </span>
        </button>
      {/each}
    </div>

    {#if question.allow_other}
      <label class="other">
        <span class="visually-hidden">Other answer</span>
        <input
          data-user-input-other
          value={draft.other[question.question_id] ?? ""}
          placeholder="Other: type your own answer"
          disabled={busy || !answerAvailable}
          oninput={(event) => writeOther(event.currentTarget.value)}
          onkeydown={otherKeydown}
        />
      </label>
    {/if}

    {#if note}<p class="note" role="alert">{note}</p>{/if}

    <footer>
      <button
        type="button"
        class="button button--ghost cancel"
        data-user-input-cancel
        disabled={busy}
        onclick={() => onCancelTurn?.()}
      >{CANCEL_TURN_ACTION.label}</button>
      <span class="spacer"></span>
      {#if step > 0}
        <button
          type="button"
          class="button button--secondary"
          data-user-input-back
          disabled={busy}
          onclick={() => (step -= 1)}
        >Back</button>
      {/if}
      <button
        type="button"
        class="button button--primary"
        data-user-input-continue
        disabled={busy || !answerAvailable || !canContinue}
        onclick={continueOrSubmit}
      >{busy && isLast ? "Sending…" : isLast ? (request.questions.length > 1 ? "Submit answers" : "Submit answer") : "Next"}</button>
    </footer>
  {:else}
    <p class="note" role="alert">This question request contained no questions.</p>
    <footer>
      <button type="button" class="button button--ghost cancel" data-user-input-cancel onclick={() => onCancelTurn?.()}>{CANCEL_TURN_ACTION.label}</button>
    </footer>
  {/if}
</section>

<style>
  .question-panel { display: grid; gap: var(--space-3); padding: var(--space-3) 0 var(--space-1); width: 100%; box-sizing: border-box; color: var(--text-default); }
  header { display: grid; gap: var(--space-1); }
  .progress { display: flex; justify-content: space-between; align-items: baseline; gap: var(--space-2); font-size: var(--type-xs); }
  .eyebrow { color: var(--accent-bright); font-weight: var(--weight-title); letter-spacing: var(--tracking-label); text-transform: uppercase; overflow-wrap: anywhere; }
  .count { flex: none; color: var(--text-muted); font-variant-numeric: tabular-nums; }
  h3 { margin: 0; font: inherit; font-size: var(--type-md); font-weight: var(--weight-title); line-height: 1.4; color: var(--text-strong); overflow-wrap: anywhere; }
  .hint, .note { margin: 0; color: var(--text-muted); font-size: var(--type-xs); line-height: 1.5; }
  .note[role="alert"] { color: var(--accent-error); }
  .options { display: grid; gap: var(--space-1); }
  /* Filled, not outlined, like the shared secondary button: one step of white on the
     composer. The chosen answer carries the accent in its mark and a tint of it. */
  .options button { display: flex; align-items: flex-start; gap: var(--space-3); width: 100%; min-height: var(--button-height); padding: var(--space-2) var(--space-3); text-align: left; border: 0; border-radius: var(--radius-md); background: var(--state-step); color: inherit; font: inherit; font-size: var(--type-row); cursor: pointer; }
  .options button:hover:not(:disabled) { background-image: var(--interaction-hover); }
  .options button.selected { background: color-mix(in srgb, var(--accent-bright) 16%, transparent); }
  .options button.selected .label { color: var(--text-strong); }
  .mark { flex: none; box-sizing: border-box; width: 14px; height: 14px; margin-top: 3px; border: 1.5px solid var(--text-muted); border-radius: 50%; }
  .mark.square { border-radius: var(--radius-sm); }
  .selected .mark { border-color: var(--accent-bright); background: radial-gradient(circle, var(--accent-bright) 0 3.5px, transparent 4px); }
  .selected .mark.square { background: linear-gradient(var(--accent-bright), var(--accent-bright)) center / 6px 6px no-repeat; }
  .words { display: grid; gap: 2px; min-width: 0; }
  .label { overflow-wrap: anywhere; }
  .options small { color: var(--text-muted); font-size: var(--type-xs); line-height: 1.45; overflow-wrap: anywhere; }
  .other { display: grid; }
  .other input { width: 100%; box-sizing: border-box; min-height: var(--button-height); padding: var(--space-2) var(--space-3); border: var(--border-hairline) solid var(--border-raised); border-radius: var(--radius-md); background: transparent; color: var(--text-strong); font: inherit; font-size: var(--type-row); }
  .other input::placeholder { color: var(--text-muted); }
  .visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
  footer { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); }
  .spacer { flex: 1; }
  /* The ask shell's action row, as the permission ask's: the way out on the left in the
     primary tone (the ghost's secondary tone read as disabled on the composer's fill), the
     one primary at the right end. A primary that cannot go yet keeps a readable label on
     the secondary fill rather than fading to the placeholder tone. */
  footer .button--ghost:not(:disabled) { color: var(--text-default); }
  footer .button--ghost:hover:not(:disabled) { color: var(--text-strong); }
  footer .cancel:hover:not(:disabled) { color: var(--accent-error); }
  footer .button--secondary { background: var(--state-step); }
  footer .button--secondary:hover:not(:disabled) { background-image: var(--interaction-hover); }
  footer .button--primary:disabled { background: var(--state-step); color: var(--text-muted); }
  .options button:disabled { cursor: default; color: var(--text-faint); }
  input:disabled { opacity: .5; cursor: default; }
</style>
