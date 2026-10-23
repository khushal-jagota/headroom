<script lang="ts">
  import { selectedRecap } from "../lib/navigation/views/recap.svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { onMount, onDestroy, tick } from "svelte";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import ManagerChildren from "./ManagerChildren.svelte";
  import { createRailHoverController } from "../lib/workspaceRailInteraction";
  import WorkspaceRailHover from "./WorkspaceRailHover.svelte";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  import { navigationApplication } from "../lib/navigation/application.svelte";
  import type { EffectiveNavigationView } from "../lib/navigation/knowledge";
  import { managerHeaderImpact, workspaceHoverImpact, managerEditImpact, managerSettleImpact, conversationOwnerImpact } from "../lib/workspaceNavigationImpact";
  import { hoverDomMatches } from "../lib/workspaceRailDom";
  import { workspaceHeaderDomMatches } from "../lib/managerWorkspaceDom";
  import { lifecycleFor } from "../lib/lifecycle";
  import { workspaceWorkerStageSentence } from "../lib/workspaceRail";
  import type { RenderObserver } from "../lib/navigation/visibility";
  import type { ConversationState } from "../lib/conversation/conversationState";
  import {
    readBackends,
    type BackendSnapshot,
    type DeliveredMessage,
    type OwnerSendBody
  } from "../lib/conversation/wire";
  import InlineEdit from "./InlineEdit.svelte";
  import LiveConversation from "./conversation/LiveConversation.svelte";
  import ResourceState from "./ResourceState.svelte";
  import ManagerHeader from "./ManagerHeader.svelte";
  import ManagerFrame from "./ManagerFrame.svelte";
  import ArtifactsBand from "./ArtifactsBand.svelte";
  import ArtifactPreview from "./ArtifactPreview.svelte";
  import {
    managedFileTargetFromLinkHref
  } from "../lib/artifactStrip";
  import { isPlainLinkClick } from "../lib/linkClick";
  import type { ManagedFileTarget } from "../lib/filePreview";
  import type { ManagerWorkspaceContent } from "../lib/types";

  export type ManagerWorkspaceResource = {
    data: ManagerWorkspaceContent | undefined;
    error: unknown;
    isLoading: boolean;
  };

  let {
    itemId,
    workspaceName,
    requestedConversationId = null,
    backHref = "#/workspace/chief-of-staff",
    workspaceResource = null,
    navigationErrorHandledByParent = false
    // A host that is already a way back can pass null so the line is not drawn.
  }: {
    itemId: string;
    requestedConversationId?: string | null;
    navigationErrorHandledByParent?: boolean;
    workspaceName: string;
    backHref?: string | null;
    workspaceResource?: ManagerWorkspaceResource | null;
  } = $props();

  const ownWorkspace = createQuery(() => ({
    ...queries.managerWorkspaceContent(itemId),
    enabled: workspaceResource === null
  }));
  const navigation=(()=>{
    const context=navigationApplication();
    if(!context) throw new Error('ManagerWorkspace requires the application navigation owner');
    return context;
  })();
  const headerObserver=navigation.registerRenderBoundary(`manager-header:${crypto.randomUUID()}`,impact => {
    const view=navigation.state.effective;return managerHeaderImpact(impact,itemId,view ? navigation.presentationFor(view) : undefined);
  });
  onDestroy(()=>navigation.disposeRenderBoundary(headerObserver));
  let displayedView=$state.raw<EffectiveNavigationView | undefined>(undefined);
  $effect(()=>{const view=navigation.selectors.effective;if(view) displayedView=view;});
  let shared=$derived(displayedView ? navigation.presentationFor(displayedView) : undefined);
  let rawPublicItem=$derived(shared?.managersById.get(itemId));
  const fullRecap=selectedRecap(()=>rawPublicItem?.recap_reference,()=>true);
  let publicItem=$derived(rawPublicItem?.recap_reference && fullRecap.body===undefined?undefined:rawPublicItem?{...rawPublicItem,recap:fullRecap.body ?? rawPublicItem.recap}:undefined);
  let content=$derived(workspaceResource ?? ownWorkspace);
  let workspace=$derived({data:publicItem && content.data ? {...publicItem,...content.data} : undefined,
    error:fullRecap.error ?? content.error ?? (navigationErrorHandledByParent ? null : navigation.state.error),isLoading:content.isLoading || !publicItem});
  const manifests=createQuery(()=>queries.workerTypeManifests());
  let headerElement=$state<HTMLElement | null>(null);
  let hoverElement=$state<HTMLDivElement | null>(null);
  let hoverObserver=$state.raw<RenderObserver | null>(null);
  const startValues = createQuery(() => queries.managerConversationStartValues(itemId));
  let conversationId = $state<string | null>(null);
  let selectedPastConversationId = $state<string | null>(null);
  let selectedConversationId = $derived(selectedPastConversationId ?? conversationId);
  $effect(()=>navigation.owner.setDemand('manager',{selected:{kind:'manager',id:itemId},selected_conversation_id:selectedPastConversationId,windows:[]}));
  onDestroy(()=>navigation.owner.setDemand('manager',undefined));
  let conversationState = $state<ConversationState>("rest");
  let requestedConversationApplied: string | null | undefined;
  $effect(() => {
    if (!publicItem || requestedConversationApplied === requestedConversationId) return;
    requestedConversationApplied = requestedConversationId;
    selectedPastConversationId = requestedConversationId && requestedConversationId !== publicItem.conversation_id ? requestedConversationId : null;
    if (requestedConversationId) conversationState = "opened";
  });
  let backends = $state<readonly BackendSnapshot[]>([]);

  let hoverTarget = $state<HTMLElement | null>(null);
  let hoverWorkerId = $state<string | null>(null);
  let rawHoverWorker = $derived(shared?.ticketsByParent.get(`manager:${itemId}`)?.find(ticket=>ticket.id===hoverWorkerId) ?? null);
  let hoverVisible = $state(false);
  const hoverBody=selectedRecap(()=>rawHoverWorker?.recap_reference,()=>hoverVisible);
  let hoverWorker=$derived(rawHoverWorker && hoverBody.body!==undefined?{...rawHoverWorker,recap:hoverBody.body}:rawHoverWorker);
  let hoverInstant = $state(false);
  const hover = createRailHoverController<{target:HTMLElement; ticket:WorkItemTicketFacts}>((value, instant) => {
    if (!value.target.isConnected) return;
    hoverTarget = value.target; hoverWorkerId = value.ticket.id;
    hoverVisible = true; hoverInstant = instant;
  }, () => hoverVisible = false);
  onDestroy(() => hover.dispose());
  function hoverRow(target: HTMLElement, ticket: WorkItemTicketFacts, immediate = false): void {
    hover.enter({target, ticket}, immediate);
  }
  let observedPointer=$state<string | null>(null);
  $effect(() => {
    const item=publicItem;
    if(!item) return;
    const identity=JSON.stringify([itemId,item.conversation_id]);
    if(identity!==observedPointer) {
      observedPointer=identity;
      conversationId=item.conversation_id;
    }
  });

  onMount(() => {
    void readBackends()
      .then((snapshots) => (backends = snapshots))
      .catch(() => undefined);
  });

  function saveItem(field: "title" | "recap", value: string): Promise<unknown> {
    return navigation.runCommand(managerEditImpact(itemId,field),responseMetadata =>
      mutateJson(`/api/managers/${encodeURIComponent(itemId)}`, {method:"PATCH",body:{[field]:value},responseMetadata}),`edit manager ${field}`);
  }

  // No navigation record carries the goal: the rail and the board show the recap. So this
  // takes the plain mutation route, like the Chief's recap, and the ordinary invalidation
  // refreshes the page.
  function saveGoal(value: string): Promise<unknown> {
    return mutateJson(`/api/managers/${encodeURIComponent(itemId)}`, {method:"PATCH",body:{goal:value}});
  }

  let settleBusy = $state(false);
  let settleError = $state("");
  async function setSettled(settled: boolean): Promise<void> {
    settleBusy = true;
    settleError = "";
    try {
      await navigation.runCommand(managerSettleImpact(itemId),responseMetadata =>
        mutateJson(`/api/managers/${encodeURIComponent(itemId)}/settled`, {method:"PATCH",body:{settled},responseMetadata}),"set manager settled");
    } catch (error) {
      settleError = error instanceof Error ? error.message : "The manager could not be updated.";
    } finally {
      settleBusy = false;
    }
  }

  async function sendMessage(body: OwnerSendBody): Promise<DeliveredMessage> {
    const view=navigation.state.effective;
    const delivered = await navigation.runCommand(conversationOwnerImpact(view ? navigation.presentationFor(view) : undefined,'manager',itemId),responseMetadata => mutateJson<DeliveredMessage>(
      `/api/managers/${encodeURIComponent(itemId)}/conversation/send`, {method:"POST",body,responseMetadata}),"send manager message");
    conversationId = delivered.conversation_id;
    return delivered;
  }

  async function newConversation(): Promise<void> {
    const view=navigation.state.effective;
    await navigation.runCommand(conversationOwnerImpact(view ? navigation.presentationFor(view) : undefined,'manager',itemId),responseMetadata => mutateJson(
      `/api/managers/${encodeURIComponent(itemId)}/conversation/reset`, {method:"POST",responseMetadata}),"reset manager conversation");
    conversationId = null;
  }

  let shownFile = $state<ManagedFileTarget | null>(null);
  let artifactReloadSignal = $state(0);
  let artifactElement = $state<HTMLElement | null>(null);
  let whatHadFocus: HTMLElement | null = null;

  function showFile(file: ManagedFileTarget | null): void {
    if (file && !shownFile) {
      whatHadFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }
    shownFile = file;
    if (file && conversationState === "opened") conversationState = "peeked";
    if (!file) {
      const restoreFocus = whatHadFocus;
      whatHadFocus = null;
      queueMicrotask(() => restoreFocus?.focus());
    }
  }

  $effect(() => {
    if (shownFile && artifactElement) artifactElement.focus();
  });

  function openManagedFileInPlace(event: MouseEvent): void {
    if (event.defaultPrevented) return;
    const anchor = (event.target as Element | null)?.closest?.("a[href]");
    if (!anchor) return;
    const link = { href: anchor.getAttribute("href"), target: anchor.getAttribute("target") };
    if (!isPlainLinkClick(link, event)) return;
    const file = managedFileTargetFromLinkHref(link.href);
    if (!file) return;
    event.preventDefault();
    showFile(file);
  }

  function closeFileOnEscape(event: KeyboardEvent): void {
    if (event.key !== "Escape" || event.defaultPrevented || !shownFile) return;
    event.preventDefault();
    showFile(null);
  }
  $effect(()=>{
    const view=navigation.selectors.effective;
    const generation=navigation.state.generation;
    const root=headerElement;
    const item=publicItem;
    if(!view?.runtimeCurrent || view!==displayedView || !root || !item) return;
    const observation=headerObserver.observeSelectors(view.publication,generation);
    let obsolete=false;
    void tick().then(()=>{
      if(obsolete || !root.isConnected || navigation.state.generation!==generation || navigation.selectors.effective!==view) return;
      if(workspaceHeaderDomMatches(root,item)) headerObserver.acknowledgeRender(view.publication,observation);
    });
    return ()=>{obsolete=true;};
  });
  $effect(()=>{
    if(!hoverVisible || !hoverElement) return;
    const observer=navigation.registerRenderBoundary(`manager-hover:${crypto.randomUUID()}`,impact => {
      const view=navigation.state.effective;return hoverWorkerId!==null && workspaceHoverImpact(impact,'ticket',hoverWorkerId,hoverWorker?.conversation_id,view ? navigation.presentationFor(view) : undefined);
    });
    hoverObserver=observer;
    return ()=>{navigation.disposeRenderBoundary(observer);hoverObserver=null;};
  });
  $effect(()=>{
    const view=navigation.selectors.effective;
    const generation=navigation.state.generation;
    const root=hoverElement, target=hoverTarget,observer=hoverObserver,worker=hoverWorker;
    const parent=publicItem;
    const stage=worker ? workspaceWorkerStageSentence(worker,lifecycleFor(manifests.data,worker.worker_type)) : '';
    if(!view?.runtimeCurrent || view!==displayedView || !root || !target?.isConnected || !observer || !worker || !parent || !hoverVisible) return;
    const observation=observer.observeSelectors(view.publication,generation);
    let obsolete=false;
    void tick().then(()=>{
      if(obsolete || !root.isConnected || !target.isConnected || hoverTarget!==target || !hoverVisible
        || navigation.state.generation!==generation || navigation.selectors.effective!==view) return;
      if(hoverDomMatches(root,worker.title,worker.recap_preview ?? '',stage,worker.conversation_signal,worker.conversation_id,parent.title))
        observer.acknowledgeRender(view.publication,observation);
    });
    return ()=>{obsolete=true;};
  });

