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
    type UserInputDraft
  } from "../../lib/conversation/userInput";
  // One name for ending the turn on every ask: the permission ask's own words.
  import { CANCEL_TURN_ACTION } from "../../lib/conversation/composer";
  import type { Snippet } from "svelte";
  import { onMount } from "svelte";
  import AskShell from "./AskShell.svelte";

  let {
    request,
    busy = false,
    answerAvailable = true,
    note = null,
    framed = true,
    replyStrip,
    onSubmit,
    onCancelTurn
  }: {
    request: { requestId: string; questions: readonly UserInputQuestion[] };
    busy?: boolean;
    answerAvailable?: boolean;
    note?: string | null;
    /** Whether it draws its own raised plane (the composer gives up its own for it). */
    framed?: boolean;
    /** The composer's one-line reply, under the answers. */
    replyStrip?: Snippet;
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
  let optionsElement = $state<HTMLDivElement | null>(null);

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

{#snippet stopTurn()}
  <button
    type="button"
    class="cv-button cv-button--ghost cancel"
    data-user-input-cancel
    disabled={busy}
    onclick={() => onCancelTurn?.()}
  ><svg class="cv-stop-glyph" viewBox="0 0 16 16" aria-hidden="true"><rect x="3" y="3" width="10" height="10" rx="2" /></svg><span>{CANCEL_TURN_ACTION.label}</span></button>
{/snippet}

{#snippet commit()}
  <!-- The one primary. Before there is an answer it waits as a quiet outline. -->
  <button
    type="button"
    class="cv-button cv-button--primary"
    data-user-input-continue
    title={!canContinue ? "Choose an answer, or write your own, to go on" : undefined}
    disabled={busy || !answerAvailable || !canContinue}
    onclick={continueOrSubmit}
  >{busy && isLast ? "Sending…" : isLast ? (request.questions.length > 1 ? "Send answers" : "Send answer") : "Next"}</button>
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
    footerStart={stopTurn}
    footerEnd={commit}
    data-user-input-panel
    data-user-input-request-id={request.requestId}
    aria-label="Questions from the agent"
  >
    <div
      class="options"
      role="group"
      aria-labelledby={`${uid}-question`}
      bind:this={optionsElement}
    >
      {#each question.options as option, index (option.label)}
        {@const selected = (draft.selected[question.question_id] ?? []).includes(option.label)}
        <button
          type="button"
          class:selected
          data-user-input-option={option.label}
          aria-pressed={selected}
          aria-keyshortcuts={index < 9 ? String(index + 1) : undefined}
          disabled={busy || !answerAvailable}
          onclick={() => choose(option.label)}
        >
          <span class="key" aria-hidden="true">
            {#if selected}<svg viewBox="0 0 16 16"><path d="m3.5 8.5 3 3 6-7" /></svg>{:else if index < 9}{index + 1}{/if}
          </span>
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
    footerStart={stopTurn}
    data-user-input-panel
    data-user-input-request-id={request.requestId}
    aria-label="Questions from the agent"
  />
{/if}

<style>
  .topic { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sep { color: var(--cv-t4); }
  .count { flex: none; }
  .options { display: grid; gap: var(--cv-s1); }
  /* An option is a two-line row (label, then its description) with its number key on the
     left. Hover is one step up; the chosen one takes the control fill and a check in place
     of its key: tone and a mark, not a coloured edge. */
  .options button {
    display: flex;
    align-items: center;
    gap: var(--cv-s3);
    width: 100%;
    min-height: 48px;
    padding: var(--cv-s2) var(--cv-s3) var(--cv-s2) var(--cv-s2);
    border: 0;
    border-radius: var(--cv-r-md);
    background: transparent;
    color: var(--cv-t1);
    font: inherit;
    text-align: left;
    cursor: pointer;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .options button:hover:not(:disabled) { background: var(--cv-control); }
  .options button.selected { background: var(--cv-control); }
  .options button:focus-visible { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: -2px; }
  .options button:disabled { cursor: default; color: var(--cv-t3); }
  .key {
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
  .selected .key { background: var(--cv-primary-fill); color: var(--cv-primary-ink); }
  .key svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
  .words { display: grid; gap: 2px; min-width: 0; }
  .label { overflow-wrap: anywhere; font: var(--cv-weight-label) var(--cv-type-ui)/var(--cv-leading-ui) var(--cv-font); }
  .options small { color: var(--cv-t3); font: var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font); overflow-wrap: anywhere; }
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
  .other input::placeholder { color: var(--cv-t4); }
  .other input:focus { outline: none; border-color: var(--cv-accent-line); }
  .other input:disabled { cursor: default; color: var(--cv-t4); }
  .note { margin: var(--cv-s3) 0 0; color: var(--cv-danger); font: var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font); }
  .visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
</style>
