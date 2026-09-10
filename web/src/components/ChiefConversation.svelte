<script lang="ts">
  /** The Chief of Staff's conversation, wherever the Chief is shown.
   *
   * There is nothing here a Ticket does not also have: the same pane, the same two doors,
   * and a conversation the server records against the Chief the way it records a Ticket's
   * against its row. What it starts as comes from the Chief's own managed settings, and
   * this asks the server what that is rather than guessing, so the backend and the models
   * shown before anybody types are the ones a first message would actually run on.
   *
   * It stays separate from the Agents layout because this component owns the canonical
   * Chief conversation while the route owns roster selection and responsive navigation.
   */
  import { createQuery } from "@tanstack/svelte-query";
  import ErrorLine from "./ErrorLine.svelte";
  import LiveConversation from "./conversation/LiveConversation.svelte";
  import { mutateJson } from "../lib/mutate";
  import { queries } from "../lib/queryCatalogue";
  import {
    readBackends,
    type BackendSnapshot,
    type DeliveredMessage,
    type OwnerSendBody
  } from "../lib/conversation/wire";

  let conversationId = $state<string | null>(null);
  let backends = $state<readonly BackendSnapshot[]>([]);
  const currentConversation = createQuery(() => queries.chiefConversation());
  // What a conversation for the Chief would start on. It is a query rather than a read on
  // arrival because the owner changes it on the Config screen, and this must not go on
  // showing what the Chief was configured on before they did.
  const startValues = createQuery(() => queries.chiefConversationStartValues());

  $effect(() => {
    if (currentConversation.data) {
      conversationId = currentConversation.data.conversation_id;
    }
  });

  async function sendToTheChief(body: OwnerSendBody): Promise<DeliveredMessage> {
    const delivered = await mutateJson<DeliveredMessage>("/api/chief/conversation/send", {
      method: "POST",
      body
    });
    if (delivered.conversation_id !== null) conversationId = delivered.conversation_id;
    return delivered;
  }

  async function newConversation(): Promise<void> {
    await mutateJson("/api/chief/conversation/reset", { method: "POST" });
    conversationId = null;
  }

  $effect(() => {
    void readBackends()
      .then((snapshots) => (backends = snapshots))
      .catch(() => {
        // The pickers fall back to the value already in force.
      });
  });
</script>

{#if currentConversation.error}
  <div class="chief-conversation-resource" data-chief-conversation-error>
    <ErrorLine error={currentConversation.error} />
    <button
      type="button"
      class="button button--quiet"
      data-chief-conversation-retry
      onclick={() => currentConversation.refetch()}
    >
      Retry
    </button>
  </div>
{:else if currentConversation.isFetching && !currentConversation.data}
  <div class="quiet-line chief-conversation-resource" data-chief-conversation-loading>
    Loading conversation...
  </div>
{:else}
  <LiveConversation
    {conversationId}
    persistenceKey="owner:chief"
    label="Chief of Staff"
    bind:backends
    startValues={startValues.data ?? null}
    senderLabel="owner"
    sendMessage={sendToTheChief}
    onNewConversation={newConversation}
  />
{/if}
