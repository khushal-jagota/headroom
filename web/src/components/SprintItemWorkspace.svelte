<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { onMount, onDestroy } from "svelte";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import OrchestratorChildren from "./OrchestratorChildren.svelte";
  import { createRailHoverController } from "../lib/workspaceRailInteraction";
  import WorkspaceRailHover from "./WorkspaceRailHover.svelte";
  import type { SprintItemWorkspaceTicket } from "../lib/types";
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
  import OrchestratorHeader from "./OrchestratorHeader.svelte";
  import OrchestratorWorkspace from "./OrchestratorWorkspace.svelte";
  import ArtifactsBand from "./ArtifactsBand.svelte";
  import ArtifactPreview from "./ArtifactPreview.svelte";
  import {
    managedFileTargetFromLinkHref,
    sprintItemArtifactStripItems
  } from "../lib/artifactStrip";
  import { isPlainLinkClick } from "../lib/linkClick";
  import type { ManagedFileTarget } from "../lib/filePreview";
  import type { SprintItemWorkspace as SprintItemWorkspaceData } from "../lib/types";

  export type SprintItemWorkspaceResource = {
    data: SprintItemWorkspaceData | undefined;
    error: unknown;
    isLoading: boolean;
  };

  let {
    itemId,
    sprintName,
    backHref = "#/sprint",
    workspaceResource = null
    // A host that is already a way back can pass null so the line is not drawn.
  }: {
    itemId: string;
    sprintName: string;
    backHref?: string | null;
    workspaceResource?: SprintItemWorkspaceResource | null;
  } = $props();

  const ownWorkspace = createQuery(() => ({
    ...queries.sprintItemWorkspace(itemId),
    enabled: workspaceResource === null
  }));
  let workspace = $derived(workspaceResource ?? ownWorkspace);
  const startValues = createQuery(() => queries.sprintItemConversationStartValues(itemId));
  let conversationId = $state<string | null>(null);
  let selectedPastConversationId = $state<string | null>(null);
  let selectedConversationId = $derived(selectedPastConversationId ?? conversationId);
  /** Every conversation this Item kept except the one it is on. Only this screen can say
   *  which that is, so the filtering is here rather than in the conversation. */
  let pastConversations = $derived(
    (workspace.data?.conversation_history ?? []).filter(
      (entry) => entry.conversation_id !== conversationId
    )
  );
  let conversationState = $state<ConversationState>("rest");
  let backends = $state<readonly BackendSnapshot[]>([]);

  let hoverTarget = $state<HTMLElement | null>(null);
  let hoverWorkerId = $state<string | null>(null);
  let hoverWorker = $derived(workspace.data?.tickets.find((ticket) => ticket.id === hoverWorkerId) ?? null);
  let hoverVisible = $state(false);
  let hoverInstant = $state(false);
  const hover = createRailHoverController<{target:HTMLElement; ticket:SprintItemWorkspaceTicket}>((value, instant) => {
    if (!value.target.isConnected) return;
    hoverTarget = value.target; hoverWorkerId = value.ticket.id;
    hoverVisible = true; hoverInstant = instant;
  }, () => hoverVisible = false);
  onDestroy(() => hover.dispose());
  function hoverRow(target: HTMLElement, ticket: SprintItemWorkspaceTicket, immediate = false): void {
    hover.enter({target, ticket}, immediate);
  }
  $effect(() => {
    if (workspace.data) conversationId = workspace.data.supervisor.conversation_id;
  });

  onMount(() => {
    void readBackends()
      .then((snapshots) => (backends = snapshots))
      .catch(() => undefined);
  });

  function saveItem(field: "title" | "body" | "priority", value: string): Promise<unknown> {
    return mutateJson(`/api/items/${encodeURIComponent(itemId)}`, {
      method: "PATCH",
      body: { [field]: value }
    });
  }

  let settleBusy = $state(false);
  let settleError = $state("");
  async function setSettled(settled: boolean): Promise<void> {
    settleBusy = true;
    settleError = "";
    try {
      await mutateJson(`/api/items/${encodeURIComponent(itemId)}/settled`, { method: "PATCH", body: { settled } });
    } catch (error) {
      settleError = error instanceof Error ? error.message : "The orchestrator could not be updated.";
    } finally {
      settleBusy = false;
    }
  }

  async function sendMessage(body: OwnerSendBody): Promise<DeliveredMessage> {
    const delivered = await mutateJson<DeliveredMessage>(
      `/api/items/${encodeURIComponent(itemId)}/conversation/send`,
      { method: "POST", body }
    );
    conversationId = delivered.conversation_id;
    return delivered;
  }

  async function newConversation(): Promise<void> {
    await mutateJson(
      `/api/items/${encodeURIComponent(itemId)}/conversation/reset`,
      { method: "POST" }
    );
    conversationId = null;
  }

  let artifactRows = $derived(
    workspace.data ? sprintItemArtifactStripItems(workspace.data.id, workspace.data.artifacts) : []
  );
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
</script>

