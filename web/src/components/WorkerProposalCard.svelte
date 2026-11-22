<script lang="ts">
  import Button from "./Button.svelte";
  import { onDestroy, untrack, type Snippet } from "svelte";
  import CeilingPicker from "./CeilingPicker.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import ErrorLine from "./ErrorLine.svelte";
  import AttachableComposerField from "./conversation/AttachableComposerField.svelte";
  import { pendingFilesAsPieces, restoredPendingFiles, type PendingConversationFile } from "../lib/conversation/pendingFiles";
  import { pendingImagesAsPieces, releasePendingImages, restoredPendingImages, type PendingConversationImage } from "../lib/conversation/pendingImages";
  import { advanceTargetFor, finalApprovalFor, fieldLabelFor, preferredScopeCeilingFor, type Lifecycle } from "../lib/lifecycle";
  import { decisionIdentity, decisionRefusals, DecisionRefusedError, recallDecisionAttachments, recallReviewDecision, submitReviewDecision } from "../lib/review/decision";
  import { buildProposalApproveDecision, buildProposalSendBackDecision, proposalContentIdentity, syncProposalDraftState } from "../lib/review/proposalState";
  import type { PendingTicketProposal, Principal } from "../lib/types";

  let { ticketId, proposal, lifecycle, stage, ceiling, holder, manager, onAccept, onScope, onReturn, canReturn = true, returnUnavailableReason = "No existing Worker conversation to send a revision to.", disabled = false, revisionEditor, revisionText = $bindable(""), revisionImages = $bindable([]), revisionFiles = $bindable([]), attachmentReadBusy = $bindable(false), holdDecisions = false, initialEditedBody, onEditedBodyChange, contextRow, utilityActions, composerOnly = false }: {
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
    /** Pictures and files attached beside the note. They go with Send it back, and with Approve. */
    revisionImages?: PendingConversationImage[];
    revisionFiles?: PendingConversationFile[];
    /** Pictures or files are still being read: the decisions wait for them. */
    attachmentReadBusy?: boolean;
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
  onDestroy(() => { mounted = false; releasePendingImages(revisionImages); });
  let attachmentPieces = $derived([...pendingImagesAsPieces(revisionImages), ...pendingFilesAsPieces(revisionFiles)]);
  let hasAttachments = $derived(revisionImages.length > 0 || revisionFiles.length > 0);
  function clearAttachments(): void {
    releasePendingImages(revisionImages);
    revisionImages = []; revisionFiles = [];
  }
  // An unconfirmed decision made with attachments brings them back with its note, so a retry matches it.
  $effect(() => {
    const key = decisionKey;
    untrack(() => {
      let saved;
      try { saved = recallReviewDecision(key); } catch { return; }
      if (!saved?.attachmentCount || hasAttachments) return;
      void recallDecisionAttachments(key).then((pieces) => {
        if (!mounted || decisionKey !== key || hasAttachments) return;
        const restoredImages = restoredPendingImages(pieces, 0);
        revisionImages = restoredImages.images;
        revisionFiles = restoredPendingFiles(pieces, restoredImages.nextId).files;
      });
    });
  });
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
  let lastRecalledKey: string | null = null;
  let newStage = $derived(advanceTargetFor(lifecycle, stage, ""));
  let nextCeiling = $derived(preferredScopeCeilingFor(lifecycle, newStage));
  let finalApproval = $derived(finalApprovalFor(lifecycle, stage));
  let identity = $derived(JSON.stringify([
    ticketId,
    proposalContentIdentity(proposal),
    lifecycle.workerType,
    newStage,
    finalApproval,
    lifecycle.stageOrder,
    lifecycle.ceilingRange,
    manager?.id || ""
  ]));
  let decisionKey = $derived(decisionIdentity(ticketId, proposal.revision));
  $effect(() => {
    const nextIdentity = identity;
    const nextDecisionKey = decisionKey;
    untrack(() => {
      const synced = syncProposalDraftState(
        {
          lastIdentity: lastKey,
          lastRecalledKey,
          draft,
          revisionText,
          resolved,
          error
        },
        {
          identity: nextIdentity,
          decisionKey: nextDecisionKey,
          body: proposal.body,
          initialEditedBody
        },
        recallReviewDecision
      );
      lastKey = synced.lastIdentity;
      lastRecalledKey = synced.lastRecalledKey;
      draft = synced.draft;
      revisionText = synced.revisionText;
      resolved = synced.resolved;
      error = synced.error;
    });
  });
  function reviewerLabel(value: Principal): string {
    return value.kind === "owner" ? "User" : value.kind === "chief" ? "Chief" : value.kind === "ticket" ? "Worker" : "Manager";
  }
  async function completeScope(change: { ceiling?: string; holder: Principal | null; approval?: boolean }): Promise<void> {
    if (!change.holder || disabled || busy || resolved || (change.approval && (holdDecisions || attachmentReadBusy))) return;
    // Capture the exact proposal displayed for this gesture before awaiting any write.
    const revision = proposal.revision;
    const editedBody = draft;
    const key = decisionKey;
    const text = revisionText;
    const attachments = attachmentPieces;
    busy = true; error = null;
    try {
      if (change.approval && (change.ceiling || finalApproval)) {
        await submitReviewDecision(ticketId, proposal.field, buildProposalApproveDecision({
          proposal: { body: proposal.body, revision },
          editedBody,
          message: text,
          attachments,
          finalApproval,
          nextCeiling: change.ceiling,
          nextHolder: change.holder
        }));
        // Run onAccept before checking mounted/decisionKey: invalidateQueries() inside
        // submitReviewDecision can clear pending_proposal and unmount this bar first.
        await onAccept();
        if (!mounted || decisionKey !== key) return;
        revisionText = "";
        clearAttachments();
        resolved = true;
      } else {
        await onScope({ ...(change.ceiling === undefined ? {} : { ceiling:change.ceiling }), ceiling_holder:change.holder });
      }
    } catch (err) { error = err instanceof DecisionRefusedError ? null : err; }
    finally { busy = false; }
  }
  async function sendBack(message: string): Promise<void> {
    if (proposal.field === "brief" || disabled || !canReturn || busy || resolved || holdDecisions || attachmentReadBusy || (!message.trim() && !hasAttachments)) return;
    const attachments = attachmentPieces;
    busy = true; error = null;
    const key = decisionKey;
    const revision = proposal.revision;
    try {
      await submitReviewDecision(ticketId, proposal.field, buildProposalSendBackDecision({
        proposal: { revision },
        message,
        attachments
      }));
      await onReturn();
      if (!mounted || decisionKey !== key) return;
      revisionText = "";
      clearAttachments();
      resolved = true;
    }
    catch (err) { error = err instanceof DecisionRefusedError ? null : err; }
    finally { busy = false; }
  }
  let revisionSlot = $state<HTMLElement | null>(null);
  function sendItBack(): void {
    if (!canReturn || holdDecisions || attachmentReadBusy) return;
    const message = revisionText;
    if (message.trim() || hasAttachments) void sendBack(message);
    else revisionSlot?.querySelector<HTMLElement>("textarea")?.focus();
  }

</script>

<section inert={disabled || busy || resolved} class="worker-proposal" class:composer-only={composerOnly} data-approval-block data-artifact-origin="proposal" data-mode="pending" data-field={proposal.field}>
  {#if !composerOnly}
  <!-- The ask shell's eyebrow (AskShell): what it needs from the person in their accent on
       the left, then the stage and who approves it as quiet facts on the right. The
       proposal's own first line is the card's title: the thing being decided. -->
  <div class="proposal-line">
    {#if holder.kind === "owner"}<span class="proposal-need"><span class="proposal-dot" aria-hidden="true"></span>Needs your approval</span>
    {:else}<span class="proposal-need is-other">Waiting for {reviewerLabel(holder)} approval</span>{/if}
    <span class="proposal-facts">
      <h2>{fieldLabelFor(lifecycle, proposal.field)}</h2>
      <span class="proposal-line-sep" aria-hidden="true">·</span>
      <span class="approver">
        <span class="approver-label">Approver</span>
        <button type="button" class="reviewer" data-reviewer-heading aria-haspopup="listbox" aria-expanded={scopePicker?.expanded ?? false} aria-controls={scopePicker?.controls} onkeydown={(event) => { if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); scopePicker?.openReviewer(event.currentTarget); } }} onclick={(event) => scopePicker?.openReviewer(event.currentTarget)}>{reviewerLabel(holder)}<svg class="reviewer-chevron" viewBox="0 0 16 16" aria-hidden="true"><path d="m4.5 6.5 3.5 3.5 3.5-3.5"/></svg></button>
      </span>
    </span>
  </div>
  <div class="proposal-prose">
    <InlineEdit value={draft} markdown multiline dataEdit onSave={async (raw) => { draft = raw; onEditedBodyChange?.(raw); }} onCancel={() => proposal.body} />
  </div>
  {/if}
  {#if contextRow}<div class="context">{@render contextRow()}</div>{/if}
  <div class="revision" bind:this={revisionSlot}>
    {#if revisionEditor}{@render revisionEditor(flashDecisions)}{:else}
      <AttachableComposerField bind:value={revisionText} bind:images={revisionImages} bind:files={revisionFiles} bind:intakeBusy={attachmentReadBusy}
        placeholder="Add a note for the worker…"
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
        <Button variant="secondary" data-send-back disabled={disabled || !canReturn || busy || resolved || holdDecisions || attachmentReadBusy}
          onclick={() => void sendItBack()}>Send it back</Button>
      {/if}
      <!-- Approve is the primary decision (owner, round 4), furthest right, as Allow once and Save are. -->
      <Button variant="primary" data-accept aria-haspopup={finalApproval ? undefined : "listbox"} aria-expanded={finalApproval ? undefined : scopePicker?.expanded ?? false} aria-controls={finalApproval ? undefined : scopePicker?.controls}
        onkeydown={(event: KeyboardEvent) => { if (!finalApproval && (event.key === "ArrowDown" || event.key === "ArrowUp")) { event.preventDefault(); scopePicker?.openApproval(event.currentTarget as HTMLElement); } }} disabled={disabled || busy || resolved || holdDecisions || (!finalApproval && !nextCeiling)}
        onclick={(event) => finalApproval ? void completeScope({ approval:true, holder }) : scopePicker?.openApproval(event.currentTarget as HTMLElement)}>Approve</Button>
    </div>
  </div>
  <!-- The decisions are the page's foot. The panel opens above them, in the viewport. -->
  <CeilingPicker externalTrigger newStage={stage} {lifecycle} {ceiling} {holder} {manager} below={!composerOnly} pendingStage={stage}
    approvalCeiling={nextCeiling} bind:controller={scopePicker}
    holderLabel={(value) => value ? reviewerLabel(value) : "User"} onComplete={(change) => void completeScope(change)} />
  {#if proposal.field !== "brief" && !canReturn}<p class="return-unavailable" data-return-unavailable>{returnUnavailableReason}</p>{/if}
  {#if visibleError}<ErrorLine error={visibleError} />{/if}
</section>
<style>
  /* The ask shell (AskShell) on the page: the raised plane of everything the person must act
     on, its edge, a 16 radius, 16/20 padding. Its actions are the one Button, in one row at
     the right, the most important furthest right. */
  .worker-proposal { --decision-button-height:var(--button-main-height); padding:var(--cv-s4) 20px 20px; background:var(--cv-raised); border:1px solid var(--cv-line-strong); border-radius:var(--cv-r-xl); color:var(--cv-t2); font-family:var(--cv-font); }
  /* Standing in for the conversation composer: the note and decisions sit bare, like it does. */
  .worker-proposal.composer-only { padding:0; background:none; border:0; border-radius:0; }
  .worker-proposal.composer-only > .revision { margin-top:0; }
  .proposal-line { display:flex; align-items:center; flex-wrap:wrap; gap:var(--cv-s1) var(--cv-s3); min-height:28px; font:var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font); color:var(--cv-t3); }
  .proposal-need { display:inline-flex; align-items:center; gap:var(--cv-s2); color:var(--cv-accent); font-weight:var(--cv-weight-label); }
  .proposal-need.is-other { color:var(--cv-t3); }
  .proposal-dot { width:6px; height:6px; border-radius:var(--cv-r-pill); background:var(--cv-accent); }
  .proposal-facts { display:inline-flex; align-items:center; flex-wrap:wrap; margin-inline-start:auto; min-width:0; }
  h2 { margin:0; font:inherit; letter-spacing:0; color:inherit; }
  .proposal-line-sep { padding-inline:6px; color:var(--cv-t4); }
  .approver { display:inline-flex; align-items:center; gap:2px; }
  .approver-label { color:var(--cv-t3); }
  /* The proposal's first paragraph is the title (16/600 t1); the rest is prose in t2, never
     wider than the prose measure. */
  .proposal-prose { max-width:var(--page-prose-measure); margin-top:6px; color:var(--cv-t2); }
  .proposal-prose :global(.ed), .proposal-prose :global(.markdown) { font-family:var(--cv-font); font-size:var(--cv-type-ui); font-weight:var(--cv-weight-body); line-height:22px; color:var(--cv-t2); }
  .proposal-prose :global(.ed > p:first-child), .proposal-prose :global(.ed > .markdown > p:first-child), .proposal-prose :global(.ed > .markdown:first-child > :first-child:is(p)) { margin-bottom:var(--cv-s3); font-size:16px; font-weight:var(--cv-weight-title); line-height:24px; color:var(--cv-t1); }
  .actions { display:flex; align-items:center; justify-content:flex-end; flex-wrap:wrap; gap:var(--space-2); margin-top:var(--space-5); }
  /* The approver is a select: the reviewer's name in the primary tone with a chevron, no fill
     until pointed at, on a 28px target. */
  .reviewer { display:inline-flex; align-items:center; gap:var(--cv-s1); height:28px; font:inherit; font-weight:var(--cv-weight-label); line-height:1; padding:0 var(--cv-s2) 0 6px; border:0; background:transparent; color:var(--cv-t2); border-radius:var(--cv-r-md); cursor:pointer; }
  .reviewer:hover, .reviewer[aria-expanded="true"] { background:var(--cv-control); color:var(--cv-t1); }
  .reviewer-chevron { width:12px; height:12px; flex:none; fill:none; stroke:var(--cv-t4); stroke-width:1.5; stroke-linecap:round; stroke-linejoin:round; }
  .reviewer:focus-visible { outline:var(--cv-focus-width) solid var(--cv-accent); outline-offset:var(--cv-focus-offset); }
  .return-unavailable { color:var(--cv-t3); font:var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font); margin:var(--cv-s2) 0 0; }
  .revision { margin-top:var(--cv-s5); min-width:0; }
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
  /* v4: Approve is the primary pill, as an ask's primary in the composer is: #f2f2f2 with dark
     words, 36px, radius 18. Send it back stays the quiet secondary beside it, the same height. */
  .decision :global(.button[data-accept]), .decision :global(.button[data-send-back]) { height:36px !important; border-radius:18px; padding-inline:16px; }
  .decision :global(.button[data-accept]:not(:disabled)) { background:#f2f2f2; color:#121212; }
  .decision :global(.button[data-accept]:hover:not(:disabled)) { background:#ffffff; }
  .worker-proposal:has(> .revision) > .actions { align-self:end; margin-block-end:4px; }
  @media (max-width:720px) {
    .worker-proposal:has(> .revision) > .actions { grid-column:1 / -1; margin-top:var(--space-3); }
  }
  /* The picker's own trigger is hidden here, so its empty line must not add height. */
  .worker-proposal > :global(div:has(> [data-ceiling-picker])) { display:flex; }
</style>
