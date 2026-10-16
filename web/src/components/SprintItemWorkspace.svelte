<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { onMount, onDestroy } from "svelte";
  import { mutateJson } from "../lib/mutate";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import { queries } from "../lib/queryCatalogue";
  import { orchestratorPageGroups } from "../lib/orchestratorPage";
  import { createRailHoverController } from "../lib/workspaceRailInteraction";
  import WorkspaceRailHover from "./WorkspaceRailHover.svelte";
  import type { SprintItemWorkspaceTicket } from "../lib/types";
  import {
    workItemActivityMark,
    workItemActivityMarkPresentation
  } from "../lib/workItemPresentation";
  import type { ConversationState } from "../lib/conversation/conversationState";
  import {
    readBackends,
    type BackendSnapshot,
    type DeliveredMessage,
    type OwnerSendBody
  } from "../lib/conversation/wire";
  import ClampedText from "./ClampedText.svelte";
  import Disclosure from "./Disclosure.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import LiveConversation from "./conversation/LiveConversation.svelte";
  import ResourceState from "./ResourceState.svelte";
  import StageMark from "./StageMark.svelte";
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

  let groups = $derived(orchestratorPageGroups(workspace.data?.tickets ?? []));
  // Reader choices outlive a group temporarily disappearing during a refresh.
  let groupOpen = $state<Record<string, boolean>>({});
  let hoverTarget = $state<HTMLElement | null>(null);
  let hoverWorker = $state<SprintItemWorkspaceTicket | null>(null);
  let hoverVisible = $state(false);
  let hoverInstant = $state(false);
  const hover = createRailHoverController<{target:HTMLElement; ticket:SprintItemWorkspaceTicket}>((value, instant) => {
    hoverTarget = value.target; hoverWorker = value.ticket;
    hoverVisible = true; hoverInstant = instant;
  }, () => hoverVisible = false);
  onDestroy(() => hover.dispose());
  function hoverRow(target: HTMLElement, ticket: SprintItemWorkspaceTicket): void {
    hover.enter({target, ticket});
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

  function dismissConversation(event: MouseEvent): void {
    const target = event.target;
    if (target instanceof Element && target.closest("[data-conversation-pane]") === null) {
      conversationState = "rest";
    }
  }

  function dismissConversationToRest(): void {
    conversationState = "rest";
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

{#snippet statusGroups()}
  {#each groups as group (group.key)}
    <Disclosure
      variant="orchestrator-group"
      class="sprint-workspace-status"
      data-workspace-group={group.key}
      defaultOpen={groupOpen[group.key] ?? (group.key !== "done" && group.key !== "cancelled")}
      onReaderToggle={(open) => groupOpen[group.key] = open}
      chevron="none"
    >
      {#snippet summary()}
        <span class="sprint-workspace-status-label">{group.label}</span>
        <span class="sprint-workspace-count" data-group-count>{group.tickets.length}</span>
      {/snippet}
      <div class="sprint-workspace-status-body">
        {#each group.tickets as ticket (ticket.id)}
          {@const mark = workItemActivityMark(ticket)}
          {@const presentation = workItemActivityMarkPresentation(mark)}
          <a
            class="ticket-row"
            class:ticket-row--quiet={ticket.stage === "done"}
            href={workspaceAddress({ kind: "ticket", id: ticket.id, openedFromItemId: itemId })}
            data-sprint-ticket-id={ticket.id}
            data-ticket-state={presentation.state}
            data-workspace-mark={mark ?? "none"}
            onmouseenter={(event) => hoverRow(event.currentTarget, ticket)}
            onmouseleave={() => hover.leave()}
            onfocus={(event) => hoverRow(event.currentTarget, ticket)}
            onblur={() => hover.leave()}
          >
            <span class="ticket-row-title">{ticket.title}</span>
            {#if presentation.state}
              <StageMark state={presentation.state} aria-label={presentation.ariaLabel} />
            {/if}
          </a>
        {/each}
      </div>
    </Disclosure>
  {/each}
  {#if !groups.length}
    <div class="sprint-workspace-empty">No Workers</div>
  {/if}
{/snippet}

<div
  class="sprint-item-page"
  data-sprint-item-workspace={itemId}
  data-sprint-item-view={itemId}
>
  <div class="sprint-item-reading">
  <!-- Links keep their keyboard behavior. This catcher only redirects plain clicks. -->
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <main class="sprint-item-doc" onclickcapture={dismissConversationToRest} onclick={openManagedFileInPlace}>
    <ResourceState
      error={workspace.error}
      loading={workspace.isLoading}
      hasData={workspace.data !== undefined}
      loadingText="Loading orchestrator…"
    >
      {#if workspace.data}
        {@const item = workspace.data}
        {@const itemMark = workItemActivityMark(item)}
        {@const itemMarkPresentation = workItemActivityMarkPresentation(itemMark)}
        <div class="sprint-item-column">
          <header class="sprint-workspace-head">
            <div class="page-eyebrow">
              <a href={item.project_id ? `#/backlog?project=${encodeURIComponent(item.project_id)}` : "#/backlog"}>{item.project}</a>
              {#if backHref !== null}<span aria-hidden="true">/</span><a href={backHref} aria-label={`Back to ${sprintName}`}>↖</a>{/if}
              <button type="button" class="page-settle" disabled={settleBusy} onclick={() => void setSettled(!(item.settled ?? false))} aria-label={`${item.settled ? "Reopen" : "Settle"} ${item.title}`}>
                <svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>{item.settled ? "Reopen" : "Settle"}
              </button>
            </div>
            {#if settleError}<p role="alert">{settleError}</p>{/if}
            <h1 class="sprint-workspace-title" data-feedback-page-title>
              <InlineEdit
                value={item.title}
                placeholder="(untitled Outcome)"
                onSave={(value) => saveItem("title", value)}
              />
              {#if itemMarkPresentation.state}
                <StageMark
                  state={itemMarkPresentation.state}
                  class="sprint-workspace-title-mark"
                  data-workspace-mark={itemMark ?? "none"}
                  aria-hidden={itemMark === null ? "true" : undefined}
                  aria-label={itemMarkPresentation.ariaLabel}
                />
              {/if}
            </h1>
            <ClampedText
              class="sprint-workspace-brief"
              contentKey={item.body}
              moreControlAttributes={{ "data-sprint-item-brief-toggle": "" }}
              data-sprint-item-brief
            >
              <InlineEdit
                value={item.body}
                markdown
                multiline
                placeholder="Write the shared brief for this outcome…"
                onSave={(value) => saveItem("body", value)}
              />
            </ClampedText>
          </header>

          <section class="orchestrator-workers" aria-label="Workers">
            {@render statusGroups()}
          </section>
          <div class="orchestrator-artifacts"><ArtifactsBand items={artifactRows} /></div>
        </div>
      {/if}
    </ResourceState>
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
  <div class="conversation-layer" onclickcapture={dismissConversation}>
    <div class="conversation-column">
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
    </div>
  </div>
</div>

<WorkspaceRailHover visible={hoverVisible} instant={hoverInstant} target={hoverTarget} title={hoverWorker?.title ?? ""} kicker={hoverWorker?.stage === "cancelled" ? "Cancelled Worker" : "Worker"} worker={hoverWorker?.stage === "cancelled" ? null : hoverWorker} cards={[]} rail={hoverTarget} />
<style>
  .sprint-item-doc { scrollbar-gutter:auto; }
  .sprint-item-column { font-family:var(--font-ui); font-size:15px; line-height:1.6; width:min(100%, 768px); max-width:768px; margin-inline:auto; padding-inline:var(--space-5); }
  .sprint-workspace-head { padding-top:var(--space-7); }
  .page-eyebrow { display:flex; align-items:center; gap:var(--space-2); font-size:13px; color:var(--text-faintest); }
  .page-eyebrow a { color:inherit; text-decoration:none; }
  .page-eyebrow a:hover { color:var(--text-strong); }
  .page-settle { margin-left:auto; display:inline-flex; align-items:center; gap:6px; padding:3px 8px; margin-right:-8px; border:0; border-radius:var(--radius-sm); background:none; color:inherit; font:inherit; cursor:pointer; opacity:0; }
  .page-settle svg { width:13px; height:13px; }
  .sprint-item-doc:hover .page-settle, .page-settle:focus-visible { opacity:1; }
  .page-settle:hover { background-image:var(--interaction-hover); color:var(--text-strong); }
  .sprint-workspace-title { font-family:var(--font-ui); font-size:28px; font-weight:600; line-height:1.24; letter-spacing:-0.014em; margin-top:var(--space-3); overflow-wrap:anywhere; }
  .sprint-workspace-head :global(.sprint-workspace-brief) { font-family:var(--font-ui); font-size:17px; line-height:1.6; margin-top:var(--space-3); max-width:66ch; }
  .orchestrator-workers { margin-top:var(--space-5); }
  .orchestrator-workers :global(.sprint-workspace-status + .sprint-workspace-status) { margin-top:0; }
  .orchestrator-workers :global(.sprint-workspace-status > summary) { display:flex; align-items:center; gap:var(--space-3); padding:7px var(--space-3); margin:0; font-family:var(--font-ui); line-height:1.6; letter-spacing:normal; border:0; border-radius:var(--radius-md); background:var(--surface-2); font-size:13px; font-weight:600; color:var(--text-faint); list-style:none; }
  .orchestrator-workers :global(.sprint-workspace-status > summary:hover) { background-image:var(--interaction-hover); color:var(--text-strong); }
  .sprint-workspace-status-label { font-size:13px; text-transform:none; letter-spacing:normal; color:inherit; }
  .orchestrator-workers :global(.sprint-workspace-status[open] > summary > .sprint-workspace-count) { display:block; }
  .sprint-workspace-count { margin-left:auto; font-size:13px; font-weight:400; color:var(--text-faintest); }
  .sprint-workspace-status-body { padding:var(--space-1) 0; }
  .sprint-item-column .ticket-row { width:100%; margin:0; padding:7px var(--space-3); gap:var(--space-3); border-radius:var(--radius-md); font-size:15px; line-height:1.6; align-items:center; color:var(--text-muted); }
  .sprint-item-column .ticket-row:hover, .sprint-item-column .ticket-row:focus-visible { color:var(--text-strong); }
  .sprint-item-column .ticket-row-title { flex:1; min-width:0; display:block; overflow:visible; white-space:normal; overflow-wrap:anywhere; line-height:inherit; line-clamp:unset; -webkit-line-clamp:unset; -webkit-box-orient:initial; }
  .orchestrator-workers :global(.stage-mark) { width:7px; height:7px; }
  .orchestrator-artifacts { margin-top:var(--space-5); }
  a:focus-visible, button:focus-visible, .orchestrator-workers :global(summary:focus-visible) { outline:var(--border-strong) solid var(--accent-bright); outline-offset:2px; }
</style>
