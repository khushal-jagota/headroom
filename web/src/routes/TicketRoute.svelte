<script lang="ts">
  import { isBrokenTicket } from "../lib/ticketIntegrity";
  import MacStatus from "../components/machines/MacStatus.svelte";
  import MachineActivityMark from "../components/machines/MachineActivityMark.svelte";
  import PageFold from "../components/pageKit/PageFold.svelte";
  import MachineIdentity from "../components/machines/MachineIdentity.svelte";
  import { machineName } from "../lib/ticketMachines";
  import { onDestroy, tick, untrack } from "svelte";
  import { SvelteMap } from "svelte/reactivity";
  import { createQuery } from "@tanstack/svelte-query";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import { navigationApplication } from "../lib/navigation/application.svelte";
  import { navigationConversationSignal, type NavigationTicketMasthead } from "../lib/navigation/presentation";
  import { ticketSection, ticketSectionFacts, ticketSectionHeading, ticketSectionRunLabel, ticketSectionFieldState } from "../lib/ticketSection";

  import { workspaceAddress } from "../lib/workspaceAddress";
  import { labelize } from "../lib/ui";
  import {
    fieldLabelFor,
    buildLifecycle,
    stageLabelFor
  } from "../lib/lifecycle";
  import { holderLabelFor } from "../lib/ceilingHolder";
  import type { EmployeeConfigurationSnapshot, TicketDetail } from "../lib/types";
  import ConversationSignalMark from "../components/ConversationSignalMark.svelte";
  import { conversationDock } from "../lib/conversation/layout/dock";
  import LiveConversation from "../components/conversation/LiveConversation.svelte";
  import { createConversationDismissal } from "../lib/conversation/dismissal";
  import type { ConversationState } from "../lib/conversation/conversationState";
  import { initialTicketConversationState } from "../lib/conversation/ticketConversationState";
  import {
    type DeliveredMessage,
    type OwnerSendBody
  } from "../lib/conversation/wire";
  import WorkerConfigurationSetup from "../components/WorkerConfigurationSetup.svelte";
  import CeilingPicker from "../components/CeilingPicker.svelte";
  import ClampedText from "../components/ClampedText.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import RequestRecapAction from "../components/RequestRecapAction.svelte";
  import RecapSlot from "../components/RecapSlot.svelte";
  import TicketLifecycleControls from "../components/TicketLifecycleControls.svelte";
  import type { InlineEditDraft } from "../lib/inlineEditDraft";
  import TicketStageSection from "../components/TicketStageSection.svelte";
  import ArtifactPreview from "../components/ArtifactPreview.svelte";
  import ArtifactsBand from "../components/ArtifactsBand.svelte";
  import WorkerProposalCard from "../components/WorkerProposalCard.svelte";
  import { workerPageHistory } from "../lib/workerPageHistory";
  import { createArtifactOnThePage } from "../lib/artifactOnThePage.svelte";
  import { type ManagedFileTarget } from "../lib/filePreview";

  let {
    id,
    // The artifact this screen is showing, when the host keeps that in its address, and
    // the way to write it there. The Workspace supplies both; without them, the screen
    // holds the value itself.
    requestedConversationId = null,
    openFile = null,
    onOpenFile = null
  }: {
    id: string;
    requestedConversationId?: string | null;
    openFile?: ManagedFileTarget | null;
    onOpenFile?: ((file: ManagedFileTarget | null) => void) | null;
  } = $props();
  const stableId = untrack(() => id);

  const navigation=(()=>{
    const context=navigationApplication();
    if(!context) throw new Error('Ticket masthead requires the application navigation owner');
    return context;
  })();

  let mastheadElement=$state<HTMLElement | null>(null);
  let screenElement=$state<HTMLElement | null>(null);
  let currentView=$derived(navigation.selectors.effective);
  let currentMasthead=$derived(currentView ? navigation.presentationFor(currentView).ticketMastheadsById.get(stableId) : undefined);
  let heldMasthead=$state.raw<NavigationTicketMasthead | undefined>(undefined);
  $effect(()=>{if(currentMasthead) heldMasthead=currentMasthead;});
  let masthead=$derived(heldMasthead ? {...heldMasthead,
    conversation_signal:navigationConversationSignal(currentView,heldMasthead.conversation_id)} : undefined);
  const machineHealth = createQuery(() => ({...queries.executorHealth(), enabled: masthead?.locale === "macos"}));
  const ticket = createQuery(() => queries.workspaceTicket(stableId));
  let brokenTicket = $derived(isBrokenTicket(ticket.data) ? ticket.data : undefined);
  let healthyTicket = $derived(ticket.data && !isBrokenTicket(ticket.data) ? ticket.data : undefined);
  // What a conversation for this Ticket's worker would start on, asked of the server
  // because the server is what resolves it: the Worker type's launch defaults and
  // whatever this Ticket last ran on, through the same code that will create it.
  const conversationStartValues = createQuery(() =>
    queries.ticketConversationStartValues(stableId)
  );
  // The Ticket's saved workflow is authoritative, independent of later preset edits.
  let conversationLifecycle = $derived(healthyTicket?.workflow ? buildLifecycle(healthyTicket.workflow) : null);
  let manifestMissingWorkerType = $derived(Boolean(healthyTicket && !healthyTicket.workflow));
  let canonicalTicket=$derived(currentView ? navigation.presentationFor(currentView).rawTicketsById.get(stableId) : undefined);
  let sectionConfiguration=$derived(healthyTicket?.workflow);
  let section=$derived(ticketSection(healthyTicket,sectionConfiguration));
  let sectionFacts=$derived(section ? ticketSectionFacts(section,currentView) : undefined);
  let conversationId=$state<string | null>(null);
  let selectedPastConversationId=$state<string | null>(null);
  let shownConversationId=$derived(selectedPastConversationId ?? conversationId);
  $effect(() => navigation.owner.setDemand("ticket", {
    selected: { kind: "ticket", id: stableId },
    selected_conversation_id: selectedPastConversationId, windows: []
  }));
  onDestroy(() => navigation.owner.setDemand("ticket", undefined));
  let observedPointer:string | undefined;
  $effect(()=>{
    if(!canonicalTicket) return;
    const key=JSON.stringify([stableId,canonicalTicket.value.conversation_id]);
    if(key===observedPointer) return;
    observedPointer=key;
    conversationId=canonicalTicket.value.conversation_id;
  });
  let requestedConversationApplied: string | null | undefined;
  $effect(() => {
    if (!canonicalTicket || requestedConversationApplied === requestedConversationId) return;
    requestedConversationApplied = requestedConversationId;
    selectedPastConversationId = requestedConversationId && requestedConversationId !== canonicalTicket.value.conversation_id ? requestedConversationId : null;
    if (requestedConversationId) conversationState = "opened";
  });
  // The one Manager this Ticket's ceiling can be handed to, named as Khushal names
  // it. Absent when the Ticket sits outside an Item, and then only me and the Chief show.
  let ticketManager = $derived(
    section?.detail.manager_id
      ? {
          id: section.detail.manager_id,
          title: section.detail.manager_title || "Manager"
        }
      : null
  );

  function pageHolderLabel(holder: import("../lib/types").Principal | null): string {
    return holderLabelFor(ticketManager, holder);
  }

  const emptyTicketFieldText = "Not written yet.";

  let headerError = $state<unknown>(null);

  /** How far open this page's conversation is.
   *
   * The state a conversation opens in belongs to the page that shows it. A user-owned Ticket
   * opens full, and every other Ticket opens at rest. The person moves it from there and
   * the conversation writes back here when they do.
   */
  let conversationState = $state<ConversationState>("rest");
  /** Seed the conversation once from the status at the start of this Ticket visit.
   *
   * A live status change is not a new visit. After the seed, only the person's expand,
   * collapse, and dismissal actions change this state. The route remounts this component
   * for another Ticket, so that Ticket receives its own seed.
   */
  let seededConversationStateFromStatus = false;
  $effect(() => {
    const ownership = canonicalTicket && 'stage_ownership' in canonicalTicket.value ? canonicalTicket.value.stage_ownership : undefined;
    if (ownership === undefined || seededConversationStateFromStatus) return;
    seededConversationStateFromStatus = true;
    conversationState = requestedConversationId ? "opened" : initialTicketConversationState(ownership);
  });

  const dismissal = createConversationDismissal(() => { conversationState = "rest"; });
  const dismissOutsideConversation = dismissal.surface;

  /** The artifact this screen is showing, kept in the address when the host keeps one. */
  let heldFile = $state<ManagedFileTarget | null>(null);
  const artifact = createArtifactOnThePage({
    shown: () => (onOpenFile ? openFile : heldFile),
    keep: (file) => { if (onOpenFile) onOpenFile(file); else heldFile = file; },
    conversationState: () => conversationState,
    dismissConversation: () => { conversationState = "rest"; },
    root: () => screenElement
  });
  // Which stages the reader has opened or closed with their own hands, by field name. A
  // stage that settles moves into the fold above and is rebuilt there, so the answer
  // cannot live in the stage. It lives here, where it outlives the move.
  let readerStageFolds = new SvelteMap<string, boolean>();
  let stepsOpen = $state(false);
  const fieldDrafts = new Map<string, InlineEditDraft>();
  function fieldDraft(name: string): InlineEditDraft {
    let draft = fieldDrafts.get(name);
    if (!draft) {
      const created = $state<InlineEditDraft>({ value: null, error: null, saving: false });
      draft = created; fieldDrafts.set(name, draft);
    }
    return draft;
  }

  /** Start this Ticket's conversation, so the first message has somewhere to go.
   *
   * The readiness loop starts one when it has a step to send; this is what happens when a
   * person gets there first. The reply carries the Ticket, so the id comes back from the
   * same write that made the link.
   */
  async function sendToTicketWorker(body: OwnerSendBody): Promise<DeliveredMessage> {
    const delivered=await mutateJson<DeliveredMessage>(`/api/tickets/${stableId}/conversation/send`, {
      method: "POST",
      body});
    if(delivered.conversation_id!==null) conversationId=delivered.conversation_id;
    return delivered;
  }

  /** New: the old conversation is killed and the Ticket stops pointing at it. The next
   *  message starts a fresh one, through the same door as the first one ever did. */
  async function resetTicketConversation(): Promise<void> {
    await mutateJson(`/api/tickets/${stableId}/conversation/reset`, { method: "POST"});
    conversationId=null;
  }

  async function patch(body: Record<string, unknown>): Promise<unknown> {
    return mutateJson(`/api/tickets/${stableId}`, {method:"PATCH",body});
  }

  function saveScope(body: Record<string, unknown>): Promise<unknown> {
    return mutateJson(`/api/tickets/${stableId}`, { method: "PATCH", body});
  }

  async function updateScope(body: Record<string, unknown>): Promise<void> {
    headerError = null;
    try {
      await saveScope(body);
    } catch (err) {
      headerError = err;
      throw err;
    }
  }

  function saveValue(field: string, body: string): Promise<unknown> {
    return mutateJson(`/api/tickets/${stableId}`, {
      method: "PATCH", body: { field_values: { [field]: body } }});
  }

  function completeGate(field: string, body: string): Promise<unknown> {
    return mutateJson(`/api/tickets/${stableId}/complete/${field}`, {
      method: "POST", body: { body }});
  }

  function saveEmployeeConfiguration(
    configuration: EmployeeConfigurationSnapshot
  ): Promise<TicketDetail> {
    return mutateJson<TicketDetail>(`/api/tickets/${stableId}/employee-configuration`, {
      method: "PUT", body: configuration});
  }

  function acceptField(field: string, body: Record<string, unknown>): Promise<unknown> {
    return mutateJson(`/api/tickets/${stableId}/accept/${field}`, { method: "POST", body});
  }

  function conversationWorkerTypeLabel(detail: TicketDetail): string {
    return conversationLifecycle?.workerTypeLabel ?? (detail.worker_type ? labelize(detail.worker_type) : 'Custom workflow');
  }

  function conversationEmployeeLabel(detail: TicketDetail): string {
    const workerTypeLabel = conversationWorkerTypeLabel(detail);
    return /worker$/i.test(workerTypeLabel) ? workerTypeLabel : `${workerTypeLabel} worker`;
  }

  // The Kickoff approval card still carries Worker configuration while that
  // choice is editable. Direct blockers belong to the Ticket itself, so they
  // always stay in the masthead instead of moving into a stage card.
  let kickoffCardShowsContextRow = $derived(
    section?.record.value.gating_field === "brief" &&
      section.detail.pending_proposal?.field === "brief" &&
      Boolean(section.detail.employee_configuration_editable)
  );
  function returnProposal(message:string):Promise<unknown> {
    return mutateJson(`/api/tickets/${stableId}/reject`,{method:"POST",body:{message}});
  }

  async function removeBlocker(blockerTicketId: string): Promise<void> {
    headerError = null;
    try {
      await mutateJson(
        `/api/collections/blockers/${encodeURIComponent(stableId)}/${encodeURIComponent(blockerTicketId)}`,
        { method: "DELETE"}
      );
    } catch (err) {
      headerError = err;
    }
  }
  let mastheadTypeLabel=$derived(masthead ? masthead.workflow_label ?? (healthyTicket?.worker_type === masthead.worker_type ? healthyTicket?.workflow?.label : null) ?? (masthead.worker_type ? labelize(masthead.worker_type) : 'Custom workflow') : '');

  // The pointer footprint belongs to the mounted pane, independent of section controls.

  let sectionElement=$state<HTMLElement | null>(null);

