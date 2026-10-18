<script lang="ts">
  import { onMount, onDestroy, tick, untrack } from "svelte";
  import { SvelteMap } from "svelte/reactivity";
  import { createQuery } from "@tanstack/svelte-query";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import { navigationApplication } from "../lib/navigation/application.svelte";
  import { navigationConversationSignal, type NavigationTicketMasthead } from "../lib/navigation/presentation";
  import { admitTicketSection, ticketSectionDetailMatches, ticketSectionFacts, ticketSectionHeading, ticketSectionRunLabel, ticketSectionFieldState, type TicketSection } from "../lib/ticketSection";
  import { ticketCommandImpact, ticketMastheadImpact, ticketSectionImpact } from "../lib/ticketNavigationImpact";
  import { ticketSectionDomMatches } from "../lib/ticketSectionDom";
  import { ticketMastheadDomMatches } from "../lib/ticketMastheadDom";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import { labelize } from "../lib/ui";
  import {
    fieldLabelFor,
    lifecycleFor,
    stageLabelFor
  } from "../lib/lifecycle";
  import { holderLabelFor } from "../lib/ceilingHolder";
  import type { EmployeeConfigurationSnapshot, TicketDetail } from "../lib/types";
  import ConversationSignalMark from "../components/ConversationSignalMark.svelte";
  import LiveConversation from "../components/conversation/LiveConversation.svelte";
  import type { ConversationState } from "../lib/conversation/conversationState";
  import { initialTicketConversationState } from "../lib/conversation/ticketConversationState";
  import {
    readBackends,
    type BackendSnapshot,
    type DeliveredMessage,
    type OwnerSendBody
  } from "../lib/conversation/wire";
  import WorkerConfigurationSetup from "../components/WorkerConfigurationSetup.svelte";
  import CeilingPicker from "../components/CeilingPicker.svelte";
  import ClampedText from "../components/ClampedText.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import InlineEdit from "../components/InlineEdit.svelte";
  import ResourceState from "../components/ResourceState.svelte";
  import TicketLifecycleControls from "../components/TicketLifecycleControls.svelte";
  import TicketStageSection from "../components/TicketStageSection.svelte";
  import ArtifactPreview from "../components/ArtifactPreview.svelte";
  import ArtifactsBand from "../components/ArtifactsBand.svelte";
  import WorkerProposalCard from "../components/WorkerProposalCard.svelte";
  import { workerPageHistory } from "../lib/workerPageHistory";
  import { ticketPageArtifactItems } from "../lib/artifactStrip";
  import { isPlainLinkClick } from "../lib/linkClick";
  import {
    targetFromPreviewHref,
    type ManagedFileTarget
  } from "../lib/filePreview";

  let {
    id,
    // The artifact this screen is showing, when the host keeps that in its address, and
    // the way to write it there. The Workspace supplies both; without them, the screen
    // holds the value itself.
    openFile = null,
    onOpenFile = null,
    navigationErrorHandledByParent = false
  }: {
    id: string;
    navigationErrorHandledByParent?: boolean;
    openFile?: ManagedFileTarget | null;
    onOpenFile?: ((file: ManagedFileTarget | null) => void) | null;
  } = $props();
  const stableId = untrack(() => id);

  const navigation=(()=>{
    const context=navigationApplication();
    if(!context) throw new Error('Ticket masthead requires the application navigation owner');
    return context;
  })();
  const mastheadObserver=navigation.registerRenderBoundary(`ticket-masthead:${crypto.randomUUID()}`,impact=>{
    const mapped=currentView ? navigation.presentationFor(currentView) : undefined;
    const row=mapped?.rawTicketsById.get(stableId);
    const parent=row?.value.sprint_item_id ?? null;
    const project=parent===null ? row?.value.project_id ?? null : mapped?.rawOrchestratorsById.get(parent)?.value.project_id ?? null;
    return ticketMastheadImpact(impact,row,parent,project);
  });
  onDestroy(()=>navigation.disposeRenderBoundary(mastheadObserver));
  let mastheadElement=$state<HTMLElement | null>(null);
  let screenElement=$state<HTMLElement | null>(null);
  let currentView=$derived(navigation.selectors.effective);
  let currentMasthead=$derived(currentView ? navigation.presentationFor(currentView).ticketMastheadsById.get(stableId) : undefined);
  let heldMasthead=$state.raw<NavigationTicketMasthead | undefined>(undefined);
  $effect(()=>{if(currentMasthead) heldMasthead=currentMasthead;});
  let masthead=$derived(heldMasthead ? {...heldMasthead,
    conversation_signal:navigationConversationSignal(currentView,heldMasthead.conversation_id)} : undefined);
  const ticket = createQuery(() => queries.ticket(stableId));
  // What a conversation for this Ticket's worker would start on, asked of the server
  // because the server is what resolves it: the Worker type's launch defaults and
  // whatever this Ticket last ran on, through the same code that will create it.
  const conversationStartValues = createQuery(() =>
    queries.ticketConversationStartValues(stableId)
  );
  const manifest = createQuery(() => queries.workerTypeManifests());

  // Conversation labels use configuration. The section below admits its controls
  // against the stored canonical Stage, gate, and ownership before display.
  let conversationLifecycle = $derived(lifecycleFor(manifest.data, ticket.data?.worker_type));
  let manifestMissingWorkerType = $derived(
    Boolean(
      ticket.data &&
        manifest.data &&
        !manifest.data.worker_types.some((item) => item.worker_type === ticket.data?.worker_type)
    )
  );
  let canonicalTicket=$derived(currentView ? navigation.presentationFor(currentView).rawTicketsById.get(stableId) : undefined);
  let sectionConfiguration=$derived(manifest.data?.worker_types.find(item=>item.worker_type===canonicalTicket?.value.worker_type));
  let admittedSection=$derived(admitTicketSection(currentView,canonicalTicket,ticket.data,sectionConfiguration));
  let section=$state.raw<TicketSection | undefined>(undefined);
  $effect(()=>{if(admittedSection) section=admittedSection;});
  let sectionFacts=$derived(section ? ticketSectionFacts(section,currentView) : undefined);
  // A changed public control tuple needs its matching exact detail/configuration.
  // One request per mismatch avoids a repeated refetch for an incompatible manifest.
  let refreshedMismatch='';
  $effect(()=>{
    if(!canonicalTicket || admittedSection || !ticket.data || !manifest.data) return;
    const key=JSON.stringify([canonicalTicket.value,ticket.data.record_sha256,sectionConfiguration]);
    if(key===refreshedMismatch) return;
    refreshedMismatch=key;
    void ticket.refetch();
    void manifest.refetch();
  });
  function assertCurrentSection():void {
    // The owner keeps its coherent accepted view during the selector handoff.
    // Runtime uncertainty does not change structural action authority.
    const view = navigation.state.effective;
    const record = view ? navigation.presentationFor(view).rawTicketsById.get(stableId) : undefined;
    if(!section || !record || !ticketSectionDetailMatches(record,section.detail) || !admitTicketSection(view,record,ticket.data,
      manifest.data?.worker_types.find(item=>item.worker_type===ticket.data?.worker_type))) {
      throw {code:'validation',message:'the Ticket changed; read it again before this action'};
    }
  }
  let conversationId=$state<string | null>(null);
  let observedPointer:string | undefined;
  $effect(()=>{
    if(!canonicalTicket) return;
    const key=JSON.stringify([stableId,canonicalTicket.value.conversation_id]);
    if(key===observedPointer) return;
    observedPointer=key;
    conversationId=canonicalTicket.value.conversation_id;
  });
  // The one Sprint Item this Ticket's ceiling can be handed to, named as Khushal names
  // it. Absent when the Ticket sits outside an Item, and then only me and the Chief show.
  let ticketSprintItem = $derived(
    section?.detail.sprint_item_id
      ? {
          id: section.detail.sprint_item_id,
          title: section.detail.resolved_priority_anchors?.sprint_item?.title || "Sprint Item"
        }
      : null
  );

  function pageHolderLabel(holder: import("../lib/types").Principal | null): string {
    return holderLabelFor(ticketSprintItem, holder);
  }

  const emptyTicketFieldText = "Not written yet.";

  let headerError = $state<unknown>(null);
  let conversationBackends = $state<readonly BackendSnapshot[]>([]);

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
    const ownership = canonicalTicket?.value.stage_ownership;
    if (ownership === undefined || seededConversationStateFromStatus) return;
    seededConversationStateFromStatus = true;
    conversationState = initialTicketConversationState(ownership);
  });

  /** A click outside the conversation dismisses it to rest. */
  function dismissConversationToRest(): void {
    if (conversationState !== "rest") conversationState = "rest";
  }

  /** A press beside the centered conversation card dismisses it to rest. */
  function dismissConversationOnAPressBesideTheCard(event: MouseEvent): void {
    const pressed = event.target;
    if (!(pressed instanceof Element)) return;
    if (pressed.closest("[data-conversation-pane]") !== null) return;
    dismissConversationToRest();
  }

  /** The artifact this screen is showing, when the host does not keep it in an address. */
  let heldFile = $state<ManagedFileTarget | null>(null);
  let shownFile = $derived(onOpenFile ? openFile : heldFile);
  let artifactReloadSignal = $state(0);
  // Which stages the reader has opened or closed with their own hands, by field name. A
  // stage that settles moves into the fold above and is rebuilt there, so the answer
  // cannot live in the stage. It lives here, where it outlives the move.
  let readerStageFolds = new SvelteMap<string, boolean>();

  /** Show a file on this screen, or close the one it is showing.
   *
   * The Ticket stays where it is underneath, so the person who came to read an artifact
   * has not left the work it belongs to. An opened conversation is the whole page, and
   * an artifact opened behind it would be an artifact nobody can see, so the
   * conversation steps back one to let it through.
   */
  function showFile(file: ManagedFileTarget | null): void {
    if (file && !shownFile) {
      whatHadFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }
    if (onOpenFile) onOpenFile(file);
    else heldFile = file;
    if (file && conversationState === "opened") conversationState = "peeked";
    if (!file) {
      const restoreFocus = whatHadFocus;
      whatHadFocus = null;
      queueMicrotask(() => restoreFocus?.focus());
    }
  }

  /** The artifact takes focus when it opens, and gives it back when it closes.
   *
   * The reader is now reading the artifact, so that is where the keyboard should be. It
   * is also what makes Escape reach this screen: a Ticket field is an editable surface
   * and Escape belongs to the editor while the caret is in one, so an artifact opened
   * from a link inside a field must move the focus out of it.
   */
  let artifactElement = $state<HTMLElement | null>(null);
  let whatHadFocus: HTMLElement | null = null;
  $effect(() => {
    if (shownFile && artifactElement) artifactElement.focus();
  });

  /** A click on a link to a managed file opens the file here.
   *
   * Every "Open plan.md" the shared preview writes points at the `#/preview` address, in
   * a Ticket field, in a note, and in the conversation alike. On this screen that address
   * is not somewhere to go: the file is opened over the Ticket's own document instead.
   * A click asking for a new tab or a new window is left alone, and so is every link that
   * names anything else — a dev server among them, which is a page and not a file.
   */
  function openManagedFileInPlace(event: MouseEvent): void {
    if (event.defaultPrevented) return;
    const anchor = (event.target as Element | null)?.closest?.("a[href]");
    if (!anchor) return;
    const link = { href: anchor.getAttribute("href"), target: anchor.getAttribute("target") };
    if (!isPlainLinkClick(link, event)) return;
    const file = targetFromPreviewHref(link.href);
    if (!file) return;
    event.preventDefault();
    showFile(file);
  }

  /** Escape closes the artifact, and belongs to it before anything else on the page.
   *
   * The conversation steps back a state on Escape too, so the press is claimed here to
   * keep one press to one thing.
   */
  function closeFileOnEscape(event: KeyboardEvent): void {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    if (!shownFile) return;
    event.preventDefault();
    showFile(null);
  }

  onMount(() => {
    // What the conversation's model and effort pickers offer. Read once on arrival rather
    // than through the query catalogue: it is a fact about the machine's agents, and
    // nothing a person does to this Ticket changes it.
    void readBackends()
      .then((snapshots) => (conversationBackends = snapshots))
      .catch(() => {
        // The pickers fall back to showing the value already in force, which is the same
        // thing they show before the catalog has arrived. Nothing here is worth a banner.
      });
  });

  /** Start this Ticket's conversation, so the first message has somewhere to go.
   *
   * The readiness loop starts one when it has a step to send; this is what happens when a
   * person gets there first. The reply carries the Ticket, so the id comes back from the
   * same write that made the link.
   */
  async function sendToTicketWorker(body: OwnerSendBody): Promise<DeliveredMessage> {
    const delivered=await navigation.runCommand(ticketCommandImpact(navigation.state.effective,stableId,'conversation'),responseMetadata=>mutateJson<DeliveredMessage>(`/api/tickets/${stableId}/conversation/send`, {
      method: "POST",
      body,responseMetadata
    }),"send Ticket message");
    if(delivered.conversation_id!==null) conversationId=delivered.conversation_id;
    return delivered;
  }

  /** New: the old conversation is killed and the Ticket stops pointing at it. The next
   *  message starts a fresh one, through the same door as the first one ever did. */
  async function resetTicketConversation(): Promise<void> {
    await navigation.runCommand(ticketCommandImpact(navigation.state.effective,stableId,'conversation'),responseMetadata=>mutateJson(`/api/tickets/${stableId}/conversation/reset`, { method: "POST",responseMetadata }),"reset Ticket conversation");
    conversationId=null;
  }

  async function patch(body: Record<string, unknown>): Promise<unknown> {
    return navigation.runCommand(ticketCommandImpact(navigation.state.effective,stableId,'title'),responseMetadata=>
      mutateJson(`/api/tickets/${stableId}`, {method:"PATCH",body,responseMetadata}));
  }

  function saveScope(body: Record<string, unknown>): Promise<unknown> {
    assertCurrentSection();
    return navigation.runCommand(ticketCommandImpact(navigation.state.effective,stableId,'scope'),responseMetadata=>mutateJson(`/api/tickets/${stableId}`, { method: "PATCH", body,responseMetadata }));
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
    assertCurrentSection();
    return navigation.runCommand(ticketCommandImpact(navigation.state.effective,stableId,'private'),responseMetadata=>mutateJson(`/api/tickets/${stableId}`, {
      method: "PATCH", body: { field_values: { [field]: body } },responseMetadata
    }));
  }

  function completeGate(field: string, body: string): Promise<unknown> {
    assertCurrentSection();
    return navigation.runCommand(ticketCommandImpact(navigation.state.effective,stableId,'stage'),responseMetadata=>mutateJson(`/api/tickets/${stableId}/complete/${field}`, {
      method: "POST", body: { body },responseMetadata
    }));
  }

  function saveEmployeeConfiguration(
    configuration: EmployeeConfigurationSnapshot
  ): Promise<TicketDetail> {
    assertCurrentSection();
    return navigation.runCommand(ticketCommandImpact(navigation.state.effective,stableId,'configuration'),responseMetadata=>mutateJson<TicketDetail>(`/api/tickets/${stableId}/employee-configuration`, {
      method: "PUT", body: configuration,responseMetadata
    }));
  }

  function acceptField(field: string, body: Record<string, unknown>): Promise<unknown> {
    assertCurrentSection();
    return navigation.runCommand(ticketCommandImpact(navigation.state.effective,stableId,'stage'),responseMetadata=>mutateJson(`/api/tickets/${stableId}/accept/${field}`, { method: "POST", body,responseMetadata }));
  }

  function conversationWorkerTypeLabel(detail: TicketDetail): string {
    return conversationLifecycle?.workerTypeLabel ?? labelize(detail.worker_type);
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
    assertCurrentSection();
    return navigation.runCommand(ticketCommandImpact(navigation.state.effective,stableId,'stage'),responseMetadata=>mutateJson(`/api/tickets/${stableId}/reject`,{method:"POST",body:{message},responseMetadata}));
  }


  async function removeBlocker(blockerTicketId: string): Promise<void> {
    headerError = null;
    try {
      await navigation.runCommand(ticketCommandImpact(navigation.state.effective,stableId,'blocker'),responseMetadata=>mutateJson(
        `/api/collections/blockers/${encodeURIComponent(stableId)}/${encodeURIComponent(blockerTicketId)}`,
        { method: "DELETE",responseMetadata }
      ));
    } catch (err) {
      headerError = err;
    }
  }
  let mastheadTypeLabel=$derived(masthead ? lifecycleFor(manifest.data,masthead.worker_type)?.workerTypeLabel ?? labelize(masthead.worker_type) : '');
  $effect(()=>{
    const view=currentView;
    const source=currentMasthead;
    const header=masthead;
    const root=mastheadElement;
    const generation=navigation.state.generation;
    const typeLabel=mastheadTypeLabel;
    if(!view?.runtimeCurrent || !source || source!==heldMasthead || !header || !root) return;
    const observation=mastheadObserver.observeSelectors(view.publication,generation);
    let obsolete=false;
    void tick().then(()=>{
      if(obsolete || !root.isConnected || navigation.state.generation!==generation || navigation.selectors.effective!==view) return;
      if(ticketMastheadDomMatches(root,header,typeLabel)) mastheadObserver.acknowledgeRender(view.publication,observation);
    });
    return ()=>{obsolete=true;};
  });

  // The pointer footprint belongs to the mounted pane, independent of section controls.
  const conversationObserver = navigation.registerRenderBoundary(`ticket-conversation:${crypto.randomUUID()}`, impact =>
    impact.kind === 'ticket' && impact.id === stableId
      && (impact.fields === '*' || impact.fields.includes('conversation_id')));
  onDestroy(() => navigation.disposeRenderBoundary(conversationObserver));
  $effect(() => {
    const view = currentView;
    const source = canonicalTicket;
    const root = screenElement;
    const detail = ticket.data;
    const shown = section;
    const pointer = conversationId;
    const generation = navigation.state.generation;
    if (!view?.runtimeCurrent || !source || !root || !detail || !shown
      || pointer !== source.value.conversation_id) return;
    const token = conversationObserver.observeSelectors(view.publication, generation);
    let obsolete = false;
    void tick().then(() => {
      if (obsolete || !root.isConnected || navigation.selectors.effective !== view
        || navigation.state.generation !== generation || conversationId !== pointer) return;
      const pane = root.querySelector('[data-conversation-pane]');
      if (pane?.getAttribute('data-conversation-id') === (pointer ?? '')) {
        conversationObserver.acknowledgeRender(view.publication, token);
      }
    });
    return () => { obsolete = true; };
  });

  const sectionObserver=navigation.registerRenderBoundary(`ticket-section:${crypto.randomUUID()}`,impact=>ticketSectionImpact(impact,canonicalTicket));
  onDestroy(()=>navigation.disposeRenderBoundary(sectionObserver));
  let sectionElement=$state<HTMLElement | null>(null);
  let sectionDisclosureVersion=$state(0);
  function sectionObservationEvents(node:HTMLElement) {
    const changed=()=>{sectionDisclosureVersion+=1;};
    const events=['click','keydown','input','toggle'];
    for(const event of events) node.addEventListener(event,changed,true);
    return {destroy(){for(const event of events) node.removeEventListener(event,changed,true);}};
  }
  $effect(()=>{
    const view=currentView,shown=section,root=sectionElement,header=mastheadElement,generation=navigation.state.generation;
    sectionDisclosureVersion;
    if(!view?.runtimeCurrent || !shown || !admittedSection || shown!==admittedSection || !root || !header) return;
    const token=sectionObserver.observeSelectors(view.publication,generation);
    let obsolete=false;
    void tick().then(()=>{
      if(obsolete || !root.isConnected || navigation.selectors.effective!==view || navigation.state.generation!==generation) return;
      if(ticketSectionDomMatches(root,header,shown,view)) sectionObserver.acknowledgeRender(view.publication,token);
    });
    return ()=>{obsolete=true;};
  });

