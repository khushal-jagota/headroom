<script lang="ts">
  /** A Sprint Item's overseer, read in full.
   *
   * Selecting the statue in the world asks for the supervisor itself rather than the
   * Item's page, so this is the supervisor's conversation and nothing else, opened
   * rather than resting. It is the same pane, the same two doors, and the same start
   * values the Sprint Item screen uses — the Item screen simply shows this at the
   * foot of a document, and here it is the whole panel.
   */
  import { createQuery } from "@tanstack/svelte-query";
  import ErrorLine from "../ErrorLine.svelte";
  import LiveConversation from "../conversation/LiveConversation.svelte";
  import { mutateJson } from "../../lib/mutate";
  import { queries } from "../../lib/queryCatalogue";
  import {
    readBackends,
    type BackendSnapshot,
    type DeliveredMessage,
    type OwnerSendBody
  } from "../../lib/conversation/wire";

  let { itemId }: { itemId: string } = $props();

  const workspace = createQuery(() => queries.sprintItemWorkspace(itemId));
  const startValues = createQuery(() => queries.sprintItemConversationStartValues(itemId));

  let conversationId = $state<string | null>(null);
  let backends = $state<readonly BackendSnapshot[]>([]);
  let conversationState = $state<"rest" | "peeked" | "opened" | null>("opened");

  $effect(() => {
    const supervisor = workspace.data?.supervisor;
    if (supervisor) conversationId = supervisor.conversation_id;
  });

  $effect(() => {
    void readBackends()
      .then((snapshots) => (backends = snapshots))
      .catch(() => {
        // The pickers fall back to the value already in force.
      });
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
    await mutateJson(`/api/items/${encodeURIComponent(itemId)}/supervisor/conversation/reset`, {
      method: "POST"
    });
    conversationId = null;
  }
</script>

<div class="atlas-supervisor" data-atlas-supervisor>
  {#if workspace.error}
    <ErrorLine error={workspace.error} />
  {:else if !workspace.data}
    <div class="quiet-line">Loading conversation...</div>
  {:else}
    <div class="atlas-supervisor-head">
      <div class="atlas-supervisor-eyebrow">Sprint Item supervisor</div>
      <a class="atlas-supervisor-title" href={`#/sprint?item=${encodeURIComponent(itemId)}`}>
        {workspace.data.title}
      </a>
    </div>
    <div class="atlas-supervisor-conversation conversation-layer">
      <LiveConversation
        bind:conversationState
        {conversationId}
        label="Sprint Item"
        {backends}
        startValues={startValues.data ?? null}
        senderLabel="owner"
        composerPlaceholder="Message this Sprint Item..."
        sendMessage={sendMessage}
        onNewConversation={newConversation}
      />
    </div>
  {/if}
</div>
