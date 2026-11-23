<script lang="ts">
  import type {
    UserInputAnswers,
    UserInputAttachment,
    UserInputQuestion
  } from "../../lib/conversation/wire";
  import {
    completeUserInputAnswers,
    pendingAnswerAttachments,
    emptyUserInputDraft,
    toggleUserInputChoice,
    userInputOptionForKey,
    userInputAnswerFor,
    withOwnAnswer,
    type UserInputDraft
  } from "../../lib/conversation/userInput";
  import type { Snippet } from "svelte";
  import { onDestroy } from "svelte";
  import ComposerField from "./ComposerField.svelte";
  import { releasePendingImages, type PendingConversationImage } from "../../lib/conversation/pendingImages";
  import type { PendingConversationFile } from "../../lib/conversation/pendingFiles";
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
    ownAttachments = [],
    onQuestionDraftChange,
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
    ownAttachments?: UserInputAttachment[];
    onQuestionDraftChange?: (questionId: string, text: string, attachments: UserInputAttachment[]) => void;
    /** Whether the question on screen takes an own answer, for the composer to say so. */
    acceptsOwnAnswer?: boolean;
    /** The composer's words were taken as an answer, so the composer can let them go. */
    onOwnAnswerUsed?: (requestId: string) => void;
    /** The person chose "Something else": the composer takes the cursor. */
    onWriteOwnAnswer?: () => void;
    onSubmit?: (answers: UserInputAnswers) => Promise<boolean> | boolean | void;
  } = $props();

  let step = $state(0);
  let draft = $state<UserInputDraft>(emptyUserInputDraft());
  let rememberedRequestId = $state("");
  let attachmentDrafts = $state<Record<string, UserInputAttachment[]>>({});
  let standaloneDrafts = $state<Record<string, { images: PendingConversationImage[]; files: PendingConversationFile[]; busy: boolean }>>({});
  let submitting = $state(false);
  function releaseStandalone(): void {
    for (const entry of Object.values(standaloneDrafts)) releasePendingImages(entry.images);
    standaloneDrafts = {};
  }
  onDestroy(releaseStandalone);
  const uid = $props.id();

  $effect(() => {
    if (request.requestId === rememberedRequestId) return;
    rememberedRequestId = request.requestId;
    step = 0;
    draft = emptyUserInputDraft();
    attachmentDrafts = {};
    releaseStandalone();
    standaloneDrafts = Object.fromEntries(request.questions.map((each) => [each.question_id, { images: [], files: [], busy: false }]));
  });
  /** Where the arrow keys are among the rows. v10 (4): nothing is pre-highlighted; a row
   *  lights only under the pointer or keyboard focus. */
  let focusedOption = $state(-1);
  $effect(() => { step; request.requestId; focusedOption = -1; });

  let question = $derived(request.questions[step]);
  let effectiveDraft = $derived(question && ownAnswer !== null && question.allow_other ? withOwnAnswer(draft, question, ownAnswer) : draft);
  let currentAnswers = $derived(
    question === undefined ? [] : userInputAnswerFor(effectiveDraft, question)
  );
  let allAttachments = $derived({ ...attachmentDrafts,
    ...Object.fromEntries(Object.entries(standaloneDrafts).map(([id, entry]) => [id, pendingAnswerAttachments(entry.images, entry.files)])),
    ...(question && ownAnswer !== null ? { [question.question_id]: ownAttachments } : {})
  });
  let currentAttachments = $derived(question ? allAttachments[question.question_id] ?? [] : []);
  let readingAttachments = $derived(Object.values(standaloneDrafts).some((entry) => entry.busy));
  let chosenSomething = $derived(currentAnswers.length > 0);
  let canContinue = $derived(chosenSomething || Boolean(question?.allow_other && currentAttachments.length));
  let isLast = $derived(step === request.questions.length - 1);
  let optionsElement = $state<HTMLDivElement | null>(null);
  let composerAnswers = $derived(ownAnswer !== null);

  $effect(() => { acceptsOwnAnswer = question?.allow_other === true; });

  function captureCurrent(): void {
    if (!question) return;
    if (ownAnswer !== null && question.allow_other) draft = withOwnAnswer(draft, question, ownAnswer);
    attachmentDrafts = { ...attachmentDrafts, [question.question_id]: [...currentAttachments] };
  }

  function selectStep(next: number): void {
    step = next;
    const target = request.questions[next];
    if (target && composerAnswers) onQuestionDraftChange?.(target.question_id, draft.other[target.question_id] ?? "", attachmentDrafts[target.question_id] ?? []);
  }

  /** Captures this question's draft before a pager move or a backend answer. */
  export function answerWith(words: string, attached: UserInputAttachment[] = ownAttachments): boolean {
    if (!question || !question.allow_other || busy || submitting || !answerAvailable || (!words.trim() && !attached.length)) return false;
    draft = withOwnAnswer(draft, question, words);
    attachmentDrafts = { ...attachmentDrafts, [question.question_id]: [...attached] };
    void continueOrSubmit();
    return true;
  }

  // v10 (4): no row takes focus on its own (it would light it); numbers and arrows work at once.

  /** 1–9 choose an answer, as a permission ask's question does. Typing a number into a
   *  field means the number. */
  function onWindowKeydown(event: KeyboardEvent): void {
    if (question === undefined || busy || submitting || readingAttachments || !answerAvailable) return;
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
    if (question === undefined || busy || submitting || readingAttachments || !answerAvailable) return;
    captureCurrent();
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
    if (composerAnswers) onQuestionDraftChange?.(question.question_id, "", attachmentDrafts[question.question_id] ?? []);
    void continueOrSubmit();
  }

  $effect(() => {
    commitReady = question !== undefined && canContinue && !busy && !submitting && !readingAttachments && answerAvailable;
  });

  /** Goes on with the answers chosen so far (a several-answer question): the composer's send. */
  export function commit(): void { captureCurrent(); void continueOrSubmit(); }

  function writeOther(value: string): void {
    if (question === undefined || busy || submitting || readingAttachments || !answerAvailable) return;
    draft = {
      selected: question.multi_select
        ? draft.selected
        : { ...draft.selected, [question.question_id]: [] },
      other: { ...draft.other, [question.question_id]: value }
    };
  }

  async function continueOrSubmit(): Promise<void> {
    if (question === undefined || busy || submitting || readingAttachments || !answerAvailable) return;
    const given = userInputAnswerFor(draft, question);
    if (!given.length && !(question.allow_other && attachmentDrafts[question.question_id]?.length)) return;
    if (!isLast) { selectStep(step + 1); return; }
    const attachments = composerAnswers ? attachmentDrafts : allAttachments;
    const answers = completeUserInputAnswers(draft, request.questions, attachments);
    if (answers !== null) {
      const submittedRequestId = request.requestId;
      submitting = true;
      try {
        const accepted = await onSubmit?.(answers);
        if (accepted === true && request.requestId === submittedRequestId) {
          onOwnAnswerUsed?.(submittedRequestId);
          releaseStandalone();
          attachmentDrafts = {};
        }
      } finally { submitting = false; }
      return;
    }
    const open = request.questions.findIndex((each) => userInputAnswerFor(draft, each).length === 0 && !(each.allow_other && attachments[each.question_id]?.length));
    if (open >= 0) selectStep(open);
  }

  /** v13 (5): the pager's forward arrow moves to the next question (answered or not); it is
   *  unavailable only on the last one. Back is unavailable only on the first. */
  function goForward(): void {
    if (busy || submitting || readingAttachments || isLast) return;
    captureCurrent();
    selectStep(step + 1);
  }
