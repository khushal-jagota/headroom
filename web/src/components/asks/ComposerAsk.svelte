<script lang="ts">
  import { cursorChoices, questionAt } from "../../lib/asks/preview";
  import AskCommit from "./AskCommit.svelte";
  import AskFrame from "./AskFrame.svelte";
  import AskSwitcher from "./AskSwitcher.svelte";
  import AskTranscript from "./AskTranscript.svelte";
  import IdleComposer from "./IdleComposer.svelte";
  import type { AskSurface } from "./askSurface";

  let { lines, asks, drafts, cursors, active, handlers }: AskSurface = $props();

  let ask = $derived(asks[active]);
  let draft = $derived(drafts[active]);
  let current = $derived(ask ? questionAt(ask, draft) : null);
  let choices = $derived(ask && draft ? cursorChoices(ask, draft) : []);
</script>

<AskFrame>
  {#snippet thread()}
    <AskTranscript {lines} />
  {/snippet}
  {#snippet foot()}
    {#if ask && draft}
      <div class="chat-box composer" data-conversation-composer>
        <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
        <section
          role="group"
          data-ask-index={active}
          data-ask-live="true"
          tabindex="-1"
          aria-label={ask.kind === "approval" ? "Approval" : "Question"}
          onkeydown={(event) => handlers.onKey(event, active)}
        >
          <AskSwitcher {asks} {active} onActivate={handlers.onActivate} />
          <p class="kicker">
            {#if ask.kind === "approval"}{ask.stage}{:else}{current?.header || "Question"}{/if}
            {#if ask.kind === "question" && ask.questions.length > 1}
              <span>{draft.step + 1} of {ask.questions.length}</span>
            {/if}
          </p>
          <p class="prompt">{ask.kind === "approval" ? ask.questions[0]?.prompt : current?.prompt}</p>
          {#if ask.detail}<div class="detail">{ask.detail}</div>{/if}
          {#if current?.multi}<p class="hint">Choose any that apply.</p>{/if}

          {#if choices.length > 0 && ask.kind === "question"}
            <div class="choices" role={current?.multi ? "group" : "radiogroup"} aria-label="Answers">
              {#each choices as choice, position (choice.id)}
                {@const selected = (draft.selected[current?.id ?? ""] ?? []).includes(choice.id)}
                <button
                  type="button"
                  class="choice"
                  role={current?.multi ? "checkbox" : "radio"}
                  aria-checked={selected}
                  data-ask-choice={choice.id}
                  data-ask-position={position}
                  data-ask-cursor={cursors[active] === position ? "true" : undefined}
                  onclick={() => current?.multi ? handlers.onToggle(active, choice.id) : handlers.onCommitChoice(active, choice.id)}
                >
                  <span>{choice.label}</span>
                  {#if choice.description}<small>{choice.description}</small>{/if}
                </button>
              {/each}
            </div>
          {/if}

          {#if current?.freeText || current?.allowOther}
            <textarea
              class="chat-ta writing"
              rows="2"
              placeholder={current.freeText ? "Write the answer" : "Other"}
              aria-label={current.freeText ? current.prompt : "Other"}
              value={current.freeText ? (draft.text[current.id] ?? "") : (draft.other[current.id] ?? "")}
              oninput={(event) => current?.freeText
                ? handlers.onText(active, event.currentTarget.value)
                : handlers.onOther(active, event.currentTarget.value)}
            ></textarea>
          {/if}
          <AskCommit {ask} {draft} index={active} cursor={cursors[active] ?? -1} {handlers} />
        </section>
      </div>
    {:else}
      <IdleComposer />
    {/if}
  {/snippet}
</AskFrame>

<style>
  .composer { display: grid; }
  section {
    display: grid;
    gap: var(--space-3);
    animation: settle var(--motion-base) var(--motion-ease);
  }
  section:focus { outline: none; }
  .kicker {
    display: flex;
    justify-content: space-between;
    margin: 0;
    color: var(--tone-tertiary);
    font-size: var(--type-small);
    font-weight: var(--weight-small);
  }
  .prompt {
    margin: 0;
    color: var(--tone-strong);
    font-size: var(--type-body);
    font-weight: var(--weight-title);
    line-height: 1.4;
  }
  .hint { margin: 0; color: var(--tone-tertiary); font-size: var(--type-small); }
  .detail {
    max-height: 9rem;
    margin: 0;
    overflow: auto;
    color: var(--tone-secondary);
    font-size: var(--type-body);
    line-height: var(--leading-body);
    white-space: pre-wrap;
  }
  .choices { display: grid; gap: 2px; }
  .choice {
    display: grid;
    gap: 2px;
    width: 100%;
    padding: var(--space-3);
    border: 0;
    border-radius: var(--radius-md);
    background: transparent;
    color: var(--tone-primary);
    font: inherit;
    font-size: var(--type-body);
    text-align: start;
    cursor: pointer;
    transition: background-color var(--motion-fast) var(--motion-ease);
  }
  .choice small { color: var(--tone-secondary); font-size: var(--type-small); font-weight: var(--weight-small); }
  .choice:hover { background: var(--surface-hover); }
  .choice[aria-checked="true"],
  .choice[data-ask-cursor="true"] { background: var(--surface-selected); color: var(--tone-strong); }
  .writing { min-height: 3.2rem; padding-block: var(--space-1); }
  @keyframes settle {
    from { opacity: 0; }
    to { opacity: 1; }
  }
  @media (prefers-reduced-motion: reduce) {
    section { animation: none; }
  }
</style>
