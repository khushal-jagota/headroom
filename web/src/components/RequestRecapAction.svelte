<script lang="ts">
  /* The owner's ask for a fresh recap, in the quiet action voice.
   *
   * The same words in every place it appears, because it does the same thing in every
   * place: it sends one message to the agent behind this piece of work.
   *
   * A request in flight is held here and nowhere else. The press remembers the line it
   * was unhappy with, and the ask clears itself the moment a different line arrives —
   * so the server stores nothing, and a reload is simply someone who has not asked yet.
   */
  import { mutateJson } from "../lib/mutate";
  import { fateSentence } from "../lib/conversation/composer";
  import type { PromptDeliveryFate } from "../lib/conversation/wire";

  let { kind, id, recap = "", class: extraClass = "" }: {
    kind: "ticket" | "sprint_item" | "chief";
    id: string;
    recap?: string;
    class?: string;
  } = $props();

  let sending = $state(false);
  let askedAgainst = $state<string | null>(null);
  let note = $state<string | null>(null);
  let asked = $derived(askedAgainst !== null && askedAgainst === recap);

  async function ask(): Promise<void> {
    if (sending || asked) return;
    sending = true;
    note = null;
    try {
      const fate = await mutateJson<PromptDeliveryFate>("/api/recap-requests", {
        method: "POST",
        body: { target: { kind, id } }
      });
      askedAgainst = recap;
      // A started turn is the press waking an idle agent. Nothing on this surface shows
      // that on its own, so the one place that knows says it.
      note = fate.fate === "started" ? "started it up" : fateSentence(fate);
    } catch (problem) {
      note = problem instanceof Error ? problem.message : "not delivered";
    } finally {
      sending = false;
    }
  }
</script>

<span class={`request-recap${extraClass ? ` ${extraClass}` : ""}`} data-request-recap={`${kind}:${id}`}>
  <!-- Marked rather than disabled: a disabled control loses focus the instant it is
       pressed, and inside a hover panel losing focus is how the panel decides you left. -->
  <button
    type="button"
    class="request-recap-action"
    aria-disabled={sending || asked}
    data-request-recap-action
    onclick={() => void ask()}
  >{asked ? "Asked" : "Ask for a recap"}</button>
  {#if note}<span class="request-recap-note" data-request-recap-note>{note}</span>{/if}
</span>

<style>
  .request-recap { display:inline-flex; align-items:center; gap:var(--space-2); flex-wrap:wrap; }
  .request-recap-action { border:0; background:none; padding:var(--space-1) 0; color:var(--text-faint); font:inherit; cursor:pointer; }
  .request-recap-action:hover { color:var(--text-strong); }
  .request-recap-action[aria-disabled="true"] { cursor:default; color:var(--text-faintest); }
  .request-recap-action[aria-disabled="true"]:hover { color:var(--text-faintest); }
  .request-recap-note { color:var(--text-faintest); font-size:var(--type-xs); }
</style>
