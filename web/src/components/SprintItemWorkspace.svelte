<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { onMount } from "svelte";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import {
    failedWorkspaceDeliveries,
    remainingWorkspaceTickets,
    todayWorkspaceTicketGroups,
    workspaceProgress
  } from "../lib/sprintItemWorkspace";
  import { sprintTicketCondition } from "../lib/sprintPresentation";
  import type { ConversationState } from "../lib/conversation/conversationState";
  import {
    readBackends,
    type BackendSnapshot,
    type DeliveredMessage,
    type OwnerSendBody
  } from "../lib/conversation/wire";
  import { previewHashHref, sprintItemFileTarget } from "../lib/filePreview";
  import InlineEdit from "./InlineEdit.svelte";
  import LiveConversation from "./conversation/LiveConversation.svelte";
  import PriorityTile from "./PriorityTile.svelte";
  import ResourceState from "./ResourceState.svelte";
  import StageMark from "./StageMark.svelte";

  let { itemId, sprintName }: { itemId: string; sprintName: string } = $props();

  const workspace = createQuery(() => queries.sprintItemWorkspace(itemId));
  const startValues = createQuery(() => queries.sprintItemConversationStartValues(itemId));
  let conversationId = $state<string | null>(null);
  let conversationState = $state<ConversationState>("rest");
  let backends = $state<readonly BackendSnapshot[]>([]);

  let todayGroups = $derived(workspace.data ? todayWorkspaceTicketGroups(workspace.data) : []);
  let remainingTickets = $derived(
    workspace.data ? remainingWorkspaceTickets(workspace.data) : []
  );
  let deliveryFailures = $derived(
    workspace.data ? failedWorkspaceDeliveries(workspace.data) : []
  );

  $effect(() => {
    if (workspace.data) conversationId = workspace.data.supervisor.conversation_id;
  });

  onMount(() => {
    void readBackends()
      .then((snapshots) => (backends = snapshots))
      .catch(() => undefined);
  });

  function saveItem(field: "title" | "body", value: string): Promise<unknown> {
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

  function artifactHref(path: string): string {
    const target = sprintItemFileTarget(itemId, path);
    return target ? previewHashHref(target) : "";
  }

  function artifactKind(path: string): string {
    const dot = path.lastIndexOf(".");
    return dot < 0 ? "file" : path.slice(dot + 1);
  }

  function artifactLabel(path: string): string {
    return path.split("/").at(-1) || path;
  }
</script>

<div class="sprint-item-page" data-sprint-item-workspace={itemId}>
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
          <a class="sprint-item-back" href="#/sprint">‹ {sprintName}</a>
          <header class="sprint-workspace-head">
            <div class="sprint-workspace-identity">
              <PriorityTile priority={item.priority} />
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
            <div class="sprint-workspace-brief" data-sprint-item-brief>
              <InlineEdit
                value={item.body}
                markdown
                multiline
                placeholder="Write the shared brief for this outcome…"
                onSave={(value) => saveItem("body", value)}
              />
            </div>
          </header>

          {#if deliveryFailures.length}
            <div class="sprint-workspace-attention" role="status" data-delivery-attention>
              <strong>Delivery needs attention.</strong>
              {deliveryFailures.length === 1
                ? deliveryFailures[0].last_error || "The supervisor delivery failed."
                : `${deliveryFailures.length} supervisor deliveries failed.`}
            </div>
          {/if}

          {#if todayGroups.length}
            <details class="sprint-workspace-section" open data-workspace-section="today">
              <summary>
                <span class="sprint-workspace-section-label">Today</span>
                <span class="sprint-workspace-count">{todayGroups.reduce((sum, group) => sum + group.tickets.length, 0)}</span>
                <span class="sprint-workspace-chevron" aria-hidden="true"></span>
              </summary>
              {#each todayGroups as group (group.key)}
                <details class="sprint-workspace-group" open data-workspace-group={group.key}>
                  <summary>
                    <span>{group.label}</span>
                    <span class="sprint-workspace-count">{group.tickets.length}</span>
                    <span class="sprint-workspace-chevron" aria-hidden="true"></span>
                  </summary>
                  {#each group.tickets as ticket (ticket.id)}
                    {@const condition = sprintTicketCondition(ticket)}
                    <a class="sprint-workspace-ticket-row" href={`#/ticket/${ticket.id}`} data-ticket-state={condition.mark}>
                      <PriorityTile priority={ticket.priority} />
                      <span>{ticket.title}</span>
                      <StageMark state={condition.mark} aria-label={condition.word} />
                    </a>
                  {/each}
                </details>
              {/each}
            </details>
          {/if}

          <details class="sprint-workspace-section" open data-workspace-section="remaining">
            <summary>
              <span class="sprint-workspace-section-label">Remaining Tickets</span>
              <span class="sprint-workspace-count">{remainingTickets.length}</span>
              <span class="sprint-workspace-chevron" aria-hidden="true"></span>
            </summary>
            {#if remainingTickets.length}
              {#each remainingTickets as ticket (ticket.id)}
                <a
                  class="sprint-workspace-name-row"
                  class:sprint-workspace-name-row--done={ticket.stage === "done"}
                  href={`#/ticket/${ticket.id}`}
                >{ticket.title}</a>
              {/each}
            {:else}
              <div class="sprint-workspace-empty">Every Ticket on this outcome is on today.</div>
            {/if}
          </details>

          <details class="sprint-workspace-section" open data-workspace-section="artifacts">
            <summary>
              <span class="sprint-workspace-section-label">Artifacts</span>
              <span class="sprint-workspace-count">{item.artifacts.length}</span>
              <span class="sprint-workspace-chevron" aria-hidden="true"></span>
            </summary>
            {#if item.artifacts.length}
              {#each item.artifacts as path (path)}
                <a class="sprint-workspace-artifact" href={artifactHref(path)}>
                  <span>{artifactLabel(path)}</span><small>{artifactKind(path)}</small>
                </a>
              {/each}
            {:else}
              <div class="sprint-workspace-empty">Nothing kept here yet.</div>
            {/if}
          </details>
        </div>
      {/if}
    </ResourceState>
  </main>
  <div class="sprint-item-conversation-layer" onclickcapture={dismissConversation}>
    <div class="sprint-item-conversation-column">
      <LiveConversation
        bind:conversationState
        {conversationId}
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
