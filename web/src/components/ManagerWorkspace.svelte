<script lang="ts">
  import { selectedRecap } from "../lib/navigation/views/recap.svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { onMount, onDestroy } from "svelte";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import {hasTickets} from '../lib/pageKit/holderDocument';
  import PageTickets from "./pageKit/PageTickets.svelte";
  import HolderDocument from "./pageKit/HolderDocument.svelte";
  import GoalHeader from "./goals/GoalHeader.svelte";
  import GoalContent from "./goals/GoalContent.svelte";
  import GoalsOverview from "./goals/GoalsOverview.svelte";
  import {goalsForHolder} from "../lib/goals/holders";
  import {holderPageQuery} from "../lib/pageKit/holderEditing";
  import {refreshAtBoundary} from "../lib/goals/presentation";
  import {queryClient} from "../lib/queryClient";
  import ConversationSignalMark from "./ConversationSignalMark.svelte";
  import { createRailHoverController, listenForUserScroll } from "../lib/workspaceRailInteraction";
  import WorkspaceRailHover from "./WorkspaceRailHover.svelte";
  import type { WorkItemTicketFacts } from "../lib/workItemPresentation";
  import { navigationApplication } from "../lib/navigation/application.svelte";
  import type { EffectiveNavigationView } from "../lib/navigation/knowledge";

  import { workspaceWorkerStageSentence } from "../lib/workspaceRail";

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
  import type {ManagedFileTarget} from "../lib/filePreview";
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
    openedFile = null,
    onOpenFile,
    backHref = "#/workspace/chief-of-staff",
    workspaceResource = null
    // A host that is already a way back can pass null so the line is not drawn.
  }: {
    itemId: string;
    requestedConversationId?: string | null;
    openedFile?:ManagedFileTarget|null;
    onOpenFile?:(file:ManagedFileTarget|null)=>void;
    workspaceName: string;
    backHref?: string | null;
    workspaceResource?: ManagerWorkspaceResource | null;
  } = $props();

  const holderPage=createQuery(()=>holderPageQuery({kind:'manager',id:itemId}));
  const ownWorkspace = createQuery(() => ({
    ...queries.managerWorkspaceContent(itemId),
    enabled: workspaceResource === null
  }));
  const navigation=(()=>{
    const context=navigationApplication();
    if(!context) throw new Error('ManagerWorkspace requires the application navigation owner');
    return context;
  })();

  let displayedView=$state.raw<EffectiveNavigationView | undefined>(undefined);
  $effect(()=>{const view=navigation.selectors.effective;if(view) displayedView=view;});
  let shared=$derived(displayedView ? navigation.presentationFor(displayedView) : undefined);
  let rawPublicItem=$derived(shared?.managersById.get(itemId));
  const fullRecap=selectedRecap(()=>rawPublicItem?.recap_reference,()=>true);
  let publicItem=$derived(rawPublicItem?.recap_reference && fullRecap.body===undefined?undefined:rawPublicItem?{...rawPublicItem,recap:fullRecap.body ?? rawPublicItem.recap}:undefined);
  let content=$derived(workspaceResource ?? ownWorkspace);
  let held=$derived(goalsForHolder(content.data?.goals??[],{kind:'manager',id:itemId}));
  let soleGoal=$derived(held.sole);
  $effect(()=>{if(content.data?.goals.length)return refreshAtBoundary(Math.min(...content.data.goals.map(goal=>goal.next_refresh_at)),()=>void queryClient.invalidateQueries({queryKey:queries.managerWorkspaceContent(itemId).queryKey}));});
  let workspace=$derived({data:publicItem && content.data ? {...publicItem,...content.data} : undefined,
    error:fullRecap.error ?? content.error,isLoading:content.isLoading || !publicItem});
  let headerElement=$state<HTMLElement | null>(null);
  let hoverElement=$state<HTMLDivElement | null>(null);

  const startValues = createQuery(() => queries.managerConversationStartValues(itemId));
  let conversationId = $state<string | null>(null);
  let selectedPastConversationId = $state<string | null>(null);
  let selectedConversationId = $derived(selectedPastConversationId ?? conversationId);
  let historyOwner=$derived({kind:'manager' as const,id:itemId});
  $effect(()=>navigation.owner.setDemand('manager',{selected:historyOwner,selected_conversation_id:selectedPastConversationId,windows:[]}));
  onDestroy(()=>navigation.owner.setDemand('manager',undefined));
  let conversationState = $state<ConversationState>("rest");
  let requestedConversationApplied: string | null | undefined;
  $effect(() => {
    if (!workspace.data || requestedConversationApplied === requestedConversationId) return;
    requestedConversationApplied = requestedConversationId;
    selectedPastConversationId = requestedConversationId && requestedConversationId !== workspace.data.conversation_id ? requestedConversationId : null;
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
  // A touch hold keeps the preview after the finger lifts (createRailPreviewInput), so a
  // press outside it and its row, or the person's own scrolling, is what dismisses it.
  onMount(() => {
    const outside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (hoverVisible && !hoverElement?.contains(target) && !hoverTarget?.contains(target)) hover.leave();
    };
    window.addEventListener("pointerdown", outside);
    const stopUserScroll = listenForUserScroll(window, event => {
      const target = event.target as Node | null;
      // A key on the previewed row itself moves focus, which opens the next preview.
      if (hoverElement?.contains(target) || (event.type === "keydown" && hoverTarget?.contains(target))) return;
      hover.leave();
    });
    return () => { window.removeEventListener("pointerdown", outside); stopUserScroll(); };
  });
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
    return mutateJson(`/api/managers/${encodeURIComponent(itemId)}`, {method:"PATCH",body:{[field]:value}});
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
      await mutateJson(`/api/managers/${encodeURIComponent(itemId)}/settled`, {method:"PATCH",body:{settled}});
    } catch (error) {
      settleError = error instanceof Error ? error.message : "The manager could not be updated.";
    } finally {
      settleBusy = false;
    }
  }

  async function sendMessage(body: OwnerSendBody): Promise<DeliveredMessage> {
    const delivered = await mutateJson<DeliveredMessage>(
      `/api/managers/${encodeURIComponent(itemId)}/conversation/send`, {method:"POST",body});
    conversationId = delivered.conversation_id;
    return delivered;
  }

  async function newConversation(): Promise<void> {
    await mutateJson(
      `/api/managers/${encodeURIComponent(itemId)}/conversation/reset`, {method:"POST"});
    conversationId = null;
  }

