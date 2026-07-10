<script lang="ts">
  import Button from "./Button.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import InlineEdit from "./InlineEdit.svelte";
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

  let draft = $state("");
  let lastProposalBody = $state<string | null>(null);
  let scope = $state<ScopePair | null>(null);
  let inFlight = $state(false);
  let resolved = $state(false);
  let error = $state<unknown>(null);

  async function saveDraft(raw: string): Promise<void> {
    draft = raw;
  }

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

  $effect(() => {
    const incoming = proposal.body || "";
    if (incoming !== lastProposalBody) {
      lastProposalBody = incoming;
      draft = incoming;
      scope = null;
      resolved = false;
    }
  });
</script>

<div class="proposal-card">
  <div class="proposal-meta">proposed by {proposal.proposed_by}</div>
  <InlineEdit
    value={draft}
    markdown
    multiline
    placeholder="Proposal..."
    className="proposal-edit"
    onSave={saveDraft}
  />
  {#if requireScope}
    <ScopePairPicker {newState} bind:scope />
  {/if}
  <div class="proposal-card-actions">
    {#if error}<ErrorLine {error} />{/if}
    <Button
      variant="primary"
      data-accept=""
      disabled={inFlight || resolved || (requireScope && scope === null)}
      onclick={() => void accept()}
    >
      Accept
    </Button>
  </div>
</div>
