<script lang="ts">
  /** A question or permission ask that stops an agent's running turn, answered from Review.
   *
   * The ask itself is read from the conversation, as the conversation page reads it, and the
   * answer goes through the same two routes. Nothing here decides what the agent does next: a
   * permission ask answers with one of its own options, and a question with its options or the
   * owner's own words. Inside a Ticket's card it is one part of that card. For a Manager or the
   * Chief it is the whole card, with the same top band as a message.
   */
  import { onMount, untrack, type Snippet } from "svelte";
  import Button from "./Button.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import ClampedText from "./ClampedText.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";
  import RequestRecapAction from "./RequestRecapAction.svelte";
  import SkeletonRegion from "./SkeletonRegion.svelte";
  import ComposerField from "./conversation/ComposerField.svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { withinScreenWait } from "../lib/mutate";
  import type { ReviewAskItem } from "../lib/types";
  import { queries } from "../lib/queryCatalogue";
  import { selectedRecap } from "../lib/navigation/views/recap.svelte";
  import { reviewAskHref } from "../lib/review/asks";
  import {
    answerPermissionAsk,
    answerUserInput,
    readConversation,
    type ConversationView,
    type UserInputAnswers
  } from "../lib/conversation/wire";
  import { executorAnswerAvailable, executorAnswerSentence } from "../lib/conversation/executorEvidence";
  import { readableConversationDetail } from "../lib/conversation/conversationDetail";
  import {
    completeUserInputAnswers,
    emptyUserInputDraft,
    toggleUserInputChoice,
    userInputAnswerFor,
    type UserInputDraft
  } from "../lib/conversation/userInput";

  let { item, onSkip, onResolved, onBusy, embedded = false, footer }: {
    item: ReviewAskItem;
    onSkip: () => void;
    /** No ask is waiting in the conversation any more: this one was answered, here or elsewhere. */
    onResolved: () => void;
    onBusy?: (busy: boolean, conversationId: string) => void;
    embedded?: boolean;
    footer?: Snippet;
  } = $props();

  // The route keys this card by its conversation, so the item never changes under it.
  const conversationId = untrack(() => item.conversation_id);
  let view = $state.raw<ConversationView | null>(null);
  let loaded = $state(false);
  let error = $state<unknown>(null);
  let busy = $state(false);
  let note = $state<string | null>(null);
  let draft = $state<UserInputDraft>(emptyUserInputDraft());
  let draftRequestId = "";
  let destroyed = false;

  const chief = createQuery(() => ({ ...queries.chiefSettings(), enabled: item.owner_kind === "chief" && !embedded }));
  const recapBody = selectedRecap(() => item.recap_reference, () => !embedded);
  let recap = $derived(item.owner_kind === "chief" ? chief.data?.recap ?? "" : recapBody.body ?? item.recap);

  let permission = $derived(view?.pending_permission_ask ?? null);
  let userInput = $derived(view?.pending_user_input ?? null);
  let from = $derived(item.owner_kind === "ticket" ? "the Worker" : item.owner_kind === "manager" ? "the Manager" : "the Chief");
  let available = $derived(
    permission
      ? executorAnswerAvailable(view, "permission", permission.ask_id)
      : userInput
        ? executorAnswerAvailable(view, "user_input", userInput.request_id)
        : false
  );
  let answers = $derived(userInput ? completeUserInputAnswers(draft, userInput.questions) : null);
  let detail = $derived(readableConversationDetail(permission?.detail));
  $effect(() => { onBusy?.(busy, conversationId); });
  // A new question starts with an empty draft.
  $effect(() => {
    const id = userInput?.request_id ?? "";
    if (id !== draftRequestId) {
      draftRequestId = id;
      draft = emptyUserInputDraft();
    }
  });

  async function load(): Promise<void> {
    try {
      const read = await readConversation(conversationId);
      if (destroyed) return;
      view = read;
      error = null;
      loaded = true;
      // Answered in another place while this card was waiting.
      if (!read.pending_permission_ask && !read.pending_user_input) onResolved();
    } catch (problem) {
      if (!destroyed) error = problem;
    }
  }
  onMount(() => {
    void load();
    return () => { destroyed = true; };
  });

  /** The ask that the server took an answer for. The card can stay for a moment after
   *  that, and a second press must not send a second answer. */
  let answered: string | null = null;
  function sentence(fate: "applied" | "saved" | "refused", refused: string): string | null {
    return fate === "refused" ? refused : fate === "saved" ? executorAnswerSentence("saved") : null;
  }
  async function answerPermission(optionId: string): Promise<void> {
    const ask = permission;
    if (!ask || busy || !available || answered === ask.ask_id) return;
    busy = true;
    note = null;
    try {
      const { fate } = await answerPermissionAsk(conversationId, ask.ask_id, optionId);
      if (fate !== "refused") answered = ask.ask_id;
      note = sentence(fate, "The backend did not take that answer. Open the conversation to stop the turn.");
      // The card leaves when the conversation is read again. Review waits for that only
      // a short, limited time.
      await withinScreenWait(load());
    } catch (problem) {
      error = problem;
    } finally {
      busy = false;
    }
  }
  async function submitUserInput(): Promise<void> {
    const request = userInput;
    if (!request || !answers || busy || !available || answered === request.request_id) return;
    busy = true;
    note = null;
    try {
      const { fate } = await answerUserInput(conversationId, request.request_id, answers);
      if (fate !== "refused") answered = request.request_id;
      note = sentence(fate, "The backend did not take those answers. You can try again.");
      await withinScreenWait(load());
    } catch (problem) {
      error = problem;
    } finally {
      busy = false;
    }
  }