</script>

<svelte:window onkeydown={onWindowKeydown} />

{#snippet position()}
  <!-- v4.1: no category label; the pager alone, "1 of 2 ‹ ›". -->
  {#if request.questions.length > 1}
    <span class="count">{step + 1} of {request.questions.length}</span>
    <button type="button" class="cv-icon-button cv-icon-button--small" data-user-input-back
      aria-label="Previous question" title="Previous question" disabled={busy || submitting || readingAttachments || step === 0}
      onclick={() => { captureCurrent(); selectStep(step - 1); }}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m10 4-4 4 4 4" /></svg></button>
    <button type="button" class="cv-icon-button cv-icon-button--small" data-user-input-forward
      aria-label="Next question" title="Next question" disabled={busy || submitting || readingAttachments || isLast}
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
          disabled={busy || submitting || readingAttachments || !answerAvailable}
          onclick={() => choose(option.label)}
          onfocus={() => (focusedOption = index)}
        >
          <AskOptionContent label={option.label} description={option.description}
            digit={index < 9 ? index + 1 : null} {selected} />
        </button>
      {/each}
    </div>

    {#if question.allow_other && !composerAnswers}
      <div class="other">
        <ComposerField
          bind:images={() => standaloneDrafts[question.question_id]?.images ?? [], (value) => standaloneDrafts[question.question_id].images = value}
          bind:files={() => standaloneDrafts[question.question_id]?.files ?? [], (value) => standaloneDrafts[question.question_id].files = value}
          bind:intakeBusy={() => standaloneDrafts[question.question_id]?.busy ?? false, (value) => standaloneDrafts[question.question_id].busy = value}
          bind:value={() => draft.other[question.question_id] ?? "", writeOther}
          placeholder="Something else…" label="Other answer" inputAttributes={{ "data-user-input-other": "" }}
          disabled={busy || submitting || !answerAvailable}
          onsubmit={() => { captureCurrent(); void continueOrSubmit(); }}
          send={{ label: "Send answer", ready: canContinue, sending: submitting, disabled: !canContinue || busy || !answerAvailable, onclick: () => { captureCurrent(); void continueOrSubmit(); } }} />
      </div>
    {:else if !question.allow_other}
      <p class="note" data-composer-attachment-unavailable>This question only accepts the listed choices. Answer with a listed choice, then attach files in a message.</p>
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
  .other { margin-block-start: var(--cv-s2); }
  .note { margin: var(--cv-s3) 0 0; color: var(--cv-danger); font: var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font); }
</style>