<svelte:window onkeydown={closeFileOnEscape} />

<OrchestratorWorkspace
  bind:conversationState
  data-sprint-item-workspace={itemId}
  data-sprint-item-view={itemId}
  onDocumentClick={openManagedFileInPlace}
>
    <ResourceState
      error={workspace.error}
      loading={workspace.isLoading}
      hasData={workspace.data !== undefined}
      loadingText="Loading orchestrator…"
    >
      {#if workspace.data}
        {@const item = workspace.data}
          <OrchestratorHeader signal={item.conversation_signal} conversationId={item.supervisor.conversation_id}
            recap={item.body} onSaveRecap={(value) => saveItem("body", value)}>
            {#snippet eyebrow()}
            <div class="page-eyebrow">
              <span>{item.project}</span>
              {#if backHref !== null}<span aria-hidden="true">/</span><a href={backHref} aria-label={`Back to ${sprintName}`}>↖</a>{/if}
              <button type="button" class="page-settle" disabled={settleBusy} onclick={() => void setSettled(!(item.settled ?? false))} aria-label={`${item.settled ? "Reopen" : "Settle"} ${item.title}`}>
                <svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>{item.settled ? "Reopen" : "Settle"}
              </button>
            </div>
            {#if settleError}<p role="alert">{settleError}</p>{/if}
            {/snippet}
            {#snippet title()}
              <InlineEdit
                value={item.title}
                placeholder="(untitled Outcome)"
                onSave={(value) => saveItem("title", value)}
              />
            {/snippet}
          </OrchestratorHeader>

          <section class="orchestrator-workers" aria-label="Workers">
            <OrchestratorChildren principal={{ kind: "sprint_item", id: itemId }} tickets={item.tickets}
              onHover={(element, id, immediate) => {
                const ticket = item.tickets.find((candidate) => candidate.id === id);
                if (ticket) hoverRow(element, ticket, immediate);
              }} onLeave={() => hover.leave()} />
          </section>
          <div class="orchestrator-artifacts"><ArtifactsBand items={artifactRows} /></div>
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
      <LiveConversation
        bind:conversationState
        conversationId={selectedConversationId}
        persistenceKey={`owner:sprint-item:${itemId}`}
        readOnly={selectedPastConversationId !== null}
        {pastConversations}
        bind:selectedPastConversationId
        label="Orchestrator"
        composerPlaceholder="Message this orchestrator…"
        bind:backends
        startValues={startValues.data ?? null}
        senderLabel="owner"
        {sendMessage}
        onNewConversation={newConversation}
      />
  {/snippet}
</OrchestratorWorkspace>

<WorkspaceRailHover visible={hoverVisible} instant={hoverInstant} target={hoverTarget} title={hoverWorker?.title ?? ""} kicker={workspace.data?.title ?? "Orchestrator"} worker={hoverWorker} cards={[]} rail={hoverTarget} />
<style>
  .page-eyebrow { display:flex; align-items:center; gap:var(--space-2); font-size:13px; color:var(--text-faintest); }
  .page-eyebrow a { color:inherit; text-decoration:none; }
  .page-eyebrow a:hover { color:var(--text-strong); }
  .page-settle { margin-left:auto; display:inline-flex; align-items:center; gap:6px; padding:3px 8px; margin-right:-8px; border:0; border-radius:var(--radius-sm); background:none; color:inherit; font:inherit; cursor:pointer; opacity:0; }
  .page-settle svg { width:13px; height:13px; }
  :global(.sprint-item-doc):hover .page-settle, .page-settle:focus-visible { opacity:1; }
  .page-settle:hover { background-image:var(--interaction-hover); color:var(--text-strong); }
  .orchestrator-workers { margin-top:var(--space-5); }
  .orchestrator-artifacts { margin-top:var(--space-5); }
  a:focus-visible, button:focus-visible, .orchestrator-workers :global(summary:focus-visible) { outline:var(--border-strong) solid var(--accent-bright); outline-offset:2px; }
</style>
