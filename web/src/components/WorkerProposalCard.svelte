<script lang="ts">
  import type { Snippet } from "svelte";
  import CeilingPicker from "./CeilingPicker.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import { advanceTargetFor, fieldLabelFor, stageLabelFor, preferredScopeCeilingFor, type Lifecycle } from "../lib/lifecycle";
  import { holderLabelFor } from "../lib/ceilingHolder";
  import type { PendingTicketProposal, Principal } from "../lib/types";

  let { proposal, lifecycle, stage, ceiling, holder, sprintItem, onAccept, onScope, onReturn, canReturn = true, returnUnavailableReason = "No existing Worker conversation to send a revision to.", contextRow }: {
    proposal: PendingTicketProposal; lifecycle: Lifecycle; stage: string; ceiling: string; holder: Principal;
    sprintItem: { id: string; title: string } | null;
    onAccept: (body: Record<string, unknown>) => Promise<unknown>;
    onScope: (body: Record<string,unknown>) => Promise<void>;
    onReturn: (message: string) => Promise<unknown>;
    canReturn?: boolean;
    returnUnavailableReason?: string;
    contextRow?: Snippet;
  } = $props();
  let scopePicker = $state<{openApproval: (anchor?: HTMLElement) => void; openReviewer: (anchor?: HTMLElement) => void} | undefined>();
  let draft = $state("");
  let note = $state("");
  let returning = $state(false);
  let busy = $state(false);
  let resolved = $state(false);
  let error = $state<unknown>(null);
  let lastKey: string | null = null;
  let newStage = $derived(advanceTargetFor(lifecycle, stage, ""));
  let nextCeiling = $derived(preferredScopeCeilingFor(lifecycle, newStage));
  let identity = $derived([proposal.field, proposal.created_at, proposal.revision, lifecycle.workerType, newStage, lifecycle.stageOrder.join(","), lifecycle.ceilingRange.join(","), sprintItem?.id || ""].join("|"));
  $effect(() => {
    if (identity === lastKey) return;
    lastKey = identity;
    draft = proposal.body;
    note = "";
    returning = false;
    resolved = false;
    error = null;
  });
  function reviewerLabel(value: Principal): string {
    return holderLabelFor(sprintItem, value);
  }
  async function completeScope(change: { ceiling?: string; holder: Principal | null; approval?: boolean }): Promise<void> {
    if (!change.holder || busy || resolved) return;
    // Capture the exact proposal displayed for this gesture before awaiting any write.
    const revision = proposal.revision;
    const editedBody = draft;
    busy = true; error = null;
    try {
      if (change.approval && change.ceiling) {
        await onAccept({ next_ceiling:change.ceiling, next_holder:change.holder, expected_proposal_revision:revision,
          ...(editedBody !== proposal.body ? { edited_body:editedBody } : {}) });
        resolved = true;
      } else {
        await onScope({ ...(change.ceiling === undefined ? {} : { ceiling:change.ceiling }), ceiling_holder:change.holder });
      }
    } catch (err) { error = err; }
    finally { busy = false; }
  }
  async function sendBack(): Promise<void> {
    if (proposal.field === "brief" || !canReturn || busy || resolved || !note.trim()) return;
    busy = true; error = null;
    try { await onReturn(note.trim()); resolved = true; }
    catch (err) { error = err; }
    finally { busy = false; }
  }
</script>

