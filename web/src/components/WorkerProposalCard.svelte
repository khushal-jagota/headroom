<script lang="ts">
  import Button from "./Button.svelte";
  import { tick, type Snippet } from "svelte";
  import CeilingPicker from "./CeilingPicker.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import { advanceTargetFor, fieldLabelFor, preferredScopeCeilingFor, type Lifecycle } from "../lib/lifecycle";
  import type { PendingTicketProposal, Principal } from "../lib/types";

  let { proposal, lifecycle, stage, ceiling, holder, manager, onAccept, onScope, onReturn, canReturn = true, returnUnavailableReason = "No existing Worker conversation to send a revision to.", disabled = false, revisionEditor, revisionText = "", holdDecisions = false, initialReturnOpen = false, initialEditedBody, onEditedBodyChange, contextRow, utilityActions }: {
    proposal: PendingTicketProposal; lifecycle: Lifecycle; stage: string; ceiling: string; holder: Principal;
    manager: { id: string; title: string } | null;
    onAccept: (body: Record<string, unknown>) => Promise<unknown>;
    onScope: (body: Record<string,unknown>) => Promise<void>;
    onReturn: (message: string) => Promise<unknown>;
    canReturn?: boolean;
    disabled?: boolean;
    /** The Review screen's own line for the Worker, always on the card above its actions.
     *  What is written there goes with Send it back, and with Approve (ReviewProposalCard). */
    revisionEditor?: Snippet;
    /** What is written in that line now. */
    revisionText?: string;
    /** The line is being spoken into: Send it back and Approve wait for its words to land. */
    holdDecisions?: boolean;
    initialReturnOpen?: boolean;
    initialEditedBody?: string;
    onEditedBodyChange?: (text: string) => void;
    returnUnavailableReason?: string;
    contextRow?: Snippet;
    /** Actions about the queue rather than the proposal (Skip, Open ticket): they come before
     *  the decision group in the row's right-hand run. */
    utilityActions?: Snippet;
  } = $props();
  let scopePicker = $state<{openApproval: (anchor?: HTMLElement) => void; openReviewer: (anchor?: HTMLElement) => void; readonly expanded: boolean; readonly controls: string | undefined} | undefined>();
  let draft = $state("");
  let note = $state("");
  let returning = $state(false);
  let busy = $state(false);
  let resolved = $state(false);
  let error = $state<unknown>(null);
  let lastKey: string | null = null;
  let newStage = $derived(advanceTargetFor(lifecycle, stage, ""));
  let nextCeiling = $derived(preferredScopeCeilingFor(lifecycle, newStage));
  let finalApproval = $derived(newStage !== null && lifecycle.stageIsTerminal[newStage] === true);
  let identity = $derived([proposal.field, proposal.created_at, proposal.revision, lifecycle.workerType, newStage, finalApproval, lifecycle.stageOrder.join(","), lifecycle.ceilingRange.join(","), manager?.id || ""].join("|"));
  $effect(() => {
    if (identity === lastKey) return;
    const first = lastKey === null;
    lastKey = identity;
    draft = first ? initialEditedBody ?? proposal.body : proposal.body;
    note = "";
    returning = first && initialReturnOpen;
    resolved = false;
    error = null;
  });
  function reviewerLabel(value: Principal): string {
    return value.kind === "owner" ? "User" : value.kind === "chief" ? "Chief" : value.kind === "ticket" ? "Worker" : "Manager";
  }
  async function completeScope(change: { ceiling?: string; holder: Principal | null; approval?: boolean }): Promise<void> {
    if (!change.holder || disabled || busy || resolved) return;
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
  async function sendBack(message: string): Promise<void> {
    if (proposal.field === "brief" || disabled || !canReturn || busy || resolved || !message) return;
    busy = true; error = null;
    try { await onReturn(message); resolved = true; }
    catch (err) { error = err; }
    finally { busy = false; }
  }
  let section = $state<HTMLElement | null>(null);
  let revisionSlot = $state<HTMLElement | null>(null);
  /** Send it back. With the Review screen's line on the card, it sends what is written there,
   *  or, with nothing written yet, takes the person to the line. Without it (the Ticket page)
   *  the return form takes the action row's place until it is sent or cancelled, so only one
   *  send-back control is ever on screen. */
  async function sendItBack(): Promise<void> {
    if (!canReturn) return;
    if (revisionEditor) {
      const message = revisionText.trim();
      if (message) void sendBack(message);
      else revisionSlot?.querySelector<HTMLElement>("textarea")?.focus();
      return;
    }
    returning = true;
    await tick();
    section?.querySelector<HTMLTextAreaElement>("[data-revision-note]")?.focus();
  }
  async function cancelReturn(): Promise<void> {
    returning = false;
    await tick();
    section?.querySelector<HTMLButtonElement>("[data-send-back]")?.focus();
  }
</script>

<section bind:this={section} inert={disabled || busy || resolved} class="worker-proposal" data-approval-block data-mode="pending" data-field={proposal.field}>
  <!-- The stage and who decides are small print over the card. The proposal's own first line
       is the card's title: the thing being decided, not the stage's generic name. -->
  <div class="proposal-line">
    <h2>{fieldLabelFor(lifecycle, proposal.field)}</h2>
    <!-- Who approves is a property of the proposal and a select, so it reads as one: 'Approver'
         and the chosen reviewer with a chevron, at the header's right (owner: the word inside a
         sentence read badly). -->
    <div class="approver">
      <span class="approver-label">Approver</span>
      <button type="button" class="reviewer" data-reviewer-heading aria-haspopup="listbox" aria-expanded={scopePicker?.expanded ?? false} aria-controls={scopePicker?.controls} onkeydown={(event) => { if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); scopePicker?.openReviewer(event.currentTarget); } }} onclick={(event) => scopePicker?.openReviewer(event.currentTarget)}>{reviewerLabel(holder)}<svg class="reviewer-chevron" viewBox="0 0 16 16" aria-hidden="true"><path d="m4.5 6.5 3.5 3.5 3.5-3.5"/></svg></button>
    </div>
  </div>
  <div class="proposal-prose">
    <InlineEdit value={draft} markdown multiline dataEdit onSave={async (raw) => { draft = raw; onEditedBodyChange?.(raw); }} onCancel={() => proposal.body} />
  </div>
  {#if contextRow}<div class="context">{@render contextRow()}</div>{/if}
  <!-- The Review screen's line for the Worker sits over the actions, always there: one line
       that both decisions can carry. -->
  {#if revisionEditor && canReturn}<div class="revision" bind:this={revisionSlot}>{@render revisionEditor()}</div>{/if}
  {#if !revisionEditor && proposal.field !== "brief" && canReturn && returning}
    <!-- The return form stands where the action row was; Cancel or Escape puts the row back. -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="return-note" onkeydown={(event) => { if (event.key === "Escape") { event.preventDefault(); void cancelReturn(); } }}>
      <label>What needs changing<textarea data-revision-note bind:value={note} placeholder="What to do differently" disabled={busy || resolved}></textarea></label>
      <div class="actions return-actions"><Button variant="ghost" onclick={() => void cancelReturn()}>Cancel</Button><Button variant="primary" data-revision-send disabled={busy || resolved || !note.trim()} onclick={() => void sendBack(note.trim())}>Send it back</Button></div>
    </div>
  {:else}
  <!-- Every button row sits at the right, the most important action furthest right, as in a
       macOS dialog: the actions about the queue (Skip, Open ticket), 16px, then the decision
       group, Send it back and Approve last. -->
  <div class="actions">
    {#if utilityActions}<div class="utilities">{@render utilityActions()}</div>{/if}
    <div class="decision">
      {#if proposal.field !== "brief"}
        <Button variant="secondary" data-send-back disabled={disabled || !canReturn || busy || resolved || holdDecisions}
          onclick={() => void sendItBack()}>Send it back</Button>
      {/if}
      <Button variant="primary" data-accept aria-haspopup={finalApproval ? undefined : "listbox"} aria-expanded={finalApproval ? undefined : scopePicker?.expanded ?? false} aria-controls={finalApproval ? undefined : scopePicker?.controls}
        onkeydown={(event: KeyboardEvent) => { if (!finalApproval && (event.key === "ArrowDown" || event.key === "ArrowUp")) { event.preventDefault(); scopePicker?.openApproval(event.currentTarget as HTMLElement); } }} disabled={disabled || busy || resolved || holdDecisions || !nextCeiling}
        onclick={(event) => finalApproval ? void completeScope({ approval:true, ceiling:"none", holder }) : scopePicker?.openApproval(event.currentTarget as HTMLElement)}>Approve</Button>
    </div>
  </div>
  {/if}
  <CeilingPicker externalTrigger newStage={stage} {lifecycle} {ceiling} {holder} {manager} below pendingStage={stage}
    approvalCeiling={nextCeiling} bind:controller={scopePicker}
    holderLabel={(value) => value ? reviewerLabel(value) : "User"} onComplete={(change) => void completeScope(change)} />
  {#if proposal.field !== "brief" && !canReturn}<p class="return-unavailable" data-return-unavailable>{returnUnavailableReason}</p>{/if}
  {#if error}<ErrorLine {error} />{/if}
</section>
<style>
  /* THE CARD (page system). Its actions are the one Button, in one row at the card's right:
     the ghost actions about the queue, 16px, then the decision group (secondary, then primary),
     the most important furthest right; 8px between buttons. */
  .worker-proposal { --decision-button-height:var(--button-main-height); padding:var(--page-card-pad); background:var(--page-card-fill); border:var(--border-hairline) solid var(--page-card-edge); border-radius:var(--page-card-radius); }
  .proposal-line { display:flex; align-items:center; flex-wrap:wrap; min-height:20px; color:var(--tone-secondary); font-size:var(--type-small); font-weight:var(--weight-small); line-height:18px; }
  h2 { margin:0; font:inherit; letter-spacing:0; color:inherit; }
  /* The header: the stage at the left, the approver at the right. */
  .proposal-line { justify-content:space-between; gap:var(--space-2) var(--space-4); }
  .approver { display:inline-flex; align-items:center; gap:var(--space-1); margin-right:calc(var(--space-2) * -1); }
  .approver-label { color:var(--tone-tertiary); }
  /* The proposal's first paragraph leads at the section-head size, at 500 so the page title stays the one peak; the rest
     is body text, all in the primary tone and never wider than the prose measure. */
  .proposal-prose { max-width:var(--page-prose-measure); margin-top:var(--space-2); color:var(--tone-primary); }
  .proposal-prose :global(.ed), .proposal-prose :global(.markdown) { font-family:var(--font-ui); font-size:var(--type-body); font-weight:var(--weight-body); line-height:var(--leading-body); color:var(--tone-primary); }
  .proposal-prose :global(.ed > p:first-child), .proposal-prose :global(.ed > .markdown > p:first-child), .proposal-prose :global(.ed > .markdown:first-child > :first-child:is(p)) { margin-bottom:var(--space-3); font-size:var(--type-page-section); font-weight:var(--weight-page-lead); line-height:22px; color:var(--tone-strong); }
  .actions { display:flex; align-items:center; justify-content:flex-end; flex-wrap:wrap; gap:var(--space-2); margin-top:var(--space-5); }
  /* The approver is a select: the reviewer's name in the primary tone with a chevron, no fill
     until pointed at, on a 28px target. */
  .reviewer { display:inline-flex; align-items:center; gap:var(--space-1); height:28px; font:inherit; font-weight:var(--weight-needs-you); line-height:1; padding:0 var(--space-2); border:0; background:transparent; color:var(--tone-primary); border-radius:var(--radius-md); cursor:pointer; }
  .reviewer:hover, .reviewer[aria-expanded="true"] { background:var(--wash-hover); color:var(--tone-strong); }
  .reviewer-chevron { width:12px; height:12px; flex:none; fill:none; stroke:var(--tone-tertiary); stroke-width:1.5; stroke-linecap:round; stroke-linejoin:round; }
  .reviewer:focus-visible { outline:var(--focus-width) solid var(--focus-ring); outline-offset:2px; }
  .return-unavailable { color:var(--tone-tertiary); font-size:var(--type-small); margin:var(--space-2) 0 0; }
  .return-note { margin-top:var(--space-5); }
  label { display:block; font-size:var(--type-small); color:var(--tone-tertiary); }
  /* The return form's field: the recessed fill reads against the card on its own, so no edge. */
  textarea { display:block; margin-top:var(--space-2); width:100%; min-height:90px; padding:var(--space-3); background:var(--surface-recessed); color:var(--tone-primary); border:0; border-radius:var(--radius-md); font:inherit; font-size:var(--type-body); }
  textarea:focus { outline:var(--focus-width) solid var(--focus-ring); outline-offset:0; }
  .return-actions { margin-top:var(--space-3); }
  /* The line for the Worker comes after the proposal; the actions follow it closely. */
  .revision { margin-top:var(--space-5); }
  .revision + .actions { margin-top:var(--space-3); }
  /* The decision's own buttons are never smaller than staging's (37px tall, 18px either side). */
  .decision :global(.button), .return-actions :global(.button) { height:var(--decision-button-height); padding-inline:18px; }
  .context { margin-top:var(--space-4); }
  .utilities { display:flex; align-items:center; gap:var(--space-2); margin-right:calc(var(--space-4) - var(--space-2)); }
  .decision { display:flex; align-items:center; gap:var(--space-2); }
  /* On the Review screen (where the card carries the line for the Worker) the card ends in one
     row: the note, then Send it back and Approve; Skip and Open ticket stand in the page's top
     band, as staging (owner's choice). On a phone the decisions go under the note. */
  .worker-proposal:has(> .revision) { display:grid; grid-template-columns:minmax(0,1fr) auto; column-gap:var(--space-2); align-items:end; }
  .worker-proposal:has(> .revision) > * { grid-column:1 / -1; }
  .worker-proposal:has(> .revision) > .revision { grid-column:1; }
  .worker-proposal:has(> .revision) > .actions { grid-column:2; margin-top:0; }
  @media (max-width:720px) {
    .worker-proposal:has(> .revision) > .actions { grid-column:1 / -1; margin-top:var(--space-3); }
  }
  /* The picker's own trigger is hidden here, so its empty line must not add height. */
  .worker-proposal > :global(div:has(> [data-ceiling-picker])) { display:flex; }
</style>