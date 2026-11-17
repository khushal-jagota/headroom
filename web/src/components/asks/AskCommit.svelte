<script lang="ts">
  import { answerReady, questionAt, type AskDraft, type PreviewAsk } from "../../lib/asks/preview";
  import type { AskHandlers } from "./askSurface";

  let {
    ask,
    draft,
    index,
    cursor = -1,
    handlers
  }: {
    ask: PreviewAsk;
    draft: AskDraft;
    index: number;
    cursor?: number;
    handlers: AskHandlers;
  } = $props();

  let question = $derived(questionAt(ask, draft));
  let needsSend = $derived(question !== null && (question.multi || question.freeText || question.allowOther));
  let ready = $derived(answerReady(ask, draft));
  let stepped = $derived(ask.kind === "question" && ask.questions.length > 1);
</script>

<div class="commit">
  <button type="button" class="button button--ghost" data-ask-cancel onclick={() => handlers.onCancel(index)}>Stop</button>
  {#if stepped && draft.step > 0}
    <button type="button" class="button button--ghost" data-ask-back onclick={() => handlers.onStep(index, -1)}>Back</button>
  {/if}
  {#if ask.kind === "approval" || needsSend}
    <div class="actions">
      {#if ask.kind === "approval"}
        {#each ask.actions as action, position (action.id)}
          <button
            type="button"
            class="button {action.emphasis === 'primary' ? 'button--primary' : 'button--ghost'} {action.emphasis === 'decline' ? 'decline' : ''}"
            data-ask-choice={action.id}
            data-ask-position={position}
            data-ask-cursor={cursor === position ? "true" : undefined}
            onclick={() => handlers.onCommitChoice(index, action.id)}
          >{action.label}</button>
        {/each}
      {:else}
        <button
          type="button"
          class="button button--primary"
          data-ask-send
          disabled={!ready}
          onclick={() => handlers.onCommit(index)}
        >{stepped && draft.step < ask.questions.length - 1 ? "Next" : "Send"}</button>
      {/if}
    </div>
  {/if}
</div>

<style>
  .commit {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: flex-end;
    gap: var(--space-2);
    margin-inline-start: auto;
  }
  .decline:hover,
  .decline:focus-visible { color: var(--accent-error); }
  button[data-ask-cursor="true"]:not(:global(.button--primary)) {
    background: var(--surface-selected);
    color: var(--tone-strong);
  }
</style>