<section inert={busy || resolved} class="worker-proposal" data-approval-block data-mode="pending" data-field={proposal.field}>
  <h2>{fieldLabelFor(lifecycle, proposal.field)} <span class="who">— for
    <button type="button" class="reviewer" data-reviewer-heading onclick={(event) => scopePicker?.openReviewer(event.currentTarget)}>{reviewerLabel(holder)}</button> to approve</span></h2>
  <div class="proposal-prose">
    <InlineEdit value={draft} markdown multiline dataEdit onSave={async (raw) => { draft = raw; }} onCancel={() => proposal.body} />
  </div>
  {#if contextRow}<div class="context">{@render contextRow()}</div>{/if}
  <div class="actions">
    <button type="button" class="primary" data-accept disabled={busy || resolved || !nextCeiling}
      onclick={(event) => scopePicker?.openApproval(event.currentTarget)}>Approve</button>
    {#if proposal.field !== "brief"}
      <button type="button" class="ghost" data-send-back disabled={!canReturn || busy || resolved}
        onclick={() => { if (canReturn) returning = true; }}>Send it back</button>
    {/if}
  </div>
  <div class="worker-ceiling-line" data-reviewer-control data-pending-ceiling>
    <CeilingPicker document newStage={stage} {lifecycle} {ceiling} {holder} {sprintItem} below pendingStage={stage}
      pendingLabel={fieldLabelFor(lifecycle,proposal.field)} approvalCeiling={nextCeiling} bind:controller={scopePicker}
      triggerLabel={`Approved until ${stageLabelFor(lifecycle,ceiling)}, then ${reviewerLabel(holder)} reviews`}
      holderLabel={(value) => value ? reviewerLabel(value) : "User"} onComplete={(change) => void completeScope(change)} />
  </div>
  {#if proposal.field !== "brief" && !canReturn}<p class="return-unavailable" data-return-unavailable>{returnUnavailableReason}</p>{/if}
  {#if proposal.field !== "brief" && canReturn && returning}
    <div class="return-note">
      <label>What needs changing<textarea data-revision-note bind:value={note} placeholder="What to do differently" disabled={busy || resolved}></textarea></label>
      <div class="actions"><button type="button" class="primary" data-revision-send disabled={busy || resolved || !note.trim()} onclick={() => void sendBack()}>Send it back</button><button type="button" class="ghost" onclick={() => returning = false}>Cancel</button></div>
    </div>
  {/if}
  {#if error}<ErrorLine {error} />{/if}
</section>
<style>
  h2 { margin:var(--space-2) 0 0; letter-spacing:-.006em; font-size:20px; font-weight:600; line-height:1.35; color:var(--text-strong); }
  .who { font-weight:400; color:var(--text-faint); }
  .proposal-prose { line-height:1.6; margin-top:var(--space-4); }
  .proposal-prose :global(.ed), .proposal-prose :global(.markdown) { font-family:var(--font-ui); font-size:15px; line-height:1.6; }
  .actions { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-3); margin-top:var(--space-5); }
  button { font:inherit; font-size:13px; font-weight:500; line-height:1.6; padding:8px 18px; border:0; border-radius:var(--radius-pill); cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  .reviewer { font:inherit; line-height:inherit; padding:0 5px; margin:0 -5px; background:transparent; color:inherit; border-radius:var(--radius-sm); }
  .reviewer:hover { background-image:var(--interaction-hover); color:var(--text-strong); }
  .primary { background:var(--accent-bright); color:var(--surface-ink); }
  .ghost { background:transparent; color:var(--text-faint); padding:8px 12px; }
  .ghost:hover { background-image:var(--interaction-hover); color:var(--text-strong); }
  .worker-ceiling-line { margin-top:var(--space-3); }
  .worker-ceiling-line :global([data-scope-ceiling]) { font-family:var(--font-ui); font-size:15px; line-height:1.3; color:var(--text-muted); padding:10px var(--space-3); margin-inline:calc(-1 * var(--space-3)); border:0; border-radius:var(--radius-sm); background:transparent; box-shadow:none; text-align:left; }
  .worker-ceiling-line :global([data-scope-ceiling]:hover) { background-image:var(--interaction-hover); }
  .return-unavailable { color:var(--text-faintest); font-size:13px; margin:var(--space-2) 0 0; }
  .return-note { margin-top:var(--space-4); }
  label { display:block; font-size:11px; color:var(--text-faintest); }
  textarea { display:block; margin-top:var(--space-2); width:100%; min-height:90px; padding:var(--space-3); background:var(--surface-recessed); color:var(--text-default); border:1px solid var(--border-color); border-radius:var(--radius-md); font:inherit; font-size:13px; }
  .context { margin-top:var(--space-4); }
</style>