</script>

<ManagerFrame
  class="manager-resource-workspace"
  {openedFile} {onOpenFile}
  bind:conversationState
  data-manager-workspace={itemId}
  data-manager-view={itemId}
>
    <ResourceState shape="document"
      error={workspace.error}
      loading={workspace.isLoading}
      hasData={workspace.data !== undefined}
      loadingText="Loading manager…"
    >
      {#if workspace.data}
        {@const item = workspace.data}
          <div bind:this={headerElement}>
          {#snippet managerTitle()}<InlineEdit value={item.title} placeholder="(untitled Manager)" ariaLabel="Manager name" onSave={(value)=>saveItem("title",value)}/>{/snippet}
          {#snippet settleButton()}
            <button type="button" class="page-settle" disabled={settleBusy} onclick={()=>void setSettled(!(item.settled??false))} aria-label={`${item.settled?'Reopen':'Settle'} ${item.title}`}>
              <svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>{item.settled?'Reopen':'Settle'}
            </button>
          {/snippet}
          {#snippet goalHeading()}
            {#if soleGoal}<GoalHeader goal={soleGoal}>
              {#snippet title()}{@render managerTitle()}<ConversationSignalMark signal={item.conversation_signal} conversationId={item.conversation_id}/>{/snippet}
              {#snippet mark()}{@render settleButton()}{/snippet}
            </GoalHeader>{/if}
          {/snippet}
          <ManagerHeader heading={soleGoal?goalHeading:undefined} signal={item.conversation_signal} conversationId={item.conversation_id}
            recap={item.recap} onSaveRecap={(value)=>saveItem("recap",value)} recapTarget={{kind:"manager",id:item.id}}>
            {#snippet eyebrow()}<div class="page-eyebrow">{#if backHref!==null}<a class="page-back" href={backHref} aria-label={`Back to ${workspaceName}`}>{workspaceName}</a>{/if}<span>{item.project}</span>
              {@render settleButton()}
            </div>{/snippet}
            {#snippet title()}{@render managerTitle()}{/snippet}
          </ManagerHeader>
          {#if settleError}<p role="alert">{settleError}</p>{/if}
          </div>

          {#if soleGoal}<GoalContent goal={soleGoal}/>
          {:else}
            {#if held.held.length>1}<GoalsOverview goals={item.goals} holderKind="manager" holderId={itemId}/>{/if}
            {#if holderPage.data?.page}<HolderDocument holder={{kind:"manager",id:itemId}}/>{/if}
          {/if}
          {#if !hasTickets(soleGoal?soleGoal.page:holderPage.data?.page)}<section class="manager-workers" aria-label="Workers">
            <PageTickets principal={{ kind: "manager", id: itemId }} publication={displayedView?.publication}
              onHover={(element, id, immediate) => {
                const ticket = shared?.ticketsByParent.get(`manager:${itemId}`)?.find(candidate=>candidate.id===id);
                if (ticket) hoverRow(element, ticket, immediate);
              }} onLeave={() => hover.leave()} />
          </section>{/if}
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
  {#snippet conversation()}
      <LiveConversation {historyOwner}
        bind:conversationState
        conversationId={selectedConversationId}
        persistenceKey={`owner:sprint-item:${itemId}`}
        uploadOwner={{kind:"manager",id:itemId}}
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
  /* The same breadcrumb voice as every page: muted words with dot separators. */
  .page-eyebrow { display:flex; align-items:center; gap:6px; min-height:24px; font-size:var(--type-sm); line-height:20px; color:var(--text-faint); }
  .page-eyebrow a { color:inherit; text-decoration:none; text-underline-offset:3px; }
  .page-back + span::before { content:"·"; margin-right:6px; color:var(--text-faintest); }
  /* Beside the rail the line above the title is the project alone, the same word the rail
     shows above this Manager's name; the rail is the way back. A phone has no rail, so
     there the line keeps its way back to the Workspace. */
  @media (min-width:721px) { .page-back, .page-back + span::before { display:none; } }
  .page-eyebrow a:hover { color:var(--text-strong); text-decoration-line:underline; }
  .page-settle { margin-left:auto; display:inline-flex; align-items:center; gap:6px; height:24px; padding:0 var(--space-2); margin-right:calc(var(--space-2) * -1); border:0; border-radius:var(--radius-md); background:none; color:inherit; font:inherit; cursor:pointer; opacity:0; }
  .page-settle svg { width:13px; height:13px; }
  :global(.manager-doc):hover .page-settle, .page-settle:focus-visible { opacity:1; }
  .page-settle:hover { background-image:var(--interaction-hover); color:var(--text-strong); }
  .manager-workers { margin-top:var(--space-5); }
  .manager-bands { margin-top:var(--space-5); }
  .manager-goal { font-size:var(--type-md); line-height:1.6; color:var(--text-muted); max-width:66ch; }
  .goal-chevron { font-size:var(--type-md); line-height:1; }
  .goal-chevron::before { content:"\203A"; }
  .goal-band[open] > summary .goal-chevron::before { content:"\2304"; }
  a:focus-visible, button:focus-visible, .manager-workers :global(summary:focus-visible) { outline:var(--focus-width) solid var(--focus-ring); outline-offset:2px; }
</style>
