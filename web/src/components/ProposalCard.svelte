<script lang="ts">
  import ErrorLine from "./ErrorLine.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";
  import ScopePairPicker from "./ScopePairPicker.svelte";

  type ScopePair = { next_ceiling: string; at_cap: string };

  let {
    proposal,
    requireScope = false,
    newState = null,
    onAccept
  }: {
    proposal: { body: string; proposed_by: string };
    requireScope?: boolean;
    newState?: string | null;
    onAccept: (payload: Record<string, unknown>) => Promise<unknown>;
  } = $props();

  let draft = $state(proposal.body || "");
  let scope = $state<ScopePair | null>(null);
  let inFlight = $state(false);
  let resolved = $state(false);
  let error = $state<unknown>(null);

  async function accept(): Promise<void> {
    const payload: Record<string, unknown> = {};
    if (draft !== proposal.body) payload.edited_body = draft;
    if (requireScope) {
      if (!scope) return;
      payload.next_ceiling = scope.next_ceiling;
      payload.at_cap = scope.at_cap;
    }
    inFlight = true;
    error = null;
    try {
      await onAccept(payload);
      resolved = true;
    } catch (err) {
      error = err;
    } finally {
      inFlight = false;
    }
  }
</script>

<div class="proposal-card">
  <div class="proposal-meta">proposed by {proposal.proposed_by}</div>
  <MarkdownBlock text={proposal.body} />
  <textarea class="field-editor-input proposal-edit" data-edit rows="8" bind:value={draft}></textarea>
  {#if requireScope}
    <ScopePairPicker {newState} bind:scope />
  {/if}
  <div class="proposal-card-actions">
    {#if error}<ErrorLine {error} />{/if}
    <button
      type="button"
      class="button button--primary"
      data-accept
      disabled={inFlight || resolved || (requireScope && scope === null)}
      onclick={() => void accept()}
    >
      Accept
    </button>
  </div>
</div>
