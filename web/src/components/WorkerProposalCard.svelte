<script lang="ts">
  import Button from "./Button.svelte";
  import { onDestroy, type Snippet } from "svelte";
  import CeilingPicker from "./CeilingPicker.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import ComposerField from "./conversation/ComposerField.svelte";
  import { advanceTargetFor, finalApprovalFor, fieldLabelFor, preferredScopeCeilingFor, type Lifecycle } from "../lib/lifecycle";
  import { decisionIdentity, decisionRefusals, DecisionRefusedError, recallReviewDecision, submitReviewDecision } from "../lib/review/decision";
  import type { PendingTicketProposal, Principal } from "../lib/types";

  let { ticketId, proposal, lifecycle, stage, ceiling, holder, manager, onAccept, onScope, onReturn, canReturn = true, returnUnavailableReason = "No existing Worker conversation to send a revision to.", disabled = false, revisionEditor, revisionText = $bindable(""), holdDecisions = false, initialEditedBody, onEditedBodyChange, contextRow, utilityActions, composerOnly = false }: {
    ticketId: string;
    proposal: PendingTicketProposal; lifecycle: Lifecycle; stage: string; ceiling: string; holder: Principal;
    manager: { id: string; title: string } | null;
    onAccept: () => Promise<unknown>;
    onScope: (body: Record<string,unknown>) => Promise<void>;
    onReturn: () => Promise<unknown>;
    canReturn?: boolean;
    disabled?: boolean;
    /** The Review screen's voice-enabled note field beside the decision controls.
     *  What is written there goes with Send it back, and with Approve. */
    revisionEditor?: Snippet<[() => void]>;
    /** What is written in that line now. */
    revisionText?: string;
    /** The line is being spoken into: Send it back and Approve wait for its words to land. */
    holdDecisions?: boolean;
    initialEditedBody?: string;
    onEditedBodyChange?: (text: string) => void;
    returnUnavailableReason?: string;
    contextRow?: Snippet;
    /** Only the note and the decisions: no stage line, approver line or proposal text. Used where the
     *  proposal's own text is already on another screen and this stands in for a conversation composer. */
    composerOnly?: boolean;
    /** Actions about the queue rather than the proposal (Skip, Open ticket): they come before
     *  the decision group in the row's right-hand run. */
    utilityActions?: Snippet;
  } = $props();
  let scopePicker = $state<{openApproval: (anchor?: HTMLElement) => void; openReviewer: (anchor?: HTMLElement) => void; readonly expanded: boolean; readonly controls: string | undefined} | undefined>();
  let draft = $state("");
  let mounted = true;
  onDestroy(() => { mounted = false; });
  let decisionRow = $state<HTMLElement | null>(null);
  // Enter in the note cannot know which decision is meant, so it shows the choices: the focus
  // ring breathes once around each decision button that can be used, then leaves.
  function flashDecisions(): void {
    if (!decisionRow) return;
    decisionRow.classList.remove("flash");
    void decisionRow.offsetWidth;
    decisionRow.classList.add("flash");
  }
  let busy = $state(false);
  let resolved = $state(false);
  let error = $state<unknown>(null);
  // A refusal names the Ticket and field, so it survives replacement of the reviewed revision.
  let terminalRefusal = $derived($decisionRefusals.filter(entry => entry.saved.ticketId === ticketId && entry.saved.command.field === proposal.field).slice(-1)[0]?.error);
  let visibleError = $derived(error ?? terminalRefusal);
  let lastKey: string | null = null;
  let newStage = $derived(advanceTargetFor(lifecycle, stage, ""));
  let nextCeiling = $derived(preferredScopeCeilingFor(lifecycle, newStage));
  let finalApproval = $derived(finalApprovalFor(lifecycle, stage));
  let identity = $derived([ticketId, proposal.field, proposal.created_at, proposal.revision, lifecycle.workerType, newStage, finalApproval, lifecycle.stageOrder.join(","), lifecycle.ceilingRange.join(","), manager?.id || ""].join("|"));
  let decisionKey = $derived(decisionIdentity(ticketId, proposal.revision));
  $effect(() => {
    if (identity === lastKey) return;
    const first = lastKey === null;
    lastKey = identity;
    draft = first ? initialEditedBody ?? proposal.body : proposal.body;
    if (!first) revisionText = "";
    resolved = false;
    error = null;
    try {
      const pending = recallReviewDecision(decisionKey);
      if (pending) {
        revisionText = pending.command.message ?? "";
        draft = pending.command.edited_body ?? proposal.body;
      }
    } catch (recoveryError) { error = recoveryError; }
  });
  function reviewerLabel(value: Principal): string {
    return value.kind === "owner" ? "User" : value.kind === "chief" ? "Chief" : value.kind === "ticket" ? "Worker" : "Manager";
  }
  async function completeScope(change: { ceiling?: string; holder: Principal | null; approval?: boolean }): Promise<void> {
    if (!change.holder || disabled || busy || resolved || (change.approval && holdDecisions)) return;
    // Capture the exact proposal displayed for this gesture before awaiting any write.
    const revision = proposal.revision;
    const editedBody = draft;
    const key = decisionKey;
    const text = revisionText;
    busy = true; error = null;
    try {
      if (change.approval && (change.ceiling || finalApproval)) {
        await submitReviewDecision(ticketId, proposal.field, { action: "approve", body: {
          message: text || null,
          ...(!finalApproval ? { next_ceiling: change.ceiling, next_holder: change.holder } : {}),
          expected_proposal_revision: revision,
          ...(editedBody !== proposal.body ? { edited_body: editedBody } : {})
        } });
        if (!mounted || decisionKey !== key) return;
        await onAccept();
        revisionText = "";
        resolved = true;
      } else {
        await onScope({ ...(change.ceiling === undefined ? {} : { ceiling:change.ceiling }), ceiling_holder:change.holder });
      }
    } catch (err) { error = err instanceof DecisionRefusedError ? null : err; }
    finally { busy = false; }
  }
  async function sendBack(message: string): Promise<void> {
    if (proposal.field === "brief" || disabled || !canReturn || busy || resolved || holdDecisions || !message) return;
    busy = true; error = null;
    const key = decisionKey;
    const revision = proposal.revision;
    try {
      await submitReviewDecision(ticketId, proposal.field, { action: "send_back", body: {
        message, expected_proposal_revision: revision
      } });
      if (!mounted || decisionKey !== key) return;
      await onReturn();
      revisionText = "";
      resolved = true;
    }
    catch (err) { error = err instanceof DecisionRefusedError ? null : err; }
    finally { busy = false; }
  }
  let revisionSlot = $state<HTMLElement | null>(null);
  function sendItBack(): void {
    if (!canReturn || holdDecisions) return;
    const message = revisionText;
    if (message.trim()) void sendBack(message);
    else revisionSlot?.querySelector<HTMLElement>("textarea")?.focus();
  }

