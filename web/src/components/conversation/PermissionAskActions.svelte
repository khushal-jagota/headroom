<script lang="ts">
  /** The answers, in the composer's own footer where its buttons already live.
   *
   * They run left to right in the order of how much they commit to, and the leftmost —
   * cancelling the turn — is the answer that always works whatever the ask turned out to
   * be. A question's choices are not here: picking one is not a commitment to grade, so
   * they live in the band above with their numbers, and only the escape hatch remains.
   */
  import { askActions, askShape } from "../../lib/conversation/composer";
  import type { PermissionAskOption } from "../../lib/conversation/wire";

  let {
    ask,
    busy = false,
    answerAvailable = true,
    onAnswer,
    onCancelTurn
  }: {
    ask: { options?: readonly PermissionAskOption[] } | null;
    busy?: boolean;
    answerAvailable?: boolean;
    onAnswer: (optionId: string) => void;
    onCancelTurn: () => void;
  } = $props();

  let shape = $derived(askShape(ask));
  let actions = $derived(shape === "question" ? askActions(null).slice(0, 1) : askActions(ask));
</script>

<div class="c2-ask-actions" role="group" aria-label="Answers">
  {#each actions as action (action.act === "cancel_turn" ? "cancel" : action.optionId)}
    {#if action.act === "cancel_turn"}
      <button
        type="button"
        class="button button--ghost c2-ask-cancel"
        data-conversation-ask-action="cancel_turn"
        disabled={busy}
        onclick={onCancelTurn}
      >{action.label}</button>
      <!-- Cancelling stands apart on the left; the answers to the ask itself sit together
           on the right, ending in the one that commits. -->
      <span class="c2-ask-gap" aria-hidden="true"></span>
    {:else}
      <!-- THE button (app.css, "Buttons"): decline is ghost, the backend's own options are
           secondary, approve once is the one primary. -->
      <button
        type="button"
        class="button c2-ask-answer"
        class:button--ghost={action.emphasis === "decline"}
        class:button--secondary={action.emphasis === "option"}
        class:button--primary={action.emphasis === "primary"}
        class:is-decline={action.emphasis === "decline"}
        class:is-primary={action.emphasis === "primary"}
        class:is-supplied={!action.supplied}
        data-conversation-ask-action={action.optionId}
        data-conversation-ask-supplied={action.supplied ? "true" : "false"}
        title={action.supplied ? undefined : "Panels supplied this answer; the backend may not take it"}
        disabled={busy || !answerAvailable}
        onclick={() => answerAvailable && onAnswer(action.optionId)}
      >{action.label}</button>
    {/if}
  {/each}
</div>

<style>
  .c2-ask-actions {
    display: flex;
    min-width: 0;
    max-width: 100%;
    flex: 1;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2);
  }
  .c2-ask-gap { flex: 1; }
  /* A long backend label wraps rather than pushing the row wider than the composer. */
  .c2-ask-answer {
    flex: 0 1 auto;
    max-width: 100%;
    min-height: var(--button-height);
    height: auto;
    white-space: normal;
    overflow-wrap: anywhere;
    line-height: 1.25;
  }
  /* The ask shell's action row, shared with the question and the key request. On the
     composer's fill the ghost's secondary tone read as disabled and the secondary fill was
     the composer's own, so neither showed: the ways out keep the primary tone, and the
     backend's other options take one step of white, as a question's answers do. */
  .c2-ask-actions .button--ghost:not(:disabled) { color: var(--text-default); }
  .c2-ask-actions .button--ghost:hover:not(:disabled) { color: var(--text-strong); }
  .c2-ask-actions .button--secondary { background: var(--state-step); }
  .c2-ask-actions .button--secondary:hover:not(:disabled) { background-image: var(--interaction-hover); }
  .c2-ask-actions .c2-ask-cancel:hover:not(:disabled),
  .c2-ask-actions .c2-ask-answer.is-decline:hover:not(:disabled) { color: var(--accent-error); }
  /* An answer Panels made up is marked as such: a dashed edge, never a solid one. */
  .c2-ask-answer.is-supplied {
    border: var(--border-hairline) dashed var(--border-raised);
  }
</style>
