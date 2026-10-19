<script lang="ts">
  /* The owner's ask for a fresh recap, in the quiet action voice.
   *
   * The same words in every place it appears, because it does the same thing in every
   * place: it sends one message to the agent behind this piece of work.
   *
   * Pressing clears the recap, so the empty line and its working agent are what say one
   * is coming — on every surface, after a reload, and for a request somebody else made.
   * Nothing is held here but the moment between the press and the server answering.
   */
  import { mutateJson } from "../lib/mutate";
  import { fateSentence } from "../lib/conversation/composer";
  import type { PromptDeliveryFate } from "../lib/conversation/wire";

  let { kind, id, class: extraClass = "" }: {
    kind: "ticket" | "sprint_item" | "chief";
    id: string;
    class?: string;
  } = $props();

  let sending = $state(false);
  let note = $state<string | null>(null);

  async function ask(): Promise<void> {
    if (sending) return;
    sending = true;
    note = null;
    try {
      const fate = await mutateJson<PromptDeliveryFate>("/api/recap-requests", {
        method: "POST",
        body: { target: { kind, id } }
      });
      // The emptied line and the agent's working mark are the whole of "it is coming",
      // so only a fate they cannot show gets words: a refusal, or a send nobody can
      // vouch for. A started, queued, or steered send says nothing and changes nothing.
      note = fate.fate === "refused" || fate.fate === "uncertain" ? fateSentence(fate) : null;
    } catch (problem) {
      note = problem instanceof Error ? problem.message : "not delivered";
    } finally {
      sending = false;
    }
  }
</script>

<span class={`request-recap${extraClass ? ` ${extraClass}` : ""}`} data-request-recap={`${kind}:${id}`}>
  <!-- It stays put and keeps its words through all of this. A control that vanishes or
       renames itself when pressed moves the page under the hand that pressed it.
       Marked rather than disabled: a disabled control loses focus the instant it is
       pressed, and inside a hover panel losing focus is how the panel decides you left. -->
  <button
    type="button"
    class="request-recap-action"
    aria-disabled={sending}
    data-request-recap-action
    onclick={() => void ask()}
  >Ask for a recap</button>
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
