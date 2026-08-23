<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { onMount } from "svelte";
  import { mutateJson } from "../lib/mutate";
  import { workspaceAddress } from "../lib/workspaceAddress";
  import { queries } from "../lib/queryCatalogue";
  import {
    remainingWorkspaceTicketGroups,
    todayWorkspaceTicketGroups,
    workspaceArtifactRows,
    workspaceProgress,
    type WorkspaceTicketGroup
  } from "../lib/sprintItemWorkspace";
  import { sprintTicketCondition } from "../lib/sprintPresentation";
  import type { ConversationState } from "../lib/conversation/conversationState";
  import {
    readBackends,
    type BackendSnapshot,
    type DeliveredMessage,
    type OwnerSendBody
  } from "../lib/conversation/wire";
  import ClampedText from "./ClampedText.svelte";
  import InlineEdit from "./InlineEdit.svelte";
  import LiveConversation from "./conversation/LiveConversation.svelte";
  import ResourceState from "./ResourceState.svelte";
  import StageMark from "./StageMark.svelte";
  import TicketConversationHistory from "./TicketConversationHistory.svelte";
  import TicketPriorityControl from "./TicketPriorityControl.svelte";

  let {
    itemId,
    sprintName,
    backHref = "#/sprint"
    // A host that is already a way back needs no link back. Atlas raises this over
    // the world with its own close, so it passes null and the line is not drawn.
  }: { itemId: string; sprintName: string; backHref?: string | null } = $props();

  const workspace = createQuery(() => queries.sprintItemWorkspace(itemId));
  const startValues = createQuery(() => queries.sprintItemConversationStartValues(itemId));
  let conversationId = $state<string | null>(null);
  let selectedPastConversationId = $state<string | null>(null);
  let selectedConversationId = $derived(selectedPastConversationId ?? conversationId);
  let conversationState = $state<ConversationState>("rest");
  let backends = $state<readonly BackendSnapshot[]>([]);

  let todayGroups = $derived(workspace.data ? todayWorkspaceTicketGroups(workspace.data) : []);
  let remainingGroups = $derived(
    workspace.data ? remainingWorkspaceTicketGroups(workspace.data) : []
  );
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

  async function sendMessage(body: OwnerSendBody): Promise<DeliveredMessage> {
    const delivered = await mutateJson<DeliveredMessage>(
      `/api/items/${encodeURIComponent(itemId)}/supervisor/conversation/send`,
      { method: "POST", body }
    );
    conversationId = delivered.conversation_id;
    return delivered;
  }

  async function newConversation(): Promise<void> {
    await mutateJson(
      `/api/items/${encodeURIComponent(itemId)}/supervisor/conversation/reset`,
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

  let artifactRows = $derived(workspace.data ? workspaceArtifactRows(workspace.data) : []);
</script>

{#snippet statusGroups(groups: WorkspaceTicketGroup[], emptyText: string)}
  {#each groups as group (group.key)}
    <details
      class="sprint-workspace-status"
      open={!group.quiet}
      data-workspace-group={group.key}
    >
      <summary>
        <span class="sprint-workspace-status-label">{group.label}</span>
        <span class="sprint-workspace-count">{group.tickets.length}</span>
        <span class="sprint-workspace-chevron" aria-hidden="true"></span>
      </summary>
      <div class="sprint-workspace-status-body">
        {#each group.tickets as ticket (ticket.id)}
          {@const condition = sprintTicketCondition(ticket)}
          <a
            class="ticket-row"
            class:ticket-row--quiet={ticket.stage === "done"}
            href={workspaceAddress({ kind: "ticket", id: ticket.id, openedFromItemId: itemId })}
            data-sprint-ticket-id={ticket.id}
            data-ticket-state={condition.mark}
          >
            <StageMark state={condition.mark} aria-label={condition.word} />
            <span class="ticket-row-title">{ticket.title}</span>
          </a>
        {/each}
      </div>
    </details>
  {/each}
  {#if !groups.length}
    <div class="sprint-workspace-empty">{emptyText}</div>
  {/if}
{/snippet}

<div
  class="sprint-item-page"
  data-sprint-item-workspace={itemId}
  data-sprint-item-view={itemId}
>
  <main class="sprint-item-doc" onclickcapture={dismissConversationToRest}>
    <ResourceState
      error={workspace.error}
      loading={workspace.isLoading}
      hasData={workspace.data !== undefined}
      loadingText="Loading Sprint Item..."
    >
      {#if workspace.data}
        {@const item = workspace.data}
        <div class="sprint-item-column">
          {#if backHref !== null}
            <a class="sprint-item-back" href={backHref}>‹ {sprintName}</a>
          {/if}
          <header class="sprint-workspace-head">
            <div class="sprint-workspace-identity">
              <TicketPriorityControl
                priority={item.priority}
                ariaLabel="Sprint Item priority"
                onChange={(value) => void saveItem("priority", value)}
              />
              <span>·</span><span>{item.project}</span>
              <span>·</span><span>{workspaceProgress(item)}</span>
            </div>
            <h1 class="sprint-workspace-title">
              <InlineEdit
                value={item.title}
                placeholder="(untitled Sprint Item)"
                onSave={(value) => saveItem("title", value)}
              />
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

          <div class="sprint-workspace-work" data-workspace-section="today">
            {@render statusGroups(todayGroups, "Nothing from this outcome is on today.")}
          </div>

          <details class="sprint-workspace-section" data-workspace-section="artifacts">
            <summary>
              <span class="sprint-workspace-section-label">Artifacts</span>
              <span class="sprint-workspace-count">{artifactRows.length}</span>
              <span class="sprint-workspace-chevron" aria-hidden="true"></span>
            </summary>
            <div class="sprint-workspace-section-body">
              {#if artifactRows.length}
                {#each artifactRows as row (row.path)}
                  {#if row.href}
                    <a class="sprint-workspace-artifact" href={row.href}>
                      <span>{row.label}</span><small>{row.kind}</small>
                    </a>
                  {:else}
                    <span
                      class="sprint-workspace-artifact quiet-line"
                      data-artifact-unavailable
                    >
                      <span>{row.label}</span><small>unavailable</small>
                    </span>
                  {/if}
                {/each}
              {:else}
                <div class="sprint-workspace-empty">Nothing kept here yet.</div>
              {/if}
            </div>
          </details>

          <details class="sprint-workspace-section" data-workspace-section="remaining">
            <summary>
              <span class="sprint-workspace-section-label">Remaining Tickets</span>
              <span class="sprint-workspace-count">
                {remainingGroups.reduce((sum, group) => sum + group.tickets.length, 0)}
              </span>
              <span class="sprint-workspace-chevron" aria-hidden="true"></span>
            </summary>
            <div class="sprint-workspace-section-body">
              {@render statusGroups(remainingGroups, "Every Ticket on this outcome is on today.")}
            </div>
          </details>
        </div>
      {/if}
    </ResourceState>
  </main>
  <div class="conversation-layer" onclickcapture={dismissConversation}>
    <div class="conversation-column">
      {#if workspace.data}
        <TicketConversationHistory
          history={workspace.data.conversation_history}
          activeConversationId={conversationId}
          label="Sprint Item conversation"
          bind:selectedPastConversationId
        />
      {/if}
      <LiveConversation
        bind:conversationState
        conversationId={selectedConversationId}
        persistenceKey={`owner:sprint-item:${itemId}`}
        readOnly={selectedPastConversationId !== null}
        label="Sprint Item"
        composerPlaceholder="Message this Sprint Item…"
        {backends}
        startValues={startValues.data ?? null}
        senderLabel="owner"
        {sendMessage}
        onNewConversation={newConversation}
      />
    </div>
  </div>
</div>