</script>

<div data-review-ask-card data-review-item-type="ask" data-conversation-id={conversationId}>
  {#if !embedded}
    <div class="review-eyebrow review-eyebrow--decisions">
      <div class="review-ticket-decision-line review-ticket-decision-line--top" data-review-top-decisions>
        <Button variant="ghost" data-open-conversation href={reviewAskHref(item)}>Open conversation</Button>
        <Button variant="ghost" data-skip disabled={busy} onclick={onSkip}>Skip</Button>
      </div>
    </div>
    <h1 class="review-ticket-title">{item.title}</h1>
    {#if recap}<ClampedText class="review-recap" contentKey={recap}><MarkdownBlock text={recap} /></ClampedText>{/if}
    <div class="review-recap-request"><RequestRecapAction kind={item.owner_kind} id={item.owner_id} /></div>
  {/if}
  <SkeletonRegion shape="block" loading={!loaded && !error} loadingText="Loading question...">
    {#if permission || userInput}
      <section class="ask" data-review-card data-review-ask={permission ? "permission" : "question"} data-artifact-origin={`ask:${conversationId}`} class:ask--alone={!embedded}>
        <div class="ask-line"><h2>{permission ? "Permission" : "Question"}</h2><span class="ask-sep" aria-hidden="true">·</span><span>From {from}</span></div>
        {#if permission}
          <p class="ask-lead">{permission.title}</p>
          {#if detail}<pre class="ask-detail" data-review-ask-detail>{detail}</pre>{/if}
          <div class="ask-actions">
            {#each permission.options as option (option.option_id)}
              <Button variant="secondary" data-review-ask-option={option.option_id} disabled={busy || !available} onclick={() => void answerPermission(option.option_id)}>{option.label}</Button>
            {/each}
          </div>
        {:else if userInput}
          {#each userInput.questions as question (question.question_id)}
            <div class="ask-question" data-review-ask-question={question.question_id}>
              {#if question.header}<div class="ask-header">{question.header}</div>{/if}
              <p class="ask-lead">{question.question}</p>
              {#if question.options.length}
                <div class="ask-choices" role="group" aria-label="Answers">
                  {#each question.options as option, index (option.label)}
                    <button type="button" class="ask-choice" data-review-ask-choice={option.label}
                      aria-pressed={(draft.selected[question.question_id] ?? []).includes(option.label)}
                      disabled={busy || !available}
                      onclick={() => { draft = toggleUserInputChoice(draft, question, option.label); }}>
                      <span class="ask-choice-text"><span>{option.label}</span>{#if option.description}<span class="ask-choice-description">{option.description}</span>{/if}</span>
                      <span class="ask-number" aria-hidden="true">{index + 1}</span>
                    </button>
                  {/each}
                </div>
              {/if}
              {#if question.allow_other || question.options.length === 0}
                <div class="ask-own-words">
                  <ComposerField
                    bind:value={
                      () => draft.other[question.question_id] ?? "",
                      (text) => {
                        draft = {
                          selected: question.multi_select ? draft.selected : { ...draft.selected, [question.question_id]: [] },
                          other: { ...draft.other, [question.question_id]: text }
                        };
                      }
                    }
                    placeholder={question.options.length ? "Or answer in your own words…" : "Type your answer…"}
                    label="Your answer"
                    disabled={busy || !available}
                    inputAttributes={{ "data-review-ask-own-words": question.question_id }} />
                </div>
              {/if}
            </div>
          {/each}
          <div class="ask-actions">
            <Button variant="secondary" data-review-ask-send disabled={busy || !available || !answers} onclick={() => void submitUserInput()}>Send answer</Button>
          </div>
        {/if}
        {#if note}<p class="ask-note" data-review-ask-note>{note}</p>{/if}
        {#if error}<ErrorLine {error} />{/if}
      </section>
    {:else if error}
      <ErrorLine {error} />
      <div class="ask-actions"><Button variant="secondary" onclick={() => void load()}>Retry</Button></div>
    {/if}
  </SkeletonRegion>
  {#if !embedded}{@render footer?.()}{/if}
</div>

<style>
  /* The same card as a proposal: its plane, its line above, its prose, and its action row. */
  .ask { padding:var(--page-card-pad); background:var(--page-card-fill); border:var(--border-hairline) solid var(--page-card-edge); border-radius:var(--page-card-radius); }
  .ask--alone { margin-top:var(--space-5); }
  .ask:not(.ask--alone) { margin-bottom:var(--space-3); }
  .ask-line { display:flex; align-items:center; flex-wrap:wrap; min-height:20px; color:var(--tone-secondary); font-size:var(--type-small); font-weight:var(--weight-small); line-height:18px; }
  h2 { margin:0; font:inherit; letter-spacing:0; color:inherit; }
  .ask-sep { padding-inline:var(--space-2); color:var(--tone-tertiary); }
  .ask-lead { max-width:var(--page-prose-measure); margin:var(--space-2) 0 0; color:var(--tone-primary); font-size:var(--type-body); line-height:var(--leading-body); overflow-wrap:anywhere; }
  .ask-header { margin-top:var(--space-4); color:var(--tone-tertiary); font-size:var(--type-small); }
  .ask-question + .ask-question { margin-top:var(--space-4); }
  .ask-detail { margin:var(--space-3) 0 0; max-height:calc(var(--type-xs) * 12); overflow:auto; color:var(--tone-secondary); font-family:var(--font-mono); font-size:var(--type-xs); white-space:pre-wrap; overflow-wrap:anywhere; }
  .ask-choices { display:grid; gap:var(--space-1); margin-top:var(--space-4); }
  .ask-choice { display:flex; align-items:center; gap:var(--space-3); width:100%; background:transparent; border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-sm); color:var(--accent-text); cursor:pointer; font:inherit; font-size:var(--type-sm); padding:var(--space-2) var(--space-3); text-align:start; }
  .ask-choice:hover:not(:disabled), .ask-choice:focus-visible { border-color:var(--accent-bright); }
  .ask-choice[aria-pressed="true"] { border-color:var(--accent-bright); background:var(--accent-surface); }
  .ask-choice:disabled { cursor:default; opacity:.7; }
  .ask-choice-text { flex:1; min-width:0; display:grid; gap:2px; overflow-wrap:anywhere; }
  .ask-choice-description { color:var(--tone-secondary); font-size:var(--type-xs); }
  .ask-number { flex:none; min-width:var(--space-4); border:var(--border-hairline) solid var(--border-color); border-radius:var(--radius-sm); color:var(--accent-bright); font-family:var(--font-mono); font-size:var(--type-xs); font-variant-numeric:tabular-nums; padding:0 var(--space-1); text-align:center; }
  .ask-own-words { margin-top:var(--space-3); }
  .ask-actions { display:flex; align-items:center; justify-content:flex-end; flex-wrap:wrap; gap:var(--space-2); margin-top:var(--space-5); }
  .ask-note { margin:var(--space-3) 0 0; color:var(--tone-secondary); font-size:var(--type-small); }
  .review-eyebrow--decisions { justify-content:flex-end; }
  .review-ticket-decision-line--top { padding:0; margin-right:calc(var(--space-3) * -1); }
  .review-recap-request { display:flex; justify-content:flex-end; margin-top:var(--space-2); font-size:var(--type-sm); }
  @media (max-width:720px) { .review-ticket-decision-line--top { margin-right:0; } }
</style>