</script>

<svelte:window onkeydown={artifact.closeOnEscape} />

<!-- The catcher for links to managed files sits on the whole screen, so a file opens in
     place wherever it was written: a Ticket field, the recap, or the conversation. -->
<!-- The links inside keep their own keyboard behaviour: Enter on a link raises this same
     event, so the catcher needs no key handling of its own. -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- svelte-ignore a11y_click_events_have_key_events -->
<section
  bind:this={screenElement}
  class="ticket-screen"
  data-screen="ticket"
  data-ticket-id={stableId}
  data-stage={section?.record.value.stage}
  onclick={artifact.claimPress}
>
  {#if brokenTicket}
    <div class="ticket-page" data-broken-ticket>
      <main class="ticket-doc">
        <header class="ticket-head">
          <p class="worker-eyebrow">Broken Ticket</p>
          <h1 class="ticket-title">{brokenTicket.title}</h1>
        </header>
        <ErrorLine error={{message:brokenTicket.integrity_error.reason}} />
      </main>
    </div>
  {:else}
  <ResourceState shape="document" layout="fill" error={ticket.error} loading={ticket.isFetching || !masthead || !section} hasData={Boolean(healthyTicket && masthead && (section || manifestMissingWorkerType))} loadingText="Loading ticket...">
    {#if healthyTicket && manifestMissingWorkerType}
      <div class="ticket-page" data-ticket-manifest-error>
        <ErrorLine
          error={{ code: "invalid_workflow", message: "The Ticket workflow is unavailable." }}
        />
      </div>
    {:else if healthyTicket && masthead}
      {@const detail = healthyTicket}
      <div class="ticket-page" use:conversationDock use:dismissOutsideConversation>
      <div class="ticket-reading">
      <main class="ticket-doc">
        <header bind:this={mastheadElement} class="ticket-head" data-ticket-masthead-id={masthead.id} data-ticket-locale={masthead.locale}>
          <!-- Every page has one line above its title: where it lives, then its own facts.
               Under the title there is only the recap. -->
          <div class="page-band">
          <div class="worker-eyebrow page-eyebrow page-band-place" data-ticket-identity>
            <!-- Where the Ticket runs leads the line, as its icon alone; the machine's name is
                 the mark's accessible name and tooltip. -->
            <span class="worker-machine" title={`Runs on ${machineName(masthead.locale)}`}><MachineIdentity locale={masthead.locale} /></span>
            <span data-project-fact>{masthead.project || "No project"}</span>
            <span class="page-eyebrow-sep" aria-hidden="true">·</span>
            <span data-ticket-type>{mastheadTypeLabel}</span>
          </div>
          <!-- The Ticket's utilities sit at the band's right end, quiet and always shown, so
               the decision below is the page's one call to action. -->
          {#if section}<TicketLifecycleControls ticket={section.detail} terminal={section.record.value.terminal}>
            {#snippet leading()}<RequestRecapAction kind="ticket" id={stableId} />{/snippet}
          </TicketLifecycleControls>{/if}
          </div>
          <div class="ticket-title-row">
            <!-- The title owns the column less one fixed right column, where the working mark
                 stands; the unread mark is on the composer's status line. -->
            <div class="ticket-title">
              <h1 class="page-title ticket-title-text"><InlineEdit
                value={masthead.title}
                placeholder="Untitled"
                onSave={(raw) => patch({ title: raw })}
              /></h1>
              <span class="ticket-title-mark"><MachineActivityMark locale={masthead.locale} signal={masthead.conversation_signal} conversationId={masthead.conversation_id} completed={detail.stage === "done" || detail.stage === "cancelled"} /></span>
            </div>
            <RecapSlot>
            {#snippet children()}
            <ClampedText
              contentKey={detail.recap}
              moreControlAttributes={{ "data-recap-toggle": "" }}
              data-recap
            >
              <InlineEdit
                value={detail.recap}
                markdown
                multiline
                placeholder="+ add orientation"
                onSave={(raw) =>
                  mutateJson(`/api/tickets/${stableId}`, {
                    method: "PATCH", body: { recap: raw }
                  })}
              />
            </ClampedText>
            {/snippet}
            </RecapSlot>
          </div>
          {#if masthead.locale === "macos"}<MacStatus health={machineHealth.isError ? undefined : machineHealth.data} showDisconnected />{/if}
          {#if detail.blocker_summary?.blocked_by.length}
            <div class="ticket-blockers" data-blocker-summary>
              <span class="ticket-blocker-heading">Blocked by</span>
              {#each detail.blocker_summary.blocked_by as blocker}
                <span class="ticket-blocker-chip" data-blocker-chip={blocker.ticket_id}>
                  <a class="ticket-blocker-link" href={blocker.href}>{blocker.title}</a>
                  <button
                    type="button"
                    class="ticket-blocker-remove"
                    data-remove-blocker={blocker.ticket_id}
                    aria-label={`Remove blocker ${blocker.title}`}
                    onclick={() => void removeBlocker(blocker.ticket_id)}
                  >×</button>
                </span>
              {/each}
            </div>
          {/if}
          {#if headerError}<ErrorLine error={headerError} />{/if}
        </header>

        {#if section}
        {@const detail=section.detail}
        {@const lc=section.lifecycle}
        <div bind:this={sectionElement} class="ticket-col" data-ticket-section={section.record.id} data-section-stage={section.record.value.stage}>
          {#snippet kickoffContextRow()}
            {#if detail.employee_configuration_editable}
              {#key JSON.stringify([stableId, detail.locale, conversationStartValues.data?.workspace_folder, conversationStartValues.data?.workspace_path_policy])}
              <WorkerConfigurationSetup destination={detail.locale} ticketId={stableId} employeeBackend={detail.employee_backend}
                employeeLaunchModel={detail.employee_launch_model} employeeLaunchReasoningEffort={detail.employee_launch_reasoning_effort}
                workspace={conversationStartValues.data}
                onSave={saveEmployeeConfiguration} />
              {/key}
            {/if}
          {/snippet}
          {#if section}
            {@const currentField = section.record.value.gating_field}
            {@const history = workerPageHistory(lc, detail)}
            {@const completedFields = history.completed}
            {@const stepFields = history.fields}
            {#if currentField}
              <section class:worker-live-card={detail.pending_proposal?.field !== currentField} class="worker-current" data-worker-live-card data-proposal-created-at={detail.pending_proposal?.created_at} data-proposal-revision={detail.pending_proposal?.revision}>
                {#if detail.pending_proposal?.field === currentField}
                  {#key `${detail.pending_proposal.field}:${detail.pending_proposal.created_at}:${detail.pending_proposal.revision}`}
                    <WorkerProposalCard proposal={detail.pending_proposal} lifecycle={lc} stage={detail.stage}
                      ceiling={detail.ceiling} holder={detail.ceiling_holder} manager={ticketManager} canReturn={Boolean(detail.conversation_id)}
                      onAccept={(payload) => acceptField(currentField, payload)}
                      onScope={updateScope}
                      onReturn={returnProposal}
                      contextRow={currentField === "brief" && kickoffCardShowsContextRow ? kickoffContextRow : undefined} />
                  {/key}
                {:else}
                  <h2>{ticketSectionHeading(section,currentView)} {fieldLabelFor(lc, currentField)}</h2>
                  {#if detail.stage !== "needs_brief"}
                    <div class="worker-ceiling-line" data-leash>
                      <CeilingPicker document newStage={detail.stage} lifecycle={lc} ceiling={detail.ceiling} holder={detail.ceiling_holder}
                        triggerLabel={`Approved until ${detail.ceiling === "none" ? "No further" : stageLabelFor(lc, detail.ceiling)}, then ${pageHolderLabel(detail.ceiling_holder)} reviews`}
                        holderLabel={pageHolderLabel} manager={ticketManager} below onComplete={(change) => void updateScope(change.ceiling === undefined
                          ? { ceiling_holder:change.holder } : { ceiling:change.ceiling, ceiling_holder:change.holder }).catch(() => undefined)} />
                    </div>
                  {/if}
                  {#if sectionFacts?.assigned}
                    <TicketStageSection name={currentField} value={detail.field_values[currentField] ?? ""} lifecycle={lc}
                      stageState={ticketSectionFieldState(section,currentView,currentField)} ticketStage={detail.stage}
                      variant="review" editableCurrentValue={true}
                      onCompleteGate={(raw) => completeGate(currentField, raw)} />
                  {/if}
                  {#if detail.employee_configuration_editable}{@render kickoffContextRow()}{/if}
                {/if}
              </section>
            {/if}
            <div class="worker-bands">
              <details class="worker-steps" data-stage-fold bind:open={stepsOpen}>
                <summary data-stage-fold-open>Steps<span class="band-count">{completedFields.length} of {lc.fieldIds.length} done</span></summary>
                {#if stepsOpen}
                <div class="worker-step-rows">
                  {#each stepFields as name (`${detail.id}:${name}`)}
                    {@const stageState = ticketSectionFieldState(section,currentView,name)}
                    <TicketStageSection {name} ticketId={stableId} draft={fieldDraft(name)} value={detail.field_values[name]}
                      approval={detail.field_approvals?.[name]}
                      {stageState} lifecycle={lc} ticketStage={detail.stage}
                      emptyText={emptyTicketFieldText} editableValue={true}
                      editableCurrentValue={false} readerLeftItOpen={readerStageFolds.get(name) ?? false}
                      onReaderToggle={(open) => readerStageFolds.set(name, open)}
                      runLabel={name === currentField ? ticketSectionRunLabel(section,currentView) : null}
                      onSaveValue={(raw) => saveValue(name,raw)}
                      onCompleteGate={(raw) => completeGate(name,raw)} />
                  {/each}
                </div>
                {/if}
              </details>
              <ArtifactsBand owner={{ kind: "ticket", id: detail.id }} />
            </div>
          {/if}
        </div>
        {/if}
        <PageFold />
      </main>
      {#if artifact.shown}
        <ArtifactPreview
          registerArtifactFrame={dismissal.registerArtifactFrame}
          target={artifact.shown}
          reloadSignal={artifact.reloadSignal}
          bind:element={artifact.element}
          onRefresh={artifact.refresh}
          onClose={artifact.close}
        />
      {/if}
      </div>
      <div
        class="conversation-layer"
        data-conversation-layer-host
      >
        <div class="conversation-column">
          <LiveConversation locale={masthead.locale}
            bind:conversationState
            conversationId={shownConversationId}
            bind:selectedPastConversationId
            readOnly={selectedPastConversationId !== null}
            persistenceKey={`owner:ticket:${detail.id}`}
            uploadOwner={{kind:"ticket",id:detail.id}}
            label={conversationWorkerTypeLabel(detail)}
            composerPlaceholder={`Message ${conversationEmployeeLabel(detail)}...`}
            startValues={conversationStartValues.data ?? null}
            senderLabel="owner"
            sendMessage={sendToTicketWorker}
            onNewConversation={resetTicketConversation}
          />
        </div>
      </div>
    </div>
    {/if}
  </ResourceState>
  {/if}
</section>

<style>
  .ticket-col { padding-top:0; }
  .ticket-doc { scrollbar-gutter:auto; isolation:isolate; }
  .ticket-head { padding-top:var(--page-top); }
  .ticket-title-row { display:block; margin-top:var(--space-1); }
  .worker-machine { display:inline-flex; align-items:center; margin-right:6px; color:var(--tone-tertiary); }
  .worker-machine :global(.machine-identity) { width:14px; height:14px; color:inherit; }
  /* The title and a fixed 16px mark column, centred on the title's first line. */
  .ticket-title { display:grid; grid-template-columns:minmax(0,1fr) 16px; column-gap:var(--space-3); align-items:start; }
  .ticket-title-text { margin:0; }
  .ticket-title :global(.ed) { font:inherit; color:var(--tone-strong); }
  .ticket-title-mark { display:flex; align-items:center; justify-content:center; height:32px; }
  /* The unread mark lives on the composer's status line (ConversationRestBar); the title's
     column keeps only the working mark. */
  .ticket-title-mark :global([data-conversation-signal="message"]) { display:none; }
  /* The gap belongs to the slot, not to the words in it: the words go while a recap is
     written, and a gap that went with them would move the page. */
  .ticket-head :global([data-recap-slot]) { margin-top:var(--space-2); }
  .ticket-head :global([data-recap]) { max-width:var(--page-prose-measure); font-family:var(--font-ui); font-size:var(--type-body); font-weight:var(--weight-body); line-height:var(--leading-body); color:var(--tone-primary); }
  .ticket-head :global([data-recap] .ed) { font:inherit; }
  .worker-current { margin-top:var(--space-6); }
  .worker-live-card { padding:var(--page-card-pad); background:var(--page-card-fill); border:var(--border-hairline) solid var(--page-card-edge); border-radius:var(--page-card-radius); }
  .worker-live-card h2 { margin:0; letter-spacing:0; font-size:var(--type-page-section); font-weight:var(--weight-page-section); line-height:20px; color:var(--tone-primary); }
  .worker-ceiling-line { margin-top:var(--space-3); }
  .worker-ceiling-line :global([data-scope-ceiling]) { font-family:var(--font-ui); font-size:var(--type-body); line-height:20px; color:var(--tone-secondary); padding:var(--space-2) var(--space-3); margin-inline:calc(-1 * var(--space-3)); border:0; border-radius:var(--radius-md); background:transparent; box-shadow:none; text-align:left; }
  .worker-ceiling-line :global([data-scope-ceiling]:hover) { background-image:var(--interaction-hover); }
  /* Steps and Artifacts are two fold rows (app.css, THE FOLD ROW) between hairlines. */
  .worker-bands { line-height:var(--leading-body); margin-top:var(--space-6); padding-bottom:var(--space-7); }
  .worker-steps > summary { cursor:pointer; list-style:none; }
  .worker-steps > summary::-webkit-details-marker { display:none; }
  .worker-step-rows { padding-bottom:var(--space-4); }
  .worker-step-rows :global(.disclosure--stage) { margin:0; border:0; }
  .worker-step-rows :global(.disclosure--stage > .disclosure-summary) { padding:var(--space-2); margin-inline:calc(-1 * var(--space-2)); border-radius:var(--radius-md); font-size:var(--type-body); color:var(--tone-secondary); }
  .worker-step-rows :global(.disclosure--stage > .disclosure-summary:hover) { background-image:var(--interaction-hover); color:var(--tone-strong); }
  .worker-step-rows :global(.disclosure--stage > .disclosure-body) { padding:var(--space-2) var(--space-2) var(--space-5); font-family:var(--font-ui); font-size:var(--type-body); color:var(--tone-primary); }
  .worker-step-rows :global(.disclosure-stage-name) { text-transform:none; font-size:var(--type-body); letter-spacing:normal; }
</style>
