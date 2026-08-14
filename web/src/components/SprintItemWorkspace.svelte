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
  import ResourceState from "./ResourceState.svelte";
  import SprintTicketRow from "./SprintTicketRow.svelte";
  import TicketConversationHistory from "./TicketConversationHistory.svelte";
  import TicketPriorityControl from "./TicketPriorityControl.svelte";

  let {
    itemId,
    sprintName,
    backHref = "#/sprint"
  }: { itemId: string; sprintName: string; backHref?: string } = $props();

  const workspace = createQuery(() => queries.sprintItemWorkspace(itemId));
  const startValues = createQuery(() => queries.sprintItemConversationStartValues(itemId));
  let conversationId = $state<string | null>(null);
  let selectedPastConversationId = $state<string | null>(null);
  let selectedConversationId = $derived(selectedPastConversationId ?? conversationId);
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

  function saveItem(field: "title" | "body" | "priority", value: string): Promise<unknown> {
    return mutateJson(`/api/items/${encodeURIComponent(itemId)}`, {
      method: "PATCH",
      body: { [field]: value }
    });
  }

  // The brief clamps to three lines. The control appears only when text is hidden,
  // measured the same way the Ticket page measures its recap.
  let briefElement = $state<HTMLElement | null>(null);
  let briefExpanded = $state(false);
  let briefCanExpand = $state(false);

  $effect(() => {
    const body = workspace.data?.body;
    const node = briefElement;
    if (!node) return;
    void body;
    const measure = () => {
      const lineHeight = Number.parseFloat(getComputedStyle(node).lineHeight);
      const maxHeight = Number.isFinite(lineHeight) ? lineHeight * 3 : node.clientHeight;
      briefCanExpand = node.scrollHeight > maxHeight + 1;
    };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(node);
    const frame = requestAnimationFrame(measure);
    return () => {
      cancelAnimationFrame(frame);
      observer?.disconnect();
    };
  });

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
          <a class="sprint-item-back" href={backHref}>‹ {sprintName}</a>
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
            <div
              bind:this={briefElement}
              class="sprint-workspace-brief ticket-recap"
              class:ticket-recap--clamped={briefCanExpand && !briefExpanded}
              data-sprint-item-brief
            >
              <InlineEdit
                value={item.body}
                markdown
                multiline
                placeholder="Write the shared brief for this outcome…"
                onSave={(value) => saveItem("body", value)}
              />
            </div>
            {#if briefCanExpand}
              <button
                type="button"
                class="ticket-recap-more"
                data-sprint-item-brief-toggle
                onclick={() => (briefExpanded = !briefExpanded)}
              >{briefExpanded ? "Show less" : "Show more"}</button>
            {/if}
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
                <div class="sprint-workspace-group" data-workspace-group={group.key}>
                  <div class="sprint-workspace-group-label">
                    <span>{group.label}</span>
                    <span class="sprint-workspace-count">{group.tickets.length}</span>
                  </div>
                  {#each group.tickets as ticket (ticket.id)}
                    {@const condition = sprintTicketCondition(ticket)}
                    <SprintTicketRow
                      priority={ticket.priority}
                      title={ticket.title}
                      state={condition.mark}
                      ariaLabel={condition.word}
                      href={`#/ticket/${ticket.id}`}
                      data-sprint-ticket-id={ticket.id}
                      data-ticket-state={condition.mark}
                    />
                  {/each}
                </div>
              {/each}
            </details>
          {/if}

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

          <details class="sprint-workspace-section" data-workspace-section="remaining">
            <summary>
              <span class="sprint-workspace-section-label">Remaining Tickets</span>
              <span class="sprint-workspace-count">{remainingTickets.length}</span>
              <span class="sprint-workspace-chevron" aria-hidden="true"></span>
            </summary>
            {#if remainingTickets.length}
              {#each remainingTickets as ticket (ticket.id)}
                {@const condition = sprintTicketCondition(ticket)}
                <SprintTicketRow
                  priority={ticket.priority}
                  title={ticket.title}
                  state={condition.mark}
                  ariaLabel={condition.word}
                  href={`#/ticket/${ticket.id}`}
                  quiet={ticket.stage === "done"}
                  data-sprint-ticket-id={ticket.id}
                  data-ticket-state={condition.mark}
                />
              {/each}
            {:else}
              <div class="sprint-workspace-empty">Every Ticket on this outcome is on today.</div>
            {/if}
          </details>
        </div>
      {/if}
    </ResourceState>
  </main>
  <div class="sprint-item-conversation-layer" onclickcapture={dismissConversation}>
    <div class="sprint-item-conversation-column">
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