</script>

<svelte:window onkeydown={closeFileOnEscape} />

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
  onclick={openManagedFileInPlace}
>
  <ResourceState error={ticket.error ?? (navigationErrorHandledByParent ? null : navigation.state.error)} loading={ticket.isFetching || !masthead || !section} hasData={Boolean(ticket.data && masthead && (section || manifest.error || manifestMissingWorkerType))} loadingText="Loading ticket...">
    {#if ticket.data && (manifest.error || manifestMissingWorkerType)}
      <div class="ticket-page" data-ticket-manifest-error>
        <ErrorLine
          error={manifest.error ?? { code: "unknown_worker_type", message: `no manifest for Worker type "${ticket.data.worker_type}"` }}
        />
      </div>
    {:else if ticket.data && masthead}
      {@const detail = ticket.data}
      <div class="ticket-page">
      <div class="ticket-reading">
      <main class="ticket-doc" onclickcapture={dismissConversationToRest}>
        <header bind:this={mastheadElement} class="ticket-head" data-ticket-masthead-id={masthead.id}>
          <div class="worker-eyebrow" data-ticket-identity>
            <span data-project-fact>{masthead.project || "No project"}</span>
            <span aria-hidden="true">·</span>
            <span data-ticket-type>{mastheadTypeLabel}</span>
          </div>
          <div class="ticket-title-row">
            <div class="ticket-title" data-feedback-page-title>
              <InlineEdit
                value={masthead.title}
                placeholder="Untitled"
                onSave={(raw) => patch({ title: raw })}
              />
              <ConversationSignalMark signal={masthead.conversation_signal} conversationId={masthead.conversation_id} />
            </div>
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
                  navigation.runCommand(ticketCommandImpact(navigation.state.effective,stableId,'recap'),responseMetadata=>mutateJson(`/api/tickets/${stableId}`, {
                    method: "PATCH", body: { recap: raw },responseMetadata
                  }))}
              />
            </ClampedText>
          </div>
          {#if section}<TicketLifecycleControls ticket={section.detail} terminal={section.record.value.terminal} assertCurrent={assertCurrentSection} runCommand={(request)=>navigation.runCommand(ticketCommandImpact(navigation.state.effective,stableId,'backlog'),request)} />{/if}
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
        <div bind:this={sectionElement} use:sectionObservationEvents class="ticket-col" data-ticket-section={section.record.id} data-section-stage={section.record.value.stage}>
          {#snippet kickoffContextRow()}
            {#if detail.employee_configuration_editable}
              <WorkerConfigurationSetup ticketId={stableId} employeeBackend={detail.employee_backend}
                employeeLaunchModel={detail.employee_launch_model} employeeLaunchReasoningEffort={detail.employee_launch_reasoning_effort}
                onSave={saveEmployeeConfiguration} />
            {/if}
          {/snippet}
          {#if section}
            {@const currentField = section.record.value.gating_field}
            {@const history = workerPageHistory(lc, detail)}
            {@const completedFields = history.completed}
            {@const occurredFields = history.fields}
            {#if currentField}
              <section class:worker-live-card={detail.pending_proposal?.field !== currentField} class="worker-current" data-worker-live-card data-proposal-created-at={detail.pending_proposal?.created_at} data-proposal-revision={detail.pending_proposal?.revision}>
                {#if detail.pending_proposal?.field === currentField}
                  {#key `${detail.pending_proposal.field}:${detail.pending_proposal.created_at}:${detail.pending_proposal.revision}`}
                    <WorkerProposalCard proposal={detail.pending_proposal} lifecycle={lc} stage={detail.stage}
                      ceiling={detail.ceiling} holder={detail.ceiling_holder} sprintItem={ticketSprintItem} canReturn={Boolean(detail.conversation_id)}
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
                        holderLabel={pageHolderLabel} sprintItem={ticketSprintItem} below onComplete={(change) => void updateScope(change.ceiling === undefined
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
              <details class="worker-steps" data-stage-fold>
                <summary data-stage-fold-open>Steps<span class="band-count">{completedFields.length} of {lc.fieldIds.length} done</span></summary>
                <div class="worker-step-rows">
                  {#each occurredFields as name (`${detail.id}:${name}`)}
                    {@const stageState = ticketSectionFieldState(section,currentView,name)}
                    <TicketStageSection {name} value={detail.field_values[name] ?? ""}
                      {stageState} lifecycle={lc} ticketStage={detail.stage}
                      emptyText={emptyTicketFieldText} editableValue={true}
                      editableCurrentValue={false} readerLeftItOpen={readerStageFolds.get(name) ?? false}
                      onReaderToggle={(open) => readerStageFolds.set(name, open)}
                      runLabel={name === currentField ? ticketSectionRunLabel(section,currentView) : null}
                      onSaveValue={(raw) => saveValue(name,raw)}
                      onCompleteGate={(raw) => completeGate(name,raw)} />
                  {/each}
                </div>
              </details>
              <ArtifactsBand items={ticketPageArtifactItems(detail.id, detail.artifacts, lc.fieldIds, detail.field_values, detail.pending_proposal)} />
            </div>
          {/if}
        </div>
        {/if}
      </main>
      {#if shownFile}
        <ArtifactPreview
          target={shownFile}
          reloadSignal={artifactReloadSignal}
          bind:element={artifactElement}
          onRefresh={() => artifactReloadSignal += 1}
          onClose={() => showFile(null)}
        />
      {/if}
      </div>
      <div
        class="conversation-layer"
        data-conversation-layer-host
        onclickcapture={dismissConversationOnAPressBesideTheCard}
      >
        <div class="conversation-column">
          <LiveConversation
            bind:conversationState
            {conversationId}
            persistenceKey={`owner:ticket:${detail.id}`}
            label={conversationWorkerTypeLabel(detail)}
            composerPlaceholder={`Message ${conversationEmployeeLabel(detail)}...`}
            bind:backends={conversationBackends}
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
</section>

<style>
  .ticket-head, .ticket-col { width:min(720px, calc(100% - 48px)); padding-inline:0; }
  .ticket-col { padding-top:0; }
  .ticket-doc { scrollbar-gutter:auto; }
  .ticket-head { padding-top:48px; }
  .worker-eyebrow { line-height:1.6; display:flex; align-items:center; gap:var(--space-2); font-size:13px; color:var(--text-faintest); }
  .ticket-title-row { display:block; margin-top:var(--space-3); }
  .ticket-title { display:flex; align-items:center; gap:var(--space-3); font-family:var(--font-ui); font-size:28px; font-weight:600; line-height:1.24; letter-spacing:-.014em; }
  .ticket-title :global(.ed) { font:inherit; color:var(--text-strong); }
  .ticket-head :global([data-recap]) { margin-top:var(--space-3); max-width:66ch; font-family:var(--font-ui); font-size:17px; line-height:1.6; color:var(--text-faint); }
  .ticket-head :global([data-recap] .ed) { font:inherit; }
  .worker-current { margin-top:var(--space-6); }
  .worker-live-card { padding:var(--space-5); background:var(--surface-2); border-radius:var(--radius-lg); }
  .worker-live-card h2 { margin:var(--space-2) 0 0; letter-spacing:-.006em; font-size:20px; font-weight:600; line-height:1.35; color:var(--text-strong); }
  .worker-ceiling-line { margin-top:var(--space-3); }
  .worker-ceiling-line :global([data-scope-ceiling]) { font-family:var(--font-ui); font-size:15px; line-height:1.3; color:var(--text-muted); padding:10px var(--space-3); margin-inline:calc(-1 * var(--space-3)); border:0; border-radius:var(--radius-sm); background:transparent; box-shadow:none; text-align:left; }
  .worker-ceiling-line :global([data-scope-ceiling]:hover) { background-image:var(--interaction-hover); }
  .worker-bands { line-height:1.6; margin-top:var(--space-7); padding-bottom:var(--space-7); }
  .worker-steps { border-top:1px solid var(--border-color); }
  .worker-steps > summary { display:flex; align-items:center; gap:var(--space-3); padding:14px var(--space-2); margin-inline:calc(-1 * var(--space-2)); border-radius:var(--radius-sm); font-size:13px; font-weight:600; color:var(--text-faint); cursor:pointer; list-style:none; }
  .worker-steps > summary::-webkit-details-marker { display:none; }
  .worker-steps > summary:hover { background-image:var(--interaction-hover); color:var(--text-strong); }
  .band-count { margin-left:auto; font-weight:400; color:var(--text-faintest); }
  .worker-step-rows { padding-bottom:var(--space-4); }
  .worker-step-rows :global(.disclosure--stage) { margin:0; border:0; }
  .worker-step-rows :global(.disclosure--stage > .disclosure-summary) { padding:9px var(--space-2); margin-inline:calc(-1 * var(--space-2)); border-radius:var(--radius-sm); font-size:15px; color:var(--text-muted); }
  .worker-step-rows :global(.disclosure--stage > .disclosure-summary:hover) { background-image:var(--interaction-hover); color:var(--text-strong); }
  .worker-step-rows :global(.disclosure--stage > .disclosure-body) { padding:var(--space-2) var(--space-2) var(--space-5); font-family:var(--font-ui); font-size:15px; color:var(--text-faint); }
  .worker-step-rows :global(.disclosure-stage-name) { text-transform:none; font-size:15px; letter-spacing:normal; }
</style>
