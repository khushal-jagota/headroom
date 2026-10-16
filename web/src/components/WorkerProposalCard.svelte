<script lang="ts">
  import type { Snippet } from "svelte";
  import CeilingPicker from "./CeilingPicker.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import { advanceTargetFor, fieldLabelFor, preferredScopeCeilingFor, type Lifecycle } from "../lib/lifecycle";
  import { holderLabelFor, OWNER_HOLDER } from "../lib/ceilingHolder";
  import type { PendingTicketProposal, Principal } from "../lib/types";

  let { proposal, lifecycle, stage, holder, sprintItem, onAccept, onReviewer, onReturn, canReturn = true, returnUnavailableReason = "No existing Worker conversation to send a revision to.", contextRow }: {
    proposal: PendingTicketProposal; lifecycle: Lifecycle; stage: string; holder: Principal;
    sprintItem: { id: string; title: string } | null;
    onAccept: (body: Record<string, unknown>) => Promise<unknown>;
    onReviewer: (holder: Principal) => Promise<void>;
    onReturn: (message: string) => Promise<unknown>;
    canReturn?: boolean;
    returnUnavailableReason?: string;
    contextRow?: Snippet;
  } = $props();
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
  async function reviewerChanged(change: { holder: Principal | null }): Promise<void> {
    if (!change.holder || busy || resolved) return;
    busy = true; error = null;
    try { await onReviewer(change.holder); }
    catch (err) { error = err; }
    finally { busy = false; }
  }
  async function approve(change: { ceiling?: string; holder: Principal | null }): Promise<void> {
    if (busy || resolved || !nextCeiling) return;
    busy = true; error = null;
    try {
      await onAccept({ next_ceiling: change.ceiling ?? nextCeiling, next_holder: change.holder ?? OWNER_HOLDER,
        ...(draft !== proposal.body ? { edited_body: draft } : {}) });
      resolved = true;
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
    <span class="reviewer" data-reviewer-control><CeilingPicker newStage={stage} {lifecycle} ceiling={stage} {holder} {sprintItem}
      stageLocked below triggerLabel={reviewerLabel(holder)} holderLabel={(value) => value ? reviewerLabel(value) : "User"}
      onComplete={(change) => void reviewerChanged(change)} /></span> to approve</span></h2>
  <div class="proposal-prose">
    <InlineEdit value={draft} markdown multiline dataEdit onSave={async (raw) => { draft = raw; }} onCancel={() => proposal.body} />
  </div>
  {#if contextRow}<div class="context">{@render contextRow()}</div>{/if}
  <div class="actions">
    <div class="approval-host" data-accept>
      <CeilingPicker {newStage} {lifecycle} ceiling={nextCeiling} holder={OWNER_HOLDER} {sprintItem} below
        triggerLabel="Approve" holderLabel={(value) => value ? reviewerLabel(value) : "User"} onComplete={(change) => void approve(change)} />
    </div>
    {#if proposal.field !== "brief"}
      <button type="button" class="ghost" data-send-back disabled={!canReturn || busy || resolved}
        onclick={() => { if (canReturn) returning = true; }}>Send it back</button>
    {/if}
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
  h2 { margin:0; font-size:20px; font-weight:600; line-height:1.35; color:var(--text-strong); }
  .who { font-weight:400; color:var(--text-faint); }
  .reviewer { position:relative; display:inline-block; padding:1px 5px; margin:0 -5px; border-radius:var(--radius-sm); }
  .reviewer:hover { background-image:var(--interaction-hover); color:var(--text-strong); }
  .reviewer :global([data-scope-ceiling]) { font:inherit; padding:1px 5px; color:inherit; background:transparent; }
  .reviewer :global(.listbox-picker-chevron) { display:none; }
  .proposal-prose { margin-top:var(--space-4); }
  .proposal-prose :global(.ed), .proposal-prose :global(.markdown) { font-family:var(--font-ui); font-size:15px; line-height:1.6; }
  .actions { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-3); margin-top:var(--space-5); }
  button { font:inherit; font-size:13px; font-weight:500; padding:8px 18px; border:0; border-radius:var(--radius-pill); cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  .primary { background:var(--accent-bright); color:var(--surface-ink); }
  .ghost { background:transparent; color:var(--text-faint); padding:8px 12px; }
  .ghost:hover { background-image:var(--interaction-hover); color:var(--text-strong); }
  .approval-host { position:relative; }
  .approval-host :global([data-scope-ceiling]) { padding:8px 18px; border-radius:var(--radius-pill); background:var(--accent-bright); color:var(--surface-ink); font-size:13px; font-weight:500; }
  .return-unavailable { color:var(--text-faintest); font-size:13px; margin:var(--space-2) 0 0; }
  .return-note { margin-top:var(--space-4); }
  label { display:block; font-size:11px; color:var(--text-faintest); }
  textarea { display:block; margin-top:var(--space-2); width:100%; min-height:90px; padding:var(--space-3); background:var(--surface-recessed); color:var(--text-default); border:1px solid var(--border-color); border-radius:var(--radius-md); font:inherit; font-size:13px; }
  .context { margin-top:var(--space-4); }
</style>