</script>

<section inert={disabled || busy || resolved} class="worker-proposal" class:composer-only={composerOnly} data-approval-block data-artifact-origin="proposal" data-mode="pending" data-field={proposal.field}>
  {#if !composerOnly}
  <!-- The stage and who decides are small print over the card. The proposal's own first line
       is the card's title: the thing being decided, not the stage's generic name. -->
  <div class="proposal-line">
    <h2>{fieldLabelFor(lifecycle, proposal.field)}</h2>
    <!-- What this is, then who approves it, as one pair (owner): 'Design · Approver User ⌄', the
         reviewer a select with a chevron (the word inside 'For User to approve' read badly). -->
    <span class="proposal-line-sep" aria-hidden="true">·</span>
    <div class="approver">
      <span class="approver-label">Approver</span>
      <button type="button" class="reviewer" data-reviewer-heading aria-haspopup="listbox" aria-expanded={scopePicker?.expanded ?? false} aria-controls={scopePicker?.controls} onkeydown={(event) => { if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); scopePicker?.openReviewer(event.currentTarget); } }} onclick={(event) => scopePicker?.openReviewer(event.currentTarget)}>{reviewerLabel(holder)}<svg class="reviewer-chevron" viewBox="0 0 16 16" aria-hidden="true"><path d="m4.5 6.5 3.5 3.5 3.5-3.5"/></svg></button>
    </div>
  </div>
  <div class="proposal-prose">
    <InlineEdit value={draft} markdown multiline dataEdit onSave={async (raw) => { draft = raw; onEditedBodyChange?.(raw); }} onCancel={() => proposal.body} />
  </div>
  {/if}
  {#if contextRow}<div class="context">{@render contextRow()}</div>{/if}
  <div class="revision" bind:this={revisionSlot}>
    {#if revisionEditor}{@render revisionEditor(flashDecisions)}{:else}
      <ComposerField bind:value={revisionText} placeholder="Add a note for the worker…"
        inputAttributes={{ "data-revision-note": "" }} onsubmit={flashDecisions} />
    {/if}
  </div>
  <!-- Every button row sits at the right, the most important action furthest right, as in a
       macOS dialog: the actions about the queue (Skip, Open ticket), 16px, then the decision
       group, Send it back and Approve last. -->
  <div class="actions">
    {#if utilityActions}<div class="utilities">{@render utilityActions()}</div>{/if}
    <div class="decision" bind:this={decisionRow} onanimationend={() => decisionRow?.classList.remove("flash")}>
      {#if proposal.field !== "brief"}
        <Button variant="secondary" data-send-back disabled={disabled || !canReturn || busy || resolved || holdDecisions}
          onclick={() => void sendItBack()}>Send it back</Button>
      {/if}
      <!-- Approve wears Send it back's fill (owner): its place, furthest right, marks it. -->
      <Button variant="secondary" data-accept aria-haspopup={finalApproval ? undefined : "listbox"} aria-expanded={finalApproval ? undefined : scopePicker?.expanded ?? false} aria-controls={finalApproval ? undefined : scopePicker?.controls}
        onkeydown={(event: KeyboardEvent) => { if (!finalApproval && (event.key === "ArrowDown" || event.key === "ArrowUp")) { event.preventDefault(); scopePicker?.openApproval(event.currentTarget as HTMLElement); } }} disabled={disabled || busy || resolved || holdDecisions || (!finalApproval && !nextCeiling)}
        onclick={(event) => finalApproval ? void completeScope({ approval:true, holder }) : scopePicker?.openApproval(event.currentTarget as HTMLElement)}>Approve</Button>
    </div>
  </div>
  <!-- Under an artifact the decisions are the page's foot, so the panel fits inside the artifact, above them. -->
  <CeilingPicker externalTrigger newStage={stage} {lifecycle} {ceiling} {holder} {manager} below={!composerOnly} fitWithin={composerOnly ? ".ticket-artifact" : undefined} pendingStage={stage}
    approvalCeiling={nextCeiling} bind:controller={scopePicker}
    holderLabel={(value) => value ? reviewerLabel(value) : "User"} onComplete={(change) => void completeScope(change)} />
  {#if proposal.field !== "brief" && !canReturn}<p class="return-unavailable" data-return-unavailable>{returnUnavailableReason}</p>{/if}
  {#if visibleError}<ErrorLine error={visibleError} />{/if}
</section>
<style>
  /* THE CARD (page system). Its actions are the one Button, in one row at the card's right:
     the ghost actions about the queue, 16px, then the decision group (secondary, then primary),
     the most important furthest right; 8px between buttons. */
  .worker-proposal { --decision-button-height:var(--button-main-height); padding:var(--page-card-pad); background:var(--page-card-fill); border:var(--border-hairline) solid var(--page-card-edge); border-radius:var(--page-card-radius); }
  /* Standing in for the conversation composer: the note and decisions sit bare, like it does. */
  .worker-proposal.composer-only { padding:0; background:none; border:0; border-radius:0; }
  .worker-proposal.composer-only > .revision { margin-top:0; }
  .proposal-line { display:flex; align-items:center; flex-wrap:wrap; min-height:20px; color:var(--tone-secondary); font-size:var(--type-small); font-weight:var(--weight-small); line-height:18px; }
  h2 { margin:0; font:inherit; letter-spacing:0; color:inherit; }
  /* The header: the stage, a dot, then its approver, one pair at the left. */
  .proposal-line-sep { padding-inline:var(--space-2); color:var(--tone-tertiary); }
  .approver { display:inline-flex; align-items:baseline; gap:2px; }
  .approver-label { color:var(--tone-tertiary); }
  .approver .reviewer { padding:0 var(--space-1) 0 6px; }
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
  .revision { margin-top:var(--space-5); min-width:0; }
  /* The decision's own buttons are never smaller than staging's (37px tall, 18px either side). */
  .decision :global(.button) { height:var(--decision-button-height); padding-inline:18px; }
  .context { margin-top:var(--space-4); }
  .utilities { display:flex; align-items:center; gap:var(--space-2); margin-right:calc(var(--space-4) - var(--space-2)); }
  .decision { display:flex; align-items:center; gap:var(--space-2); }
  .decision:global(.flash) :global(.button:not(:disabled)) { animation:decision-flash .9s ease-in-out; outline-offset:2px; }
  @keyframes decision-flash {
    0%, 100% { outline:var(--focus-width) solid color-mix(in srgb, var(--focus-ring) 0%, transparent); }
    50% { outline:var(--focus-width) solid color-mix(in srgb, var(--focus-ring) 60%, transparent); }
  }
  @media (prefers-reduced-motion:reduce) {
    .decision:global(.flash) :global(.button:not(:disabled)) { animation:none; outline:var(--focus-width) solid color-mix(in srgb, var(--focus-ring) 60%, transparent); }
  }
  /* On both proposal screens the card ends in one
     row: the note, then Send it back and Approve; Skip and Open ticket stand in the page's top
     band, as staging (owner's choice). On a phone the decisions go under the note. */
  .worker-proposal:has(> .revision) { display:grid; grid-template-columns:minmax(0,1fr) auto; column-gap:var(--space-2); align-items:end; }
  .worker-proposal:has(> .revision) > * { grid-column:1 / -1; }
  .worker-proposal:has(> .revision) > .revision { grid-column:1; }
  .worker-proposal:has(> .revision) > .actions { grid-column:2; margin-top:0; }
  /* Beside the note the decisions are the note's height at rest (44px), so their edges line up. */
  .worker-proposal:has(> .revision) > .actions .decision :global(.button) { height:var(--composer-rest-height, 44px); }
  @media (max-width:720px) {
    .worker-proposal:has(> .revision) > .actions { grid-column:1 / -1; margin-top:var(--space-3); }
  }
  /* The picker's own trigger is hidden here, so its empty line must not add height. */
  .worker-proposal > :global(div:has(> [data-ceiling-picker])) { display:flex; }
</style>