</script>

<svelte:window onkeydown={closeFileOnEscape} />

<ManagerFrame
  class="manager-resource-workspace"
  bind:conversationState
  data-manager-workspace={itemId}
  data-manager-view={itemId}
  onDocumentClick={openManagedFileInPlace}
>
    <ResourceState shape="document" layout="fill"
      error={workspace.error}
      loading={workspace.isLoading}
      hasData={workspace.data !== undefined}
      loadingText="Loading manager…"
    >
      {#if workspace.data}
        {@const item = workspace.data}
          <div bind:this={headerElement}>
          <ManagerHeader signal={item.conversation_signal} conversationId={item.conversation_id}
            recap={item.recap} onSaveRecap={(value) => saveItem("recap", value)}
            recapTarget={{ kind: "manager", id: item.id }}>
            {#snippet eyebrow()}
            <div class="page-eyebrow">
              <span>{item.project}</span>
              {#if backHref !== null}<span aria-hidden="true">/</span><a href={backHref} aria-label={`Back to ${workspaceName}`}>↖</a>{/if}
              <button type="button" class="page-settle" disabled={settleBusy} onclick={() => void setSettled(!(item.settled ?? false))} aria-label={`${item.settled ? "Reopen" : "Settle"} ${item.title}`}>
                <svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>{item.settled ? "Reopen" : "Settle"}
              </button>
            </div>
            {#if settleError}<p role="alert">{settleError}</p>{/if}
            {/snippet}
            {#snippet title()}
              <InlineEdit
                value={item.title}
                placeholder="(untitled Manager)"
                onSave={(value) => saveItem("title", value)}
              />
            {/snippet}
          </ManagerHeader>
          </div>

          <section class="manager-workers" aria-label="Workers">
            <ManagerChildren navigationErrorHandledByParent principal={{ kind: "manager", id: itemId }} publication={displayedView?.publication} tickets={shared?.ticketsByParent.get(`manager:${itemId}`) ?? []}
              onHover={(element, id, immediate) => {
                const ticket = shared?.ticketsByParent.get(`manager:${itemId}`)?.find(candidate=>candidate.id===id);
                if (ticket) hoverRow(element, ticket, immediate);
              }} onLeave={() => hover.leave()} />
          </section>
          <div class="manager-bands disclosure-bands">
            <details class="disclosure-band goal-band" data-goal-band>
              <summary>Goal<span class="disclosure-band-trailing goal-chevron" aria-hidden="true"></span></summary>
              <div class="disclosure-band-body">
                <div class="manager-goal">
                  <InlineEdit
                    value={item.goal}
                    markdown
                    multiline
                    ariaLabel="Manager goal"
                    placeholder="Write what this manager is for…"
                    onSave={saveGoal}
                  />
                </div>
              </div>
            </details>
            <ArtifactsBand owner={{kind:"manager",id:itemId}} />
          </div>
      {/if}
    </ResourceState>
  {#snippet readingAside()}
  {#if shownFile}
    <ArtifactPreview
      target={shownFile}
      reloadSignal={artifactReloadSignal}
      bind:element={artifactElement}
      onRefresh={() => artifactReloadSignal += 1}
      onClose={() => showFile(null)}
    />
  {/if}
  {/snippet}
  {#snippet conversation()}
      <LiveConversation historyOwner={{kind:"manager",id:itemId}}
        bind:conversationState
        conversationId={selectedConversationId}
        persistenceKey={`owner:sprint-item:${itemId}`}
        readOnly={selectedPastConversationId !== null}
        bind:selectedPastConversationId
        label="Manager"
        composerPlaceholder="Message this manager…"
        bind:backends
        startValues={startValues.data ?? null}
        senderLabel="owner"
        {sendMessage}
        onNewConversation={newConversation}
      />
  {/snippet}
</ManagerFrame>

<WorkspaceRailHover bind:element={hoverElement} visible={hoverVisible} instant={hoverInstant} target={hoverTarget} title={hoverWorker?.title ?? ""} kicker={workspace.data?.title ?? "Manager"} worker={hoverWorker} cards={[]} rail={hoverTarget} recapTarget={hoverWorker ? { kind: "ticket", id: hoverWorker.id } : null} onKeepOpen={() => hover.keepOpen()} onLeave={() => hover.leave()} />
<style>
  :global(.manager-resource-workspace .manager-column) { height:100%; min-height:0; display:flex; flex-direction:column; }
  .page-eyebrow { display:flex; align-items:center; gap:var(--space-2); font-size:13px; color:var(--text-faintest); }
  .page-eyebrow a { color:inherit; text-decoration:none; }
  .page-eyebrow a:hover { color:var(--text-strong); }
  .page-settle { margin-left:auto; display:inline-flex; align-items:center; gap:6px; padding:3px 8px; margin-right:-8px; border:0; border-radius:var(--radius-sm); background:none; color:inherit; font:inherit; cursor:pointer; opacity:0; }
  .page-settle svg { width:13px; height:13px; }
  :global(.manager-doc):hover .page-settle, .page-settle:focus-visible { opacity:1; }
  .page-settle:hover { background-image:var(--interaction-hover); color:var(--text-strong); }
  .manager-workers { margin-top:var(--space-5); }
  .manager-bands { margin-top:var(--space-5); }
  .manager-goal { font-size:var(--type-md); line-height:1.6; color:var(--text-faint); max-width:66ch; }
  .goal-chevron { font-size:var(--type-md); line-height:1; }
  .goal-chevron::before { content:"\203A"; }
  .goal-band[open] > summary .goal-chevron::before { content:"\2304"; }
  a:focus-visible, button:focus-visible, .manager-workers :global(summary:focus-visible) { outline:var(--border-strong) solid var(--accent-bright); outline-offset:2px; }
</style>
