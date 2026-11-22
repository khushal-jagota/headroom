<script lang="ts">
  /** The answers to a permission ask, in the ask shell's footer.
   *
   * They run left to right in the order of how much they commit to. Stop turn, the answer
   * that always works whatever the ask turned out to be, stands apart on the left; the
   * answers to the ask itself sit together on the right, ending in the one primary. A
   * question's choices are not here: picking one is not a commitment to grade, so they live
   * in the body with their numbers, and only the way out remains.
   *
   * `part` lets the shell place the two halves itself; "all" draws the whole row.
   */
  import { askActions, askShape } from "../../lib/conversation/composer";
  import type { PermissionAskOption } from "../../lib/conversation/wire";

  let {
    ask,
    busy = false,
    answerAvailable = true,
    part = "all",
    onAnswer,
    onCancelTurn
  }: {
    ask: { options?: readonly PermissionAskOption[] } | null;
    busy?: boolean;
    answerAvailable?: boolean;
    part?: "all" | "start" | "end";
    onAnswer: (optionId: string) => void;
    onCancelTurn: () => void;
  } = $props();

  let shape = $derived(askShape(ask));
  let actions = $derived(shape === "question" ? askActions(null).slice(0, 1) : askActions(ask));
  let answers = $derived(actions.filter(action => action.act === "answer"));
</script>

{#snippet stop()}
  <button
    type="button"
    class="cv-button cv-button--ghost c2-ask-cancel"
    data-conversation-ask-action="cancel_turn"
    disabled={busy}
    onclick={onCancelTurn}
  ><svg class="cv-stop-glyph" viewBox="0 0 16 16" aria-hidden="true"><rect x="3" y="3" width="10" height="10" rx="2" /></svg><span>{actions[0].label}</span></button>
{/snippet}

{#snippet answerButtons()}
  {#each answers as action (action.act === "answer" ? action.optionId : "")}
    {#if action.act === "answer"}
      <!-- Three weights for three commitments: Deny is an outline, the standing permissions
           (Always allow …) are filled secondaries, and approve once is the one primary. An
           answer Panels had to supply says so in its tooltip. -->
      <button
        type="button"
        class="cv-button c2-ask-answer"
        class:cv-button--outline={action.emphasis === "decline"}
        class:cv-button--secondary={action.emphasis === "option"}
        class:cv-button--primary={action.emphasis === "primary"}
        data-conversation-ask-action={action.optionId}
        data-conversation-ask-supplied={action.supplied ? "true" : "false"}
        title={action.supplied ? undefined : "Panels supplied this answer; the backend may not take it"}
        disabled={busy || !answerAvailable}
        onclick={() => answerAvailable && onAnswer(action.optionId)}
      >{action.label}</button>
    {/if}
  {/each}
{/snippet}

{#if part === "start"}
  {@render stop()}
{:else if part === "end"}
  {@render answerButtons()}
{:else}
  <div class="c2-ask-actions" role="group" aria-label="Answers">
    {@render stop()}
    <span class="c2-ask-gap" aria-hidden="true"></span>
    {@render answerButtons()}
  </div>
{/if}

<style>
  .c2-ask-actions {
    display: flex;
    min-width: 0;
    max-width: 100%;
    flex: 1;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--cv-s2);
  }
  .c2-ask-gap { flex: 1; }
  /* A long backend label wraps rather than pushing the row wider than the composer. */
  .c2-ask-answer { flex: 0 1 auto; white-space: normal; line-height: 1.25; }
</style>
